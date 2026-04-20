from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import CharRange, HighlightFragment
from app.services.extractor import CharAtom, DocumentProjection


MERGE_GAP = 10.0


@dataclass(slots=True)
class FragmentProjection:
    fragments: list[HighlightFragment]
    excerpt: str
    char_range: CharRange | None


def project_bbox(
    *,
    page: int,
    bbox: tuple[float, float, float, float],
    viewport_ref: str,
) -> list[HighlightFragment]:
    return [
        HighlightFragment(
            page=page,
            bbox=[bbox[0], bbox[1], bbox[2], bbox[3]],
            viewport_ref=viewport_ref,
        )
    ]


def _real_char_slice(
    document: DocumentProjection,
    start: int | None,
    end: int | None,
) -> list[CharAtom]:
    if start is None or end is None or start >= end:
        return []
    return [
        char
        for char in document.chars[start:end]
        if not char.synthetic and char.bbox is not None and char.char.strip()
    ]


def project_range(
    document: DocumentProjection,
    start: int | None,
    end: int | None,
) -> FragmentProjection:
    real_chars = _real_char_slice(document, start, end)
    if not real_chars:
        return FragmentProjection(fragments=[], excerpt="", char_range=None)

    groups: list[list[CharAtom]] = []
    current: list[CharAtom] = []
    for char in real_chars:
        if not current:
            current = [char]
            continue
        previous = current[-1]
        same_row = (
            char.page == previous.page
            and char.block == previous.block
            and char.line == previous.line
        )
        close_enough = same_row and char.bbox[0] - previous.bbox[2] <= MERGE_GAP
        if close_enough:
            current.append(char)
        else:
            groups.append(current)
            current = [char]

    if current:
        groups.append(current)

    fragments: list[HighlightFragment] = []
    seen_boxes: set[tuple[int, int, int, int, int]] = set()
    for group_index, group in enumerate(groups):
        x0 = min(item.bbox[0] for item in group)
        y0 = min(item.bbox[1] for item in group)
        x1 = max(item.bbox[2] for item in group)
        y1 = max(item.bbox[3] for item in group)
        page = group[0].page
        dedupe_key = (
            page,
            round(x0),
            round(y0),
            round(x1),
            round(y1),
        )
        if dedupe_key in seen_boxes:
            continue
        seen_boxes.add(dedupe_key)
        fragments.append(
            HighlightFragment(
                page=page,
                bbox=[x0, y0, x1, y1],
                viewport_ref=f"page-{page}-fragment-{group_index}",
            )
        )

    excerpt = "".join(char.char for char in real_chars[:80]).strip()
    return FragmentProjection(
        fragments=fragments,
        excerpt=excerpt,
        char_range=CharRange(start=start, end=end),
    )
