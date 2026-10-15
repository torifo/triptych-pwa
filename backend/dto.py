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
AiringStatus = Literal["NOT_YET_RELEASED", "RELEASING", "FINISHED", "CANCELLED", "HIATUS"]
DatePrecision = Literal["day", "month", "year"]


class SearchResult(BaseModel):
    """A single hit from the cross-source search endpoint (`/api/search`)."""

    source: Source
    externalId: str
    mediaType: MediaType
    title: str
    cover: Optional[str] = None
    url: str
    subtitle: Optional[str] = None  # 作者・出版社・巻数などの補足（表示用）
    direct: bool = False  # URL / ncode / ISBN からの直接解決なら True


class ReleaseDTO(BaseModel):
    """A normalized release (volume / episode / season start / stream start).

    `date` is always YYYY-MM-DD so it sorts; when `precision` is "month" or
    "year" the day/month parts are filler (1日・1月) and must not be shown.
    Anime dates follow the Japanese broadcast convention (JST, day boundary at
    05:00), with `time` written like "25:30".
    """

    kind: ReleaseKind
    label: str
    date: str  # YYYY-MM-DD
    precision: DatePrecision = "day"
    time: Optional[str] = None
    source: Source
    externalId: Optional[str] = None  # volume=ISBN13 / episode="<anilistId>:<n>" / season-start=anilistId


class MediaDetailDTO(BaseModel):
    """Detail payload for a single external work (e.g. `/api/anilist/media/{id}`)."""

    source: Source
    externalId: str
    mediaType: MediaType
    title: str
    cover: Optional[str] = None
    url: str
    status: Optional[AiringStatus] = None  # AniList の放送状態（同期がしょぼかるの照会・配信の扱いに使う）
    releases: list[ReleaseDTO] = Field(default_factory=list)


class DetectionCandidateDTO(BaseModel):
    """Candidate anime match returned by `/api/anilist/detect`."""

    source: Literal["anilist"] = "anilist"
    externalId: str
    title: str
    airingStatus: AiringStatus
    format: Optional[str] = None
    startDate: Optional[str] = None
    startPrecision: Optional[DatePrecision] = None
    matchScore: float = Field(ge=0.0, le=1.0)
    via: Literal["relation", "sequel", "title"] = "title"


class RelationEdge(BaseModel):
    """One AniList relation edge (`/api/anilist/relations/{id}`)."""

    relationType: str  # ADAPTATION / SEQUEL / SOURCE / PREQUEL / SIDE_STORY ...
    externalId: str
    type: Literal["ANIME", "MANGA"]
    format: Optional[str] = None
    title: str  # native 優先
    status: Optional[AiringStatus] = None
    startDate: Optional[str] = None
    startPrecision: Optional[DatePrecision] = None
    isAdult: bool = False


class RelationsDTO(BaseModel):
    externalId: str
    type: Literal["ANIME", "MANGA"]
    format: Optional[str] = None
    title: str
    relations: list[RelationEdge] = Field(default_factory=list)


class NdlLine(BaseModel):
    key: str  # '' = 本編
    label: str
    count: int


class NdlSeriesDTO(BaseModel):
    """New-volume candidates for one series (`/api/ndl/series`)."""

    lines: list[NdlLine] = Field(default_factory=list)
    line: str = ""
    releases: list[ReleaseDTO] = Field(default_factory=list)  # newest first, max 30


class BroadcastDTO(BaseModel):
    """One broadcast / streaming slot from しょぼいカレンダー."""

    channel: str
    isStreaming: bool
    date: str  # YYYY-MM-DD（朝5時区切り）
    time: str  # "25:30"
    episodeCount: Optional[int] = None  # しょぼかるの Count（通算話数）


class SyoboiTitleDTO(BaseModel):
    status: Literal["ok", "queued", "not_found"]
    tid: Optional[str] = None
    title: Optional[str] = None
    programs: list[BroadcastDTO] = Field(default_factory=list)
    streamStarts: list[ReleaseDTO] = Field(default_factory=list)
    startedStreams: Optional[list[str]] = None  # 配信中（直近 14 日にも配信あり）の externalId。None は不明


class WebNovelDTO(BaseModel):
    """なろう・カクヨム共通の作品情報."""

    source: Literal["narou", "kakuyomu"]
    externalId: str
    title: str
    author: Optional[str] = None
    url: str
    episodeCount: int
    completed: bool
    isShortStory: bool = False
    lastUpdate: Optional[str] = None  # YYYY-MM-DD（JST）


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
