"""Generate a reviewable unified diff for an existing PromVector backend at migration 0006."""

import argparse
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"Backend baseline mismatch at {old!r}. Rebase the integration changes.")
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    base = parser.parse_args().backend
    if not (base / "migrations/versions/0006_queue_existing_telemetry.py").is_file():
        raise SystemExit("Expected the PromVector backend with migration 0006")

    files = {
        "promvector/agent_wire.py": ROOT / "promvector_agent/models.py",
        "promvector/agent_tables.py": ROOT / "backend-integration/agent_tables.py",
        "promvector/api/agent_routes.py": ROOT / "backend-integration/agent_routes.py",
        "promvector/agent_admin.py": ROOT / "backend-integration/agent_admin.py",
        "migrations/versions/0007_edge_agent.py": ROOT / "backend-integration/0007_edge_agent.py",
    }
    changes = {}
    for path, source in files.items():
        if (base / path).exists():
            raise SystemExit(f"Existing target {path}. Review before applying the integration.")
        changes[path] = ("", source.read_text(encoding="utf-8"))

    path = "promvector/main.py"
    old = (base / path).read_text(encoding="utf-8")
    updated = replace(old, "from .api import auth,", "from .api import agent_routes, auth,")
    updated = replace(
        updated,
        "    api.include_router(auth.router)\n",
        "    api.include_router(auth.router)\n    api.include_router(agent_routes.router)\n",
    )
    changes[path] = (old, updated)

    path = "migrations/env.py"
    old = (base / path).read_text(encoding="utf-8")
    changes[path] = (
        old,
        replace(
            old,
            "from promvector.config import Settings",
            "from promvector import agent_tables  # noqa: F401\nfrom promvector.config import Settings",
        ),
    )

    path = "promvector/services/telemetry.py"
    old = (base / path).read_text(encoding="utf-8")
    updated = replace(
        old,
        "from collections import defaultdict\n",
        "from collections import defaultdict\nfrom contextlib import nullcontext\n",
    )
    updated = replace(
        updated,
        "    session: AsyncSession, batch: Ingestion, *, device_identity: tuple[str, str, datetime] | None = None",
        "    session: AsyncSession,\n    batch: Ingestion,\n    *,\n"
        "    device_identity: tuple[str, str, datetime] | None = None,\n"
        "    within_transaction: bool = False,",
    )
    updated = replace(
        updated,
        "    async with session.begin():",
        "    async with (nullcontext() if within_transaction else session.begin()):",
    )
    changes[path] = (old, updated)

    for path, (before, after) in changes.items():
        print(
            "".join(
                unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    f"a/{path}" if before else "/dev/null",
                    f"b/{path}",
                )
            ),
            end="",
        )


if __name__ == "__main__":
    main()
