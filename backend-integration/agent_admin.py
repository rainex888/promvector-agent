"""Provision short-lived enrollment tokens or revoke an agent from a trusted server shell."""

import argparse
import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import insert, select, update

from .agent_tables import agents
from .api.agent_routes import SIMULATOR_PREFIX
from .auth import digest
from .config import Settings
from .db import make_engine
from .models import Equipment


async def administer(args):
    engine = make_engine(Settings())
    try:
        async with engine.begin() as connection:
            if args.action == "revoke":
                result = await connection.execute(
                    update(agents)
                    .where(agents.c.id == args.agent_id, agents.c.revoked_at.is_(None))
                    .values(revoked_at=datetime.now(UTC))
                )
                if not result.rowcount:
                    raise SystemExit("No active matching agent")
                from .models import AuditLog

                await connection.execute(
                    insert(AuditLog).values(action="edge_agent.revoke", target=str(args.agent_id))
                )
                return

            equipment = (
                await connection.execute(
                    select(Equipment.id, Equipment.name).where(
                        Equipment.id.in_(args.equipment_ids),
                        Equipment.factory_id == args.factory,
                        Equipment.archived_at.is_(None),
                    )
                )
            ).all()
            if {item.id for item in equipment} != set(args.equipment_ids):
                raise SystemExit("Every equipment ID must be active and belong to the selected factory")
            if not args.allow_simulated and any(item.name.startswith(SIMULATOR_PREFIX) for item in equipment):
                raise SystemExit("Explicitly allow simulator equipment when provisioning this agent")
            invitation = secrets.token_urlsafe(48)
            await connection.execute(
                insert(agents).values(
                    id=args.agent_id,
                    factory_id=args.factory,
                    equipment_ids=sorted(set(args.equipment_ids)),
                    enrollment_hash=digest(invitation),
                    enrollment_until=datetime.now(UTC) + timedelta(minutes=args.minutes),
                    allow_simulated=args.allow_simulated,
                )
            )
        print(invitation)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    provision = actions.add_parser("provision")
    provision.add_argument("agent_id", type=UUID)
    provision.add_argument("factory")
    provision.add_argument("equipment_ids", nargs="+")
    provision.add_argument("--minutes", type=int, choices=range(1, 61), default=15, metavar="1-60")
    provision.add_argument("--allow-simulated", action="store_true")
    actions.add_parser("revoke").add_argument("agent_id", type=UUID)
    asyncio.run(administer(parser.parse_args()))


if __name__ == "__main__":
    main()
