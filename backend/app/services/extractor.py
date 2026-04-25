from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unicodedata
from typing import Any

import fitz

from app.services.docx_outline import DocxParagraphBlock, load_docx_structure, render_docx_review_html
from app.services.normalizer import normalize_char


LINE_TOLERANCE = 3.0


@dataclass(slots=True)
class PageInfo:
    page: int
    width: float
    height: float


@dataclass(slots=True)
class CharAtom:
    id: str
    char: str
    norm_char: str
    page: int
    bbox: tuple[float, float, float, float] | None
    block: int
    line: int
    word: int | None
    stream_index: int
    synthetic: bool
    dom_id: str | None = None
    dom_char_index: int | None = None


@dataclass(slots=True)
class WordAtom:
    id: str
    text: str
    norm_text: str
    page: int
    bbox: tuple[float, float, float, float]
    block: int
    line: int
    word: int
    char_start: int
    char_end: int


@dataclass(slots=True)
class TableCell:
    id: str
    table_id: str
    page: int
    row: int
    col: int
    bbox: tuple[float, float, float, float] | None
    text: str
    row_label: str | None = None
    col_label: str | None = None
    dom_id: str | None = None


@dataclass(slots=True)
class TableRegion:
    id: str
    page: int
    bbox: tuple[float, float, float, float] | None
    row_count: int
    col_count: int
    header_names: list[str] = field(default_factory=list)
    cells: list[TableCell] = field(default_factory=list)
    dom_id: str | None = None


@dataclass(slots=True)
class TextSegment:
    id: str
    page: int
    block: int
    line: int
    bbox: tuple[float, float, float, float]
    raw_text: str
    aligned_text: str
    raw_start: int
    raw_end: int
    dom_id: str | None = None


@dataclass(slots=True)
class DocumentProjection:
    pdf_path: Path | None
    document_kind: str
    pages: list[PageInfo]
    chars: list[CharAtom]
    words: list[WordAtom]
    tables: list[TableRegion]
    text_segments: list[TextSegment]
    raw_text: str
    normalized_text: str
    aligned_text: str
    aligned_to_raw: list[int]
    review_html: str | None


@dataclass(slots=True)
class _RawLine:
    page: int
    block: int
    line: int
    bbox: tuple[float, float, float, float]
    chars: list[tuple[str, tuple[float, float, float, float] | None]]


def _coerce_bbox(raw_bbox: Any) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = raw_bbox
    return (float(x0), float(y0), float(x1), float(y1))


def _clean_cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _should_keep_extracted_char(char: str) -> bool:
    if not char:
        return False
    if char.isspace():
        return True
    category = unicodedata.category(char)
    if category.startswith("C"):
        return False
    return True


def _line_sort_key(line: _RawLine) -> tuple[int, int, float, float]:
    return (
        line.page,
        round(line.bbox[1] / LINE_TOLERANCE),
        line.bbox[0],
        line.block,
    )


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _bbox_contains_point(
    bbox: tuple[float, float, float, float],
    point: tuple[float, float],
) -> bool:
    x0, y0, x1, y1 = bbox
    x, y = point
    return x0 <= x <= x1 and y0 <= y <= y1


def _is_inside_any_table(
    bbox: tuple[float, float, float, float] | None,
    tables: list[TableRegion],
) -> bool:
    if bbox is None:
        return False
    center = _bbox_center(bbox)
    return any(table.bbox is not None and _bbox_contains_point(table.bbox, center) for table in tables)


def _char_boxes_from_span_text(
    text: str,
    bbox: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    if not text:
        return []

    x0, y0, x1, y1 = bbox
    width = max(x1 - x0, 1.0)
    char_width = width / len(text)
    return [
        (x0 + (char_width * index), y0, x0 + (char_width * (index + 1)), y1)
        for index in range(len(text))
    ]


def _extract_line_chars(
    span: dict[str, Any],
    *,
    tables: list[TableRegion],
) -> list[tuple[str, tuple[float, float, float, float] | None]]:
    span_chars = span.get("chars")
    if span_chars:
        ordered = sorted(span_chars, key=lambda item: item["bbox"][0])
        return [
            (str(item.get("c", "")), _coerce_bbox(item["bbox"]) if item.get("bbox") else None)
            for item in ordered
            if _should_keep_extracted_char(str(item.get("c", "")))
            and not _is_inside_any_table(_coerce_bbox(item["bbox"]), tables)
        ]

    text = span.get("text", "")
    span_bbox = span.get("bbox")
    if not text or not span_bbox:
        return []

    boxes = _char_boxes_from_span_text(text, _coerce_bbox(span_bbox))
    return [
        (char, box)
        for char, box in zip(text, boxes)
        if _should_keep_extracted_char(char) and not _is_inside_any_table(box, tables)
    ]


def _extract_tables_from_page(
    page: fitz.Page,
    *,
    page_number: int,
    header_margin: float,
    footer_margin: float,
) -> list[TableRegion]:
    if not hasattr(page, "find_tables"):
        return []

    page_height = float(page.rect.height)
    tables: list[TableRegion] = []
    finder = page.find_tables()
    for table_index, table in enumerate(finder.tables):
        bbox = _coerce_bbox(table.bbox)
        if bbox[1] < header_margin or bbox[3] > (page_height - footer_margin):
            continue
        if table.row_count <= 0 or table.col_count <= 0:
            continue

        header_names = [_clean_cell_text(name) for name in (getattr(table.header, "names", None) or [])]
        extracted_rows = table.extract() or []
        header_is_first_row = (
            bool(header_names)
            and bool(extracted_rows)
            and not getattr(table.header, "external", False)
            and [_clean_cell_text(value) for value in extracted_rows[0]] == header_names
        )
        region = TableRegion(
            id=f"table-{page_number}-{table_index}",
            page=page_number,
            bbox=bbox,
            row_count=table.row_count - (1 if header_is_first_row else 0),
            col_count=table.col_count,
            header_names=header_names,
        )

        for row_index, row in enumerate(table.rows):
            if header_is_first_row and row_index == 0:
                continue

            row_values = extracted_rows[row_index] if row_index < len(extracted_rows) else []
            logical_row_index = row_index - (1 if header_is_first_row else 0)
            row_label_candidate = None
            if logical_row_index >= 0 and row_values:
                first_value = _clean_cell_text(row_values[0])
                row_label_candidate = first_value or None

            for col_index, cell_bbox in enumerate(row.cells):
                if not cell_bbox:
                    continue
                text = ""
                if col_index < len(row_values):
                    text = _clean_cell_text(row_values[col_index])
                col_label = None
                if col_index < len(header_names):
                    col_label = header_names[col_index] or None
                row_label = row_label_candidate if col_index != 0 else None
                region.cells.append(
                    TableCell(
                        id=f"{region.id}-r{row_index}-c{col_index}",
                        table_id=region.id,
                        page=page_number,
                        row=logical_row_index,
                        col=col_index,
                        bbox=_coerce_bbox(cell_bbox),
                        text=text,
                        row_label=row_label,
                        col_label=col_label,
                    )
                )
        tables.append(region)
    return tables


def _extract_lines_from_page(
    page: fitz.Page,
    *,
    page_number: int,
    header_margin: float,
    footer_margin: float,
    tables: list[TableRegion],
) -> list[_RawLine]:
    rawdict = page.get_text("rawdict")
    page_height = float(page.rect.height)
    lines: list[_RawLine] = []

    for block_index, block in enumerate(rawdict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            line_bbox = _coerce_bbox(line["bbox"])
            if line_bbox[1] < header_margin or line_bbox[3] > (page_height - footer_margin):
                continue

            line_chars: list[tuple[str, tuple[float, float, float, float] | None]] = []
            spans = sorted(line.get("spans", []), key=lambda item: item["bbox"][0])
            for span in spans:
                line_chars.extend(_extract_line_chars(span, tables=tables))

            if not line_chars:
                continue

            lines.append(
                _RawLine(
                    page=page_number,
                    block=int(block.get("number", block_index)),
                    line=line_index,
                    bbox=line_bbox,
                    chars=line_chars,
                )
            )

    lines.sort(key=_line_sort_key)
    return lines


def _add_synthetic_char(
    chars: list[CharAtom],
    char: str,
    page: int,
    block: int,
    line: int,
    word: int | None,
) -> None:
    index = len(chars)
    chars.append(
        CharAtom(
            id=f"c-{index}",
            char=char,
            norm_char=normalize_char(char),
            page=page,
            bbox=None,
            block=block,
            line=line,
            word=word,
            stream_index=index,
            synthetic=True,
        )
    )


def _flush_word(
    words: list[WordAtom],
    word_chars: list[CharAtom],
    *,
    word_index: int,
) -> None:
    if not word_chars:
        return

    bboxes = [char.bbox for char in word_chars if char.bbox is not None]
    if not bboxes:
        return

    x0 = min(bbox[0] for bbox in bboxes)
    y0 = min(bbox[1] for bbox in bboxes)
    x1 = max(bbox[2] for bbox in bboxes)
    y1 = max(bbox[3] for bbox in bboxes)
    first = word_chars[0]
    text = "".join(char.char for char in word_chars)
    words.append(
        WordAtom(
            id=f"w-{len(words)}",
            text=text,
            norm_text="".join(normalize_char(char.char) for char in word_chars),
            page=first.page,
            bbox=(x0, y0, x1, y1),
            block=first.block,
            line=first.line,
            word=word_index,
            char_start=first.stream_index,
            char_end=word_chars[-1].stream_index + 1,
        )
    )


def _build_aligned_text(chars: list[CharAtom]) -> tuple[str, list[int]]:
    aligned_chars: list[str] = []
    aligned_to_raw: list[int] = []
    previous_was_space = False

    for raw_index, char in enumerate(chars):
        normalized = char.norm_char
        if not normalized:
            continue
        if char.synthetic and char.char.isspace():
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


def _aligned_text_from_atoms(chars: list[CharAtom]) -> str:
    return _build_aligned_text(chars)[0]


def _append_docx_block_text(
    chars: list[CharAtom],
    text_segments: list[TextSegment],
    *,
    block_index: int,
    dom_id: str,
    text: str,
) -> None:
    if chars:
        _add_synthetic_char(chars, "\n", 0, block_index, 0, None)

    segment_start = len(chars)
    for char_index, char in enumerate(text):
        stream_index = len(chars)
        chars.append(
            CharAtom(
                id=f"c-{stream_index}",
                char=char,
                norm_char=normalize_char(char),
                page=0,
                bbox=None,
                block=block_index,
                line=0,
                word=None,
                stream_index=stream_index,
                synthetic=False,
                dom_id=dom_id,
                dom_char_index=char_index,
            )
        )

    segment_end = len(chars)
    if segment_end == segment_start:
        return

    segment_chars = chars[segment_start:segment_end]
    aligned_text = _aligned_text_from_atoms(segment_chars)
    if not aligned_text:
        return

    text_segments.append(
        TextSegment(
            id=f"segment-{len(text_segments)}",
            page=0,
            block=block_index,
            line=0,
            bbox=(0.0, float(block_index), max(float(len(text)), 1.0), float(block_index) + 1.0),
            raw_text=text,
            aligned_text=aligned_text,
            raw_start=segment_start,
            raw_end=segment_end,
            dom_id=dom_id,
        )
    )


def _extract_docx_document(
    docx_path: str | Path,
    *,
    block_range: tuple[int, int] | None = None,
) -> DocumentProjection:
    path = Path(docx_path)
    structure = load_docx_structure(path)
    blocks = structure.blocks
    if block_range is not None:
        start_block, end_block = block_range
        blocks = [block for block in blocks if start_block <= block.index <= end_block]

    chars: list[CharAtom] = []
    text_segments: list[TextSegment] = []
    tables: list[TableRegion] = []

    for block in blocks:
        if isinstance(block, DocxParagraphBlock):
            _append_docx_block_text(
                chars,
                text_segments,
                block_index=block.index,
                dom_id=block.dom_id,
                text=block.text,
            )
            continue

        region = TableRegion(
            id=block.dom_id,
            page=0,
            bbox=(0.0, float(block.index), max(float(block.col_count), 1.0), float(block.index) + 1.0),
            row_count=block.row_count,
            col_count=block.col_count,
            dom_id=block.dom_id,
        )
        for cell in block.cells:
            region.cells.append(
                TableCell(
                    id=cell.dom_id,
                    table_id=block.dom_id,
                    page=0,
                    row=cell.row,
                    col=cell.col,
                    bbox=(float(cell.col), float(cell.row), float(cell.col + 1), float(cell.row + 1)),
                    text=cell.text,
                    row_label=cell.row_label,
                    col_label=cell.col_label,
                    dom_id=cell.dom_id,
                )
            )
        tables.append(region)

    raw_text = "".join(char.char for char in chars)
    normalized_text = "".join(char.norm_char for char in chars)
    aligned_text, aligned_to_raw = _build_aligned_text(chars)

    return DocumentProjection(
        pdf_path=path,
        document_kind="docx",
        pages=[],
        chars=chars,
        words=[],
        tables=tables,
        text_segments=text_segments,
        raw_text=raw_text,
        normalized_text=normalized_text,
        aligned_text=aligned_text,
        aligned_to_raw=aligned_to_raw,
        review_html=render_docx_review_html(structure) if block_range is None else None,
    )


def extract_document(
    pdf_path: str | Path,
    *,
    header_margin: float,
    footer_margin: float,
    page_range: tuple[int, int] | None = None,
) -> DocumentProjection:
    path = Path(pdf_path)
    if path.suffix.lower() == ".docx":
        return _extract_docx_document(path, block_range=page_range)

    pages: list[PageInfo] = []
    extracted_lines: list[_RawLine] = []
    tables: list[TableRegion] = []

    with fitz.open(path) as doc:
        if page_range is None:
            page_numbers = range(len(doc))
        else:
            start_page, end_page = page_range
            page_numbers = range(max(start_page, 0), min(end_page, len(doc) - 1) + 1)

        for page_number in page_numbers:
            page = doc.load_page(page_number)
            rect = page.rect
            pages.append(PageInfo(page=page_number, width=rect.width, height=rect.height))
            page_tables = _extract_tables_from_page(
                page,
                page_number=page_number,
                header_margin=header_margin,
                footer_margin=footer_margin,
            )
            tables.extend(page_tables)
            extracted_lines.extend(
                _extract_lines_from_page(
                    page,
                    page_number=page_number,
                    header_margin=header_margin,
                    footer_margin=footer_margin,
                    tables=page_tables,
                )
            )

    chars: list[CharAtom] = []
    words: list[WordAtom] = []
    text_segments: list[TextSegment] = []
    previous_page = -1
    previous_block = -1
    previous_line = -1

    for line_item in extracted_lines:
        if previous_page != -1:
            if line_item.page != previous_page:
                _add_synthetic_char(chars, "\n", previous_page, previous_block, previous_line, None)
                _add_synthetic_char(chars, "\n", line_item.page, line_item.block, line_item.line, None)
            else:
                _add_synthetic_char(chars, "\n", previous_page, previous_block, previous_line, None)

        current_word_chars: list[CharAtom] = []
        word_index = 0
        segment_start = len(chars)

        for char, box in line_item.chars:
            stream_index = len(chars)
            char_atom = CharAtom(
                id=f"c-{stream_index}",
                char=char,
                norm_char=normalize_char(char),
                page=line_item.page,
                bbox=box,
                block=line_item.block,
                line=line_item.line,
                word=word_index if not char.isspace() else None,
                stream_index=stream_index,
                synthetic=False,
            )
            chars.append(char_atom)

            if char.isspace():
                _flush_word(words, current_word_chars, word_index=word_index)
                current_word_chars = []
                word_index += 1
            else:
                current_word_chars.append(char_atom)

        _flush_word(words, current_word_chars, word_index=word_index)
        segment_end = len(chars)
        segment_chars = chars[segment_start:segment_end]
        segment_raw_text = "".join(char.char for char in segment_chars)
        segment_aligned_text = _aligned_text_from_atoms(segment_chars)
        if segment_aligned_text:
            text_segments.append(
                TextSegment(
                    id=f"segment-{len(text_segments)}",
                    page=line_item.page,
                    block=line_item.block,
                    line=line_item.line,
                    bbox=line_item.bbox,
                    raw_text=segment_raw_text,
                    aligned_text=segment_aligned_text,
                    raw_start=segment_start,
                    raw_end=segment_end,
                )
            )
        previous_page = line_item.page
        previous_block = line_item.block
        previous_line = line_item.line

    raw_text = "".join(char.char for char in chars)
    normalized_text = "".join(char.norm_char for char in chars)
    aligned_text, aligned_to_raw = _build_aligned_text(chars)

    return DocumentProjection(
        pdf_path=path,
        document_kind="pdf",
        pages=pages,
        chars=chars,
        words=words,
        tables=tables,
        text_segments=text_segments,
        raw_text=raw_text,
        normalized_text=normalized_text,
        aligned_text=aligned_text,
        aligned_to_raw=aligned_to_raw,
        review_html=None,
    )


def extract_page_infos(pdf_path: str | Path) -> list[PageInfo]:
    path = Path(pdf_path)
    if path.suffix.lower() == ".docx":
        return []
    with fitz.open(path) as doc:
        return [
            PageInfo(page=index, width=float(page.rect.width), height=float(page.rect.height))
            for index, page in enumerate(doc)
        ]
