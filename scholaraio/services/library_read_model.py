"""Read model for library-facing HTTP queries.

The durable paper source remains ``meta.json`` under ``papers_dir``.  This
module presents a faster query interface backed by ``index.db`` when available
and falls back to disk scanning when the index is absent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from scholaraio.core.config import Config
from scholaraio.stores.papers import best_citation, iter_paper_dirs, parse_year_range, read_meta

PAPER_SORTS = frozenset({"year_desc", "year_asc", "citations_desc", "title_asc"})


def indexed_ids(db_path: Path) -> set[str]:
    """Return paper UUIDs currently visible through ``papers_registry``."""
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(db_path) as conn:
            if not _table_exists(conn, "papers_registry"):
                return set()
            rows = conn.execute("SELECT id FROM papers_registry").fetchall()
            return {str(r[0]) for r in rows}
    except sqlite3.Error:
        return set()


def paper_to_ref(pdir: Path, meta: dict, indexed: set[str]) -> dict[str, Any]:
    """Convert a paper directory and metadata into the HTTP paper shape."""
    paper_id = meta.get("id") or pdir.name
    has_md = (pdir / "paper.md").exists()
    has_pdf_file = (pdir / "paper.pdf").exists()
    is_indexed = paper_id in indexed

    authors_raw = meta.get("authors") or []
    authors_str = ", ".join(authors_raw) if isinstance(authors_raw, list) else str(authors_raw)

    return {
        "id": paper_id,
        "dirName": pdir.name,
        "title": meta.get("title") or pdir.name,
        "authors": authors_str,
        "source": meta.get("journal") or "",
        "year": _safe_int(meta.get("year")) or 0,
        "date": meta.get("date") or None,
        "citations": str(best_citation(meta)),
        "status": _paper_status(has_md=has_md, is_indexed=is_indexed),
        "hasPdf": has_md,
        "hasPdfFile": has_pdf_file,
        "isIndexed": is_indexed,
        "abstract": meta.get("abstract") or "",
        "doi": meta.get("doi") or "",
        "paperType": meta.get("paper_type") or "",
    }


def list_papers(
    cfg: Config,
    *,
    sort: str = "year_desc",
    year: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """List papers with pagination, using SQLite when the index is ready."""
    if sort not in PAPER_SORTS:
        sort = "year_desc"
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    if _papers_table_ready(cfg.index_db):
        try:
            return _list_papers_from_db(cfg, sort=sort, year=year, limit=limit, offset=offset)
        except sqlite3.Error:
            pass

    papers = _load_all_papers_from_disk(cfg)
    if year:
        start, end = parse_year_range(year)
        papers = [p for p in papers if _year_in_range(p.get("year"), start, end)]
    papers = _sort_refs(papers, sort)
    return {"papers": papers[offset : offset + limit], "total": len(papers)}


def get_stats(cfg: Config) -> dict[str, int]:
    """Return library status counts for the HTTP stats endpoint."""
    if _papers_table_ready(cfg.index_db):
        try:
            with sqlite3.connect(cfg.index_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT paper_id, md_path FROM papers").fetchall()
            total = len(rows)
            missing_md = sum(1 for row in rows if not row["md_path"])
            return {
                "total": total,
                "missingPdf": missing_md,
                "pendingIngest": missing_md,
                "notIndexed": 0,
            }
        except sqlite3.Error:
            pass

    papers = _load_all_papers_from_disk(cfg)
    return {
        "total": len(papers),
        "missingPdf": sum(1 for p in papers if not p["hasPdf"]),
        "pendingIngest": sum(1 for p in papers if p["status"] == "pending"),
        "notIndexed": sum(1 for p in papers if not p["isIndexed"]),
    }


def find_paper_dir(cfg: Config, paper_id: str) -> Path | None:
    """Resolve a user-visible paper identifier to a paper directory."""
    if cfg.index_db.exists():
        try:
            from scholaraio.services.index import lookup_paper

            record = lookup_paper(cfg.index_db, paper_id)
        except sqlite3.Error:
            record = None
        if record and record.get("dir_name"):
            pdir = cfg.papers_dir / str(record["dir_name"])
            if pdir.exists():
                return pdir

    for pdir in iter_paper_dirs(cfg.papers_dir):
        try:
            meta = read_meta(pdir)
        except (ValueError, FileNotFoundError):
            continue
        pid = meta.get("id") or pdir.name
        if pid == paper_id or pdir.name == paper_id:
            return pdir
    return None


def index_status(cfg: Config) -> dict[str, Any]:
    """Inspect index readiness without mutating the database."""
    status: dict[str, Any] = {
        "exists": cfg.index_db.exists(),
        "path": str(cfg.index_db),
        "ftsReady": False,
        "paperCount": 0,
        "registryReady": False,
        "registryCount": 0,
        "vectorReady": False,
        "vectorCount": 0,
        "faissCached": False,
    }
    if not cfg.index_db.exists():
        return status

    try:
        with sqlite3.connect(cfg.index_db) as conn:
            if _table_exists(conn, "papers"):
                status["ftsReady"] = True
                status["paperCount"] = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            if _table_exists(conn, "papers_registry"):
                status["registryReady"] = True
                status["registryCount"] = conn.execute("SELECT COUNT(*) FROM papers_registry").fetchone()[0]
            if _table_exists(conn, "paper_vectors"):
                status["vectorReady"] = True
                status["vectorCount"] = conn.execute("SELECT COUNT(*) FROM paper_vectors").fetchone()[0]
    except sqlite3.Error as exc:
        status["error"] = str(exc)

    faiss_index = cfg.index_db.parent / "faiss.index"
    faiss_ids = cfg.index_db.parent / "faiss_ids.json"
    status["faissCached"] = faiss_index.exists() and faiss_ids.exists()
    return status


def _list_papers_from_db(
    cfg: Config,
    *,
    sort: str,
    year: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    year_expr = "CAST(NULLIF(p.year, '') AS INTEGER)"
    filters = ["1=1"]
    params: list[Any] = []
    if year:
        start, end = parse_year_range(year)
        if start is not None:
            filters.append(f"{year_expr} >= ?")
            params.append(start)
        if end is not None:
            filters.append(f"{year_expr} <= ?")
            params.append(end)

    order_by = {
        "year_desc": f"{year_expr} DESC, LOWER(p.title) ASC",
        "year_asc": f"{year_expr} ASC, LOWER(p.title) ASC",
        "citations_desc": "CAST(NULLIF(p.citation_count, '') AS INTEGER) DESC, LOWER(p.title) ASC",
        "title_asc": "LOWER(p.title) ASC",
    }[sort]
    where = " AND ".join(filters)

    with sqlite3.connect(cfg.index_db) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) FROM papers p WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT p.paper_id, p.title, p.authors, p.year, p.journal, p.doi,
                   p.paper_type, p.citation_count, p.abstract, p.md_path,
                   r.dir_name
            FROM papers p
            LEFT JOIN papers_registry r ON r.id = p.paper_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    return {"papers": [_row_to_ref(cfg, row) for row in rows], "total": int(total)}


def _row_to_ref(cfg: Config, row: sqlite3.Row) -> dict[str, Any]:
    paper_id = row["paper_id"] or ""
    dir_name = row["dir_name"] or ""
    has_md = bool(row["md_path"])
    pdir = cfg.papers_dir / dir_name if dir_name else None
    has_pdf_file = bool(pdir and (pdir / "paper.pdf").exists())
    return {
        "id": paper_id,
        "dirName": dir_name,
        "title": row["title"] or dir_name or paper_id,
        "authors": row["authors"] or "",
        "source": row["journal"] or "",
        "year": _safe_int(row["year"]) or 0,
        "date": None,
        "citations": str(row["citation_count"] or "0"),
        "status": _paper_status(has_md=has_md, is_indexed=True),
        "hasPdf": has_md,
        "hasPdfFile": has_pdf_file,
        "isIndexed": True,
        "abstract": row["abstract"] or "",
        "doi": row["doi"] or "",
        "paperType": row["paper_type"] or "",
    }


def _load_all_papers_from_disk(cfg: Config) -> list[dict[str, Any]]:
    indexed = indexed_ids(cfg.index_db)
    papers = []
    for pdir in iter_paper_dirs(cfg.papers_dir):
        try:
            meta = read_meta(pdir)
        except (ValueError, FileNotFoundError):
            continue
        papers.append(paper_to_ref(pdir, meta, indexed))
    return papers


def _sort_refs(papers: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "year_asc":
        return sorted(papers, key=lambda p: p["year"] or 0)
    if sort == "citations_desc":
        return sorted(papers, key=lambda p: int(p["citations"] or 0), reverse=True)
    if sort == "title_asc":
        return sorted(papers, key=lambda p: (p["title"] or "").lower())
    return sorted(papers, key=lambda p: p["year"] or 0, reverse=True)


def _papers_table_ready(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            return _table_exists(conn, "papers")
    except sqlite3.Error:
        return False


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _paper_status(*, has_md: bool, is_indexed: bool) -> str:
    if not has_md:
        return "pending"
    if not is_indexed:
        return "indexing"
    return "ingested"


def _year_in_range(value: object, start: int | None, end: int | None) -> bool:
    year = _safe_int(value)
    if year is None:
        return False
    if start is not None and year < start:
        return False
    if end is not None and year > end:
        return False
    return True


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
