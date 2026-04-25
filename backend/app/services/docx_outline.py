from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Iterator

from docx import Document as open_docx_document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph


@dataclass(frozen=True, slots=True)
class DocxParagraphBlock:
    index: int
    kind: str
    dom_id: str
    text: str
    style_name: str | None = None


@dataclass(frozen=True, slots=True)
class DocxTableCellData:
    dom_id: str
    row: int
    col: int
    text: str
    row_label: str | None = None
    col_label: str | None = None


@dataclass(frozen=True, slots=True)
class DocxTableBlock:
    index: int
    kind: str
    dom_id: str
    row_count: int
    col_count: int
    cells: list[DocxTableCellData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocxStructure:
    blocks: list[DocxParagraphBlock | DocxTableBlock]


def iter_document_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def load_docx_structure(path: str | Path) -> DocxStructure:
    document = open_docx_document(path)
    blocks: list[DocxParagraphBlock | DocxTableBlock] = []

    for index, block in enumerate(iter_document_blocks(document)):
        if isinstance(block, Paragraph):
            style_name = block.style.name.strip() if block.style and block.style.name else None
            kind = "heading" if (style_name or "").strip().lower() == "heading 1" else "paragraph"
            blocks.append(
                DocxParagraphBlock(
                    index=index,
                    kind=kind,
                    dom_id=f"{kind}-{index}",
                    text=block.text.strip(),
                    style_name=style_name,
                )
            )
            continue

        table_id = f"table-{index}"
        header_names = [cell.text.strip() for cell in block.rows[0].cells] if block.rows else []
        cells: list[DocxTableCellData] = []
        for row_index, row in enumerate(block.rows):
            row_label_candidate = row.cells[0].text.strip() if row.cells else None
            for col_index, cell in enumerate(row.cells):
                cells.append(
                    DocxTableCellData(
                        dom_id=f"{table_id}-r{row_index}-c{col_index}",
                        row=row_index,
                        col=col_index,
                        text=cell.text.strip(),
                        row_label=row_label_candidate if col_index != 0 else None,
                        col_label=header_names[col_index] if col_index < len(header_names) else None,
                    )
                )
        blocks.append(
            DocxTableBlock(
                index=index,
                kind="table",
                dom_id=table_id,
                row_count=len(block.rows),
                col_count=max((len(row.cells) for row in block.rows), default=0),
                cells=cells,
            )
        )

    return DocxStructure(blocks=blocks)


def load_docx_blocks(path: str | Path) -> list[DocxParagraphBlock | DocxTableBlock]:
    return load_docx_structure(path).blocks


def is_heading_one_block(block: DocxParagraphBlock | DocxTableBlock) -> bool:
    return isinstance(block, DocxParagraphBlock) and block.kind == "heading"


def render_docx_review_html(structure: DocxStructure) -> str:
    lines = ['<article class="docx-review" data-kind="docx">']
    for block in structure.blocks:
        if isinstance(block, DocxParagraphBlock):
            tag = "h1" if block.kind == "heading" else "p"
            content = escape(block.text) if block.text else "&nbsp;"
            lines.append(f'<{tag} id="{escape(block.dom_id)}" data-dom-id="{escape(block.dom_id)}">{content}</{tag}>')
            continue

        lines.append(f'<table id="{escape(block.dom_id)}" data-dom-id="{escape(block.dom_id)}"><tbody>')
        current_row = -1
        for cell in block.cells:
            if cell.row != current_row:
                if current_row != -1:
                    lines.append('</tr>')
                lines.append('<tr>')
                current_row = cell.row
            cell_tag = "th" if cell.row == 0 else "td"
            content = escape(cell.text) if cell.text else "&nbsp;"
            lines.append(f'<{cell_tag} id="{escape(cell.dom_id)}" data-dom-id="{escape(cell.dom_id)}">{content}</{cell_tag}>')
        if current_row != -1:
            lines.append('</tr>')
        lines.append('</tbody></table>')
    lines.append('</article>')
    return ''.join(lines)