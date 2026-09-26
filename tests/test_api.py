"""End-to-end API tests: preview/commit flow, versioning and conflicts."""
import ipaddress
import threading

from app.config import load_config
from app.db import ConflictError, PoolStore

PREVIEW = "/api/v1/allocations/preview"
COMMIT = "/api/v1/allocations/commit"
POOL = "/api/v1/pool"


def _preview(client, prefixes):
    return client.post(PREVIEW, json={"prefix_lengths": prefixes})


def _commit(client, version, allocations):
    return client.post(COMMIT, json={"version": version, "allocations": allocations})


# ------------------------------------------------------------------ preview
def test_preview_returns_version_and_plan(client):
    resp = _preview(client, [25, 26])
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["allocations"] == ["10.0.1.0/25", "10.0.1.128/26"]
    assert body["remaining_free_cidrs"]


def test_preview_does_not_change_pool_state(client):
    before = client.get(POOL).json()
    _preview(client, [24, 24])
    after = client.get(POOL).json()
    assert before == after


def test_preview_infeasible_returns_409(client):
    assert _preview(client, [16, 16]).status_code == 409
    assert _preview(client, [15]).status_code == 409
    assert _preview(client, [33]).status_code == 409  # too long for IPv4


def test_preview_validates_batch_size(client):
    assert _preview(client, []).status_code == 422
    assert _preview(client, [24] * 9).status_code == 422
    assert _preview(client, [-1]).status_code == 422
    assert _preview(client, [129]).status_code == 422


# ------------------------------------------------------------------- commit
def test_commit_success_bumps_version(client):
    plan = _preview(client, [24]).json()
    resp = _commit(client, plan["version"], plan["allocations"])
    assert resp.status_code == 201
    assert resp.json() == {"version": 2, "allocations": ["10.0.1.0/24"]}

    pool = client.get(POOL).json()
    assert pool["version"] == 2
    assert pool["allocated"] == ["10.0.1.0/24"]
    assert "10.0.1.0/24" not in pool["free_cidrs"]


def test_commit_replay_conflicts_and_changes_nothing(client):
    plan = _preview(client, [24]).json()
    assert _commit(client, plan["version"], plan["allocations"]).status_code == 201
    replay = _commit(client, plan["version"], plan["allocations"])
    assert replay.status_code == 409
    pool = client.get(POOL).json()
    assert pool["version"] == 2
    assert pool["allocated"] == ["10.0.1.0/24"]


def test_commit_with_stale_version_conflicts(client):
    stale = _preview(client, [24]).json()
    other = _preview(client, [24]).json()
    assert _commit(client, other["version"], other["allocations"]).status_code == 201

    resp = _commit(client, stale["version"], stale["allocations"])
    assert resp.status_code == 409
    assert "version" in resp.json()["detail"]

    pool = client.get(POOL).json()
    assert pool["version"] == 2
    assert pool["allocated"] == ["10.0.1.0/24"]


def test_commit_unavailable_plan_conflicts_without_partial_writes(client):
    # First CIDR is free, second overlaps the reserved range: nothing may
    # be written at all.
    resp = _commit(client, 1, ["10.0.5.0/24", "10.0.0.0/25"])
    assert resp.status_code == 409
    pool = client.get(POOL).json()
    assert pool["version"] == 1
    assert pool["allocated"] == []


def test_commit_overlapping_allocations_conflict(client):
    resp = _commit(client, 1, ["10.0.2.0/24", "10.0.2.128/25"])
    assert resp.status_code == 409
    assert client.get(POOL).json()["allocated"] == []


def test_commit_outside_pool_conflicts(client):
    assert _commit(client, 1, ["192.168.0.0/24"]).status_code == 409
    assert _commit(client, 1, ["10.0.0.0/15"]).status_code == 409


def test_commit_rejects_malformed_cidrs(client):
    assert _commit(client, 1, ["not-a-cidr"]).status_code == 422
    assert _commit(client, 1, ["10.0.1.7/24"]).status_code == 422  # host bits set
    assert _commit(client, 1, []).status_code == 422


def test_sequential_commits_keep_incrementing_version(client):
    first = _preview(client, [24]).json()
    assert _commit(client, first["version"], first["allocations"]).status_code == 201
    second = _preview(client, [24]).json()
    assert second["version"] == 2
    assert second["allocations"] == ["10.0.2.0/24"]
    assert _commit(client, second["version"], second["allocations"]).status_code == 201
    assert client.get(POOL).json()["version"] == 3


# ------------------------------------------------------- store-level atomicity
def test_concurrent_commits_only_one_wins(tmp_path):
    config = load_config(
        {
            "POOL_PARENT": "10.0.0.0/16",
            "POOL_RESERVED": "",
            "DB_PATH": str(tmp_path / "race.db"),
        }
    )
    store = PoolStore(config)
    outcomes = []

    def attempt():
        try:
            store.commit(1, [ipaddress.ip_network("10.0.0.0/24")])
            outcomes.append("ok")
        except ConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()

    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 7
