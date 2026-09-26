"""Unit tests for the allocation planner, including a differential test
against an exhaustive brute-force search over all aligned combinations."""
import ipaddress
import itertools
import random

import pytest

from app.planner import compute_free, plan_allocations

net = ipaddress.ip_network


def plan_strings(free, requests):
    plan = plan_allocations(free, requests)
    return None if plan is None else [str(n) for n in plan]


# ---------------------------------------------------------------- unit cases
def test_single_request_uses_lowest_block():
    free = compute_free(net("10.0.0.0/16"), [], [])
    assert plan_strings(free, [24]) == ["10.0.0.0/24"]


def test_reserved_space_is_skipped():
    free = compute_free(net("10.0.0.0/16"), [net("10.0.0.0/24")], [])
    assert plan_strings(free, [25]) == ["10.0.1.0/25"]


def test_minimising_free_cidrs_beats_lower_address():
    # Free space: a /23 at the low end and an exact /24 higher up.
    # Taking the /24 leaves fewer free CIDRs, so it must win even though
    # carving a /24 out of the /23 would yield a lower address.
    parent = net("10.0.0.0/16")
    reserved = [net("10.0.2.128/25")]
    free = compute_free(parent, reserved, [])
    assert net("10.0.0.0/23") in free and net("10.0.3.0/24") in free
    assert plan_strings(free, [24]) == ["10.0.3.0/24"]


def test_requests_pack_at_low_edge():
    free = compute_free(net("10.0.0.0/16"), [], [])
    assert plan_strings(free, [24, 24, 24]) == [
        "10.0.0.0/24",
        "10.0.1.0/24",
        "10.0.2.0/24",
    ]


def test_mixed_sizes_follow_request_order():
    free = compute_free(net("10.0.0.0/16"), [], [])
    assert plan_strings(free, [25, 24]) == ["10.0.0.0/25", "10.0.1.0/24"]
    assert plan_strings(free, [24, 25]) == ["10.0.0.0/24", "10.0.1.0/25"]


def test_larger_request_after_smaller_one():
    free = compute_free(net("10.0.0.0/16"), [], [])
    assert plan_strings(free, [24, 17]) == ["10.0.0.0/24", "10.0.128.0/17"]


def test_exact_fit_block_is_preferred_over_splitting():
    parent = net("10.0.0.0/8")
    reserved = [net("10.0.0.0/24")]
    free = compute_free(parent, reserved, [])
    # 10.0.1.0/24 fits exactly; splitting anything bigger adds free CIDRs.
    assert plan_strings(free, [24]) == ["10.0.1.0/24"]


def test_infeasible_requests_return_none():
    free = compute_free(net("10.0.0.0/24"), [], [])
    assert plan_allocations(free, [24, 24]) is None
    assert plan_allocations(free, [23]) is None
    assert plan_allocations(free, []) == []


def test_whole_parent_can_be_allocated():
    free = compute_free(net("10.0.0.0/24"), [], [])
    assert plan_strings(free, [24]) == ["10.0.0.0/24"]


# ------------------------------------------------------- differential testing
def _brute_force(parent, reserved, free, requests):
    """Exhaustively try every aligned combination; return the optimal key."""
    candidates = []
    for prefix in requests:
        per_request = []
        for block in free:
            if block.prefixlen <= prefix:
                per_request.extend(
                    block.subnets(prefixlen_diff=prefix - block.prefixlen)
                )
        candidates.append(per_request)
    best = None
    for combo in itertools.product(*candidates):
        if any(a.overlaps(b) for a, b in itertools.combinations(combo, 2)):
            continue
        remaining = compute_free(parent, reserved, list(combo))
        key = (len(remaining), tuple(int(n.network_address) for n in combo))
        if best is None or key < best:
            best = key
    return best


def _random_reserved(rng, parent):
    blocks = list(parent.subnets(prefixlen_diff=4))  # sixteen /28s
    return sorted(rng.sample(blocks, rng.randint(0, 5)))


@pytest.mark.parametrize("seed", range(60))
def test_matches_brute_force(seed):
    rng = random.Random(seed)
    parent = net("10.0.0.0/24")
    reserved = _random_reserved(rng, parent)
    free = compute_free(parent, reserved, [])
    requests = [rng.randint(27, 29) for _ in range(rng.randint(1, 4))]

    # Keep the brute-force search space bounded.
    space = 1
    for prefix in requests:
        space *= sum(
            2 ** (prefix - b.prefixlen) for b in free if b.prefixlen <= prefix
        )
    if space > 60_000:
        pytest.skip("brute-force space too large")

    expected = _brute_force(parent, reserved, free, requests)
    plan = plan_allocations(free, requests)
    if expected is None:
        assert plan is None
    else:
        assert plan is not None
        remaining = compute_free(parent, reserved, plan)
        got = (len(remaining), tuple(int(n.network_address) for n in plan))
        assert got == expected
