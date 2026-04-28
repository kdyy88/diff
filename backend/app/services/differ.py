from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher as DiffLibSequenceMatcher
import re

from diff_match_patch import diff_match_patch
from patiencediff import PatienceSequenceMatcher

from app.core.config import REFLOW_MIN_CHARS, REFLOW_MIN_DELTA_Y
from app.models.schemas import DiffAnchor, DiffResult, DiffSummary, HighlightFragment, PageMeta, TableContext
from app.services.extractor import CharAtom, DocumentProjection, TableCell, TableRegion, TextSegment
from app.services.projector import LARGE_REGION_MIN_WORDS, project_bbox, project_dom_fragment, project_large_range, project_range, range_word_count


DIFF_DELETE = -1
DIFF_INSERT = 1
DIFF_EQUAL = 0
WEAK_DELIMITER_CHARS = {" ", ",", "，", "、", ":", "：", "/", "-", "(", ")", "（", "）"}
STRONG_BOUNDARY_CHARS = {"\n", ".", "。", ";", "；", "!", "！", "?", "？"}
EXCERPT_TARGET_MIN_CHARS = 80
EXCERPT_TARGET_MAX_CHARS = 160
MAX_DMP_SEGMENTS = 4
LIST_PREFIX_RE = re.compile(r"^\(?[0-9一二三四五六七八九十]+\)?[、.．)]?$")
WORD_DOM_ID_RE = re.compile(r"^(heading|paragraph|table)-(?P<block>\d+)(?:-r(?P<row>\d+)-c(?P<col>\d+))?$")


@dataclass(slots=True)
class _ReviewAnchorCandidate:
    kind: str
    start_a: int | None
    end_a: int | None
    start_b: int | None
    end_b: int | None
    raw_event_count: int = 1


@dataclass(slots=True)
class _TextWindow:
    raw_start: int
    raw_end: int
    aligned_text: str
    aligned_to_raw: list[int]
    segment_count: int


def _is_token_char(char: str) -> bool:
    return bool(char and not char.isspace())


def _text_for_range(document: DocumentProjection, char_range: tuple[int | None, int | None]) -> str:
    start, end = char_range
    if start is None or end is None or start >= end:
        return ""
    return document.raw_text[start:end]


def _build_aligned_window(
    document: DocumentProjection,
    raw_start: int,
    raw_end: int,
) -> tuple[str, list[int]]:
    aligned_chars: list[str] = []
    aligned_to_raw: list[int] = []
    previous_was_space = False

    for raw_index in range(raw_start, raw_end):
        char = document.chars[raw_index]
        if char.synthetic and char.char.isspace():
            continue

        normalized = char.norm_char or char.char
        if not normalized:
            continue
        if normalized.isspace():
            if previous_was_space:
                continue
            aligned_chars.append(" ")
            aligned_to_raw.append(raw_index)
            previous_was_space = True
            continue

        aligned_chars.append(normalized)
        aligned_to_raw.append(raw_index)
        previous_was_space = False

    return "".join(aligned_chars), aligned_to_raw


def _segment_window_bounds(
    segments: list[TextSegment],
    start: int,
    end: int,
) -> tuple[int, int]:
    if start < end:
        return segments[start].raw_start, segments[end - 1].raw_end
    if start < len(segments):
        position = segments[start].raw_start
        return position, position
    if segments:
        position = segments[-1].raw_end
        return position, position
    return 0, 0


def _build_text_window(
    document: DocumentProjection,
    segments: list[TextSegment],
    start: int,
    end: int,
) -> _TextWindow:
    raw_start, raw_end = _segment_window_bounds(segments, start, end)
    aligned_text, aligned_to_raw = _build_aligned_window(document, raw_start, raw_end)
    return _TextWindow(
        raw_start=raw_start,
        raw_end=raw_end,
        aligned_text=aligned_text,
        aligned_to_raw=aligned_to_raw,
        segment_count=max(end - start, 0),
    )


def _window_aligned_range_to_raw_range(
    window: _TextWindow,
    start: int,
    end: int,
) -> tuple[int, int]:
    if not window.aligned_to_raw:
        return window.raw_start, window.raw_start

    raw_start = window.aligned_to_raw[start] if start < len(window.aligned_to_raw) else window.raw_end
    if end <= start:
        return raw_start, raw_start
    raw_end = window.aligned_to_raw[end - 1] + 1 if end - 1 < len(window.aligned_to_raw) else window.raw_end
    return raw_start, raw_end


def _semantic_text_for_range(
    document: DocumentProjection,
    start: int | None,
    end: int | None,
) -> str:
    if start is None or end is None or start >= end:
        return ""

    parts: list[str] = []
    previous_was_space = False
    for char in document.chars[start:end]:
        if char.synthetic and char.char.isspace():
            continue

        normalized = char.norm_char or char.char
        if not normalized:
            continue
        if normalized.isspace():
            if previous_was_space:
                continue
            parts.append(" ")
            previous_was_space = True
            continue

        parts.append(normalized)
        previous_was_space = False

    return "".join(parts).strip()


def _is_strong_boundary(document: DocumentProjection, index: int) -> bool:
    if not (0 <= index < len(document.chars)):
        return True
    char = document.chars[index]
    if char.char == ".":
        left_char = document.chars[index - 1].char if index > 0 else ""
        right_char = document.chars[index + 1].char if index + 1 < len(document.chars) else ""
        if left_char.isdigit() and right_char.isdigit():
            return False
    if char.char in STRONG_BOUNDARY_CHARS:
        return True
    if char.char.isspace():
        return False

    left = index
    right = index + 1
    while left > 0 and _is_token_char(document.chars[left - 1].char):
        left -= 1
    while right < len(document.chars) and _is_token_char(document.chars[right].char):
        right += 1

    token = document.raw_text[left:right].strip()
    return bool(token and LIST_PREFIX_RE.match(token))


def _expand_phrase_range(
    document: DocumentProjection,
    start: int | None,
    end: int | None,
    *,
    min_index: int = 0,
    max_index: int | None = None,
) -> tuple[int, int] | None:
    if start is None or end is None:
        return None

    if max_index is None:
        max_index = len(document.chars)

    left = start
    right = end
    if left == right:
        if left > min_index and _is_token_char(document.chars[left - 1].char):
            left -= 1
        elif right < max_index and _is_token_char(document.chars[right].char):
            right += 1
        else:
            return None

    while left > min_index:
        previous_char = document.chars[left - 1].char
        if _is_strong_boundary(document, left - 1):
            break
        if _is_token_char(previous_char) or previous_char in WEAK_DELIMITER_CHARS:
            left -= 1
            continue
        break

    while right < max_index:
        current_char = document.chars[right].char
        if _is_strong_boundary(document, right):
            break
        if _is_token_char(current_char) or current_char in WEAK_DELIMITER_CHARS:
            right += 1
            continue
        break

    while left < right and document.chars[left].char.isspace():
        left += 1
    while right > left and document.chars[right - 1].char.isspace():
        right -= 1

    if left >= right:
        return None
    return left, right


def _promote_single_side_edit_to_replace(
    kind: str,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
    *,
    min_a: int = 0,
    max_a: int | None = None,
    min_b: int = 0,
    max_b: int | None = None,
) -> tuple[str, int | None, int | None, int | None, int | None]:
    if kind not in {"insert", "delete"}:
        return kind, start_a, end_a, start_b, end_b

    range_a = _expand_phrase_range(document_a, start_a, end_a, min_index=min_a, max_index=max_a)
    range_b = _expand_phrase_range(document_b, start_b, end_b, min_index=min_b, max_index=max_b)
    if not range_a or not range_b:
        return kind, start_a, end_a, start_b, end_b

    text_a = _text_for_range(document_a, range_a).strip()
    text_b = _text_for_range(document_b, range_b).strip()
    if not text_a or not text_b or text_a == text_b:
        return kind, start_a, end_a, start_b, end_b
    if len(text_a) > 120 or len(text_b) > 120:
        return kind, start_a, end_a, start_b, end_b

    return "replace", range_a[0], range_a[1], range_b[0], range_b[1]


def _is_semantic_noop_candidate(
    kind: str,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
    *,
    min_a: int = 0,
    max_a: int | None = None,
    min_b: int = 0,
    max_b: int | None = None,
) -> bool:
    semantic_a = _semantic_text_for_range(document_a, start_a, end_a)
    semantic_b = _semantic_text_for_range(document_b, start_b, end_b)

    if semantic_a == semantic_b:
        return True
    if kind == "replace":
        return False

    expanded_a = _expand_phrase_range(document_a, start_a, end_a, min_index=min_a, max_index=max_a)
    expanded_b = _expand_phrase_range(document_b, start_b, end_b, min_index=min_b, max_index=max_b)
    if not expanded_a or not expanded_b:
        return False

    expanded_text_a = _semantic_text_for_range(document_a, expanded_a[0], expanded_a[1])
    expanded_text_b = _semantic_text_for_range(document_b, expanded_b[0], expanded_b[1])
    return bool(expanded_text_a) and expanded_text_a == expanded_text_b


def _is_semantic_noop_review_candidate(
    candidate: _ReviewAnchorCandidate,
    *,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
) -> bool:
    semantic_a = _semantic_text_for_range(document_a, candidate.start_a, candidate.end_a)
    semantic_b = _semantic_text_for_range(document_b, candidate.start_b, candidate.end_b)
    return bool(semantic_a or semantic_b) and semantic_a == semantic_b


def _merge_diff_ops(raw_diffs: list[tuple[int, str]]) -> list[tuple[str, str, str]]:
    merged: list[tuple[str, str, str]] = []
    index = 0
    while index < len(raw_diffs):
        op, text = raw_diffs[index]
        if op == DIFF_DELETE and index + 1 < len(raw_diffs) and raw_diffs[index + 1][0] == DIFF_INSERT:
            merged.append(("replace", text, raw_diffs[index + 1][1]))
            index += 2
            continue
        if op == DIFF_INSERT and index + 1 < len(raw_diffs) and raw_diffs[index + 1][0] == DIFF_DELETE:
            merged.append(("replace", raw_diffs[index + 1][1], text))
            index += 2
            continue
        if op == DIFF_DELETE:
            merged.append(("delete", text, ""))
        elif op == DIFF_INSERT:
            merged.append(("insert", "", text))
        else:
            merged.append(("equal", text, text))
        index += 1
    return merged


def _slice_excerpt(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


def _trim_char_range(document: DocumentProjection, start: int, end: int) -> tuple[int, int]:
    start = max(start, 0)
    end = min(end, len(document.chars))
    while start < end and document.chars[start].char.isspace():
        start += 1
    while end > start and document.chars[end - 1].char.isspace():
        end -= 1
    return start, end


def _previous_strong_boundary(document: DocumentProjection, start: int) -> int | None:
    for index in range(min(start - 1, len(document.chars) - 1), -1, -1):
        if _is_strong_boundary(document, index):
            return index
    return None


def _next_strong_boundary(document: DocumentProjection, end: int) -> int | None:
    for index in range(max(end, 0), len(document.chars)):
        if _is_strong_boundary(document, index):
            return index
    return None


def _expand_excerpt_window(
    document: DocumentProjection,
    start: int,
    end: int,
    *,
    target_length: int,
    can_grow_left: bool,
    can_grow_right: bool,
) -> tuple[int, int]:
    if end - start >= target_length:
        return start, end

    remaining = target_length - (end - start)
    grow_left = remaining // 2 if can_grow_left else 0
    grow_right = remaining - grow_left if can_grow_right else 0
    if not can_grow_right:
        grow_left = remaining
    if not can_grow_left:
        grow_right = remaining
    next_start = max(0, start - grow_left)
    next_end = min(len(document.chars), end + grow_right)

    shortfall = target_length - (next_end - next_start)
    if shortfall > 0 and can_grow_left and next_start > 0:
        borrowed = min(next_start, shortfall)
        next_start -= borrowed
        shortfall -= borrowed
    if shortfall > 0 and can_grow_right and next_end < len(document.chars):
        next_end = min(len(document.chars), next_end + shortfall)

    return _trim_char_range(document, next_start, next_end)


def _clip_excerpt_window(
    document: DocumentProjection,
    start: int,
    end: int,
    *,
    focus_start: int,
    focus_end: int,
    max_length: int,
) -> tuple[int, int]:
    focus_start, focus_end = _trim_char_range(document, focus_start, focus_end)
    if focus_start >= focus_end:
        focus_start, focus_end = start, min(start + max_length, end)

    focus_length = focus_end - focus_start
    if focus_length >= max_length:
        clipped_start = focus_start
        clipped_end = focus_start + max_length
        return _trim_char_range(document, clipped_start, clipped_end)

    remaining = max_length - focus_length
    available_left = max(focus_start - start, 0)
    available_right = max(end - focus_end, 0)

    take_left = min(available_left, remaining // 2)
    take_right = min(available_right, remaining - take_left)
    missing = remaining - take_left - take_right
    if missing > 0 and available_left > take_left:
        extra_left = min(available_left - take_left, missing)
        take_left += extra_left
        missing -= extra_left
    if missing > 0 and available_right > take_right:
        take_right += min(available_right - take_right, missing)

    clipped_start = focus_start - take_left
    clipped_end = focus_end + take_right
    return _trim_char_range(document, clipped_start, clipped_end)


def _contextual_excerpt(
    document: DocumentProjection,
    start: int | None,
    end: int | None,
    *,
    min_length: int = EXCERPT_TARGET_MIN_CHARS,
    max_length: int = EXCERPT_TARGET_MAX_CHARS,
) -> str:
    if start is None or end is None or start >= end:
        return ""

    focus_start, focus_end = _trim_char_range(document, start, end)
    if focus_start >= focus_end:
        return ""

    previous_boundary = _previous_strong_boundary(document, focus_start)
    next_boundary = _next_strong_boundary(document, focus_end)
    window_start = previous_boundary + 1 if previous_boundary is not None else 0
    window_end = next_boundary + 1 if next_boundary is not None else len(document.chars)
    window_start, window_end = _trim_char_range(document, window_start, window_end)

    if window_start >= window_end or window_end - window_start > max_length:
        window_start, window_end = focus_start, focus_end

    can_grow_left = previous_boundary is None
    can_grow_right = next_boundary is None
    if can_grow_left or can_grow_right:
        window_start, window_end = _expand_excerpt_window(
            document,
            window_start,
            window_end,
            target_length=min_length,
            can_grow_left=can_grow_left,
            can_grow_right=can_grow_right,
        )
    if window_end - window_start > max_length:
        window_start, window_end = _clip_excerpt_window(
            document,
            window_start,
            window_end,
            focus_start=focus_start,
            focus_end=focus_end,
            max_length=max_length,
        )

    return _slice_excerpt(_text_for_range(document, (window_start, window_end)), limit=max_length)


def _compact_table_value(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if not compact:
        return "—"
    return compact[:limit]


def _first_real_char(chars: list[CharAtom]) -> CharAtom | None:
    for char in chars:
        if not char.synthetic and char.bbox is not None and char.char.strip():
            return char
    return None


def _detect_reflow(
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
    text: str,
) -> bool:
    if len(text.strip()) < REFLOW_MIN_CHARS:
        return False

    chars_a = document_a.chars[start_a:end_a]
    chars_b = document_b.chars[start_b:end_b]
    first_a = _first_real_char(chars_a)
    first_b = _first_real_char(chars_b)
    if not first_a or not first_b or not first_a.bbox or not first_b.bbox:
        return False
    if first_a.page != first_b.page:
        return True
    y_a = (first_a.bbox[1] + first_a.bbox[3]) / 2
    y_b = (first_b.bbox[1] + first_b.bbox[3]) / 2
    return abs(y_a - y_b) >= REFLOW_MIN_DELTA_Y


def _ranges_overlap(
    start_1: int | None,
    end_1: int | None,
    start_2: int | None,
    end_2: int | None,
) -> bool:
    if None in {start_1, end_1, start_2, end_2}:
        return False
    return max(start_1, start_2) < min(end_1, end_2)


def _range_gap(
    start_1: int | None,
    end_1: int | None,
    start_2: int | None,
    end_2: int | None,
) -> int | None:
    if None in {start_1, end_1, start_2, end_2}:
        return None
    if end_1 <= start_2:
        return start_2 - end_1
    if end_2 <= start_1:
        return start_1 - end_2
    return 0


def _can_bridge_gap(document: DocumentProjection, left_end: int, right_start: int) -> bool:
    if right_start <= left_end:
        return True
    if right_start - left_end > 6:
        return False
    gap_text = document.raw_text[left_end:right_start]
    stripped = gap_text.strip()
    if not stripped:
        return True
    if len(stripped) <= 2 and all(char in WEAK_DELIMITER_CHARS for char in stripped):
        return True
    return False


def _should_merge_review_candidates(
    previous: _ReviewAnchorCandidate,
    current: _ReviewAnchorCandidate,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
) -> bool:
    if previous.kind == current.kind == "insert":
        gap_b = _range_gap(previous.start_b, previous.end_b, current.start_b, current.end_b)
        return gap_b is not None and gap_b <= 8 and _can_bridge_gap(document_b, previous.end_b or 0, current.start_b or 0)

    if previous.kind == current.kind == "delete":
        gap_a = _range_gap(previous.start_a, previous.end_a, current.start_a, current.end_a)
        return gap_a is not None and gap_a <= 8 and _can_bridge_gap(document_a, previous.end_a or 0, current.start_a or 0)

    if previous.kind != "replace" or current.kind != "replace":
        return False

    if _ranges_overlap(previous.start_a, previous.end_a, current.start_a, current.end_a):
        return True
    if _ranges_overlap(previous.start_b, previous.end_b, current.start_b, current.end_b):
        return True

    gap_a = _range_gap(previous.start_a, previous.end_a, current.start_a, current.end_a)
    gap_b = _range_gap(previous.start_b, previous.end_b, current.start_b, current.end_b)
    if gap_a is None or gap_b is None:
        return False
    if gap_a > 8 or gap_b > 8:
        return False

    can_bridge_a = _can_bridge_gap(document_a, previous.end_a or 0, current.start_a or 0)
    can_bridge_b = _can_bridge_gap(document_b, previous.end_b or 0, current.start_b or 0)
    return can_bridge_a and can_bridge_b


def _merge_review_candidates(
    left: _ReviewAnchorCandidate,
    right: _ReviewAnchorCandidate,
) -> _ReviewAnchorCandidate:
    if left.kind == right.kind == "insert":
        return _ReviewAnchorCandidate(
            kind="insert",
            start_a=None,
            end_a=None,
            start_b=min(value for value in [left.start_b, right.start_b] if value is not None),
            end_b=max(value for value in [left.end_b, right.end_b] if value is not None),
            raw_event_count=left.raw_event_count + right.raw_event_count,
        )
    if left.kind == right.kind == "delete":
        return _ReviewAnchorCandidate(
            kind="delete",
            start_a=min(value for value in [left.start_a, right.start_a] if value is not None),
            end_a=max(value for value in [left.end_a, right.end_a] if value is not None),
            start_b=None,
            end_b=None,
            raw_event_count=left.raw_event_count + right.raw_event_count,
        )
    return _ReviewAnchorCandidate(
        kind="replace",
        start_a=min(value for value in [left.start_a, right.start_a] if value is not None),
        end_a=max(value for value in [left.end_a, right.end_a] if value is not None),
        start_b=min(value for value in [left.start_b, right.start_b] if value is not None),
        end_b=max(value for value in [left.end_b, right.end_b] if value is not None),
        raw_event_count=left.raw_event_count + right.raw_event_count,
    )


def coalesce_review_anchors(
    candidates: list[_ReviewAnchorCandidate],
    *,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
) -> list[_ReviewAnchorCandidate]:
    if not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda item: (
            item.start_a if item.start_a is not None else 10**12,
            item.start_b if item.start_b is not None else 10**12,
        ),
    )

    merged: list[_ReviewAnchorCandidate] = [ordered[0]]
    for candidate in ordered[1:]:
        previous = merged[-1]
        if _should_merge_review_candidates(previous, candidate, document_a, document_b):
            merged[-1] = _merge_review_candidates(previous, candidate)
        else:
            merged.append(candidate)
    return merged


def _anchor_from_candidate(
    candidate: _ReviewAnchorCandidate,
    *,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    index: int,
    confidence: str = "high",
) -> DiffAnchor:
    large_left = candidate.kind in {"delete", "replace"} and range_word_count(document_a, candidate.start_a, candidate.end_a) >= LARGE_REGION_MIN_WORDS
    large_right = candidate.kind in {"insert", "replace"} and range_word_count(document_b, candidate.start_b, candidate.end_b) >= LARGE_REGION_MIN_WORDS
    is_large_region = large_left or large_right
    left_projection = (
        project_large_range(document_a, candidate.start_a, candidate.end_a)
        if large_left
        else project_range(document_a, candidate.start_a, candidate.end_a)
    )
    right_projection = (
        project_large_range(document_b, candidate.start_b, candidate.end_b)
        if large_right
        else project_range(document_b, candidate.start_b, candidate.end_b)
    )

    excerpt_left = _contextual_excerpt(document_a, candidate.start_a, candidate.end_a)
    excerpt_right = _contextual_excerpt(document_b, candidate.start_b, candidate.end_b)

    return DiffAnchor(
        id=f"anchor-text-{index}",
        kind=candidate.kind,
        source_type="text",
        confidence=confidence,
        excerpt_left=excerpt_left,
        excerpt_right=excerpt_right,
        left_fragments=left_projection.fragments,
        right_fragments=right_projection.fragments,
        left_range=left_projection.char_range,
        right_range=right_projection.char_range,
        raw_event_count=candidate.raw_event_count,
        group_key=f"text-group-{index}",
        is_large_region=is_large_region,
    )


def _cell_kind(left_text: str, right_text: str) -> str | None:
    if left_text == right_text:
        return None
    if not left_text and right_text:
        return "insert"
    if left_text and not right_text:
        return "delete"
    return "replace"


def _table_context(cell: TableCell) -> TableContext:
    return TableContext(
        table_id=cell.table_id,
        row=cell.row,
        col=cell.col,
        row_label=cell.row_label,
        col_label=cell.col_label,
    )


def _table_excerpt(cell: TableCell | None, text: str) -> str:
    if not cell:
        return _compact_table_value(text)
    parts = []
    if cell.col_label:
        parts.append(cell.col_label)
    else:
        parts.append(f"C{cell.col + 1}")
    if cell.row_label:
        parts.append(cell.row_label)
    else:
        parts.append(f"R{cell.row + 1}")
    parts.append(_compact_table_value(text))
    return " | ".join(parts)


def _table_sort_key(table: TableRegion) -> tuple[int, float, float]:
    if table.bbox is None:
        return (10**9, 10**9, 10**9)
    return (table.page, table.bbox[1], table.bbox[0])


def _project_table_cell(cell: TableCell, *, viewport_ref: str) -> list[HighlightFragment]:
    if cell.dom_id is not None:
        return project_dom_fragment(dom_id=cell.dom_id, char_start=0, char_end=max(len(cell.text), 1))
    if cell.bbox is None:
        return []
    return project_bbox(page=cell.page, bbox=cell.bbox, viewport_ref=viewport_ref)


def _project_table_region(table: TableRegion, *, viewport_ref: str) -> list[HighlightFragment]:
    if table.dom_id is not None:
        return project_dom_fragment(dom_id=table.dom_id, char_start=0, char_end=0)
    if table.bbox is None:
        return []
    return project_bbox(page=table.page, bbox=table.bbox, viewport_ref=viewport_ref)


def _table_rows(table: TableRegion) -> list[list[TableCell]]:
    rows: list[list[TableCell]] = [[] for _ in range(max(table.row_count, 0))]
    for cell in sorted(table.cells, key=lambda item: (item.row, item.col)):
        if 0 <= cell.row < len(rows):
            rows[cell.row].append(cell)
    return rows


def _row_signature(cells: list[TableCell]) -> str:
    values = [_compact_table_value(cell.text, limit=40) for cell in cells if cell.text.strip()]
    return " | ".join(values) if values else "__EMPTY_ROW__"


def _build_row_cell_anchors(
    *,
    left_cells: list[TableCell],
    right_cells: list[TableCell],
    anchor_prefix: str,
) -> list[DiffAnchor]:
    anchors: list[DiffAnchor] = []
    left_map = {cell.col: cell for cell in left_cells}
    right_map = {cell.col: cell for cell in right_cells}
    all_cols = sorted(set(left_map) | set(right_map))

    for col_index in all_cols:
        left_cell = left_map.get(col_index)
        right_cell = right_map.get(col_index)
        left_text = left_cell.text if left_cell else ""
        right_text = right_cell.text if right_cell else ""
        kind = _cell_kind(left_text, right_text)
        if not kind:
            continue
        anchors.append(
            _build_table_cell_anchor(
                anchor_id=f"{anchor_prefix}-c{col_index}",
                kind=kind,
                left_cell=left_cell,
                right_cell=right_cell,
            )
        )
    return anchors


def _build_table_cell_anchor(
    *,
    anchor_id: str,
    kind: str,
    left_cell: TableCell | None,
    right_cell: TableCell | None,
    raw_event_count: int = 1,
) -> DiffAnchor:
    context_cell = left_cell or right_cell
    left_text = left_cell.text if left_cell else ""
    right_text = right_cell.text if right_cell else ""
    left_fragments = (
        _project_table_cell(left_cell, viewport_ref=f"{anchor_id}-left")
        if left_cell
        else []
    )
    right_fragments = (
        _project_table_cell(right_cell, viewport_ref=f"{anchor_id}-right")
        if right_cell
        else []
    )
    return DiffAnchor(
        id=anchor_id,
        kind=kind,
        source_type="table",
        confidence="high",
        excerpt_left=_table_excerpt(left_cell, left_text),
        excerpt_right=_table_excerpt(right_cell, right_text),
        left_fragments=left_fragments,
        right_fragments=right_fragments,
        left_range=None,
        right_range=None,
        raw_event_count=raw_event_count,
        group_key=f"{context_cell.table_id if context_cell else anchor_id}",
        table_context=_table_context(context_cell) if context_cell else None,
    )


def _build_table_structure_anchor(
    *,
    anchor_id: str,
    left_table: TableRegion | None,
    right_table: TableRegion | None,
    kind: str,
) -> DiffAnchor:
    left_fragments = (
        _project_table_region(left_table, viewport_ref=f"{anchor_id}-left")
        if left_table
        else []
    )
    right_fragments = (
        _project_table_region(right_table, viewport_ref=f"{anchor_id}-right")
        if right_table
        else []
    )
    label_left = (
        f"Table structure changed | {left_table.row_count}x{left_table.col_count}"
        if left_table
        else "—"
    )
    label_right = (
        f"Table structure changed | {right_table.row_count}x{right_table.col_count}"
        if right_table
        else "—"
    )
    context_table = left_table or right_table
    return DiffAnchor(
        id=anchor_id,
        kind=kind,
        source_type="table",
        confidence="high",
        excerpt_left=label_left,
        excerpt_right=label_right,
        left_fragments=left_fragments,
        right_fragments=right_fragments,
        left_range=None,
        right_range=None,
        raw_event_count=1,
        group_key=context_table.id if context_table else anchor_id,
        table_context=TableContext(
            table_id=context_table.id if context_table else anchor_id,
            row=-1,
            col=-1,
            row_label=None,
            col_label=None,
        ) if context_table else None,
    )


def _build_reflow_anchor(
    *,
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
    index: int,
) -> DiffAnchor:
    left_projection = project_range(document_a, start_a, end_a)
    right_projection = project_range(document_b, start_b, end_b)
    return DiffAnchor(
        id=f"reflow-{index}",
        kind="reflow",
        source_type="text",
        confidence="high",
        excerpt_left=_contextual_excerpt(document_a, start_a, end_a),
        excerpt_right=_contextual_excerpt(document_b, start_b, end_b),
        left_fragments=left_projection.fragments,
        right_fragments=right_projection.fragments,
        left_range=left_projection.char_range,
        right_range=right_projection.char_range,
        raw_event_count=1,
        group_key=f"reflow-{index}",
        table_context=None,
    )


def _compare_text_window(
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    *,
    window_a: _TextWindow,
    window_b: _TextWindow,
    confidence: str,
    anchor_index_start: int,
) -> list[DiffAnchor]:
    if not window_a.aligned_text and not window_b.aligned_text:
        return []

    dmp = diff_match_patch()
    diffs = dmp.diff_main(window_a.aligned_text, window_b.aligned_text, checklines=False)
    dmp.diff_cleanupSemantic(diffs)
    merged_ops = _merge_diff_ops(diffs)

    cursor_a = 0
    cursor_b = 0
    raw_candidates: list[_ReviewAnchorCandidate] = []

    for kind, text_a, text_b in merged_ops:
        len_a = len(text_a)
        len_b = len(text_b)
        aligned_start_a = cursor_a
        aligned_start_b = cursor_b
        aligned_end_a = cursor_a + len_a
        aligned_end_b = cursor_b + len_b
        start_a, end_a = _window_aligned_range_to_raw_range(window_a, aligned_start_a, aligned_end_a)
        start_b, end_b = _window_aligned_range_to_raw_range(window_b, aligned_start_b, aligned_end_b)

        if kind != "equal":
            resolved_kind, resolved_start_a, resolved_end_a, resolved_start_b, resolved_end_b = (
                _promote_single_side_edit_to_replace(
                    kind,
                    document_a,
                    document_b,
                    start_a,
                    end_a,
                    start_b,
                    end_b,
                    min_a=window_a.raw_start,
                    max_a=window_a.raw_end,
                    min_b=window_b.raw_start,
                    max_b=window_b.raw_end,
                )
            )
            if _is_semantic_noop_candidate(
                resolved_kind,
                document_a,
                document_b,
                resolved_start_a,
                resolved_end_a,
                resolved_start_b,
                resolved_end_b,
                min_a=window_a.raw_start,
                max_a=window_a.raw_end,
                min_b=window_b.raw_start,
                max_b=window_b.raw_end,
            ):
                cursor_a = aligned_end_a
                cursor_b = aligned_end_b
                continue

            raw_candidates.append(
                _ReviewAnchorCandidate(
                    kind=resolved_kind,
                    start_a=resolved_start_a if resolved_start_a != resolved_end_a else None,
                    end_a=resolved_end_a if resolved_start_a != resolved_end_a else None,
                    start_b=resolved_start_b if resolved_start_b != resolved_end_b else None,
                    end_b=resolved_end_b if resolved_start_b != resolved_end_b else None,
                )
            )

        cursor_a = aligned_end_a
        cursor_b = aligned_end_b

    review_candidates = coalesce_review_anchors(
        raw_candidates,
        document_a=document_a,
        document_b=document_b,
    )
    review_candidates = [
        candidate
        for candidate in review_candidates
        if not _is_semantic_noop_review_candidate(
            candidate,
            document_a=document_a,
            document_b=document_b,
        )
    ]
    return [
        _anchor_from_candidate(
            candidate,
            document_a=document_a,
            document_b=document_b,
            index=anchor_index_start + index,
            confidence=confidence,
        )
        for index, candidate in enumerate(review_candidates)
    ]


def _compare_large_replace_window(
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    *,
    left_segments: list[TextSegment],
    right_segments: list[TextSegment],
    i1: int,
    i2: int,
    j1: int,
    j2: int,
    anchor_index_start: int,
) -> list[DiffAnchor]:
    inner_matcher = DiffLibSequenceMatcher(
        None,
        [segment.aligned_text for segment in left_segments[i1:i2]],
        [segment.aligned_text for segment in right_segments[j1:j2]],
        autojunk=False,
    )
    anchors: list[DiffAnchor] = []
    next_anchor_index = anchor_index_start

    for tag, local_i1, local_i2, local_j1, local_j2 in inner_matcher.get_opcodes():
        if tag == "equal":
            continue

        absolute_i1 = i1 + local_i1
        absolute_i2 = i1 + local_i2
        absolute_j1 = j1 + local_j1
        absolute_j2 = j1 + local_j2
        left_count = absolute_i2 - absolute_i1
        right_count = absolute_j2 - absolute_j1

        if tag == "replace" and max(left_count, right_count) > MAX_DMP_SEGMENTS:
            shared = min(left_count, right_count)
            for offset in range(shared):
                segment_anchors = _compare_text_window(
                    document_a,
                    document_b,
                    window_a=_build_text_window(document_a, left_segments, absolute_i1 + offset, absolute_i1 + offset + 1),
                    window_b=_build_text_window(document_b, right_segments, absolute_j1 + offset, absolute_j1 + offset + 1),
                    confidence="low",
                    anchor_index_start=next_anchor_index,
                )
                anchors.extend(segment_anchors)
                next_anchor_index += len(segment_anchors)

            if left_count > shared:
                segment_anchors = _compare_text_window(
                    document_a,
                    document_b,
                    window_a=_build_text_window(document_a, left_segments, absolute_i1 + shared, absolute_i2),
                    window_b=_build_text_window(document_b, right_segments, absolute_j2, absolute_j2),
                    confidence="low",
                    anchor_index_start=next_anchor_index,
                )
                anchors.extend(segment_anchors)
                next_anchor_index += len(segment_anchors)
            if right_count > shared:
                segment_anchors = _compare_text_window(
                    document_a,
                    document_b,
                    window_a=_build_text_window(document_a, left_segments, absolute_i2, absolute_i2),
                    window_b=_build_text_window(document_b, right_segments, absolute_j1 + shared, absolute_j2),
                    confidence="low",
                    anchor_index_start=next_anchor_index,
                )
                anchors.extend(segment_anchors)
                next_anchor_index += len(segment_anchors)
            continue

        segment_anchors = _compare_text_window(
            document_a,
            document_b,
            window_a=_build_text_window(document_a, left_segments, absolute_i1, absolute_i2),
            window_b=_build_text_window(document_b, right_segments, absolute_j1, absolute_j2),
            confidence="low",
            anchor_index_start=next_anchor_index,
        )
        anchors.extend(segment_anchors)
        next_anchor_index += len(segment_anchors)

    return anchors


def _compare_tables(
    document_a: DocumentProjection,
    document_b: DocumentProjection,
) -> list[DiffAnchor]:
    left_tables = sorted(document_a.tables, key=_table_sort_key)
    right_tables = sorted(document_b.tables, key=_table_sort_key)
    anchors: list[DiffAnchor] = []
    max_len = max(len(left_tables), len(right_tables))

    for table_index in range(max_len):
        left_table = left_tables[table_index] if table_index < len(left_tables) else None
        right_table = right_tables[table_index] if table_index < len(right_tables) else None

        if left_table and not right_table:
            anchors.append(
                _build_table_structure_anchor(
                    anchor_id=f"anchor-table-{table_index}",
                    left_table=left_table,
                    right_table=None,
                    kind="delete",
                )
            )
            continue
        if right_table and not left_table:
            anchors.append(
                _build_table_structure_anchor(
                    anchor_id=f"anchor-table-{table_index}",
                    left_table=None,
                    right_table=right_table,
                    kind="insert",
                )
            )
            continue
        if not left_table or not right_table:
            continue

        if left_table.col_count != right_table.col_count:
            anchors.append(
                _build_table_structure_anchor(
                    anchor_id=f"anchor-table-{table_index}",
                    left_table=left_table,
                    right_table=right_table,
                    kind="replace",
                )
            )
            continue

        left_rows = _table_rows(left_table)
        right_rows = _table_rows(right_table)
        left_signatures = [_row_signature(row) for row in left_rows]
        right_signatures = [_row_signature(row) for row in right_rows]
        matcher = DiffLibSequenceMatcher(a=left_signatures, b=right_signatures, autojunk=False)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag == "replace":
                shared = min(i2 - i1, j2 - j1)
                for offset in range(shared):
                    anchors.extend(
                        _build_row_cell_anchors(
                            left_cells=left_rows[i1 + offset],
                            right_cells=right_rows[j1 + offset],
                            anchor_prefix=f"anchor-table-{table_index}-r{i1 + offset}",
                        )
                    )
                for extra_left in range(i1 + shared, i2):
                    anchors.extend(
                        _build_row_cell_anchors(
                            left_cells=left_rows[extra_left],
                            right_cells=[],
                            anchor_prefix=f"anchor-table-{table_index}-r{extra_left}-delete",
                        )
                    )
                for extra_right in range(j1 + shared, j2):
                    anchors.extend(
                        _build_row_cell_anchors(
                            left_cells=[],
                            right_cells=right_rows[extra_right],
                            anchor_prefix=f"anchor-table-{table_index}-r{extra_right}-insert",
                        )
                    )
            elif tag == "delete":
                for row_index in range(i1, i2):
                    anchors.extend(
                        _build_row_cell_anchors(
                            left_cells=left_rows[row_index],
                            right_cells=[],
                            anchor_prefix=f"anchor-table-{table_index}-r{row_index}-delete",
                        )
                    )
            elif tag == "insert":
                for row_index in range(j1, j2):
                    anchors.extend(
                        _build_row_cell_anchors(
                            left_cells=[],
                            right_cells=right_rows[row_index],
                            anchor_prefix=f"anchor-table-{table_index}-r{row_index}-insert",
                        )
                    )
    return anchors


def _word_fragment_sort_key(fragment: HighlightFragment) -> tuple[int, int, int, float, str]:
    dom_id = fragment.dom_id or ""
    match = WORD_DOM_ID_RE.match(dom_id)
    if not match:
        return (10**8, 10**8, 10**8, float(fragment.char_start or 0), dom_id)

    row_group = match.group("row")
    col_group = match.group("col")
    return (
        int(match.group("block")),
        int(row_group) if row_group is not None else -1,
        int(col_group) if col_group is not None else -1,
        float(fragment.char_start or 0),
        dom_id,
    )


def _anchor_sort_key(anchor: DiffAnchor) -> tuple[int, float, float, float, str]:
    fragments = anchor.left_fragments or anchor.right_fragments
    if fragments:
        fragment = fragments[0]
        if fragment.kind == "pdf" and fragment.page is not None and fragment.bbox is not None:
            return (0, float(fragment.page), fragment.bbox[1], fragment.bbox[0], anchor.id)

        word_positions = [_word_fragment_sort_key(item) for item in fragments if item.kind == "word"]
        if word_positions:
            block_index, row_index, col_index, char_start, dom_id = min(word_positions)
            cell_order = (row_index * 1000) + col_index if row_index >= 0 and col_index >= 0 else float(row_index)
            return (1, float(block_index), cell_order, char_start, f"{dom_id}:{anchor.id}")

    return (2, 10**9, 10**9, 10**9, anchor.id)


def compare_documents(
    document_a: DocumentProjection,
    document_b: DocumentProjection,
    *,
    include_reflow: bool = False,
) -> DiffResult:
    left_segments = document_a.text_segments
    right_segments = document_b.text_segments
    matcher = PatienceSequenceMatcher(
        None,
        [segment.aligned_text for segment in left_segments],
        [segment.aligned_text for segment in right_segments],
    )

    text_anchors: list[DiffAnchor] = []
    reflow_anchors: list[DiffAnchor] = []
    summary = DiffSummary(
        pages_a=len(document_a.pages),
        pages_b=len(document_b.pages),
    )
    allow_reflow = include_reflow and document_a.document_kind == document_b.document_kind == "pdf"

    next_anchor_index = 0
    next_reflow_index = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        window_a = _build_text_window(document_a, left_segments, i1, i2)
        window_b = _build_text_window(document_b, right_segments, j1, j2)

        if tag == "equal":
            if allow_reflow and (
                _detect_reflow(
                    document_a,
                    document_b,
                    window_a.raw_start,
                    window_a.raw_end,
                    window_b.raw_start,
                    window_b.raw_end,
                    _semantic_text_for_range(document_a, window_a.raw_start, window_a.raw_end),
                )
            ):
                reflow_anchors.append(
                    _build_reflow_anchor(
                        document_a=document_a,
                        document_b=document_b,
                        start_a=window_a.raw_start,
                        end_a=window_a.raw_end,
                        start_b=window_b.raw_start,
                        end_b=window_b.raw_end,
                        index=next_reflow_index,
                    )
                )
                next_reflow_index += 1
                summary.reflows += 1
            continue

        is_large_replace = tag == "replace" and max(window_a.segment_count, window_b.segment_count) > MAX_DMP_SEGMENTS
        confidence = "low" if is_large_replace else "high"
        if is_large_replace:
            window_anchors = _compare_large_replace_window(
                document_a,
                document_b,
                left_segments=left_segments,
                right_segments=right_segments,
                i1=i1,
                i2=i2,
                j1=j1,
                j2=j2,
                anchor_index_start=next_anchor_index,
            )
        else:
            window_anchors = _compare_text_window(
                document_a,
                document_b,
                window_a=window_a,
                window_b=window_b,
                confidence=confidence,
                anchor_index_start=next_anchor_index,
            )
        text_anchors.extend(window_anchors)
        next_anchor_index += len(window_anchors)

    table_anchors = _compare_tables(document_a, document_b)
    anchors = sorted(text_anchors + table_anchors + reflow_anchors, key=_anchor_sort_key)

    for anchor in anchors:
        if anchor.kind == "insert":
            summary.insertions += 1
        elif anchor.kind == "delete":
            summary.deletions += 1
        elif anchor.kind == "replace":
            summary.replacements += 1

    return DiffResult(
        document_kind=document_a.document_kind,
        pages_left=[PageMeta(page=item.page, width=item.width, height=item.height) for item in document_a.pages],
        pages_right=[PageMeta(page=item.page, width=item.width, height=item.height) for item in document_b.pages],
        summary=summary,
        anchors=anchors,
    )
