"""Exact CIDR allocation planner.

Objective (per specification), applied in order:

1. minimise the number of free CIDR blocks remaining after the allocation;
2. among all plans achieving that minimum, pick the one whose sequence of
   network addresses — compared in request order — is lexicographically
   smallest.

The search is exhaustive yet cheap thanks to one structural fact: carving an
aligned child of prefix ``p`` out of a free block of prefix ``q`` always
leaves exactly one remainder block of each prefix ``q+1, q+2, ..., p``,
*regardless of where inside the block the child is placed*.  Consequently the
number of remaining free CIDRs depends only on **which** free block each
request is carved from, never on the position inside the block.  We exploit
this in two phases:

* a memoised shape-level search (shapes = sorted multisets of block prefix
  lengths) finds the minimal achievable remaining-CIDR count — this is the
  exhaustive search over all feasible aligned combinations, collapsed to
  the only dimension that affects the count;
* a left-to-right greedy then fixes each request, in request order, to the
  lowest-addressed concrete block that still admits an optimal completion,
  which yields the lexicographically smallest plan among all optimal ones.
"""
from __future__ import annotations

import ipaddress
from typing import Optional, Sequence, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

INF = 10**9  # sentinel for "infeasible" in the shape search


def compute_free(
    parent: Network,
    reserved: Sequence[Network],
    allocated: Sequence[Network],
) -> list[Network]:
    """Parent pool minus reserved and allocated subnets, as collapsed CIDRs."""
    free = [parent]
    holes = sorted(
        (*reserved, *allocated), key=lambda n: (n.version, int(n.network_address))
    )
    for hole in holes:
        remaining: list[Network] = []
        for block in free:
            if hole.subnet_of(block):
                if hole != block:
                    remaining.extend(block.address_exclude(hole))
                # hole == block: the block is fully consumed, drop it
            else:
                # Invariant: holes never partially overlap a free block
                # (reserved/allocated are disjoint and inside the parent).
                remaining.append(block)
        free = remaining
    return list(ipaddress.collapse_addresses(free))


def _shape(blocks: Sequence[Network]) -> tuple[int, ...]:
    """Shape of a free-block list: sorted multiset of prefix lengths."""
    return tuple(sorted(b.prefixlen for b in blocks))


class _ShapeOracle:
    """Memoised exhaustive search over free-block shapes."""

    def __init__(self) -> None:
        self._memo: dict[tuple[tuple[int, ...], tuple[int, ...]], int] = {}

    def min_remaining(self, blocks: tuple[int, ...], reqs: tuple[int, ...]) -> int:
        """Minimal free-CIDR count reachable by placing all of ``reqs``.

        ``blocks`` is the sorted multiset of free-block prefix lengths,
        ``reqs`` the sorted multiset of not-yet-placed request prefixes.
        Returns ``INF`` when the requests cannot all be placed.
        """
        if not reqs:
            return len(blocks)
        key = (blocks, reqs)
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        prefix = reqs[0]
        rest = reqs[1:]
        best = INF
        tried: set[int] = set()
        for idx, block_prefix in enumerate(blocks):
            if block_prefix > prefix or block_prefix in tried:
                continue
            tried.add(block_prefix)
            nxt = list(blocks)
            nxt.pop(idx)
            # Carving ``prefix`` out of ``block_prefix`` leaves one block of
            # each intermediate prefix length (see module docstring).
            nxt.extend(range(block_prefix + 1, prefix + 1))
            nxt.sort()
            best = min(best, self.min_remaining(tuple(nxt), rest))
        self._memo[key] = best
        return best


def plan_allocations(
    free: Sequence[Network], requests: Sequence[int]
) -> Optional[list[Network]]:
    """Choose one aligned subnet per requested prefix length.

    ``free`` is the current list of free CIDR blocks; ``requests`` are prefix
    lengths in the order they must appear in the returned plan.  Returns the
    optimal plan (one network per request, in request order), or ``None``
    when no feasible combination exists.
    """
    if not requests:
        return []
    if free:
        max_prefixlen = free[0].max_prefixlen
        if any(p < 0 or p > max_prefixlen for p in requests):
            return None

    oracle = _ShapeOracle()
    optimal = oracle.min_remaining(_shape(free), tuple(sorted(requests)))
    if optimal >= INF:
        return None

    concrete = sorted(free, key=lambda n: int(n.network_address))
    plan: list[Network] = []
    for idx, prefix in enumerate(requests):
        rest = tuple(sorted(requests[idx + 1 :]))
        current_shape = _shape(concrete)
        chosen: Optional[Network] = None
        # Lowest-addressed block first: the first block that still admits an
        # optimal completion gives this request its smallest possible address.
        for block in concrete:
            block_prefix = block.prefixlen
            if block_prefix > prefix:
                continue
            nxt = list(current_shape)
            nxt.remove(block_prefix)
            nxt.extend(range(block_prefix + 1, prefix + 1))
            nxt.sort()
            if oracle.min_remaining(tuple(nxt), rest) == optimal:
                chosen = block
                break
        if chosen is None:  # pragma: no cover - guaranteed by the invariant
            raise RuntimeError("planner invariant violated: no optimal block")
        # The block's own network address is always aligned for ``prefix``.
        subnet = type(chosen)((int(chosen.network_address), prefix))
        plan.append(subnet)
        concrete.remove(chosen)
        if chosen != subnet:
            concrete.extend(chosen.address_exclude(subnet))
        concrete.sort(key=lambda n: int(n.network_address))
    return plan
