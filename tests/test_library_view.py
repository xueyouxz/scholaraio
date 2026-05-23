from __future__ import annotations

import json
import threading
import time
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


def test_main_library_view_reuses_audit_map_for_poll_and_detail(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view
    from scholaraio.services.audit import Issue

    papers_root = tmp_path / "data" / "libraries" / "papers"
    _write_main_paper(
        papers_root,
        "Doe-2026-Poll",
        paper_id="paper-poll",
        title="Polling paper",
    )
    cfg = _build_config({}, tmp_path)
    calls = {"count": 0}

    def fake_audit(papers_dir: Path):
        calls["count"] += 1
        assert papers_dir == papers_root
        return [Issue("Doe-2026-Poll", "warning", "sample", "Sample warning")]

    monkeypatch.setattr(library_view, "audit_papers", fake_audit)

    first = library_view.build_main_library_view(cfg)
    second = library_view.build_main_library_view(cfg)
    detail = library_view.get_main_paper_detail(cfg, "paper-poll")

    assert first["issue_totals"]["warning"] == 1
    assert second["issue_totals"]["warning"] == 1
    assert detail["issue_counts"]["warning"] == 1
    assert calls["count"] == 1


def test_main_library_audit_cache_is_thread_safe(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view
    from scholaraio.services.audit import Issue

    papers_root = tmp_path / "data" / "libraries" / "papers"
    _write_main_paper(
        papers_root,
        "Doe-2026-Concurrent",
        paper_id="paper-concurrent",
        title="Concurrent paper",
    )
    cfg = _build_config({}, tmp_path)
    with library_view._AUDIT_CACHE_LOCK:
        library_view._AUDIT_CACHE.clear()
    calls = {"count": 0}
    call_lock = threading.Lock()
    first_started = threading.Event()
    release_audit = threading.Event()

    def fake_audit(papers_dir: Path):
        with call_lock:
            calls["count"] += 1
        first_started.set()
        assert papers_dir == papers_root
        assert release_audit.wait(timeout=5)
        return [Issue("Doe-2026-Concurrent", "warning", "sample", "Sample warning")]

    monkeypatch.setattr(library_view, "audit_papers", fake_audit)
    results: list[dict] = []
    errors: list[BaseException] = []

    def collect_view() -> None:
        try:
            results.append(library_view.build_main_library_view(cfg))
        except BaseException as exc:  # pragma: no cover - preserves thread assertion context
            errors.append(exc)

    first = threading.Thread(target=collect_view)
    second = threading.Thread(target=collect_view)
    first.start()
    assert first_started.wait(timeout=5)
    second.start()
    time.sleep(0.05)
    release_audit.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert [view["issue_totals"]["warning"] for view in results] == [1, 1]
    assert calls["count"] == 1


def test_main_library_pdf_lookup_does_not_audit_entire_library(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view

    papers_root = tmp_path / "data" / "libraries" / "papers"
    paper_dir = _write_main_paper(
        papers_root,
        "Doe-2026-PDF",
        paper_id="paper-pdf",
        title="PDF paper",
        write_pdf=True,
    )
    cfg = _build_config({}, tmp_path)

    def fail_audit(*_args, **_kwargs):
        raise AssertionError("PDF lookup should not audit the full library")

    monkeypatch.setattr(library_view, "audit_papers", fail_audit)

    assert library_view.get_main_paper_pdf(cfg, "paper-pdf") == paper_dir / "Doe-2026-PDF.pdf"


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


def test_main_library_detail_returns_fallback_for_malformed_metadata_row(tmp_path: Path) -> None:
    from scholaraio.services.library_view import get_main_paper_detail

    papers_root = tmp_path / "data" / "libraries" / "papers"
    bad_dir = papers_root / "Bad-2026-Broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{not json", encoding="utf-8")
    (bad_dir / "Bad-2026-Broken.pdf").write_bytes(b"%PDF-bad")
    cfg = _build_config({}, tmp_path)

    detail = get_main_paper_detail(cfg, "Bad-2026-Broken")

    assert detail["paper_id"] == "Bad-2026-Broken"
    assert detail["title"] == "Bad-2026-Broken"
    assert detail["has_pdf"] is True
    assert detail["issue_counts"]["error"] == 1
    assert detail["issues"][0]["rule"] == "invalid_json"


def test_main_library_view_reports_unreadable_metadata_separately(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view

    papers_root = tmp_path / "data" / "libraries" / "papers"
    paper_dir = _write_main_paper(
        papers_root,
        "Locked-2026-Metadata",
        paper_id="locked-paper",
        title="Locked metadata",
    )
    cfg = _build_config({}, tmp_path)
    original_read_meta = library_view.read_meta

    def read_meta_with_lock(current_dir: Path) -> dict:
        if current_dir == paper_dir:
            raise PermissionError("metadata is locked")
        return original_read_meta(current_dir)

    monkeypatch.setattr(library_view, "read_meta", read_meta_with_lock)

    view = library_view.build_main_library_view(cfg)

    row = view["papers"][0]
    assert row["paper_id"] == "Locked-2026-Metadata"
    assert row["issues"][0]["rule"] == "metadata_unreadable"
    assert "Failed to read metadata" in row["issues"][0]["message"]


def test_main_library_detail_returns_fallback_for_unreadable_metadata_row(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view

    papers_root = tmp_path / "data" / "libraries" / "papers"
    paper_dir = _write_main_paper(
        papers_root,
        "Locked-2026-Detail",
        paper_id="locked-detail",
        title="Locked detail",
        write_pdf=True,
    )
    cfg = _build_config({}, tmp_path)
    original_read_meta = library_view.read_meta

    def read_meta_with_lock(current_dir: Path) -> dict:
        if current_dir == paper_dir:
            raise PermissionError("metadata is locked")
        return original_read_meta(current_dir)

    monkeypatch.setattr(library_view, "read_meta", read_meta_with_lock)

    detail = library_view.get_main_paper_detail(cfg, paper_dir.name)

    assert detail["paper_id"] == paper_dir.name
    assert detail["title"] == paper_dir.name
    assert detail["has_pdf"] is True
    assert detail["issues"][0]["rule"] == "metadata_unreadable"


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


def test_proceedings_view_isolates_malformed_child_metadata(tmp_path: Path) -> None:
    from scholaraio.services.library_view import build_proceedings_library_view

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    _write_proceedings_child(proceedings_root)
    bad_dir = proceedings_root / "Proc-2026-Test" / "papers" / "Broken-2026-Child"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{not json", encoding="utf-8")
    (bad_dir / "Broken-2026-Child.pdf").write_bytes(b"%PDF-bad")
    cfg = _build_config({}, tmp_path)

    view = build_proceedings_library_view(cfg)

    rows = {row["paper_id"]: row for row in view["papers"]}
    assert set(rows) == {"proc-paper-1", "Broken-2026-Child"}
    assert rows["proc-paper-1"]["title"] == "Wave proceedings paper"
    assert rows["Broken-2026-Child"]["title"] == "Broken-2026-Child"
    assert rows["Broken-2026-Child"]["proceeding_title"] == "Proceedings of Tests"
    assert rows["Broken-2026-Child"]["has_pdf"] is True
    assert rows["Broken-2026-Child"]["issue_counts"]["error"] == 1
    assert rows["Broken-2026-Child"]["issues"][0]["rule"] == "invalid_json"
    assert view["issue_totals"]["error"] == 1


def test_proceedings_view_isolates_unreadable_volume_metadata(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    _write_proceedings_child(proceedings_root)
    proceeding_meta_path = proceedings_root / "Proc-2026-Test" / "meta.json"
    cfg = _build_config({}, tmp_path)
    original_read_json = library_view.read_json

    def read_json_with_lock(path: Path) -> dict:
        if path == proceeding_meta_path:
            raise PermissionError("metadata is locked")
        return original_read_json(path)

    monkeypatch.setattr(library_view, "read_json", read_json_with_lock)

    view = library_view.build_proceedings_library_view(cfg)

    row = view["papers"][0]
    assert row["paper_id"] == "proc-paper-1"
    assert row["issues"][0]["rule"] == "metadata_unreadable"
    assert row["issues"][0]["paper_id"] == "Proc-2026-Test"


def test_proceedings_view_isolates_unreadable_child_metadata(tmp_path: Path, monkeypatch) -> None:
    from scholaraio.services import library_view

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    _write_proceedings_child(proceedings_root)
    child_meta_path = proceedings_root / "Proc-2026-Test" / "papers" / "Wave-2026-Test" / "meta.json"
    cfg = _build_config({}, tmp_path)
    original_read_json = library_view.read_json

    def read_json_with_lock(path: Path) -> dict:
        if path == child_meta_path:
            raise PermissionError("metadata is locked")
        return original_read_json(path)

    monkeypatch.setattr(library_view, "read_json", read_json_with_lock)

    view = library_view.build_proceedings_library_view(cfg)

    row = view["papers"][0]
    assert row["paper_id"] == "Wave-2026-Test"
    assert row["title"] == "Wave-2026-Test"
    assert row["issues"][0]["rule"] == "metadata_unreadable"
    assert view["issue_totals"]["error"] == 1


def test_proceedings_view_stringifies_non_string_authors(tmp_path: Path) -> None:
    from scholaraio.services.library_view import build_proceedings_library_view

    proceedings_root = tmp_path / "data" / "libraries" / "proceedings"
    proceeding_dir = proceedings_root / "Proc-2026-Test"
    child_dir = proceeding_dir / "papers" / "Mixed-2026-Authors"
    child_dir.mkdir(parents=True)
    (proceeding_dir / "meta.json").write_text(
        json.dumps({"id": "proc-1", "title": "Proceedings of Tests", "year": 2026}),
        encoding="utf-8",
    )
    (child_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": "mixed-authors",
                "title": "Mixed author metadata",
                "authors": ["Pat Chen", 42, None],
                "year": 2026,
            }
        ),
        encoding="utf-8",
    )
    (child_dir / "paper.md").write_text("# Mixed author metadata\n", encoding="utf-8")
    cfg = _build_config({}, tmp_path)

    view = build_proceedings_library_view(cfg)

    assert view["total"] == 1
    assert view["papers"][0]["authors_text"] == "Pat Chen, 42"


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
