"""Request/response schemas for the allocation API."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

PrefixLength = Annotated[int, Field(ge=0, le=128)]


class PreviewRequest(BaseModel):
    """Batch of requested prefix lengths (1-8 per request)."""

    prefix_lengths: Annotated[list[PrefixLength], Field(min_length=1, max_length=8)]


class PreviewResponse(BaseModel):
    version: int
    allocations: list[str]
    remaining_free_cidrs: list[str]


class CommitRequest(BaseModel):
    """A plan previously obtained from the preview endpoint."""

    version: int = Field(ge=0)
    allocations: Annotated[list[str], Field(min_length=1, max_length=8)]


class CommitResponse(BaseModel):
    version: int
    allocations: list[str]


class PoolInfo(BaseModel):
    version: int
    parent: str
    reserved: list[str]
    allocated: list[str]
    free_cidrs: list[str]
