"""SQLite-backed allocation store with optimistic version checks.

The pool version is a monotonically increasing integer stored in the ``meta``
table.  Every successful commit increments it by exactly one; previews never
change it.  Commits run inside a ``BEGIN IMMEDIATE`` transaction guarded by a
process-local lock, so the version check, the availability re-check and the
inserts are atomic: a conflicting commit writes nothing at all.
"""
from __future__ import annotations

import ipaddress
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator, Sequence

from .config import Network, PoolConfig
from .planner import compute_free

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS allocations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr          TEXT NOT NULL UNIQUE,
    prefix_length INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INITIAL_VERSION = 1


class ConflictError(Exception):
    """A commit could not be applied atomically (maps to HTTP 409)."""


class PoolStore:
    def __init__(self, config: PoolConfig) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(config.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._init_meta()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ meta
    def _init_meta(self) -> None:
        fingerprint = self._config.fingerprint()
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'config_fingerprint'"
        ).fetchone()
        if row is None:
            with self._lock, self._immediate():
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('config_fingerprint', ?)",
                    (fingerprint,),
                )
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('version', ?)",
                    (str(INITIAL_VERSION),),
                )
        elif row[0] != fingerprint:
            raise RuntimeError(
                "pool configuration (POOL_PARENT/POOL_RESERVED) does not match "
                "the existing database; use a fresh DB_PATH or restore the "
                "previous configuration"
            )

    @contextmanager
    def _immediate(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # ----------------------------------------------------------------- reads
    def get_state(self) -> tuple[int, list[Network]]:
        """Current ``(version, allocated networks)`` snapshot."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'version'"
        ).fetchone()
        version = int(row[0])
        rows = self._conn.execute("SELECT cidr FROM allocations ORDER BY id")
        return version, [ipaddress.ip_network(r[0]) for r in rows.fetchall()]

    def free_cidrs(self, allocated: Sequence[Network] | None = None) -> list[Network]:
        if allocated is None:
            _, allocated = self.get_state()
        return compute_free(self._config.parent, self._config.reserved, allocated)

    # ----------------------------------------------------------------- write
    def commit(self, expected_version: int, cidrs: Sequence[Network]) -> int:
        """Atomically persist ``cidrs`` and return the new pool version.

        Raises :class:`ConflictError` — leaving the database untouched — when
        the pool version has moved on or any requested CIDR is no longer
        available (outside the free space, or overlapping another entry).
        """
        with self._lock, self._immediate():
            version, allocated = self.get_state()
            if version != expected_version:
                raise ConflictError(
                    f"pool version mismatch: plan was built on version "
                    f"{expected_version}, current version is {version}"
                )

            parent = self._config.parent
            for net in cidrs:
                if net.version != parent.version:
                    raise ConflictError(
                        f"{net} does not belong to address family of pool {parent}"
                    )

            ordered = sorted(cidrs, key=lambda n: int(n.network_address))
            for prev, cur in zip(ordered, ordered[1:]):
                if prev.overlaps(cur):
                    raise ConflictError(f"allocations overlap: {prev} and {cur}")

            free = self.free_cidrs(allocated)
            for net in cidrs:
                if not any(net.subnet_of(block) for block in free):
                    raise ConflictError(f"{net} is no longer available")

            for net in cidrs:
                self._conn.execute(
                    "INSERT INTO allocations(cidr, prefix_length) VALUES (?, ?)",
                    (str(net), net.prefixlen),
                )
            new_version = version + 1
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'version'",
                (str(new_version),),
            )
            return new_version
