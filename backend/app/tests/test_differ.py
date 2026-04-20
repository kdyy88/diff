from pathlib import Path
import tempfile

import fitz

from app.services.differ import compare_documents
from app.services.extractor import CharAtom, DocumentProjection, PageInfo, WordAtom


def _build_document(text: str, *, page: int = 0, y: float = 100.0) -> DocumentProjection:
    chars = []
    words = []
    cursor = 0
    current_x = 20.0
    for word_index, token in enumerate(text.split(" ")):
        char_start = len(chars)
        for char in token:
            box = (current_x, y, current_x + 8.0, y + 12.0)
            chars.append(
                CharAtom(
                    id=f"c-{len(chars)}",
                    char=char,
                    norm_char=char,
                    page=page,
                    bbox=box,
                    block=0,
                    line=0,
                    word=word_index,
                    stream_index=len(chars),
                    synthetic=False,
                )
            )
            current_x += 8.0
        char_end = len(chars)
        words.append(
            WordAtom(
                id=f"w-{word_index}",
                text=token,
                norm_text=token,
                page=page,
                bbox=(current_x - (len(token) * 8.0), y, current_x, y + 12.0),
                block=0,
                line=0,
                word=word_index,
                char_start=char_start,
                char_end=char_end,
            )
        )
        if word_index < len(text.split(" ")) - 1:
            chars.append(
                CharAtom(
                    id=f"c-{len(chars)}",
                    char=" ",
                    norm_char=" ",
                    page=page,
                    bbox=None,
                    block=0,
                    line=0,
                    word=word_index,
                    stream_index=len(chars),
                    synthetic=True,
                )
            )
            current_x += 4.0
    return DocumentProjection(
        pdf_path=None,  # type: ignore[arg-type]
        pages=[PageInfo(page=page, width=600.0, height=800.0)],
        chars=chars,
        words=words,
        tables=[],
        raw_text=text,
        normalized_text=text,
    )


def _build_table_pdf(path: Path, cell_values: list[list[str]], *, top_label: str) -> None:
    doc = fitz.open()
    row_height = 36
    table_top = 60
    table_bottom = table_top + (row_height * len(cell_values))
    page = doc.new_page(width=400, height=max(260, table_bottom + 80))
    page.insert_text((24, 24), top_label)

    x_positions = [24, 124, 244, 364]
    y_positions = [table_top + (row_height * index) for index in range(len(cell_values) + 1)]
    for x in x_positions:
        page.draw_line((x, y_positions[0]), (x, y_positions[-1]), color=(0, 0, 0), width=1)
    for y in y_positions:
        page.draw_line((x_positions[0], y), (x_positions[-1], y), color=(0, 0, 0), width=1)

    for row_index, row in enumerate(cell_values):
        for col_index, value in enumerate(row):
            page.insert_text((x_positions[col_index] + 8, y_positions[row_index] + 22), value)

    doc.save(path)
    doc.close()


def test_compare_documents_emits_replace_anchor() -> None:
    left = _build_document("Dose 0.5 mg")
    right = _build_document("Dose 0.05 mg")

    result = compare_documents(left, right, include_reflow=False)

    assert result.summary.replacements == 1
    assert any(anchor.kind == "replace" for anchor in result.anchors)


def test_compare_documents_emits_reflow_anchor_for_large_equal_block() -> None:
    text = "This section remains stable across pages but moves later into the next page without content changes"
    left = _build_document(text, page=0, y=120.0)
    right = _build_document(text, page=1, y=60.0)

    result = compare_documents(left, right, include_reflow=True)

    assert result.summary.reflows == 1
    assert result.anchors[0].kind == "reflow"


def test_compare_documents_coalesces_overlapping_replace_anchors() -> None:
    left = _build_document("超时未确认的，视为承租人认可")
    right = _build_document("确认、超时未确认，视为承租人认可")

    result = compare_documents(left, right, include_reflow=False)
    replace_anchors = [anchor for anchor in result.anchors if anchor.kind == "replace"]

    assert len(replace_anchors) == 1
    assert replace_anchors[0].raw_event_count >= 1
    assert "确认" in replace_anchors[0].excerpt_right


def test_compare_documents_does_not_merge_across_strong_boundaries() -> None:
    left = _build_document("第一句改动。第二句保持")
    right = _build_document("第一句变化。第二句调整")

    result = compare_documents(left, right, include_reflow=False)
    replace_anchors = [anchor for anchor in result.anchors if anchor.kind == "replace"]

    assert len(replace_anchors) >= 2


def test_compare_documents_emits_table_cell_anchor() -> None:
    left_path = Path(tempfile.gettempdir()) / "table-left.pdf"
    right_path = Path(tempfile.gettempdir()) / "table-right.pdf"

    _build_table_pdf(
        left_path,
        [
            ["Date", "Amount", "Status"],
            ["2025-04-15", "14,147.19", "成功"],
            ["2025-04-16", "9,000.00", "待处理"],
        ],
        top_label="outside-left",
    )
    _build_table_pdf(
        right_path,
        [
            ["Date", "Amount", "Status"],
            ["2025-04-15", "11,147.19", "成功"],
            ["2025-04-16", "9,000.00", "待处理"],
        ],
        top_label="outside-right",
    )

    from app.services.extractor import extract_document

    left = extract_document(left_path, header_margin=0, footer_margin=0)
    right = extract_document(right_path, header_margin=0, footer_margin=0)

    result = compare_documents(left, right, include_reflow=False)
    table_anchors = [anchor for anchor in result.anchors if anchor.source_type == "table"]

    assert len(table_anchors) == 1
    assert table_anchors[0].kind == "replace"
    assert table_anchors[0].table_context is not None
    assert table_anchors[0].table_context.col_label == "Amount"
    assert "11,147.19" in table_anchors[0].excerpt_right


def test_compare_documents_does_not_mark_whole_table_replace_on_row_insert() -> None:
    left_path = Path(tempfile.gettempdir()) / "table-insert-left.pdf"
    right_path = Path(tempfile.gettempdir()) / "table-insert-right.pdf"

    _build_table_pdf(
        left_path,
        [
            ["Date", "Amount", "Status"],
            ["2025-04-15", "14,147.19", "成功"],
            ["2025-04-16", "9,000.00", "待处理"],
        ],
        top_label="outside-left",
    )
    _build_table_pdf(
        right_path,
        [
            ["Date", "Amount", "Status"],
            ["2025-04-15", "14,147.19", "成功"],
            ["2025-04-16", "9,000.00", "待处理"],
            ["2025-04-17", "1,000.00", "新增"],
        ],
        top_label="outside-right",
    )

    from app.services.extractor import extract_document

    left = extract_document(left_path, header_margin=0, footer_margin=0)
    right = extract_document(right_path, header_margin=0, footer_margin=0)

    result = compare_documents(left, right, include_reflow=False)
    structure_anchors = [
        anchor for anchor in result.anchors
        if anchor.source_type == "table" and anchor.table_context and anchor.table_context.row == -1
    ]
    inserted_cells = [
        anchor for anchor in result.anchors
        if anchor.source_type == "table" and anchor.kind == "insert"
    ]

    assert not structure_anchors
    assert inserted_cells
    assert all("2025-04-15" not in anchor.excerpt_right for anchor in inserted_cells)
