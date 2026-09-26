"""Pool configuration, loaded from environment variables.

- ``POOL_PARENT``:   the parent CIDR of the address pool (e.g. ``10.0.0.0/8``).
- ``POOL_RESERVED``: comma-separated CIDRs inside the parent that are never
  handed out (e.g. ``10.0.0.0/16,10.1.0.0/16``). May be empty.
- ``DB_PATH``:       location of the SQLite database file.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
from dataclasses import dataclass
from typing import Mapping, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

DEFAULT_PARENT = "10.0.0.0/8"
DEFAULT_DB_PATH = "pool.db"


@dataclass(frozen=True)
class PoolConfig:
    parent: Network
    reserved: tuple[Network, ...]
    db_path: str

    def fingerprint(self) -> str:
        """Stable hash of the pool layout, used to detect config changes."""
        digest = hashlib.sha256()
        digest.update(str(self.parent).encode())
        for net in self.reserved:
            digest.update(b"\x00")
            digest.update(str(net).encode())
        return digest.hexdigest()


def _parse_network(raw: str, what: str) -> Network:
    try:
        return ipaddress.ip_network(raw.strip(), strict=True)
    except ValueError as exc:
        raise ValueError(f"invalid {what} CIDR {raw!r}: {exc}") from exc


def load_config(environ: Mapping[str, str] | None = None) -> PoolConfig:
    env = os.environ if environ is None else environ

    parent = _parse_network(env.get("POOL_PARENT", DEFAULT_PARENT), "POOL_PARENT")

    reserved: list[Network] = []
    for item in env.get("POOL_RESERVED", "").split(","):
        item = item.strip()
        if not item:
            continue
        net = _parse_network(item, "POOL_RESERVED")
        if net.version != parent.version or not net.subnet_of(parent):
            raise ValueError(
                f"reserved CIDR {net} is not contained in parent pool {parent}"
            )
        reserved.append(net)

    reserved.sort(key=lambda n: int(n.network_address))
    for prev, cur in zip(reserved, reserved[1:]):
        if prev.overlaps(cur):
            raise ValueError(f"reserved CIDRs overlap: {prev} and {cur}")

    return PoolConfig(
        parent=parent,
        reserved=tuple(reserved),
        db_path=env.get("DB_PATH", DEFAULT_DB_PATH),
    )
