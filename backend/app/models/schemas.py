from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


JobState = Literal["uploaded", "extracting", "aligning", "projecting", "done", "failed"]
DocumentKind = Literal["pdf", "docx"]
FragmentKind = Literal["pdf", "word"]
DiffKind = Literal["insert", "delete", "replace", "reflow"]
SourceType = Literal["text", "table"]
ConfidenceLevel = Literal["high", "low"]
ChapterAnalysisState = Literal["uploaded", "analyzing", "fallback", "done", "failed"]
ChapterSource = Literal["bookmark", "heading", "manual", "synthetic"]
ChapterConfidenceLevel = Literal["high", "medium", "low"]
AnalysisSide = Literal["source", "modified"]
ChapterValidationIssueCode = Literal[
    "invalid_page",
    "non_increasing_start",
    "coverage_gap",
    "duplicate_normalized_title",
    "unmatched_chapter",
]


class HighlightFragment(BaseModel):
    kind: FragmentKind = "pdf"
    page: int | None = None
    bbox: list[float] | None = None
    viewport_ref: str | None = None
    dom_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None


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
    chapter_id: str | None = None
    chapter_title: str | None = None
    chapter_index: int | None = None


class DiffSummary(BaseModel):
    insertions: int = 0
    deletions: int = 0
    replacements: int = 0
    reflows: int = 0
    pages_a: int = 0
    pages_b: int = 0


class ChapterDraft(BaseModel):
    id: str
    title: str
    normalized_title: str
    start_page: int = Field(ge=0, description="0-based inclusive")
    end_page: int = Field(ge=0, description="0-based inclusive")
    source: ChapterSource
    confidence: ChapterConfidenceLevel

    @model_validator(mode="after")
    def validate_page_range(self) -> "ChapterDraft":
        if self.end_page < self.start_page:
            raise ValueError("end_page must be greater than or equal to start_page")
        return self


class DocumentChapterPlan(BaseModel):
    side: AnalysisSide
    total_pages: int
    chapters: list[ChapterDraft] = Field(default_factory=list)


class ChapterValidationIssue(BaseModel):
    code: ChapterValidationIssueCode
    side: AnalysisSide
    chapter_id: str
    message: str
    raw_title: str = ""
    normalized_title: str = ""
    peer_chapter_id: str | None = None
    peer_raw_title: str | None = None
    peer_normalized_title: str | None = None
    suggested_peer_score: float | None = None


class ChapterValidationRequestItem(BaseModel):
    id: str
    title: str
    start_page: int


class ChapterValidationRequest(BaseModel):
    source_chapters: list[ChapterValidationRequestItem] = Field(default_factory=list)
    modified_chapters: list[ChapterValidationRequestItem] = Field(default_factory=list)


class ChapterValidationResult(BaseModel):
    can_continue: bool
    issues: list[ChapterValidationIssue] = Field(default_factory=list)
    source_plan: DocumentChapterPlan
    modified_plan: DocumentChapterPlan


class PageMeta(BaseModel):
    page: int
    width: float
    height: float


class ChapterDiffSummary(BaseModel):
    id: str
    title: str
    index: int
    anchor_count: int = 0
    first_anchor_id: str | None = None
    summary: DiffSummary


class DiffResult(BaseModel):
    document_kind: DocumentKind = "pdf"
    pages_left: list[PageMeta]
    pages_right: list[PageMeta]
    summary: DiffSummary
    anchors: list[DiffAnchor]
    chapters: list[ChapterDiffSummary] = Field(default_factory=list)


class JobStatus(BaseModel):
    id: str
    document_kind: DocumentKind = "pdf"
    status: JobState
    stage: str
    progress: int = 0
    error: str | None = None
    summary: DiffSummary | None = None


class CreateJobResponse(BaseModel):
    id: str
    document_kind: DocumentKind = "pdf"
    status: JobState


class ChapterAnalysisStatus(BaseModel):
    id: str
    document_kind: DocumentKind = "pdf"
    status: ChapterAnalysisState
    stage: str
    progress: int = 0
    error: str | None = None


class ChapterAnalysisResult(BaseModel):
    id: str
    document_kind: DocumentKind = "pdf"
    status: ChapterAnalysisState
    source_plan: DocumentChapterPlan
    modified_plan: DocumentChapterPlan


class FeatureFlags(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_split: bool = Field(alias="chapterSplit")