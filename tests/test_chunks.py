from __future__ import annotations

import json
from pathlib import Path

from scholaraio.services.chunks import build_chunk_index, chunk_search, iter_paper_chunks
from scholaraio.services.index import build_index


def _write_paper(
    papers_dir: Path,
    dir_name: str,
    *,
    paper_id: str,
    title: str,
    md_text: str,
    toc: list[dict] | None = None,
    year: int = 2026,
    journal: str = "Journal of Evidence Retrieval",
    paper_type: str = "journal-article",
) -> Path:
    paper_dir = papers_dir / dir_name
    paper_dir.mkdir(parents=True)
    meta = {
        "id": paper_id,
        "title": title,
        "authors": ["Ada Lovelace"],
        "first_author_lastname": "Lovelace",
        "year": year,
        "journal": journal,
        "doi": f"10.5555/{paper_id}",
        "abstract": f"Abstract for {title}.",
        "paper_type": paper_type,
    }
    if toc is not None:
        meta["toc"] = toc
    (paper_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (paper_dir / "paper.md").write_text(md_text, encoding="utf-8")
    return paper_dir


def test_iter_paper_chunks_prefers_toc_and_preserves_line_addresses(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Chunked-LES",
        paper_id="paper-les",
        title="Chunked LES evidence retrieval",
        toc=[
            {"line": 5, "level": 1, "title": "1. Methods"},
            {"line": 10, "level": 1, "title": "2. Results"},
        ],
        md_text="\n".join(
            [
                "# Chunked LES evidence retrieval",
                "",
                "Front matter that should not become the Methods section.",
                "",
                "# 1. Methods",
                "We evaluate self-conditioned LES for particle-laden turbulence.",
                "",
                "The subgrid coupling term is retained as local evidence.",
                "",
                "# 2. Results",
                "The retrieved evidence names the exact section.",
            ]
        ),
    )

    chunks = list(iter_paper_chunks(paper_dir, target_chars=5000))

    assert [chunk.section_title for chunk in chunks] == ["1. Methods", "2. Results"]
    assert chunks[0].paper_id == "paper-les"
    assert chunks[0].start_line == 5
    assert chunks[0].end_line == 8
    assert "subgrid coupling" in chunks[0].text
    assert "Paper: Chunked LES evidence retrieval" in chunks[0].context_text
    assert "Section: 1. Methods" in chunks[0].context_text


def test_iter_paper_chunks_reports_last_non_empty_end_line(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    long_sentence = (
        "The evidence sentence is here with enough detail to force its own chunk when "
        "the target size is clamped to the minimum."
    )
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Line-Precision",
        paper_id="paper-lines",
        title="Line precision evidence retrieval",
        toc=[{"line": 3, "level": 1, "title": "1. Evidence"}],
        md_text="\n".join(
            [
                "# Line precision evidence retrieval",
                "",
                "# 1. Evidence",
                long_sentence,
                "",
                "A later paragraph starts after the blank separator.",
            ]
        ),
    )

    chunks = list(iter_paper_chunks(paper_dir, target_chars=80))

    assert chunks[0].start_line == 3
    assert chunks[0].end_line == 4


def test_iter_paper_chunks_falls_back_to_markdown_headings_and_splits_long_sections(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    long_paragraphs = "\n\n".join(
        [
            "Sentence one describes coherent structures in wall turbulence.",
            "Sentence two describes local particle acceleration evidence.",
            "Sentence three describes model-form uncertainty and validation.",
        ]
    )
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Heading-Fallback",
        paper_id="paper-heading",
        title="Heading fallback evidence retrieval",
        toc=None,
        md_text=f"# Heading fallback evidence retrieval\n\n# Background\n\n{long_paragraphs}",
    )

    chunks = list(iter_paper_chunks(paper_dir, target_chars=80))

    assert len(chunks) >= 2
    assert {chunk.section_title for chunk in chunks} == {"Background"}
    assert all(chunk.start_line <= chunk.end_line for chunk in chunks)


def test_build_chunk_index_then_chunk_search_returns_line_addressable_snippets(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    db_path = tmp_path / "index.db"
    _write_paper(
        papers_dir,
        "Lovelace-2026-Chunked-LES",
        paper_id="paper-les",
        title="Chunked LES evidence retrieval",
        toc=[
            {"line": 3, "level": 1, "title": "1. Methods"},
            {"line": 8, "level": 1, "title": "2. Results"},
        ],
        md_text="\n".join(
            [
                "# Chunked LES evidence retrieval",
                "",
                "# 1. Methods",
                "We retain the subgrid coupling term in the mesoscopic LES model.",
                "",
                "This paragraph is the best local evidence for particle-laden turbulence.",
                "",
                "# 2. Results",
                "Results discuss unrelated reporting details.",
            ]
        ),
    )
    _write_paper(
        papers_dir,
        "Lovelace-2026-Other",
        paper_id="paper-other",
        title="Other evidence retrieval",
        toc=None,
        md_text="# Other evidence retrieval\n\n# Introduction\n\nThis paper discusses acoustic waves.",
    )
    build_index(papers_dir, db_path)

    indexed = build_chunk_index(papers_dir, db_path)
    results = chunk_search("subgrid coupling mesoscopic LES", db_path, top_k=3)

    assert indexed >= 2
    assert results
    assert results[0]["paper_id"] == "paper-les"
    assert results[0]["dir_name"] == "Lovelace-2026-Chunked-LES"
    assert results[0]["section_title"] == "1. Methods"
    assert results[0]["start_line"] == 3
    assert results[0]["end_line"] == 6
    assert "subgrid coupling" in results[0]["snippet"]


def test_build_chunk_index_reuses_preloaded_metadata(tmp_path: Path, monkeypatch) -> None:
    papers_dir = tmp_path / "papers"
    db_path = tmp_path / "index.db"
    _write_paper(
        papers_dir,
        "Lovelace-2026-Meta-Once",
        paper_id="paper-meta-once",
        title="Metadata reuse evidence retrieval",
        toc=None,
        md_text="# Metadata reuse evidence retrieval\n\n# Evidence\nsubgrid marker local evidence",
    )

    from scholaraio.services import chunks as chunks_module

    original_read_meta = chunks_module.read_meta
    calls = 0

    def counting_read_meta(paper_dir: Path) -> dict:
        nonlocal calls
        calls += 1
        return original_read_meta(paper_dir)

    monkeypatch.setattr(chunks_module, "read_meta", counting_read_meta)

    build_chunk_index(papers_dir, db_path)

    assert calls == 1


def test_build_chunk_index_refreshes_metadata_only_changes(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    db_path = tmp_path / "index.db"
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Retitled",
        paper_id="paper-retitled",
        title="Original evidence title",
        toc=[{"line": 3, "level": 1, "title": "Evidence"}],
        md_text="# Original evidence title\n\n# Evidence\nsubgrid marker local evidence",
    )

    build_chunk_index(papers_dir, db_path)
    assert chunk_search("subgrid marker", db_path, top_k=1)[0]["title"] == "Original evidence title"

    meta_path = paper_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["title"] = "Updated evidence title"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    build_chunk_index(papers_dir, db_path)

    result = chunk_search("subgrid marker", db_path, top_k=1)[0]
    assert result["title"] == "Updated evidence title"
    assert "Updated evidence title" in result["context_text"]


def test_chunk_search_honors_year_journal_and_type_filters(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    db_path = tmp_path / "index.db"
    _write_paper(
        papers_dir,
        "Lovelace-2024-Review",
        paper_id="paper-review",
        title="Review evidence retrieval",
        journal="Physics of Fluids",
        paper_type="review",
        year=2024,
        md_text="# Review evidence retrieval\n\n# Evidence\nsubgrid marker filtered evidence",
    )
    _write_paper(
        papers_dir,
        "Lovelace-2026-Article",
        paper_id="paper-article",
        title="Article evidence retrieval",
        journal="Journal of Fluid Mechanics",
        paper_type="journal-article",
        year=2026,
        md_text="# Article evidence retrieval\n\n# Evidence\nsubgrid marker filtered evidence",
    )

    build_chunk_index(papers_dir, db_path)

    assert chunk_search("subgrid marker", db_path, top_k=10, year="1900") == []

    filtered = chunk_search(
        "subgrid marker",
        db_path,
        top_k=10,
        year="2024",
        journal="Physics",
        paper_type="review",
    )

    assert [result["paper_id"] for result in filtered] == ["paper-review"]
    assert filtered[0]["year"] == "2024"
    assert filtered[0]["journal"] == "Physics of Fluids"
    assert filtered[0]["paper_type"] == "review"


def test_chunk_content_hash_uses_full_sha256_digest(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Hash",
        paper_id="paper-hash",
        title="Hash evidence retrieval",
        toc=None,
        md_text="# Hash evidence retrieval\n\n# Evidence\nsubgrid marker local evidence",
    )

    chunks = list(iter_paper_chunks(paper_dir))

    assert len(chunks[0].content_hash) == 64


def test_build_chunk_index_incrementally_removes_stale_chunks(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    db_path = tmp_path / "index.db"
    paper_dir = _write_paper(
        papers_dir,
        "Lovelace-2026-Editable",
        paper_id="paper-editable",
        title="Editable evidence retrieval",
        toc=[{"line": 3, "level": 1, "title": "1. Evidence"}],
        md_text="# Editable evidence retrieval\n\n# 1. Evidence\noldmarker local evidence",
    )

    build_chunk_index(papers_dir, db_path)
    assert chunk_search("oldmarker", db_path, top_k=3)

    (paper_dir / "paper.md").write_text(
        "# Editable evidence retrieval\n\n# 1. Evidence\nnewmarker replacement evidence",
        encoding="utf-8",
    )

    build_chunk_index(papers_dir, db_path)

    assert chunk_search("oldmarker", db_path, top_k=3) == []
    assert chunk_search("newmarker", db_path, top_k=3)
