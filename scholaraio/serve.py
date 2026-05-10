"""
serve.py — ScholarAIO HTTP API server
======================================

FastAPI server exposing library data as REST JSON for external UIs (e.g. scholaraio-hub).

Usage:
    scholaraio serve [--host HOST] [--port PORT]
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from scholaraio.core.config import Config, load_config
from scholaraio.stores.papers import best_citation, iter_paper_dirs, read_meta
from scholaraio.projects import workspace as ws_mod

_cfg: Config | None = None


def _get_cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


# ============================================================================
#  Data helpers
# ============================================================================


def _indexed_ids(db_path: Path) -> set[str]:
    """Return set of paper UUIDs currently in papers_registry."""
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT id FROM papers_registry").fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _paper_to_ref(pdir: Path, meta: dict, indexed: set[str]) -> dict[str, Any]:
    """Convert a paper directory + meta.json into a PaperReference-compatible dict."""
    paper_id = meta.get("id") or pdir.name
    has_pdf = (pdir / "paper.md").exists()
    is_indexed = paper_id in indexed

    if not has_pdf:
        status = "pending"
    elif not is_indexed:
        status = "indexing"
    else:
        status = "ingested"

    authors_raw = meta.get("authors") or []
    authors_str = ", ".join(authors_raw) if isinstance(authors_raw, list) else str(authors_raw)

    return {
        "id": paper_id,
        "dirName": pdir.name,
        "title": meta.get("title") or pdir.name,
        "authors": authors_str,
        "source": meta.get("journal") or "",
        "year": meta.get("year") or 0,
        "date": meta.get("date") or None,
        "citations": str(best_citation(meta)),
        "status": status,
        "hasPdf": has_pdf,
        "isIndexed": is_indexed,
        "abstract": meta.get("abstract") or "",
        "doi": meta.get("doi") or "",
    }


def _load_all_papers(cfg: Config) -> list[dict[str, Any]]:
    """Load all papers from disk with index status."""
    indexed = _indexed_ids(cfg.index_db)
    papers = []
    for pdir in iter_paper_dirs(cfg.papers_dir):
        try:
            meta = read_meta(pdir)
            papers.append(_paper_to_ref(pdir, meta, indexed))
        except (ValueError, FileNotFoundError):
            continue
    return papers


# ============================================================================
#  App factory (called at startup so cfg is resolved from cwd)
# ============================================================================


def create_app(cfg: Config | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError as e:
        raise RuntimeError(
            "FastAPI not installed. Run: pip install 'scholaraio[serve]'"
        ) from e

    resolved_cfg = cfg or _get_cfg()

    app = FastAPI(title="ScholarAIO API", version="1.0.0", docs_url="/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    #  Request models
    # ------------------------------------------------------------------

    class SearchRequest(BaseModel):
        query: str
        mode: str = "unified"  # unified | keyword | semantic | author
        year: str | None = None
        journal: str | None = None
        paper_type: str | None = None
        limit: int = 20

    class CreateWorkspaceRequest(BaseModel):
        name: str

    # ------------------------------------------------------------------
    #  GET /api/papers
    # ------------------------------------------------------------------

    @app.get("/api/papers")
    def list_papers(
        sort: str = "year_desc",
        year: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        papers = _load_all_papers(resolved_cfg)

        if year:
            from scholaraio.stores.papers import parse_year_range
            try:
                start, end = parse_year_range(year)
                papers = [
                    p for p in papers
                    if (start is None or (p["year"] or 0) >= start)
                    and (end is None or (p["year"] or 0) <= end)
                ]
            except ValueError:
                pass

        if sort == "year_desc":
            papers.sort(key=lambda p: p["year"] or 0, reverse=True)
        elif sort == "year_asc":
            papers.sort(key=lambda p: p["year"] or 0)
        elif sort == "citations_desc":
            papers.sort(key=lambda p: int(p["citations"] or 0), reverse=True)
        elif sort == "title_asc":
            papers.sort(key=lambda p: (p["title"] or "").lower())

        total = len(papers)
        page = papers[offset : offset + limit]
        return {"papers": page, "total": total}

    # ------------------------------------------------------------------
    #  GET /api/papers/{paper_id}
    # ------------------------------------------------------------------

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, Any]:
        indexed = _indexed_ids(resolved_cfg.index_db)
        for pdir in iter_paper_dirs(resolved_cfg.papers_dir):
            try:
                meta = read_meta(pdir)
            except (ValueError, FileNotFoundError):
                continue
            pid = meta.get("id") or pdir.name
            if pid == paper_id or pdir.name == paper_id:
                ref = _paper_to_ref(pdir, meta, indexed)
                md_path = pdir / "paper.md"
                if md_path.exists():
                    ref["content"] = md_path.read_text(encoding="utf-8")
                return {"paper": ref}
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")

    # ------------------------------------------------------------------
    #  GET /api/stats
    # ------------------------------------------------------------------

    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        papers = _load_all_papers(resolved_cfg)
        total = len(papers)
        missing_pdf = sum(1 for p in papers if not p["hasPdf"])
        pending_ingest = sum(1 for p in papers if p["status"] == "pending")
        not_indexed = sum(1 for p in papers if not p["isIndexed"])
        return {
            "total": total,
            "missingPdf": missing_pdf,
            "pendingIngest": pending_ingest,
            "notIndexed": not_indexed,
        }

    # ------------------------------------------------------------------
    #  POST /api/search
    # ------------------------------------------------------------------

    @app.post("/api/search")
    def search_papers(req: SearchRequest) -> dict[str, Any]:
        from scholaraio.services import index as idx

        db = resolved_cfg.index_db
        if not db.exists():
            return {"results": [], "mode": req.mode, "total": 0}

        kwargs: dict[str, Any] = {
            "db_path": db,
            "top_k": req.limit,
            "cfg": resolved_cfg,
            "year": req.year,
            "journal": req.journal,
            "paper_type": req.paper_type,
        }

        try:
            if req.mode == "unified":
                results = idx.unified_search(req.query, **kwargs)
            elif req.mode == "semantic":
                from scholaraio.services.vectors import vsearch
                results = vsearch(req.query, **kwargs)
            elif req.mode == "author":
                results = idx.search_author(req.query, **kwargs)
            else:
                results = idx.search(req.query, **kwargs)
        except FileNotFoundError:
            return {"results": [], "mode": req.mode, "total": 0}

        indexed = _indexed_ids(db)
        enriched = []
        for r in results:
            paper_id = r.get("paper_id") or r.get("id", "")
            dir_name = r.get("dir_name", "")
            pdir = resolved_cfg.papers_dir / dir_name if dir_name else None

            entry: dict[str, Any] = {
                "id": paper_id,
                "dirName": dir_name,
                "title": r.get("title") or "",
                "authors": r.get("authors") or "",
                "source": r.get("journal") or "",
                "year": int(r.get("year") or 0),
                "citations": str(r.get("citation_count") or "0"),
                "doi": r.get("doi") or "",
                "isIndexed": paper_id in indexed,
                "hasPdf": bool(pdir and (pdir / "paper.md").exists()),
                "match": r.get("match"),
                "score": r.get("score"),
            }
            entry["status"] = (
                "ingested" if entry["hasPdf"] and entry["isIndexed"]
                else "indexing" if entry["hasPdf"]
                else "pending"
            )

            if pdir and pdir.exists():
                try:
                    meta = read_meta(pdir)
                    entry["abstract"] = meta.get("abstract") or ""
                except Exception:
                    pass

            enriched.append(entry)

        return {"results": enriched, "mode": req.mode, "total": len(enriched)}

    # ------------------------------------------------------------------
    #  GET /api/workspaces
    # ------------------------------------------------------------------

    @app.get("/api/workspaces")
    def list_workspaces() -> dict[str, Any]:
        ws_root = resolved_cfg.workspace_dir
        names = ws_mod.list_workspaces(ws_root)
        result = []
        for name in names:
            ws_dir = ws_root / name
            entries = ws_mod._read(ws_dir)
            result.append({
                "id": name,
                "name": name,
                "paperCount": len(entries),
                "createdAt": _dir_ctime(ws_dir),
            })
        return {"workspaces": result}

    # ------------------------------------------------------------------
    #  GET /api/workspaces/{name}
    # ------------------------------------------------------------------

    @app.get("/api/workspaces/{name}")
    def get_workspace(name: str) -> dict[str, Any]:
        if not ws_mod.validate_workspace_name(name):
            raise HTTPException(status_code=400, detail="Invalid workspace name")
        ws_root = resolved_cfg.workspace_dir
        ws_dir = ws_root / name
        if not ws_dir.exists() or not (ws_dir / "papers.json").exists():
            raise HTTPException(status_code=404, detail=f"Workspace not found: {name}")

        entries = ws_mod.show(ws_dir, resolved_cfg.index_db)
        indexed = _indexed_ids(resolved_cfg.index_db)

        papers = []
        for e in entries:
            dir_name = e.get("dir_name", "")
            pdir = resolved_cfg.papers_dir / dir_name if dir_name else None
            if pdir and pdir.exists():
                try:
                    meta = read_meta(pdir)
                    papers.append(_paper_to_ref(pdir, meta, indexed))
                    continue
                except Exception:
                    pass
            papers.append({
                "id": e.get("id", ""),
                "dirName": dir_name,
                "title": dir_name,
                "authors": "",
                "source": "",
                "year": 0,
                "citations": "0",
                "status": "pending",
                "hasPdf": False,
                "isIndexed": e.get("id", "") in indexed,
            })

        return {
            "workspace": {
                "id": name,
                "name": name,
                "paperCount": len(papers),
                "createdAt": _dir_ctime(ws_dir),
                "papers": papers,
            }
        }

    # ------------------------------------------------------------------
    #  POST /api/workspaces
    # ------------------------------------------------------------------

    @app.post("/api/workspaces", status_code=201)
    def create_workspace(req: CreateWorkspaceRequest) -> dict[str, Any]:
        if not ws_mod.validate_workspace_name(req.name):
            raise HTTPException(status_code=400, detail="Invalid workspace name")
        ws_root = resolved_cfg.workspace_dir
        ws_dir = ws_root / req.name
        if ws_dir.exists():
            raise HTTPException(status_code=409, detail=f"Workspace already exists: {req.name}")
        ws_mod.create(ws_dir)
        return {
            "workspace": {
                "id": req.name,
                "name": req.name,
                "paperCount": 0,
                "createdAt": _dir_ctime(ws_dir),
                "papers": [],
            }
        }

    return app


def _dir_ctime(path: Path) -> str:
    """Return ISO date of directory mtime."""
    import datetime
    ts = path.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


# ============================================================================
#  CLI entry point
# ============================================================================


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the FastAPI server. Called from CLI."""
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "uvicorn not installed. Run: pip install 'scholaraio[serve]'"
        ) from e

    cfg = load_config()
    cfg.ensure_dirs()
    app = create_app(cfg)
    print(f"ScholarAIO API server starting on http://{host}:{port}")
    print(f"  Papers dir : {cfg.papers_dir}")
    print(f"  Index DB   : {cfg.index_db}")
    print(f"  Workspace  : {cfg.workspace_dir}")
    print(f"  API docs   : http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port, log_level="info")
