import asyncio
import sqlite3
from contextlib import closing

from ..config import PostgresSource, SqliteSource
from ..security import Vault, secret_name
from . import Collection
from .tabular import row_key, sample_from_row

SQLITE_FUNCTIONS = {
    "abs",
    "avg",
    "cast",
    "coalesce",
    "count",
    "date",
    "datetime",
    "ifnull",
    "instr",
    "julianday",
    "length",
    "lower",
    "ltrim",
    "max",
    "min",
    "nullif",
    "replace",
    "round",
    "rtrim",
    "strftime",
    "substr",
    "substring",
    "sum",
    "time",
    "total",
    "trim",
    "typeof",
    "unixepoch",
    "upper",
}


def sqlite_authorizer(action, _arg, function, _database, _trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and function in SQLITE_FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def source_secret(source, namespace: str) -> str:
    return "source:" + secret_name(
        namespace, source.kind, source.id, getattr(source, "endpoint", ""), getattr(source, "username", "")
    )


class Database:
    def __init__(self, source: SqliteSource | PostgresSource, vault: Vault, namespace: str):
        self.source = source
        self.vault = vault
        self.namespace = namespace

    def sqlite_rows(self):
        path = self.source.path.resolve(strict=True)
        uri = path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.set_authorizer(sqlite_authorizer)
            remaining = 1000

            def limit_work():
                nonlocal remaining
                remaining -= 1
                return int(remaining <= 0)

            connection.set_progress_handler(limit_work, 1000)
            cursor = connection.execute(
                f"SELECT * FROM ({self.source.query}) AS source_rows LIMIT ?", (self.source.row_limit,)
            )
            return [dict(row) for row in cursor], [item[0] for item in cursor.description]

    async def postgres_rows(self):
        from psycopg import AsyncConnection, sql
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row

        dsn = self.vault.get(source_secret(self.source, self.namespace))
        if not dsn:
            raise ValueError("Store the PostgreSQL DSN in the OS credential store first")
        options = conninfo_to_dict(dsn)
        if (
            options.get("sslmode") != "verify-full"
            or not options.get("host")
            or options["host"].startswith("/")
        ):
            raise ValueError("PostgreSQL requires a hostname and sslmode=verify-full")
        async with await AsyncConnection.connect(dsn, connect_timeout=5, row_factory=dict_row) as connection:
            await connection.set_read_only(True)
            async with connection.cursor() as cursor:
                await cursor.execute("SET LOCAL statement_timeout = '5s'")
                await cursor.execute(
                    sql.SQL("SELECT * FROM ({}) AS source_rows LIMIT {}").format(
                        sql.SQL(self.source.query), sql.Literal(self.source.row_limit)
                    ),
                    prepare=True,
                )
                return await cursor.fetchall(), [item.name for item in cursor.description]

    async def collection(self):
        if isinstance(self.source, SqliteSource):
            rows, columns = await asyncio.to_thread(self.sqlite_rows)
        else:
            async with asyncio.timeout(12):
                rows, columns = await self.postgres_rows()
        required = set(self.source.columns) | {self.source.timestamp_column, self.source.row_id_column}
        if len(set(columns)) != len(columns) or not required.issubset(columns):
            raise ValueError("Query must return unique column names and all mapped fields")
        samples = []
        invalid = 0
        for row in rows:
            try:
                identifier = row[self.source.row_id_column]
                sample = sample_from_row(self.source, row, {"row_id": str(identifier)})
                samples.append(sample.model_copy(update={"event_key": row_key(identifier, sample)}))
            except (ValueError, TypeError, KeyError, OverflowError):
                invalid += 1
        return Collection(samples), invalid

    async def test(self):
        _, invalid = await self.collection()
        if invalid:
            raise ValueError("The query returned invalid measurements")

    async def collect(self):
        result, invalid = await self.collection()
        if result.samples:
            yield result
        if invalid:
            raise ValueError(f"{invalid} invalid database row(s)")
