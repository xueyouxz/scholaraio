"""Regression tests for ingest pipeline edge cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from scholaraio.core.config import _build_config
from scholaraio.services.ingest.pipeline import (
    InboxCtx,
    StepResult,
    _collect_existing_ids,
    import_external,
    run_pipeline,
    step_dedup,
    step_extract,
    step_extract_doc,
    step_office_convert,
    step_translate,
)
from scholaraio.services.ingest_metadata._api import query_semantic_scholar
from scholaraio.services.ingest_metadata._models import PaperMetadata
from scholaraio.services.translate import SKIP_ALL_CHUNKS_FAILED, TranslateResult


class _DummyResponse:
    status_code = 404
    headers: dict[str, str]

    def __init__(self) -> None:
        self.headers = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


def test_query_semantic_scholar_encodes_old_style_arxiv_id(monkeypatch):
    seen: dict[str, str] = {}

    def fake_get(url: str, timeout: int):
        seen["url"] = url
        return _DummyResponse()

    monkeypatch.setattr("scholaraio.services.ingest_metadata._api.SESSION.get", fake_get)

    query_semantic_scholar(arxiv_id="hep-th/9901001")

    assert seen["url"] == (
        "https://api.semanticscholar.org/graph/v1/paper/"
        "arXiv%3Ahep-th%2F9901001?fields="
        "title,abstract,citationCount,year,externalIds,authors,venue,"
        "publicationTypes,references.externalIds"
    )


def test_query_semantic_scholar_encodes_doi_path_segment(monkeypatch):
    seen: dict[str, str] = {}

    def fake_get(url: str, timeout: int):
        seen["url"] = url
        return _DummyResponse()

    monkeypatch.setattr("scholaraio.services.ingest_metadata._api.SESSION.get", fake_get)

    query_semantic_scholar(doi="10.1017/S0022112094000431")

    assert seen["url"] == (
        "https://api.semanticscholar.org/graph/v1/paper/"
        "DOI%3A10.1017%2FS0022112094000431?fields="
        "title,abstract,citationCount,year,externalIds,authors,venue,"
        "publicationTypes,references.externalIds"
    )


def test_collect_existing_ids_includes_arxiv_ids(tmp_path: Path):
    papers_dir = tmp_path / "papers"
    paper_dir = papers_dir / "Imamura-1999-String-Junctions"
    paper_dir.mkdir(parents=True)
    (paper_dir / "meta.json").write_text(
        json.dumps(
            {
                "title": "String Junctions and Their Duals in Heterotic String Theory",
                "doi": "",
                "ids": {"arxiv": "hep-th/9901001v3"},
            }
        ),
        encoding="utf-8",
    )

    dois, pub_nums, arxiv_ids = _collect_existing_ids(papers_dir)

    assert dois == {}
    assert pub_nums == {}
    assert arxiv_ids["hep-th/9901001"] == paper_dir / "meta.json"


def test_parse_detect_json_handles_fences_embedded_objects_and_missing_json():
    from scholaraio.services.ingest.detection import parse_detect_json

    assert parse_detect_json('```json\n{"is_thesis": true, "reason": "ok"}\n```') == {
        "is_thesis": True,
        "reason": "ok",
    }
    assert parse_detect_json('prefix {"is_book": false, "reason": "no"} suffix') == {
        "is_book": False,
        "reason": "no",
    }
    assert parse_detect_json("no structured response") == {}


def test_document_detection_fast_paths_do_not_need_llm(tmp_path: Path):
    from scholaraio.services.ingest.detection import detect_book, detect_patent, detect_thesis

    md_path = tmp_path / "paper.md"
    md_path.write_text("# Placeholder", encoding="utf-8")
    cfg = SimpleNamespace(resolved_api_key=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be used")))

    patent_ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=cfg,
        opts={},
        md_path=md_path,
        meta=PaperMetadata(title="Patent", publication_number="US20260104498A1"),
    )
    thesis_ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=cfg,
        opts={},
        md_path=md_path,
        meta=PaperMetadata(title="A doctoral dissertation on testing"),
    )
    book_ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=cfg,
        opts={},
        md_path=md_path,
        meta=PaperMetadata(title="Testing Handbook", paper_type="book"),
    )

    assert detect_patent(patent_ctx) is True
    assert detect_thesis(thesis_ctx) is True
    assert detect_book(book_ctx) is True


def test_step_dedup_rejects_duplicate_arxiv_only_preprint(tmp_path: Path, monkeypatch):
    existing_json = tmp_path / "papers" / "Imamura-1999-String-Junctions" / "meta.json"
    existing_json.parent.mkdir(parents=True)
    existing_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("scholaraio.services.ingest_metadata.enrich_metadata", lambda meta: meta)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_patent", lambda ctx: False)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_thesis", lambda ctx: False)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_book", lambda ctx: False)

    moved: dict[str, object] = {}

    def fake_move_to_pending(ctx, *, issue="no_doi", message="", extra=None):
        moved["issue"] = issue
        moved["extra"] = extra or {}

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._move_to_pending", fake_move_to_pending)

    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path / "inbox",
        papers_dir=tmp_path / "papers",
        existing_dois={},
        existing_pub_nums={},
        cfg=SimpleNamespace(_root=tmp_path),
        opts={"no_api": False, "dry_run": False},
        pending_dir=tmp_path / "pending",
        md_path=None,
        meta=PaperMetadata(
            title="String Junctions and Their Duals in Heterotic String Theory",
            arxiv_id="hep-th/9901001v1",
        ),
    )
    ctx.existing_arxiv_ids = {"hep-th/9901001": existing_json}

    result = step_dedup(ctx)

    assert result == StepResult.FAIL
    assert ctx.status == "duplicate"
    assert moved["issue"] == "duplicate"
    assert moved["extra"] == {
        "duplicate_of": "Imamura-1999-String-Junctions",
        "arxiv_id": "hep-th/9901001",
    }


def test_step_dedup_rejects_duplicate_when_existing_preprint_has_only_arxiv_id_but_new_record_gets_doi(
    tmp_path: Path, monkeypatch
):
    existing_json = tmp_path / "papers" / "Imamura-1999-String-Junctions" / "meta.json"
    existing_json.parent.mkdir(parents=True)
    existing_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_patent", lambda ctx: False)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_thesis", lambda ctx: False)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._detect_book", lambda ctx: False)

    def fake_enrich(meta):
        meta.doi = "10.1000/test-preprint"
        return meta

    monkeypatch.setattr("scholaraio.services.ingest_metadata.enrich_metadata", fake_enrich)

    moved: dict[str, object] = {}

    def fake_move_to_pending(ctx, *, issue="no_doi", message="", extra=None):
        moved["issue"] = issue
        moved["extra"] = extra or {}

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._move_to_pending", fake_move_to_pending)

    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path / "inbox",
        papers_dir=tmp_path / "papers",
        existing_dois={},
        existing_pub_nums={},
        cfg=SimpleNamespace(_root=tmp_path),
        opts={"no_api": False, "dry_run": False},
        pending_dir=tmp_path / "pending",
        md_path=None,
        meta=PaperMetadata(
            title="String Junctions and Their Duals in Heterotic String Theory",
            arxiv_id="hep-th/9901001v2",
        ),
    )
    ctx.existing_arxiv_ids = {"hep-th/9901001": existing_json}

    result = step_dedup(ctx)

    assert result == StepResult.FAIL
    assert ctx.status == "duplicate"
    assert moved["issue"] == "duplicate"
    assert moved["extra"] == {
        "duplicate_of": "Imamura-1999-String-Junctions",
        "arxiv_id": "hep-th/9901001",
    }


def test_step_office_convert_reports_scholaraio_office_extra(tmp_path: Path, monkeypatch):
    office_path = tmp_path / "report.docx"
    office_path.write_text("dummy", encoding="utf-8")

    errors: list[str] = []
    monkeypatch.setattr(
        "scholaraio.services.ingest.pipeline._log.error", lambda msg, *args: errors.append(msg % args if args else msg)
    )

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "markitdown":
            raise ModuleNotFoundError("No module named 'markitdown'", name="markitdown")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=SimpleNamespace(_root=tmp_path),
        opts={"office_path": office_path, "dry_run": False},
    )

    result = step_office_convert(ctx)

    assert result == StepResult.FAIL
    assert ctx.status == "failed"
    assert any("pip install scholaraio[office]" in msg for msg in errors)


def test_step_extract_labels_arxiv_id_as_generic_id(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "paper.md"
    md_path.write_text("# test", encoding="utf-8")

    messages: list[str] = []

    class DummyExtractor:
        def extract(self, _path: Path) -> PaperMetadata:
            return PaperMetadata(
                title="String Junctions and Their Duals in Heterotic String Theory",
                first_author_lastname="Imamura",
                year=1999,
                arxiv_id="hep-th/9901001",
            )

    monkeypatch.setattr("scholaraio.services.ingest.pipeline.ui", lambda msg="": messages.append(msg))
    monkeypatch.setattr("scholaraio.services.ingest_metadata.extractor.get_extractor", lambda cfg: DummyExtractor())

    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=SimpleNamespace(_root=tmp_path),
        opts={"dry_run": False},
        md_path=md_path,
    )

    result = step_extract(ctx)

    assert result == StepResult.OK
    assert any("ID: arXiv:hep-th/9901001" in msg for msg in messages)
    assert all("DOI: arXiv:" not in msg for msg in messages)


def test_step_extract_doc_reads_same_stem_json_sidecar(tmp_path: Path):
    md_path = tmp_path / "page.md"
    md_path.write_text("# Example Page\n\nBody text", encoding="utf-8")
    md_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "title": "Example Page",
                "source_url": "https://example.com/article",
                "source_type": "web",
                "extracted_at": "2026-04-14T00:00:00+00:00",
                "extraction_method": "qt-web-extractor",
            }
        ),
        encoding="utf-8",
    )

    cfg = SimpleNamespace(resolved_api_key=lambda: None)
    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=cfg,
        opts={"dry_run": False},
        md_path=md_path,
    )

    result = step_extract_doc(ctx)

    assert result == StepResult.OK
    assert ctx.meta.source_url == "https://example.com/article"
    assert ctx.meta.source_type == "web"
    assert ctx.meta.extraction_method == "qt-web-extractor"


def test_update_registry_migrates_publication_number_and_upserts_record(tmp_path: Path):
    import sqlite3

    from scholaraio.services.ingest.registry import update_registry

    db_path = tmp_path / "index.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE papers_registry (
                id TEXT PRIMARY KEY,
                dir_name TEXT,
                title TEXT,
                doi TEXT,
                year INTEGER,
                first_author TEXT
            )"""
        )

    meta = PaperMetadata(
        id="paper-1",
        title="Example Patent",
        doi="10.1000/Patent",
        publication_number="us20260104498a1",
        year=2026,
        first_author_lastname="Doe",
    )
    cfg = SimpleNamespace(index_db=db_path)

    update_registry(cfg, meta, "Doe-2026-Example-Patent")

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(papers_registry)")}
        row = conn.execute(
            "SELECT id, dir_name, title, doi, publication_number, year, first_author FROM papers_registry"
        ).fetchone()

    assert "publication_number" in columns
    assert row == (
        "paper-1",
        "Doe-2026-Example-Patent",
        "Example Patent",
        "10.1000/patent",
        "US20260104498A1",
        2026,
        "Doe",
    )


def test_cleanup_inbox_honors_dry_run_before_deleting_files(tmp_path: Path):
    from scholaraio.services.ingest.cleanup import cleanup_inbox

    pdf_path = tmp_path / "paper.pdf"
    md_path = tmp_path / "paper.md"
    pdf_path.write_text("pdf", encoding="utf-8")
    md_path.write_text("md", encoding="utf-8")

    cleanup_inbox(pdf_path, md_path, dry_run=True)

    assert pdf_path.exists()
    assert md_path.exists()

    cleanup_inbox(pdf_path, md_path, dry_run=False)

    assert not pdf_path.exists()
    assert not md_path.exists()


def test_repair_abstract_backfills_missing_abstract(tmp_path: Path, monkeypatch):
    from scholaraio.services.ingest.documents import repair_abstract

    paper_dir = tmp_path / "papers" / "Doe-2026-Test"
    paper_dir.mkdir(parents=True)
    json_path = paper_dir / "meta.json"
    md_path = paper_dir / "paper.md"
    json_path.write_text(json.dumps({"title": "Test Paper"}), encoding="utf-8")
    md_path.write_text("# Test Paper\n\nAbstract text", encoding="utf-8")

    monkeypatch.setattr(
        "scholaraio.services.ingest_metadata.extract_abstract_from_md", lambda _md_path, _cfg: "Abstract text"
    )

    repair_abstract(json_path, md_path, SimpleNamespace())

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["abstract"] == "Abstract text"


def test_move_to_pending_moves_files_and_writes_marker(tmp_path: Path):
    from scholaraio.services.ingest.pending import move_to_pending

    inbox_dir = tmp_path / "inbox"
    pending_dir = tmp_path / "pending"
    inbox_dir.mkdir()
    pdf_path = inbox_dir / "paper.pdf"
    md_path = inbox_dir / "paper.md"
    pdf_path.write_text("pdf", encoding="utf-8")
    md_path.write_text("# Paper", encoding="utf-8")

    ctx = InboxCtx(
        pdf_path=pdf_path,
        inbox_dir=inbox_dir,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=SimpleNamespace(_root=tmp_path),
        opts={},
        pending_dir=pending_dir,
        md_path=md_path,
        meta=PaperMetadata(title="Pending Paper", doi="10.1000/pending"),
    )

    move_to_pending(
        ctx,
        issue="duplicate",
        message="Duplicate paper",
        extra={"duplicate_of": "Existing-2026-Paper"},
    )

    paper_dir = pending_dir / "paper"
    marker = json.loads((paper_dir / "pending.json").read_text(encoding="utf-8"))

    assert (paper_dir / "paper.md").read_text(encoding="utf-8") == "# Paper"
    assert (paper_dir / "paper.pdf").read_text(encoding="utf-8") == "pdf"
    assert not pdf_path.exists()
    assert not md_path.exists()
    assert marker["issue"] == "duplicate"
    assert marker["message"] == "Duplicate paper"
    assert marker["duplicate_of"] == "Existing-2026-Paper"
    assert marker["extracted_metadata"]["title"] == "Pending Paper"
    assert marker["extracted_metadata"]["doi"] == "10.1000/pending"


def test_step_ingest_preserves_pdf_with_paper_directory_name(tmp_path: Path, monkeypatch):
    from scholaraio.services.ingest.inbox_steps import step_ingest

    inbox_dir = tmp_path / "inbox"
    papers_dir = tmp_path / "papers"
    inbox_dir.mkdir()
    pdf_path = inbox_dir / "source-download.pdf"
    md_path = inbox_dir / "source-download.md"
    pdf_path.write_bytes(b"%PDF-source")
    md_path.write_text("# Preserved PDF\n\nBody", encoding="utf-8")
    cfg = SimpleNamespace(_root=tmp_path)

    monkeypatch.setattr(
        "scholaraio.services.ingest_metadata.generate_new_stem",
        lambda _meta: "Doe-2026-Preserved-PDF",
    )
    monkeypatch.setattr("scholaraio.stores.papers.generate_uuid", lambda: "uuid-1")
    monkeypatch.setattr("scholaraio.services.ingest.registry.update_registry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._update_registry", lambda *_args, **_kwargs: None)

    ctx = InboxCtx(
        pdf_path=pdf_path,
        inbox_dir=inbox_dir,
        papers_dir=papers_dir,
        existing_dois={},
        cfg=cfg,
        opts={},
        md_path=md_path,
        meta=PaperMetadata(title="Preserved PDF", doi="10.1000/preserved", year=2026, abstract="Already extracted."),
    )

    assert step_ingest(ctx) == StepResult.OK

    paper_dir = papers_dir / "Doe-2026-Preserved-PDF"
    assert (paper_dir / "paper.md").exists()
    assert (paper_dir / "Doe-2026-Preserved-PDF.pdf").read_bytes() == b"%PDF-source"
    assert not pdf_path.exists()


def test_ingest_proceedings_ctx_dry_run_marks_skipped_without_deleting_md(tmp_path: Path):
    from scholaraio.services.ingest.proceedings import ingest_proceedings_ctx

    md_path = tmp_path / "volume.md"
    md_path.write_text("# Proceedings", encoding="utf-8")
    cfg = SimpleNamespace(proceedings_dir=tmp_path / "proceedings")
    ctx = InboxCtx(
        pdf_path=None,
        inbox_dir=tmp_path,
        papers_dir=tmp_path / "papers",
        existing_dois={},
        cfg=cfg,
        opts={"dry_run": True},
        md_path=md_path,
    )

    assert ingest_proceedings_ctx(ctx, force=True) is True
    assert ctx.status == "skipped"
    assert md_path.exists()
    assert not cfg.proceedings_dir.exists()


def test_step_translate_treats_all_chunks_failed_as_failure(tmp_path: Path, monkeypatch):
    paper_dir = tmp_path / "papers" / "Smith-2023-Test"
    paper_dir.mkdir(parents=True)
    json_path = paper_dir / "meta.json"
    json_path.write_text("{}", encoding="utf-8")
    (paper_dir / "paper.md").write_text("Original text", encoding="utf-8")

    messages: list[str] = []
    monkeypatch.setattr("scholaraio.services.ingest.pipeline.ui", lambda msg="": messages.append(msg))
    monkeypatch.setattr(
        "scholaraio.services.translate.translate_paper",
        lambda *args, **kwargs: TranslateResult(skip_reason=SKIP_ALL_CHUNKS_FAILED, total_chunks=3),
    )

    cfg = SimpleNamespace(
        translate=SimpleNamespace(target_lang="zh", chunk_size=1000, concurrency=1),
        llm=SimpleNamespace(model="test-model"),
    )

    result = step_translate(json_path, cfg, {"force": False})

    assert result == StepResult.FAIL
    assert any("Translation failed: all chunks failed" in msg for msg in messages)


def test_run_pipeline_auto_injects_translate_for_new_ingest(tmp_path: Path, monkeypatch):
    cfg = SimpleNamespace(
        translate=SimpleNamespace(auto_translate=True, target_lang="zh", concurrency=2),
        llm=SimpleNamespace(concurrency=3),
        _root=tmp_path,
        papers_dir=tmp_path / "data" / "papers",
    )

    seen_steps: list[str] = []

    def fake_process_inbox(
        inbox_dir,
        papers_dir,
        pending_dir,
        existing_dois,
        inbox_steps,
        cfg,
        opts,
        dry_run,
        ingested_jsons,
        **kwargs,
    ):
        seen_steps.extend(inbox_steps)
        paper_dir = papers_dir / "Smith-2024-Test"
        paper_dir.mkdir(parents=True, exist_ok=True)
        meta_json = paper_dir / "meta.json"
        meta_json.write_text("{}", encoding="utf-8")
        (paper_dir / "paper.md").write_text("content", encoding="utf-8")
        ingested_jsons.append(meta_json)

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._collect_existing_ids", lambda *_: ({}, {}, {}))
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._process_inbox", fake_process_inbox)

    paper_calls: list[str] = []

    def fake_toc(json_path, cfg, opts):
        paper_calls.append("toc")
        return StepResult.OK

    def fake_translate(json_path, cfg, opts):
        paper_calls.append("translate")
        return StepResult.OK

    def fake_embed(papers_dir, cfg, opts):
        paper_calls.append("embed")
        return StepResult.OK

    def fake_index(papers_dir, cfg, opts):
        paper_calls.append("index")
        return StepResult.OK

    monkeypatch.setattr(
        "scholaraio.services.ingest.pipeline.STEPS",
        {
            "mineru": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "extract": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "dedup": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "ingest": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "toc": SimpleNamespace(scope="papers", fn=fake_toc, desc=""),
            "translate": SimpleNamespace(scope="papers", fn=fake_translate, desc=""),
            "embed": SimpleNamespace(scope="global", fn=fake_embed, desc=""),
            "index": SimpleNamespace(scope="global", fn=fake_index, desc=""),
        },
    )

    run_pipeline(["mineru", "extract", "dedup", "ingest", "toc", "embed", "index"], cfg, {})

    assert seen_steps == ["mineru", "extract", "dedup", "ingest"]
    assert paper_calls == ["toc", "translate", "embed", "index"]


def test_run_pipeline_uses_custom_doc_inbox(tmp_path: Path, monkeypatch):
    cfg = SimpleNamespace(
        translate=SimpleNamespace(auto_translate=False),
        llm=SimpleNamespace(concurrency=1),
        _root=tmp_path,
        papers_dir=tmp_path / "data" / "papers",
    )

    seen_dirs: list[Path] = []

    def fake_process_inbox(
        inbox_dir,
        papers_dir,
        pending_dir,
        existing_dois,
        inbox_steps,
        cfg,
        opts,
        dry_run,
        ingested_jsons,
        **kwargs,
    ):
        seen_dirs.append(inbox_dir)

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._collect_existing_ids", lambda *_: ({}, {}, {}))
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._process_inbox", fake_process_inbox)

    custom_doc_inbox = tmp_path / "tmp-docs"
    custom_doc_inbox.mkdir()
    run_pipeline(["extract_doc", "ingest"], cfg, {"doc_inbox_dir": custom_doc_inbox})

    assert custom_doc_inbox in seen_dirs


def test_run_pipeline_processes_custom_doc_inbox_even_when_aux_inboxes_disabled(tmp_path: Path, monkeypatch):
    cfg = SimpleNamespace(
        translate=SimpleNamespace(auto_translate=False),
        llm=SimpleNamespace(concurrency=1),
        _root=tmp_path,
        papers_dir=tmp_path / "data" / "papers",
    )

    seen_dirs: list[Path] = []

    def fake_process_inbox(
        inbox_dir,
        papers_dir,
        pending_dir,
        existing_dois,
        inbox_steps,
        cfg,
        opts,
        dry_run,
        ingested_jsons,
        **kwargs,
    ):
        seen_dirs.append(inbox_dir)

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._collect_existing_ids", lambda *_: ({}, {}, {}))
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._process_inbox", fake_process_inbox)

    custom_doc_inbox = tmp_path / "tmp-docs"
    custom_doc_inbox.mkdir()
    run_pipeline(
        ["extract_doc", "ingest"],
        cfg,
        {"doc_inbox_dir": custom_doc_inbox, "include_aux_inboxes": False},
    )

    assert custom_doc_inbox in seen_dirs


def test_run_pipeline_uses_configured_aux_inboxes_and_pending_dir(tmp_path: Path, monkeypatch):
    cfg = _build_config(
        {
            "paths": {
                "inbox_dir": "queues/inbox-main",
                "doc_inbox_dir": "queues/inbox-docs",
                "thesis_inbox_dir": "queues/inbox-thesis",
                "patent_inbox_dir": "queues/inbox-patent",
                "proceedings_inbox_dir": "queues/inbox-proceedings",
                "pending_dir": "queues/pending-review",
            }
        },
        tmp_path,
    )
    cfg.ensure_dirs()

    seen: list[tuple[Path, Path]] = []

    def fake_process_inbox(
        inbox_dir,
        papers_dir,
        pending_dir,
        existing_dois,
        inbox_steps,
        cfg,
        opts,
        dry_run,
        ingested_jsons,
        **kwargs,
    ):
        seen.append((inbox_dir, pending_dir))

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._collect_existing_ids", lambda *_: ({}, {}, {}))
    monkeypatch.setattr("scholaraio.services.ingest.pipeline._process_inbox", fake_process_inbox)

    run_pipeline(["extract", "dedup", "ingest"], cfg, {})

    expected_inboxes = {
        cfg.inbox_dir,
        cfg.thesis_inbox_dir,
        cfg.patent_inbox_dir,
        cfg.doc_inbox_dir,
        cfg.proceedings_inbox_dir,
    }
    assert {inbox_dir for inbox_dir, _ in seen} == expected_inboxes
    assert {pending_dir for _, pending_dir in seen} == {cfg.pending_dir}


def test_run_pipeline_uses_canonical_pipeline_when_legacy_alias_missing(tmp_path: Path, monkeypatch):
    from scholaraio.services.ingest import pipeline as services_pipeline

    cfg = SimpleNamespace(
        translate=SimpleNamespace(auto_translate=False),
        llm=SimpleNamespace(concurrency=1),
        _root=tmp_path,
        papers_dir=tmp_path / "data" / "papers",
    )

    seen_steps: list[str] = []

    def fake_process_inbox(
        inbox_dir,
        papers_dir,
        pending_dir,
        existing_dois,
        inbox_steps,
        cfg,
        opts,
        dry_run,
        ingested_jsons,
        **kwargs,
    ):
        seen_steps.extend(inbox_steps)

    monkeypatch.delitem(sys.modules, "scholaraio.services.ingest.pipeline", raising=False)
    monkeypatch.setattr(services_pipeline, "_collect_existing_ids", lambda *_: ({}, {}, {}))
    monkeypatch.setattr(services_pipeline, "_process_inbox", fake_process_inbox)
    monkeypatch.setattr(
        services_pipeline,
        "STEPS",
        {
            "extract": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "dedup": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
            "ingest": SimpleNamespace(scope="inbox", fn=lambda ctx: StepResult.OK, desc=""),
        },
    )

    run_pipeline(["extract", "dedup", "ingest"], cfg, {})

    assert seen_steps == ["extract", "dedup", "ingest"]


def test_import_external_uses_configured_inbox_and_pending_dir(tmp_path: Path, monkeypatch):
    cfg = _build_config(
        {
            "paths": {
                "inbox_dir": "queues/inbox-main",
                "pending_dir": "queues/pending-review",
            }
        },
        tmp_path,
    )
    cfg.ensure_dirs()

    observed: dict[str, Path] = {}

    def fake_collect_existing_ids(_papers_dir):
        return ({}, {}, {})

    def fake_step_dedup(ctx):
        observed["inbox_dir"] = ctx.inbox_dir
        observed["pending_dir"] = ctx.pending_dir
        return StepResult.OK

    def fake_step_ingest(ctx):
        ctx.status = "ingested"
        return StepResult.OK

    monkeypatch.setattr("scholaraio.services.ingest.pipeline._collect_existing_ids", fake_collect_existing_ids)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline.step_dedup", fake_step_dedup)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline.step_ingest", fake_step_ingest)
    monkeypatch.setattr("scholaraio.services.ingest.pipeline.STEPS", {})

    stats = import_external(
        [PaperMetadata(title="Test import", doi="10.1000/test")],
        cfg,
        no_api=True,
    )

    assert stats["ingested"] == 1
    assert observed["inbox_dir"] == cfg.inbox_dir
    assert observed["pending_dir"] == cfg.pending_dir
