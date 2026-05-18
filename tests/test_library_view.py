from __future__ import annotations

import json
from pathlib import Path

from scholaraio.core.config import _build_config


def _write_main_paper(
    papers_root: Path,
    dirname: str,
    *,
    paper_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int | None = 2026,
    abstract: str = "Abstract text.",
    l3_conclusion: str = "",
    toc: list[dict] | None = None,
    write_md: bool = True,
    paper_type: str = "journal-article",
    write_pdf: bool = False,
) -> Path:
    paper_dir = papers_root / dirname
    paper_dir.mkdir(parents=True)
    meta = {
        "id": paper_id,
        "title": title,
        "authors": authors or ["Jane Doe"],
        "year": year,
        "journal": "Journal of Tests",
        "doi": "10.1000/test",
        "abstract": abstract,
        "paper_type": paper_type,
    }
    if l3_conclusion:
        meta["l3_conclusion"] = l3_conclusion
    if toc is not None:
        meta["toc"] = toc
    (paper_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if write_md:
        (paper_dir / "paper.md").write_text(f"# {title}\n\nBody.", encoding="utf-8")
    if write_pdf:
        (paper_dir / f"{paper_dir.name}.pdf").write_bytes(b"%PDF-test")
    return paper_dir


def _write_proceedings_child(proceedings_root: Path) -> None:
    proceeding_dir = proceedings_root / "Proc-2026-Test"
    child_dir = proceeding_dir / "papers" / "Wave-2026-Test"
    child_dir.mkdir(parents=True)
    (proceeding_dir / "meta.json").write_text(
        json.dumps({"id": "proc-1", "title": "Proceedings of Tests", "year": 2026}),
        encoding="utf-8",
    )
    (child_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": "proc-paper-1",
                "title": "Wave proceedings paper",
                "authors": ["Pat Chen"],
                "year": 2026,
                "doi": "10.1000/proc",
                "abstract": "Proceedings abstract.",
                "paper_type": "conference-paper",
                "proceeding_title": "Proceedings of Tests",
            }
        ),
        encoding="utf-8",
    )
    (child_dir / "paper.md").write_text("# Wave proceedings paper\n", encoding="utf-8")


def test_main_library_view_lists_papers_with_status_and_audit_counts(tmp_path: Path) -> None:
    from scholaraio.services.library_view import build_main_library_view

    papers_root = tmp_path / "data" / "libraries" / "papers"
    _write_main_paper(
        papers_root,
        "Doe-2026-Complete",
        paper_id="paper-1",
        title="Complete paper",
        l3_conclusion="Conclusion text.",
        toc=[{"line": 1, "level": 1, "title": "Introduction"}],
    )
    _write_main_paper(
        papers_root,
        "Missing-2026-Metadata",
        paper_id="paper-2",
        title="Missing metadata",
        authors=[],
        year=None,
        abstract="",
        write_md=False,
    )
    cfg = _build_config({}, tmp_path)

    view = build_main_library_view(cfg)

    assert view["source"] == "main"
    assert view["total"] == 2
    assert view["root"].endswith("data/libraries/papers")
    rows = {row["paper_id"]: row for row in view["papers"]}
    assert rows["paper-1"]["has_md"] is True
    assert rows["paper-1"]["has_abstract"] is True
    assert rows["paper-1"]["has_l3"] is True
    assert rows["paper-1"]["toc_count"] == 1
    assert rows["paper-2"]["has_md"] is False
    assert rows["paper-2"]["issue_counts"]["error"] >= 1
    assert rows["paper-2"]["issue_counts"]["warning"] >= 1
    assert any(issue["rule"] == "missing_md" for issue in rows["paper-2"]["issues"])


def test_main_library_view_normalizes_type_variants_and_reports_pdf(tmp_path: Path) -> None:
    from scholaraio.services.library_view import build_main_library_view

    papers_root = tmp_path / "data" / "libraries" / "papers"
    _write_main_paper(
        papers_root,
        "Doe-2026-Journal",
        paper_id="paper-1",
        title="Journal paper",
        paper_type="JournalArticle",
        write_pdf=True,
    )
    _write_main_paper(
        papers_root,
        "Roe-2026-Article",
        paper_id="paper-2",
        title="Article paper",
        paper_type="article",
    )
    cfg = _build_config({}, tmp_path)

    view = build_main_library_view(cfg)

    rows = {row["paper_id"]: row for row in view["papers"]}
    assert rows["paper-1"]["paper_type"] == "journal-article"
    assert rows["paper-1"]["paper_type_raw"] == "JournalArticle"
    assert rows["paper-1"]["has_pdf"] is True
    assert rows["paper-1"]["pdf_url"] == "/api/main/pdf?id=paper-1"
    assert rows["paper-2"]["paper_type"] == "journal-article"


def test_main_library_detail_returns_abstract_conclusion_toc_and_pdf_without_commands(tmp_path: Path) -> None:
    from scholaraio.services.library_view import get_main_paper_detail

    papers_root = tmp_path / "data" / "libraries" / "papers"
    _write_main_paper(
        papers_root,
        "Doe-2026-Detail",
        paper_id="paper-detail",
        title="Detailed paper",
        abstract="Detailed abstract.",
        l3_conclusion="Detailed conclusion.",
        toc=[{"line": 5, "level": 1, "title": "Methods"}],
        write_pdf=True,
    )
    cfg = _build_config({}, tmp_path)

    detail = get_main_paper_detail(cfg, "paper-detail")

    assert detail["paper_id"] == "paper-detail"
    assert detail["abstract"] == "Detailed abstract."
    assert detail["l3_conclusion"] == "Detailed conclusion."
    assert detail["toc"] == [{"line": 5, "level": 1, "title": "Methods"}]
    assert detail["has_pdf"] is True
    assert detail["pdf_url"] == "/api/main/pdf?id=paper-detail"
    assert "commands" not in detail


def test_main_library_detail_skips_malformed_metadata_before_requested_paper(tmp_path: Path) -> None:
    from scholaraio.services.library_view import get_main_paper_detail

    papers_root = tmp_path / "data" / "libraries" / "papers"
    bad_dir = papers_root / "Bad-2026-Broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{not json", encoding="utf-8")
    _write_main_paper(
        papers_root,
        "Zoo-2026-Valid",
        paper_id="valid-paper",
        title="Valid paper",
        abstract="Valid abstract.",
    )
    cfg = _build_config({}, tmp_path)

    detail = get_main_paper_detail(cfg, "valid-paper")

    assert detail["paper_id"] == "valid-paper"
    assert detail["abstract"] == "Valid abstract."


def test_proceedings_view_lists_child_papers_by_volume(tmp_path: Path) -> None:
    from scholaraio.services.library_view import build_proceedings_library_view

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    _write_proceedings_child(proceedings_root)
    cfg = _build_config({}, tmp_path)

    view = build_proceedings_library_view(cfg)

    assert view["source"] == "proceedings"
    assert view["total"] == 1
    row = view["papers"][0]
    assert row["paper_id"] == "proc-paper-1"
    assert row["dir_name"] == "Wave-2026-Test"
    assert row["proceeding_dir"] == "Proc-2026-Test"
    assert row["proceeding_title"] == "Proceedings of Tests"
    assert row["has_md"] is True


def test_proceedings_detail_returns_volume_context_without_commands(tmp_path: Path) -> None:
    from scholaraio.services.library_view import get_proceedings_paper_detail

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    _write_proceedings_child(proceedings_root)
    cfg = _build_config({}, tmp_path)

    detail = get_proceedings_paper_detail(cfg, "proc-paper-1")

    assert detail["paper_id"] == "proc-paper-1"
    assert detail["proceeding_title"] == "Proceedings of Tests"
    assert detail["abstract"] == "Proceedings abstract."
    assert "commands" not in detail
