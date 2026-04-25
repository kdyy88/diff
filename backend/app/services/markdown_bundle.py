from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Sequence

from app.models.schemas import ChapterDiffSummary, DiffAnchor, DiffResult, HighlightFragment
from app.services.docx_markdown_diff import DocxAnchorDetail, DocxDiffDetailOp, build_docx_anchor_detail_lookup


MISSING_SIDE_PLACEHOLDER = "(此版本无对应内容)"
ORIGINAL_DOCUMENT_LABEL = "原文档"
MODIFIED_DOCUMENT_LABEL = "修订后文档"
DEFAULT_DETAIL_SHARD_SIZE = 20
DEFAULT_REPRESENTATIVE_LIMIT = 6


@dataclass(frozen=True, slots=True)
class MarkdownBundleFile:
    name: str
    content: str


@dataclass(frozen=True, slots=True)
class MarkdownBundle:
    files: list[MarkdownBundleFile]


@dataclass(frozen=True, slots=True)
class _DetailShardPlan:
    name: str
    anchors: list[DiffAnchor]
    chapter_id: str | None
    chapter_title: str | None


@dataclass(frozen=True, slots=True)
class _DocxMoveInfo:
    paired_anchor_id: str
    role: str
    moved_text: str
    source_location: str
    target_location: str


def build_markdown_bundle(
    job_id: str,
    result: DiffResult,
    *,
    detail_shard_size: int = DEFAULT_DETAIL_SHARD_SIZE,
    representative_limit: int = DEFAULT_REPRESENTATIVE_LIMIT,
    source_document_path: str | Path | None = None,
    modified_document_path: str | Path | None = None,
) -> MarkdownBundle:
    non_reflow_anchors = [anchor for anchor in result.anchors if anchor.kind != "reflow"]
    chapter_mode, chapter_lookup = _resolve_chapter_mode(result, non_reflow_anchors)
    docx_anchor_details = _build_docx_anchor_details(
        result,
        anchors=non_reflow_anchors,
        source_document_path=source_document_path,
        modified_document_path=modified_document_path,
    )
    docx_move_lookup = _build_docx_move_lookup(non_reflow_anchors, docx_anchor_details)
    detail_plans = _build_detail_plans(
        non_reflow_anchors,
        chapter_lookup=chapter_lookup,
        chapter_mode=chapter_mode,
        detail_shard_size=detail_shard_size,
    )
    detail_files = [
        MarkdownBundleFile(
            name=plan.name,
            content=_render_detail_file(
                job_id,
                plan,
                docx_anchor_details=docx_anchor_details,
                docx_move_lookup=docx_move_lookup,
            ),
        )
        for plan in detail_plans
    ]
    overview_file = MarkdownBundleFile(
        name="00-overview.md",
        content=_render_overview_file(
            job_id,
            result,
            detail_plans=detail_plans,
            chapter_mode=chapter_mode,
            representative_limit=representative_limit,
        ),
    )
    return MarkdownBundle(files=[overview_file, *detail_files])


def _build_docx_anchor_details(
    result: DiffResult,
    *,
    anchors: list[DiffAnchor],
    source_document_path: str | Path | None,
    modified_document_path: str | Path | None,
) -> dict[str, DocxAnchorDetail] | None:
    if result.document_kind != "docx":
        return None
    if source_document_path is None or modified_document_path is None:
        return None
    return build_docx_anchor_detail_lookup(source_document_path, modified_document_path, anchors)


def _build_docx_move_lookup(
    anchors: list[DiffAnchor],
    docx_anchor_details: dict[str, DocxAnchorDetail] | None,
) -> dict[str, _DocxMoveInfo]:
    if not docx_anchor_details:
        return {}

    ordered = list(enumerate(anchors))
    inserts = [
        (index, anchor, docx_anchor_details[anchor.id])
        for index, anchor in ordered
        if anchor.kind == "insert" and anchor.id in docx_anchor_details
    ]
    used_insert_ids: set[str] = set()
    move_lookup: dict[str, _DocxMoveInfo] = {}

    for delete_index, delete_anchor in ordered:
        if delete_anchor.kind != "delete" or delete_anchor.id not in docx_anchor_details:
            continue
        delete_detail = docx_anchor_details[delete_anchor.id]
        delete_text = delete_detail.left_text.strip()
        if not delete_text:
            continue

        match: tuple[int, DiffAnchor, DocxAnchorDetail] | None = None
        for insert_index, insert_anchor, insert_detail in inserts:
            if insert_anchor.id in used_insert_ids:
                continue
            if insert_anchor.source_type != delete_anchor.source_type:
                continue
            if insert_detail.right_text.strip() != delete_text:
                continue
            if insert_detail.block_kind != delete_detail.block_kind:
                continue
            if match is None or abs(insert_index - delete_index) < abs(match[0] - delete_index):
                match = (insert_index, insert_anchor, insert_detail)

        if match is None:
            continue

        _, insert_anchor, insert_detail = match
        used_insert_ids.add(insert_anchor.id)
        source_location = _page_label(delete_anchor.left_fragments)
        target_location = _page_label(insert_anchor.right_fragments)
        moved_text = delete_text
        move_lookup[delete_anchor.id] = _DocxMoveInfo(
            paired_anchor_id=insert_anchor.id,
            role="source",
            moved_text=moved_text,
            source_location=source_location,
            target_location=target_location,
        )
        move_lookup[insert_anchor.id] = _DocxMoveInfo(
            paired_anchor_id=delete_anchor.id,
            role="target",
            moved_text=insert_detail.right_text.strip(),
            source_location=source_location,
            target_location=target_location,
        )

    return move_lookup


def _resolve_chapter_mode(
    result: DiffResult,
    anchors: list[DiffAnchor],
) -> tuple[bool, dict[str, ChapterDiffSummary]]:
    if not result.chapters:
        return False, {}

    chapter_lookup = {chapter.id: chapter for chapter in result.chapters}
    for anchor in anchors:
        if not anchor.chapter_id or anchor.chapter_id not in chapter_lookup:
            return False, {}
    return True, chapter_lookup


def _build_detail_plans(
    anchors: list[DiffAnchor],
    *,
    chapter_lookup: dict[str, ChapterDiffSummary],
    chapter_mode: bool,
    detail_shard_size: int,
) -> list[_DetailShardPlan]:
    if detail_shard_size <= 0:
        raise ValueError("detail_shard_size must be positive")

    plans: list[_DetailShardPlan] = []
    if not chapter_mode:
        for index, chunk in enumerate(_chunked(anchors, detail_shard_size), start=1):
            plans.append(
                _DetailShardPlan(
                    name=f"10-part-{index:02d}.md",
                    anchors=chunk,
                    chapter_id=None,
                    chapter_title=None,
                )
            )
        return plans

    grouped_anchors: dict[str, list[DiffAnchor]] = {chapter_id: [] for chapter_id in chapter_lookup}
    for anchor in anchors:
        assert anchor.chapter_id is not None
        grouped_anchors[anchor.chapter_id].append(anchor)

    for chapter_position, chapter in enumerate(result_chapters_in_order(chapter_lookup), start=1):
        chapter_anchors = grouped_anchors.get(chapter.id, [])
        for shard_index, chunk in enumerate(_chunked(chapter_anchors, detail_shard_size), start=1):
            plans.append(
                _DetailShardPlan(
                    name=f"10-chapter-{chapter_position:02d}-part-{shard_index:02d}.md",
                    anchors=chunk,
                    chapter_id=chapter.id,
                    chapter_title=chapter.title,
                )
            )
    return plans


def result_chapters_in_order(chapter_lookup: dict[str, ChapterDiffSummary]) -> list[ChapterDiffSummary]:
    return sorted(chapter_lookup.values(), key=lambda chapter: chapter.index)


def _chunked(items: list[DiffAnchor], size: int) -> list[list[DiffAnchor]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _render_overview_file(
    job_id: str,
    result: DiffResult,
    *,
    detail_plans: list[_DetailShardPlan],
    chapter_mode: bool,
    representative_limit: int,
) -> str:
    non_reflow_anchors = [anchor for anchor in result.anchors if anchor.kind != "reflow"]
    lines = [
        _render_front_matter(
            kind="overview",
            job_id=job_id,
            anchors=non_reflow_anchors,
            chapter_id=None,
            chapter_title=None,
            reflows=result.summary.reflows,
        ),
        "# 变更概览",
        "",
        "## 全局摘要",
        "",
        f"- 非 reflow 变更总数：{len(non_reflow_anchors)}",
        f"- 插入：{result.summary.insertions}",
        f"- 删除：{result.summary.deletions}",
        f"- 替换：{result.summary.replacements}",
        f"- reflow：{result.summary.reflows}",
    ]

    if chapter_mode:
        plan_lookup: dict[str, list[str]] = {}
        for plan in detail_plans:
            if plan.chapter_id is None:
                continue
            plan_lookup.setdefault(plan.chapter_id, []).append(plan.name)
        lines.extend(["", "## 章节索引", ""])
        for chapter in sorted(result.chapters, key=lambda item: item.index):
            shard_names = plan_lookup.get(chapter.id, [])
            shard_label = "、".join(shard_names) if shard_names else "无 detail shard"
            lines.append(
                f"- {chapter.title}（anchor: {chapter.anchor_count}，详情：{shard_label}）"
            )
    else:
        lines.extend(["", "## 分片索引", ""])
        if detail_plans:
            for plan in detail_plans:
                lines.append(f"- {plan.name}（anchor: {len(plan.anchors)}）")
        else:
            lines.append("- 当前结果没有可展开的 detail shard。")

    anchor_to_file = {
        anchor.id: plan.name
        for plan in detail_plans
        for anchor in plan.anchors
    }
    lines.extend(["", "## 代表性变更", ""])
    for anchor in _representative_anchors(non_reflow_anchors, limit=representative_limit):
        shard_name = anchor_to_file.get(anchor.id, "未分片")
        lines.append(f"- {_representative_summary(anchor)}（详见 {shard_name}）")
    if not non_reflow_anchors:
        lines.append("- 当前结果没有非 reflow 变更。")

    return "\n".join(lines).rstrip() + "\n"


def _render_detail_file(
    job_id: str,
    plan: _DetailShardPlan,
    *,
    docx_anchor_details: dict[str, DocxAnchorDetail] | None,
    docx_move_lookup: dict[str, _DocxMoveInfo],
) -> str:
    title = plan.chapter_title or plan.name.removesuffix(".md")
    lines = [
        _render_front_matter(
            kind="detail",
            job_id=job_id,
            anchors=plan.anchors,
            chapter_id=plan.chapter_id,
            chapter_title=plan.chapter_title,
            reflows=0,
        ),
        f"# {title}",
    ]

    for index, anchor in enumerate(plan.anchors, start=1):
        lines.extend(["", f"## {index}. {anchor.id}"])
        if docx_anchor_details and anchor.id in docx_anchor_details:
            lines.extend(
                _render_docx_anchor(
                    anchor,
                    docx_anchor_details[anchor.id],
                    move_info=docx_move_lookup.get(anchor.id),
                )
            )
        elif anchor.source_type == "table":
            lines.extend(_render_table_anchor(anchor))
        else:
            lines.extend(_render_text_anchor(anchor))

    return "\n".join(lines).rstrip() + "\n"


def _render_text_anchor(anchor: DiffAnchor) -> list[str]:
    return [
        f"变更类型：{anchor.kind}",
        f"置信度：{anchor.confidence}",
        f"章节：{anchor.chapter_title or '(无章节)'}",
        f"{ORIGINAL_DOCUMENT_LABEL}页码：{_page_label(anchor.left_fragments)}",
        f"{MODIFIED_DOCUMENT_LABEL}页码：{_page_label(anchor.right_fragments)}",
        f"{ORIGINAL_DOCUMENT_LABEL}片段：{anchor.excerpt_left or _missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL)}",
        f"{MODIFIED_DOCUMENT_LABEL}片段：{anchor.excerpt_right or _missing_document_placeholder(MODIFIED_DOCUMENT_LABEL)}",
        f"多次编辑合并：{'是' if anchor.raw_event_count > 1 else '否'}",
    ]


def _render_table_anchor(anchor: DiffAnchor) -> list[str]:
    return [
        _table_change_summary(anchor),
        f"变更类型：{anchor.kind}",
        f"置信度：{anchor.confidence}",
        f"章节：{anchor.chapter_title or '(无章节)'}",
        f"{ORIGINAL_DOCUMENT_LABEL}页码：{_page_label(anchor.left_fragments)}",
        f"{MODIFIED_DOCUMENT_LABEL}页码：{_page_label(anchor.right_fragments)}",
        f"多次编辑合并：{'是' if anchor.raw_event_count > 1 else '否'}",
    ]


def _render_docx_anchor(
    anchor: DiffAnchor,
    detail: DocxAnchorDetail,
    *,
    move_info: _DocxMoveInfo | None,
) -> list[str]:
    left_full_text = detail.left_text or (move_info.moved_text if move_info else _missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL))
    right_full_text = detail.right_text or (move_info.moved_text if move_info else _missing_document_placeholder(MODIFIED_DOCUMENT_LABEL))
    lines = []
    if anchor.source_type == "table":
        lines.append(_docx_table_change_summary(anchor, detail))
    lines.extend(
        [
            f"块类型：{_docx_block_kind_label(detail.block_kind)}",
            f"变更类型：{anchor.kind}",
            f"置信度：{anchor.confidence}",
            f"章节：{anchor.chapter_title or '(无章节)'}",
            f"{ORIGINAL_DOCUMENT_LABEL}位置：{_page_label(anchor.left_fragments)}",
            f"{MODIFIED_DOCUMENT_LABEL}位置：{_page_label(anchor.right_fragments)}",
            f"{ORIGINAL_DOCUMENT_LABEL}全文：{left_full_text}",
            f"{MODIFIED_DOCUMENT_LABEL}全文：{right_full_text}",
            "差异明细：",
        ]
    )
    lines.extend(_render_docx_diff_operations(detail.operations, move_info=move_info))
    lines.append(f"多次编辑合并：{'是' if anchor.raw_event_count > 1 else '否'}")
    return lines


def _docx_block_kind_label(block_kind: str) -> str:
    return {
        "heading": "标题",
        "paragraph": "段落",
        "table-cell": "表格单元格",
        "table": "表格",
    }.get(block_kind, block_kind)


def _render_docx_diff_operations(
    operations: list[DocxDiffDetailOp],
    *,
    move_info: _DocxMoveInfo | None,
) -> list[str]:
    if move_info is not None:
        if move_info.role == "source":
            return [
                f"- 位置移动：文本内容未改写，与 {move_info.paired_anchor_id} 配对后判定为移动，"
                f"从 {move_info.source_location} 移动到 {move_info.target_location}。"
            ]
        return [
            f"- 位置移动：文本内容未改写，与 {move_info.paired_anchor_id} 配对后判定为移动，"
            f"由 {move_info.source_location} 移动到 {move_info.target_location}。"
        ]

    if not operations:
        return ["- 当前节点没有可展开的逐项文本改写，差异主要体现在结构或定位。"]

    lines: list[str] = []
    for operation in operations:
        if operation.kind == "replace":
            lines.append(
                f"- 替换：{json.dumps(operation.left_text, ensure_ascii=False)} -> {json.dumps(operation.right_text, ensure_ascii=False)}"
            )
        elif operation.kind == "delete":
            lines.append(f"- 删除：{json.dumps(operation.left_text, ensure_ascii=False)}")
        elif operation.kind == "insert":
            lines.append(f"- 新增：{json.dumps(operation.right_text, ensure_ascii=False)}")
    return lines


def _docx_table_change_summary(anchor: DiffAnchor, detail: DocxAnchorDetail) -> str:
    context = anchor.table_context
    if context and context.row == -1 and context.col == -1:
        return "表格结构发生变化。"

    col_label = _table_axis_label(context.col_label if context else None, "C", context.col if context else None)
    row_label = _table_axis_label(context.row_label if context else None, "R", context.row if context else None)
    left_text = detail.left_text or _missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL)
    right_text = detail.right_text or _missing_document_placeholder(MODIFIED_DOCUMENT_LABEL)

    if anchor.kind == "insert":
        return (
            f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，"
            f"原值为 {json.dumps(_missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL), ensure_ascii=False)}，"
            f"变更为 {json.dumps(right_text, ensure_ascii=False)}。"
        )
    if anchor.kind == "delete":
        return (
            f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，"
            f"原值为 {json.dumps(left_text, ensure_ascii=False)}，"
            f"变更为 {json.dumps(_missing_document_placeholder(MODIFIED_DOCUMENT_LABEL), ensure_ascii=False)}。"
        )
    return (
        f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，原值为 {json.dumps(left_text, ensure_ascii=False)}，"
        f"变更为 {json.dumps(right_text, ensure_ascii=False)}。"
    )


def _table_change_summary(anchor: DiffAnchor) -> str:
    context = anchor.table_context
    if context and context.row == -1 and context.col == -1:
        return "表格结构发生变化。"

    col_label = _table_axis_label(context.col_label if context else None, "C", context.col if context else None)
    row_label = _table_axis_label(context.row_label if context else None, "R", context.row if context else None)
    left_text = anchor.excerpt_left or _missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL)
    right_text = anchor.excerpt_right or _missing_document_placeholder(MODIFIED_DOCUMENT_LABEL)

    if anchor.kind == "insert":
        return (
            f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，"
            f"原值为 {json.dumps(_missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL), ensure_ascii=False)}，"
            f"变更为 {json.dumps(right_text, ensure_ascii=False)}。"
        )
    if anchor.kind == "delete":
        return (
            f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，"
            f"原值为 {json.dumps(left_text, ensure_ascii=False)}，"
            f"变更为 {json.dumps(_missing_document_placeholder(MODIFIED_DOCUMENT_LABEL), ensure_ascii=False)}。"
        )
    return (
        f"表格变更：所在列 [{col_label}]，所在行 [{row_label}]，原值为 {json.dumps(left_text, ensure_ascii=False)}，"
        f"变更为 {json.dumps(right_text, ensure_ascii=False)}。"
    )


def _missing_document_placeholder(document_label: str) -> str:
    return f"（{document_label}无对应内容）"


def _table_axis_label(label: str | None, prefix: str, index: int | None) -> str:
    if label:
        return label
    if index is None or index < 0:
        return f"{prefix}?"
    return f"{prefix}{index + 1}"


def _representative_anchors(anchors: list[DiffAnchor], *, limit: int) -> list[DiffAnchor]:
    ordered = list(enumerate(anchors))
    ranked = sorted(
        ordered,
        key=lambda item: (-_representative_score(item[1]), item[0]),
    )
    return [anchor for _, anchor in ranked[:limit]]


def _representative_score(anchor: DiffAnchor) -> int:
    score = 0
    if anchor.kind == "replace":
        score += 40
    elif anchor.kind in {"insert", "delete"}:
        score += 20
    if anchor.source_type == "table":
        score += 30
    if anchor.confidence == "low":
        score += 25
    if anchor.raw_event_count > 1:
        score += 15
    return score


def _representative_summary(anchor: DiffAnchor) -> str:
    if anchor.source_type == "table":
        summary = _table_change_summary(anchor)
        if anchor.confidence == "low":
            summary = f"{summary} [low confidence]"
        if anchor.raw_event_count > 1:
            summary = f"{summary} [merged]"
        return summary

    chapter_title = anchor.chapter_title or "(无章节)"
    confidence_suffix = " | low confidence" if anchor.confidence == "low" else ""
    merged_suffix = " | merged" if anchor.raw_event_count > 1 else ""
    return (
        f"{anchor.kind}{confidence_suffix}{merged_suffix} | {chapter_title} | "
        f"{ORIGINAL_DOCUMENT_LABEL}：{anchor.excerpt_left or _missing_document_placeholder(ORIGINAL_DOCUMENT_LABEL)} | "
        f"{MODIFIED_DOCUMENT_LABEL}：{anchor.excerpt_right or _missing_document_placeholder(MODIFIED_DOCUMENT_LABEL)}"
    )


def _page_reference(anchor: DiffAnchor) -> str:
    return f"{ORIGINAL_DOCUMENT_LABEL}：{_page_label(anchor.left_fragments)}；{MODIFIED_DOCUMENT_LABEL}：{_page_label(anchor.right_fragments)}"


def _page_label(fragments: Sequence[HighlightFragment]) -> str:
    if fragments and getattr(fragments[0], "kind", "pdf") == "word":
        ranges_by_dom_id: dict[str, tuple[int | None, int | None]] = {}
        for fragment in fragments:
            dom_id = getattr(fragment, "dom_id", None) or "(unknown-node)"
            char_start = getattr(fragment, "char_start", None)
            char_end = getattr(fragment, "char_end", None)
            current_start, current_end = ranges_by_dom_id.get(dom_id, (None, None))
            merged_start = char_start if current_start is None else current_start if char_start is None else min(current_start, char_start)
            merged_end = char_end if current_end is None else current_end if char_end is None else max(current_end, char_end)
            ranges_by_dom_id[dom_id] = (merged_start, merged_end)

        labels = []
        for dom_id, (char_start, char_end) in ranges_by_dom_id.items():
            if char_start is None or char_end is None:
                labels.append(dom_id)
            else:
                labels.append(f"{dom_id}[{char_start}:{char_end}]")
        return "节点 " + "，".join(labels)

    pages = sorted({fragment.page + 1 for fragment in fragments if fragment.page is not None})
    if not pages:
        return "无"

    ranges: list[str] = []
    start = pages[0]
    previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(f"{start}-{previous}" if start != previous else str(start))
        start = page
        previous = page
    ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return f"第 {'，'.join(ranges)} 页"


def _render_front_matter(
    *,
    kind: str,
    job_id: str,
    anchors: list[DiffAnchor],
    chapter_id: str | None,
    chapter_title: str | None,
    reflows: int,
) -> str:
    insertions = sum(1 for anchor in anchors if anchor.kind == "insert")
    deletions = sum(1 for anchor in anchors if anchor.kind == "delete")
    replacements = sum(1 for anchor in anchors if anchor.kind == "replace")
    contains_tables = any(anchor.source_type == "table" for anchor in anchors)
    contains_low_confidence = any(anchor.confidence == "low" for anchor in anchors)
    front_matter = {
        "kind": kind,
        "job_id": job_id,
        "original_document_label": ORIGINAL_DOCUMENT_LABEL,
        "modified_document_label": MODIFIED_DOCUMENT_LABEL,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "anchor_count": len(anchors),
        "insertions": insertions,
        "deletions": deletions,
        "replacements": replacements,
        "reflows": reflows,
        "contains_tables": contains_tables,
        "contains_low_confidence": contains_low_confidence,
        "first_anchor_id": anchors[0].id if anchors else None,
        "last_anchor_id": anchors[-1].id if anchors else None,
    }
    lines = ["---"]
    for key, value in front_matter.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _yaml_scalar(value: str | int | bool | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)