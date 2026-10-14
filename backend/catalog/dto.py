"""Catalog API shapes (`/api/catalog/*`). Mirrored in src/lib/catalog/types.ts."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

CatalogKind = Literal["anime", "manga", "novel", "web-novel", "book"]
CatalogSort = Literal["popularity", "recent", "upcoming", "title"]


class CatalogSourceCount(BaseModel):
    source: str
    entries: int
    lastHarvestAt: Optional[str] = None
    errorSlices: int = 0


class CatalogStats(BaseModel):
    ready: bool
    works: int = 0
    entries: int = 0
    links: int = 0
    sources: list[CatalogSourceCount] = Field(default_factory=list)
    kinds: dict[str, int] = Field(default_factory=dict)  # kind -> works having that face
    lastLinkAt: Optional[str] = None


class CatalogWorkSummary(BaseModel):
    id: str  # cluster id
    title: str
    kinds: list[CatalogKind]
    entryCount: int
    animeStatus: Optional[str] = None
    firstYear: Optional[int] = None
    lastYear: Optional[int] = None
    nextDate: Optional[str] = None
    nextPrecision: Optional[Literal["day", "month", "year"]] = None
    nextLabel: Optional[str] = None
    popularity: int = 0
    cover: Optional[str] = None


class CatalogWorkPage(BaseModel):
    total: int
    page: int
    per: int
    items: list[CatalogWorkSummary]


class CatalogEntry(BaseModel):
    id: str  # "<source>:<externalId>"
    source: str
    externalId: str
    kind: CatalogKind
    format: Optional[str] = None
    title: str
    titleNative: Optional[str] = None
    titleRomaji: Optional[str] = None
    titleEnglish: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    status: Optional[str] = None
    startDate: Optional[str] = None
    startPrecision: Optional[Literal["day", "month", "year"]] = None
    endDate: Optional[str] = None
    season: Optional[str] = None
    seasonYear: Optional[int] = None
    episodes: Optional[int] = None
    chapters: Optional[int] = None
    volumes: Optional[int] = None
    genres: list[str] = Field(default_factory=list)
    studios: list[str] = Field(default_factory=list)
    creators: list[str] = Field(default_factory=list)
    popularity: Optional[int] = None
    score: Optional[int] = None
    url: Optional[str] = None
    cover: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    # How this entry joined the work: None for AniList members, else the link that attached it
    linkMethod: Optional[str] = None
    linkConfidence: Optional[float] = None


class CatalogWorkDetail(BaseModel):
    id: str
    title: str
    kinds: list[CatalogKind]
    entries: list[CatalogEntry]  # members, AniList first then by kind / date
    candidates: list[CatalogEntry] = Field(default_factory=list)  # low-confidence links (< 0.8)
