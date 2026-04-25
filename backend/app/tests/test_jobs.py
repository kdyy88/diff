from pathlib import Path

from app.models.schemas import DiffAnchor, DiffResult, DiffSummary, PageMeta
from app.services.jobs import JobStore


def _build_result() -> DiffResult:
    return DiffResult(
        pages_left=[PageMeta(page=0, width=600.0, height=800.0)],
        pages_right=[PageMeta(page=0, width=600.0, height=800.0)],
        summary=DiffSummary(replacements=1, pages_a=1, pages_b=1),
        anchors=[
            DiffAnchor(
                id="anchor-1",
                kind="replace",
                excerpt_left="原句包含足够的上下文信息。",
                excerpt_right="新句同样包含足够的上下文信息。",
            )
        ],
        chapters=[],
    )


def test_job_store_persists_markdown_bundle(monkeypatch, tmp_path: Path) -> None:
    store = JobStore()
    monkeypatch.setattr("app.services.jobs.ensure_markdown_output_dir", lambda: tmp_path)

    output_dir = store._persist_markdown_bundle("job-123", _build_result())

    assert output_dir == tmp_path / "job-123"
    assert (output_dir / "00-overview.md").exists()
    assert (output_dir / "10-part-01.md").exists()
    overview = (output_dir / "00-overview.md").read_text(encoding="utf-8")
    detail = (output_dir / "10-part-01.md").read_text(encoding="utf-8")

    assert "kind: \"overview\"" in overview
    assert 'original_document_label: "原文档"' in overview
    assert "原句包含足够的上下文信息。" in detail
    assert "原文档片段：原句包含足够的上下文信息。" in detail