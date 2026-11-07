import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator

from .config import prepare_directory
from .models import Telemetry

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    seq INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    enterprise_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    equipment_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    simulated INTEGER NOT NULL,
    destination TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    metric_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_at TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_queue
    ON telemetry(enterprise_id, agent_id, destination, status, seq);
CREATE INDEX IF NOT EXISTS ix_equipment_history ON telemetry(equipment_id, source_id, seq DESC);
CREATE INDEX IF NOT EXISTS ix_status_history ON telemetry(status, seq DESC);
CREATE TABLE IF NOT EXISTS queue_stats (
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    messages INTEGER NOT NULL,
    metrics INTEGER NOT NULL,
    PRIMARY KEY(destination, status)
);
CREATE TRIGGER IF NOT EXISTS queue_stats_insert AFTER INSERT ON telemetry BEGIN
    INSERT INTO queue_stats VALUES (NEW.destination, NEW.status, 1, NEW.metric_count)
    ON CONFLICT(destination, status) DO UPDATE SET messages=messages+1, metrics=metrics+NEW.metric_count;
END;
CREATE TRIGGER IF NOT EXISTS queue_stats_update AFTER UPDATE OF status ON telemetry
WHEN OLD.status != NEW.status BEGIN
    UPDATE queue_stats SET messages=messages-1, metrics=metrics-OLD.metric_count
    WHERE destination=OLD.destination AND status=OLD.status;
    INSERT INTO queue_stats VALUES (NEW.destination, NEW.status, 1, NEW.metric_count)
    ON CONFLICT(destination, status) DO UPDATE SET messages=messages+1, metrics=metrics+NEW.metric_count;
END;
CREATE TRIGGER IF NOT EXISTS queue_stats_delete AFTER DELETE ON telemetry BEGIN
    UPDATE queue_stats SET messages=messages-1, metrics=metrics-OLD.metric_count
    WHERE destination=OLD.destination AND status=OLD.status;
END;
CREATE TABLE IF NOT EXISTS processed_files (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_history (
    key TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    result TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Queue:
    def __init__(self, path: Path):
        self.path = path
        prepare_directory(path.parent)
        path.touch(mode=0o600, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(
        self, messages: Iterable[Telemetry], *, file_key: str | None = None, destination: str = ""
    ) -> int:
        with self.connection() as db:
            if file_key is not None:
                claim = db.execute("INSERT OR IGNORE INTO processed_files VALUES (?)", (file_key,))
                if claim.rowcount == 0:
                    return 0
            inserted = 0
            for msg in messages:
                row = db.execute(
                    """INSERT INTO telemetry (message_id, enterprise_id, agent_id, equipment_id, source_id,
                    timestamp, simulated, destination, payload, metric_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO NOTHING""",
                    (
                        str(msg.message_id),
                        msg.enterprise_id,
                        str(msg.agent_id),
                        msg.equipment_id,
                        msg.source_id,
                        msg.timestamp.isoformat(),
                        msg.simulated,
                        destination,
                        msg.model_dump_json(),
                        len(msg.metrics),
                    ),
                )
                inserted += row.rowcount
            return inserted

    def seen_file(self, key: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM processed_files WHERE key = ?", (key,)).fetchone() is not None

    def pending(
        self, enterprise_id: str, agent_id: str, limit: int, allow_simulated: bool, destination: str = ""
    ) -> list[Telemetry]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT payload FROM telemetry WHERE enterprise_id=? AND agent_id=? AND destination=? AND status='pending'
                AND (? OR simulated=0) ORDER BY seq LIMIT ?""",
                (enterprise_id, agent_id, destination, allow_simulated, limit),
            ).fetchall()
        return [Telemetry.model_validate_json(row["payload"]) for row in rows]

    def resolve(self, delivered: list[str], blocked: dict[str, str]) -> None:
        with self.connection() as db:
            stamp = now()
            db.executemany(
                "UPDATE telemetry SET status='delivered', resolved_at=?, reason=NULL WHERE message_id=?",
                ((stamp, key) for key in delivered),
            )
            db.executemany(
                "UPDATE telemetry SET status='blocked', resolved_at=?, reason=? WHERE message_id=?",
                ((stamp, reason, key) for key, reason in blocked.items()),
            )

    def requeue_blocked(self, enterprise_id: str, agent_id: str, destination: str = "") -> int:
        with self.connection() as db:
            return db.execute(
                """UPDATE telemetry SET status='pending', resolved_at=NULL, reason=NULL
                WHERE status='blocked' AND enterprise_id=? AND agent_id=? AND destination=?""",
                (enterprise_id, agent_id, destination),
            ).rowcount

    def counts(self, destination: str | None = None) -> dict[str, int]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT status, sum(messages) FROM queue_stats WHERE messages > 0
                AND (? IS NULL OR destination=?) GROUP BY status""",
                (destination, destination),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def total_measurements(self) -> int:
        with self.connection() as db:
            return db.execute("SELECT coalesce(sum(metrics), 0) FROM queue_stats").fetchone()[0]

    def latest(self, equipment: str, source: str) -> Telemetry | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM telemetry WHERE equipment_id=? AND source_id=? ORDER BY seq DESC LIMIT 1",
                (equipment, source),
            ).fetchone()
        return Telemetry.model_validate_json(row[0]) if row else None

    def history(
        self, equipment: str = "", metric: str = "", limit: int = 100, status: str = ""
    ) -> list[tuple[Telemetry, str, str | None]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT payload, status, reason FROM telemetry
                WHERE (?='' OR equipment_id=?)
                AND (?='' OR status=?)
                AND (?='' OR EXISTS(SELECT 1 FROM json_each(payload, '$.metrics') WHERE key=?))
                ORDER BY seq DESC LIMIT ?""",
                (equipment, equipment, status, status, metric, metric, limit),
            ).fetchall()
        return [(Telemetry.model_validate_json(row[0]), row[1], row[2]) for row in rows]

    def source_status(self, source: str, status: str, detail: str | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO source_state VALUES (?, ?, ?, ?)",
                (source, status, now(), detail),
            )

    def sources(self) -> dict:
        with self.connection() as db:
            return {row["source_id"]: dict(row) for row in db.execute("SELECT * FROM source_state")}

    def set_state(self, **values) -> None:
        with self.connection() as db:
            db.executemany(
                "INSERT OR REPLACE INTO agent_state VALUES (?, ?)",
                ((key, json.dumps(value)) for key, value in values.items()),
            )

    def state(self) -> dict:
        with self.connection() as db:
            return {row[0]: json.loads(row[1]) for row in db.execute("SELECT * FROM agent_state")}

    def log(self, event: str, detail: str) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO audit(timestamp, event, detail) VALUES (?, ?, ?)", (now(), event, detail))

    def audit(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
            ]

    def cached_ai(self, key: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT result FROM ai_history WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_ai(self, key: str, result: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO ai_history VALUES (?, ?, ?)", (key, now(), json.dumps(result)))

    def ai_history(self) -> list[tuple[str, dict]]:
        with self.connection() as db:
            return [
                (row[0], json.loads(row[1]))
                for row in db.execute(
                    "SELECT timestamp, result FROM ai_history ORDER BY timestamp DESC LIMIT 100"
                )
            ]
