import asyncio
import csv
import io
import json
import os
import stat
import time
from hashlib import sha256
from pathlib import Path

from ..config import FileSource
from ..storage import Queue
from . import Collection
from .tabular import sample_from_row

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
SUPPORTED = {".csv", ".json"}


def unique_object(items):
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("JSON object has duplicate fields")
    return result


def parse(path: Path, content: bytes):
    text = content.decode("utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text, object_pairs_hook=unique_object)
        rows = data if isinstance(data, list) else [data]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("Expected a JSON object or array of objects")
        return rows

    import pandas as pd

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = next(reader, [])
    if not header or "" in header or len(set(header)) != len(header):
        raise ValueError("CSV requires unique, nonempty column names")
    rows = []
    for row in reader:
        if len(row) != len(header):
            raise ValueError("CSV row length differs from header")
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValueError("CSV exceeds row limit")
    return pd.DataFrame(rows, columns=header).to_dict(orient="records")


def read_complete(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise ValueError("Symbolic links are not imported")
    with os.fdopen(os.open(path, flags), "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError("File must be regular and no larger than 10 MiB")
        if time.time() - before.st_mtime < 1:
            return None
        content = handle.read(MAX_BYTES + 1)
        after = os.fstat(handle.fileno())
        if len(content) != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            return None
    return content


class Files:
    def __init__(self, source: FileSource, queue: Queue, namespace: str):
        self.source = source
        self.queue = queue
        self.namespace = namespace

    def paths(self):
        target = self.source.path
        if target.is_symlink():
            raise ValueError("The selected path cannot be a symbolic link")
        if target.is_dir():
            return sorted(
                path
                for path in target.iterdir()
                if path.suffix.lower() in SUPPORTED and not path.is_symlink() and path.is_file()
            )
        if not target.is_file() or target.suffix.lower() not in SUPPORTED:
            raise ValueError("Select an existing CSV, JSON or monitored folder")
        return [target]

    def load(self, path: Path, *, check: bool = False):
        content = read_complete(path)
        if content is None:
            if check:
                raise ValueError("The file is still being written")
            return None
        digest = sha256(content).hexdigest()
        key = f"{self.namespace}/{self.source.id}/{self.source.equipment_id}/{digest}"
        if not check and self.queue.seen_file(key):
            return None
        rows = parse(path, content)
        if not rows or len(rows) > MAX_ROWS:
            raise ValueError("File must contain 1 to 50,000 rows")
        samples = [
            sample_from_row(
                self.source,
                row,
                {"file": path.name, "row": number, "sha256": digest},
                identity=f"{digest}/{number}",
            )
            for number, row in enumerate(rows, 1)
        ]
        return Collection(samples, key)

    async def test(self):
        paths = await asyncio.to_thread(self.paths)
        if paths:
            await asyncio.to_thread(self.load, paths[0], check=True)

    async def collect(self):
        failed = 0
        for path in await asyncio.to_thread(self.paths):
            try:
                collection = await asyncio.to_thread(self.load, path)
            except (ValueError, OSError, KeyError, TypeError, OverflowError, csv.Error):
                failed += 1
                continue
            if collection:
                yield collection
        if failed:
            raise ValueError(f"{failed} file(s) could not be imported")
