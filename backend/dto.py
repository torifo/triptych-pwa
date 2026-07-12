"""Common DTOs exchanged between the collector and the frontend.

All source adapters normalize their raw responses into these shapes before
returning them to the client. Raw HTML / GraphQL / JSON payloads never leave
the collector process (see specs/mvp-core/design.md, "API Design").
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

MediaType = Literal["web-novel", "light-novel", "manga", "anime"]
Source = Literal["anilist", "narou", "openbd", "ndl", "syoboi", "kakuyomu", "manual"]
ReleaseKind = Literal["volume", "episode", "season-start", "stream-start"]
AiringStatus = Literal["NOT_YET_RELEASED", "RELEASING", "FINISHED"]


class SearchResult(BaseModel):
    """A single hit from the cross-source search endpoint (`/api/search`)."""

    source: Source
    externalId: str
    mediaType: MediaType
    title: str
    cover: Optional[str] = None
    url: str


class ReleaseDTO(BaseModel):
    """A normalized release (volume / episode / season start / stream start)."""

    kind: ReleaseKind
    label: str
    date: str  # YYYY-MM-DD
    source: Source
    externalId: Optional[str] = None


class MediaDetailDTO(BaseModel):
    """Detail payload for a single external work (e.g. `/api/anilist/media/{id}`)."""

    source: Source
    externalId: str
    mediaType: MediaType
    title: str
    cover: Optional[str] = None
    url: str
    releases: list[ReleaseDTO] = Field(default_factory=list)


class DetectionCandidateDTO(BaseModel):
    """Candidate anime match returned by `/api/anilist/detect`."""

    source: Literal["anilist"] = "anilist"
    externalId: str
    title: str
    airingStatus: AiringStatus
    startDate: Optional[str] = None
    matchScore: float = Field(ge=0.0, le=1.0)


class AdapterErrorInfo(BaseModel):
    """Most recent error recorded for a single source adapter (`/api/health`)."""

    source: Source
    message: str
    occurredAt: str  # ISO 8601


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    errors: dict[str, AdapterErrorInfo] = Field(default_factory=dict)


class SourceUnavailable(Exception):
    """Raised by an adapter when its external source cannot be reached.

    The sync engine (client-side) is expected to skip the failing source and
    continue with the rest; the collector records the error for `/api/health`.
    """

    def __init__(self, source: Source, message: str) -> None:
        self.source = source
        self.message = message
        super().__init__(f"{source}: {message}")
