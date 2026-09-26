"""FastAPI application exposing the CIDR batch allocation API."""
from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config import load_config
from .db import ConflictError, PoolStore
from .planner import compute_free, plan_allocations
from .schemas import (
    CommitRequest,
    CommitResponse,
    PoolInfo,
    PreviewRequest,
    PreviewResponse,
)

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    config = load_config()
    store = PoolStore(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        store.close()

    app = FastAPI(
        title="Intranet CIDR Allocator",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.store = store

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/")
    def root() -> dict:
        return {
            "service": "intranet-cidr-allocator",
            "api": API_PREFIX,
            "docs": "/docs",
        }

    @app.get(f"{API_PREFIX}/pool", response_model=PoolInfo)
    def pool_info() -> PoolInfo:
        version, allocated = store.get_state()
        free = compute_free(config.parent, config.reserved, allocated)
        return PoolInfo(
            version=version,
            parent=str(config.parent),
            reserved=[str(n) for n in config.reserved],
            allocated=[str(n) for n in allocated],
            free_cidrs=[str(n) for n in free],
        )

    @app.post(f"{API_PREFIX}/allocations/preview", response_model=PreviewResponse)
    def preview(request: PreviewRequest) -> PreviewResponse:
        version, allocated = store.get_state()
        free = compute_free(config.parent, config.reserved, allocated)
        plan = plan_allocations(free, request.prefix_lengths)
        if plan is None:
            raise HTTPException(
                status_code=409,
                detail="requested prefixes cannot be satisfied from the "
                "remaining pool space",
            )
        remaining = compute_free(config.parent, config.reserved, [*allocated, *plan])
        return PreviewResponse(
            version=version,
            allocations=[str(n) for n in plan],
            remaining_free_cidrs=[str(n) for n in remaining],
        )

    @app.post(
        f"{API_PREFIX}/allocations/commit",
        response_model=CommitResponse,
        status_code=201,
    )
    def commit(request: CommitRequest) -> CommitResponse:
        cidrs = []
        for raw in request.allocations:
            try:
                cidrs.append(ipaddress.ip_network(raw, strict=True))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid CIDR {raw!r}: {exc}",
                )
        try:
            new_version = store.commit(request.version, cidrs)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return CommitResponse(
            version=new_version, allocations=[str(n) for n in cidrs]
        )

    return app


app = create_app()
