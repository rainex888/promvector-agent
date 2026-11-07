import argparse
import asyncio
import getpass
import json
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .ai import gemini_secret
from .collectors.databases import source_secret
from .config import DATA_DIR, load_settings, prepare_directory
from .runtime import Runtime
from .security import KeyringVault
from .storage import Queue


def configure_logs(directory: Path):
    path = directory / "agent.log"
    path.touch(mode=0o600, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("promvector_agent")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def run_headless(runtime: Runtime):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(_signum, _frame):
        loop.call_soon_threadsafe(stop.set)

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, request_stop)
    await runtime.run(stop)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="promvector-agent", description="PromVector industrial telemetry agent"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("desktop")
    commands.add_parser("run")
    commands.add_parser("status")
    commands.add_parser("register")
    commands.add_parser("check")
    credential = commands.add_parser("credential")
    credential.add_argument("target", help="A configured source ID, or gemini")
    args = parser.parse_args(argv)
    directory = args.data_dir.expanduser().absolute()
    prepare_directory(directory)
    configure_logs(directory)

    from PySide6.QtCore import QLockFile

    lock = QLockFile(str(directory / "agent.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        parser.exit(2, "PromVector Agent is already using this data directory.\n")
    try:
        path = directory / "settings.json"
        settings = load_settings(path)
        queue = Queue(directory / "queue.sqlite3")
        vault = KeyringVault()
        runtime = Runtime(settings, queue, vault)
        if args.command in (None, "desktop"):
            from .ui import launch

            return launch(path, runtime)
        if args.command == "run":
            asyncio.run(run_headless(runtime), loop_factory=asyncio.SelectorEventLoop)
        elif args.command == "status":
            print(
                json.dumps(
                    {
                        "agent_id": str(settings.agent_id),
                        "enterprise_id": settings.enterprise_id,
                        "queue": queue.counts(),
                        "communication": queue.state(),
                        "sources": queue.sources(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "register":
            asyncio.run(runtime.communication.register(getpass.getpass("Enrollment token: ")))
            print("Registered")
        elif args.command == "check":
            print(asyncio.run(runtime.communication.check()).model_dump_json(indent=2))
        elif args.command == "credential":
            if args.target == "gemini":
                key = gemini_secret(runtime.namespace)
            else:
                source = next((source for source in settings.sources if source.id == args.target), None)
                if source is None or source.kind not in ("postgresql", "opc_ua"):
                    parser.error("Select a PostgreSQL or OPC UA source, or gemini")
                key = source_secret(source, runtime.namespace)
            secret = getpass.getpass("Credential: ")
            if not secret:
                parser.error("Credential is empty")
            vault.set(key, secret)
            queue.log("credential_saved", "Credential updated in OS store")
        return 0
    except Exception as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        print(
            f"PromVector Agent: {type(exc).__name__}" + (f" (HTTP {code})" if code else ""), file=sys.stderr
        )
        return 1
    finally:
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
