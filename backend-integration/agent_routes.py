from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import Field, SecretStr, ValidationError
from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from ..agent_tables import agents, receipts
from ..agent_wire import Identifier, StrictModel, Telemetry
from ..auth import audit, digest, limit_requests
from ..db import Session
from ..errors import ApiError
from ..models import Equipment, Sensor
from ..schemas import Ingestion, Reading
from ..services.telemetry import ingest

router = APIRouter(tags=["Standalone PromVector Agent"])
Authorization = Annotated[str | None, Header()]
SIMULATOR_PREFIX = "[SIMULATOR] "


class Enrollment(StrictModel):
    enterprise_id: Identifier
    agent_id: UUID
    agent_token: SecretStr = Field(min_length=48, max_length=96)
    version: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9.+-]+$")


class Batch(StrictModel):
    messages: list[Telemetry] = Field(min_length=1, max_length=500)


class Heartbeat(StrictModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9.+-]+$")
    pending: int = Field(ge=0, le=2**31 - 1)


def token_digest(header: str | None) -> str:
    kind, _, token = (header or "").partition(" ")
    if kind.lower() != "bearer" or not 48 <= len(token) <= 96 or not token.isascii():
        raise ApiError(401, "agent_unauthorized", "Agent authentication failed")
    return digest(token)


async def authenticate(session: Session, header: str | None, *, write: bool = False):
    result = await session.execute(
        select(agents)
        .where(agents.c.token_hash == token_digest(header), agents.c.revoked_at.is_(None))
        .with_for_update(read=not write)
    )
    agent = result.mappings().one_or_none()
    if agent is None:
        raise ApiError(401, "agent_unauthorized", "Agent authentication failed")
    return agent


@router.post("/agents/register")
async def register(request: Request, body: Enrollment, session: Session, authorization: Authorization = None):
    await limit_requests(
        request, f"agent-enrollment:{request.client.host if request.client else 'unknown'}", 30
    )
    invitation = token_digest(authorization)
    candidate = digest(body.agent_token.get_secret_value())
    async with session.begin():
        result = await session.execute(
            select(agents)
            .where(
                agents.c.id == body.agent_id,
                agents.c.enrollment_hash == invitation,
                agents.c.factory_id == body.enterprise_id,
                agents.c.revoked_at.is_(None),
            )
            .with_for_update()
        )
        agent = result.mappings().one_or_none()
        if (
            not agent
            or agent["enrollment_until"] <= datetime.now(UTC)
            or agent["token_hash"] not in (None, candidate)
        ):
            raise ApiError(401, "enrollment_denied", "Enrollment is invalid, expired or already used")
        if agent["token_hash"] is None:
            await session.execute(
                update(agents)
                .where(agents.c.id == body.agent_id)
                .values(token_hash=candidate, version=body.version)
            )
            audit(session, "edge_agent.register", target=str(body.agent_id))
    return {"enterprise_id": body.enterprise_id, "agent_id": body.agent_id}


@router.post("/agents/heartbeat")
async def heartbeat(body: Heartbeat, session: Session, authorization: Authorization = None):
    async with session.begin():
        agent = await authenticate(session, authorization, write=True)
        await session.execute(
            update(agents)
            .where(agents.c.id == agent["id"])
            .values(
                last_heartbeat=datetime.now(UTC),
                version=body.version,
                pending=body.pending,
            )
        )
    return {"status": "ok"}


@router.get("/agents/configuration")
async def configuration(session: Session, authorization: Authorization = None):
    async with session.begin():
        agent = await authenticate(session, authorization)
        return {
            "agent_id": agent["id"],
            "enterprise_id": agent["factory_id"],
            "equipment_ids": agent["equipment_ids"],
            "allow_simulated": agent["allow_simulated"],
        }


def reject(code: str):
    raise ApiError(422, code, "Message is outside its approved telemetry contract")


async def persist(session: Session, message: Telemetry, agent) -> str:
    if message.agent_id != agent["id"] or message.enterprise_id != agent["factory_id"]:
        reject("agent_identity_mismatch")
    if message.equipment_id not in agent["equipment_ids"]:
        reject("equipment_outside_scope")
    if message.simulated != (message.source == "simulator") or (
        message.simulated and not agent["allow_simulated"]
    ):
        reject("simulation_not_allowed")

    payload = message.model_dump(mode="json")
    claimed = await session.scalar(
        insert(receipts)
        .values(message_id=message.message_id, agent_id=message.agent_id, payload=payload)
        .on_conflict_do_nothing(index_elements=[receipts.c.message_id])
        .returning(receipts.c.message_id)
    )
    if claimed is None:
        existing = await session.scalar(
            select(receipts.c.payload).where(receipts.c.message_id == message.message_id)
        )
        if existing != payload:
            reject("message_id_conflict")
        return "duplicate"

    equipment = await session.scalar(
        select(Equipment)
        .where(
            Equipment.id == message.equipment_id,
            Equipment.factory_id == agent["factory_id"],
            Equipment.archived_at.is_(None),
        )
        .with_for_update(read=True)
    )
    if not equipment:
        reject("equipment_unavailable")
    if equipment.name.startswith(SIMULATOR_PREFIX) != message.simulated:
        reject("simulation_equipment_mismatch")

    metrics = {code: metric for code, metric in message.metrics.items() if type(metric.value) in (int, float)}
    if message.quality != "good" or not metrics:
        return "accepted"
    keys = [(message.equipment_id, code) for code in metrics]
    sensors = {
        sensor.code: sensor
        for sensor in await session.scalars(
            select(Sensor)
            .where(tuple_(Sensor.equipment_id, Sensor.code).in_(keys), Sensor.active.is_(True))
            .with_for_update(read=True)
        )
    }
    if set(sensors) != set(metrics):
        reject("unknown_sensor")

    readings = []
    for code, metric in metrics.items():
        if (metric.unit or "") != sensors[code].unit:
            reject("sensor_unit_mismatch")
        try:
            readings.append(
                Reading(
                    equipment_id=message.equipment_id,
                    sensor_code=code,
                    at=message.timestamp,
                    value=metric.value,
                )
            )
        except (ValidationError, OverflowError):
            reject("invalid_reading")
    await ingest(session, Ingestion(readings=readings), within_transaction=True)
    return "accepted"


@router.post("/telemetry/batch")
async def batch(body: Batch, session: Session, authorization: Authorization = None):
    ids = [message.message_id for message in body.messages]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "duplicate_batch_ids", "A batch must contain distinct message IDs")

    acknowledgments = []
    async with session.begin():
        agent = await authenticate(session, authorization)
        # Stable ordering reduces deadlocks when two requests overlap.
        for message in sorted(
            body.messages, key=lambda item: (item.equipment_id, item.timestamp, item.message_id)
        ):
            receipt = {"message_id": message.message_id}
            try:
                async with session.begin_nested():
                    if message.timestamp > datetime.now(UTC) + timedelta(minutes=5):
                        reject("timestamp_in_future")
                    receipt["status"] = await persist(session, message, agent)
            except ApiError as error:
                if error.status not in (404, 409, 422):
                    raise
                receipt.update(status="rejected", code=error.code)
            acknowledgments.append(receipt)
    return {"receipts": acknowledgments}
