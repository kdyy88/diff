from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from diff_match_patch import diff_match_patch

from app.models.schemas import DiffAnchor, HighlightFragment
from app.services.docx_outline import DocxParagraphBlock, DocxStructure, DocxTableBlock, DocxTableCellData, load_docx_structure


@dataclass(frozen=True, slots=True)
class DocxDiffDetailOp:
    kind: str
    left_text: str
    right_text: str


@dataclass(frozen=True, slots=True)
class DocxAnchorDetail:
    block_kind: str
    left_dom_ids: list[str]
    right_dom_ids: list[str]
    left_text: str
    right_text: str
    operations: list[DocxDiffDetailOp]


def build_docx_anchor_detail_lookup(
    source_path: str | Path,
    modified_path: str | Path,
    anchors: list[DiffAnchor],
) -> dict[str, DocxAnchorDetail]:
    source_maps = _DocxStructureMaps(load_docx_structure(source_path))
    modified_maps = _DocxStructureMaps(load_docx_structure(modified_path))

    return {
        anchor.id: _build_anchor_detail(anchor, source_maps=source_maps, modified_maps=modified_maps)
        for anchor in anchors
    }


@dataclass(frozen=True, slots=True)
class _DocxStructureMaps:
    paragraphs: dict[str, DocxParagraphBlock]
    tables: dict[str, DocxTableBlock]
    cells: dict[str, DocxTableCellData]

    def __init__(self, structure: DocxStructure) -> None:
        paragraphs: dict[str, DocxParagraphBlock] = {}
        tables: dict[str, DocxTableBlock] = {}
        cells: dict[str, DocxTableCellData] = {}
        for block in structure.blocks:
            if isinstance(block, DocxParagraphBlock):
                paragraphs[block.dom_id] = block
                continue

            tables[block.dom_id] = block
            for cell in block.cells:
                cells[cell.dom_id] = cell

        object.__setattr__(self, "paragraphs", paragraphs)
        object.__setattr__(self, "tables", tables)
        object.__setattr__(self, "cells", cells)


def _build_anchor_detail(
    anchor: DiffAnchor,
    *,
    source_maps: _DocxStructureMaps,
    modified_maps: _DocxStructureMaps,
) -> DocxAnchorDetail:
    left_dom_ids = _ordered_dom_ids(anchor.left_fragments)
    right_dom_ids = _ordered_dom_ids(anchor.right_fragments)
    left_text, left_kind = _resolve_dom_text(left_dom_ids, source_maps)
    right_text, right_kind = _resolve_dom_text(right_dom_ids, modified_maps)
    block_kind = _pick_block_kind(left_kind, right_kind, anchor.source_type)
    operations = _build_detail_operations(left_text, right_text)
    return DocxAnchorDetail(
        block_kind=block_kind,
        left_dom_ids=left_dom_ids,
        right_dom_ids=right_dom_ids,
        left_text=left_text,
        right_text=right_text,
        operations=operations,
    )


def _ordered_dom_ids(fragments: list[HighlightFragment]) -> list[str]:
    dom_ids: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        if fragment.kind != "word" or not fragment.dom_id or fragment.dom_id in seen:
            continue
        seen.add(fragment.dom_id)
        dom_ids.append(fragment.dom_id)
    return dom_ids


def _resolve_dom_text(dom_ids: list[str], maps: _DocxStructureMaps) -> tuple[str, str | None]:
    if not dom_ids:
        return "", None

    parts: list[str] = []
    resolved_kinds: list[str] = []
    for dom_id in dom_ids:
        if dom_id in maps.paragraphs:
            block = maps.paragraphs[dom_id]
            parts.append(block.text)
            resolved_kinds.append(block.kind)
            continue
        if dom_id in maps.cells:
            cell = maps.cells[dom_id]
            parts.append(cell.text)
            resolved_kinds.append("table-cell")
            continue
        if dom_id in maps.tables:
            table = maps.tables[dom_id]
            parts.append(_render_table_snapshot(table))
            resolved_kinds.append("table")

    return "\n\n".join(part for part in parts if part), resolved_kinds[0] if resolved_kinds else None


def _render_table_snapshot(table: DocxTableBlock) -> str:
    rows: dict[int, list[str]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell.text)
    return "\n".join(
        f"R{row_index + 1}: " + " | ".join(values)
        for row_index, values in sorted(rows.items())
    )


def _pick_block_kind(left_kind: str | None, right_kind: str | None, source_type: str) -> str:
    if source_type == "table":
        return right_kind or left_kind or "table"
    return right_kind or left_kind or "paragraph"


def _build_detail_operations(left_text: str, right_text: str) -> list[DocxDiffDetailOp]:
    if not left_text and not right_text:
        return []
    if not left_text:
        return [DocxDiffDetailOp(kind="insert", left_text="", right_text=right_text)]
    if not right_text:
        return [DocxDiffDetailOp(kind="delete", left_text=left_text, right_text="")]
    if left_text == right_text:
        return []

    dmp = diff_match_patch()
    diffs = dmp.diff_main(left_text, right_text, checklines=False)
    dmp.diff_cleanupSemantic(diffs)

    merged: list[DocxDiffDetailOp] = []
    index = 0
    while index < len(diffs):
        op, text = diffs[index]
        if op == diff_match_patch.DIFF_EQUAL:
            index += 1
            continue
        if op == diff_match_patch.DIFF_DELETE and index + 1 < len(diffs) and diffs[index + 1][0] == diff_match_patch.DIFF_INSERT:
            merged.append(DocxDiffDetailOp(kind="replace", left_text=text, right_text=diffs[index + 1][1]))
            index += 2
            continue
        if op == diff_match_patch.DIFF_INSERT and index + 1 < len(diffs) and diffs[index + 1][0] == diff_match_patch.DIFF_DELETE:
            merged.append(DocxDiffDetailOp(kind="replace", left_text=diffs[index + 1][1], right_text=text))
            index += 2
            continue
        if op == diff_match_patch.DIFF_DELETE:
            merged.append(DocxDiffDetailOp(kind="delete", left_text=text, right_text=""))
        elif op == diff_match_patch.DIFF_INSERT:
            merged.append(DocxDiffDetailOp(kind="insert", left_text="", right_text=text))
        index += 1

    return [operation for operation in merged if operation.left_text or operation.right_text]