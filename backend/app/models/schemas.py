from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


JobState = Literal["uploaded", "extracting", "aligning", "projecting", "done", "failed"]
DiffKind = Literal["insert", "delete", "replace", "reflow"]
SourceType = Literal["text", "table"]
ConfidenceLevel = Literal["high", "low"]


class HighlightFragment(BaseModel):
    page: int
    bbox: list[float]
    viewport_ref: str


class CharRange(BaseModel):
    start: int
    end: int


class TableContext(BaseModel):
    table_id: str
    row: int
    col: int
    row_label: str | None = None
    col_label: str | None = None


class DiffAnchor(BaseModel):
    id: str
    kind: DiffKind
    source_type: SourceType = "text"
    confidence: ConfidenceLevel = "high"
    excerpt_left: str = ""
    excerpt_right: str = ""
    left_fragments: list[HighlightFragment] = Field(default_factory=list)
    right_fragments: list[HighlightFragment] = Field(default_factory=list)
    left_range: CharRange | None = None
    right_range: CharRange | None = None
    raw_event_count: int = 1
    group_key: str | None = None
    table_context: TableContext | None = None


class DiffSummary(BaseModel):
    insertions: int = 0
    deletions: int = 0
    replacements: int = 0
    reflows: int = 0
    pages_a: int = 0
    pages_b: int = 0


class PageMeta(BaseModel):
    page: int
    width: float
    height: float


class DiffResult(BaseModel):
    pages_left: list[PageMeta]
    pages_right: list[PageMeta]
    summary: DiffSummary
    anchors: list[DiffAnchor]


class JobStatus(BaseModel):
    id: str
    status: JobState
    stage: str
    progress: int = 0
    error: str | None = None
    summary: DiffSummary | None = None


class CreateJobResponse(BaseModel):
    id: str
    status: JobState
