"""Bounded on-disk LRU cache. Large RGB arrays stay memory mapped, not in RAM caches."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

DEFAULT_DISK_LIMIT = 5 * 1024**3
MEMORY_CACHE_LIMIT = 1024**3


def cache_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class DiskCache:
    """The worker is serial; readers may hold entries while eviction skips them.

    No decoded image is retained in an in-memory cache. This deliberately spends
    disk bandwidth to keep cache RAM usage below the 1 GiB contract.
    """

    def __init__(self, directory=None, max_bytes=DEFAULT_DISK_LIMIT):
        default = Path(os.environ.get("FILM_LAB_CACHE", Path(tempfile.gettempdir()) / "sony-film-lab-cache"))
        self.directory = (Path(directory) if directory else default).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes)
        if self.max_bytes < 0:
            raise ValueError("Cache size cannot be negative")

    def path(self, key: str, suffix: str = ".npy") -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Cache keys must be SHA-256 hex digests")
        if suffix not in {".npy", ".json", ".png"}:
            raise ValueError("Unsupported cache suffix")
        return self.directory / (key + suffix)

    def get(self, key: str, suffix: str = ".npy") -> Path | None:
        path = self.path(key, suffix)
        if not path.is_file():
            return None
        try:
            os.utime(path, None)
        except OSError:
            pass
        return path

    def temporary(self, suffix=".npy") -> Path:
        return self.directory / ("work-" + uuid.uuid4().hex + suffix + ".part")

    def commit(self, temporary: Path, key: str, suffix: str = ".npy") -> Path:
        target = self.path(key, suffix)
        os.replace(temporary, target)
        return target

    def prune(self, protected=()) -> int:
        """Remove least-recently-used complete entries; never delete active arrays."""
        protected = {Path(p).resolve() for p in protected}
        entries = []
        for path in self.directory.iterdir():
            if not path.is_file() or path.resolve() in protected:
                continue
            if path.name.startswith("work-"):
                # A recent .part can be an active worker's temporary file.
                if time.time() - path.stat().st_mtime > 86400:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                continue
            if ((len(path.stem) == 64 and all(c in "0123456789abcdef" for c in path.stem))
                    or (path.name.startswith("thumb-") and path.suffix == ".png")):
                entries.append((path.stat().st_mtime, path.stat().st_size, path))
        protected_size = sum(p.stat().st_size for p in protected if p.is_file())
        total = sum(s for _, s, _ in entries) + protected_size
        removed = 0
        for _, size, path in sorted(entries):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += size
        return removed
