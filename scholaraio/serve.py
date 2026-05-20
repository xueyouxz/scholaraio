"""
serve.py — ScholarAIO HTTP API server
======================================

FastAPI server exposing library data as REST JSON for external UIs (e.g. scholaraio-hub).

Usage:
    scholaraio serve [--host HOST] [--port PORT]
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from scholaraio.core.config import Config, load_config
from scholaraio.projects import workspace as ws_mod
from scholaraio.services.jobs import JobNotFoundError, JobRunner, JobValidationError
from scholaraio.services import library_read_model as library_model
from scholaraio.stores.papers import read_meta

try:
    from pydantic import BaseModel, Field

    class SearchRequest(BaseModel):
        query: str = Field(min_length=1, max_length=1000)
        mode: Literal["unified", "keyword", "semantic", "author"] = "unified"
        year: str | None = None
        journal: str | None = None
        paper_type: str | None = None
        limit: int = Field(default=20, ge=1, le=100)

    class CreateWorkspaceRequest(BaseModel):
        name: str

    class AddPaperRequest(BaseModel):
        paperId: str

    class CreateJobRequest(BaseModel):
        kind: str = Field(min_length=1, max_length=32)
        preset: str | None = None
        steps: list[str] | None = None
        rebuild: bool = False
        dryRun: bool = False
        noApi: bool = False
        force: bool = False
        inspect: bool = False
        maxRetries: int = Field(default=2, ge=0, le=20)
        inboxDir: str | None = None
        papersDir: str | None = None

except ImportError:
    SearchRequest = None  # type: ignore[assignment,misc]
    CreateWorkspaceRequest = None  # type: ignore[assignment,misc]
    AddPaperRequest = None  # type: ignore[assignment,misc]
    CreateJobRequest = None  # type: ignore[assignment,misc]

_cfg: Config | None = None


def _get_cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


# ============================================================================
#  App factory (called at startup so cfg is resolved from cwd)
# ============================================================================


def create_app(cfg: Config | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as e:
        raise RuntimeError(
            "FastAPI not installed. Run: pip install 'scholaraio[serve]'"
        ) from e

    resolved_cfg = cfg or _get_cfg()
    job_runner = JobRunner()

    @asynccontextmanager
    async def lifespan(_app: Any):
        try:
            yield
        finally:
            job_runner.shutdown(wait=False)

    app = FastAPI(title="ScholarAIO API", version="1.0.0", docs_url="/docs", lifespan=lifespan)
    app.state.job_runner = job_runner

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    #  GET /api/v1/health
    # ------------------------------------------------------------------

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "index": library_model.index_status(resolved_cfg)}

    # ------------------------------------------------------------------
    #  GET /api/v1/capabilities
    # ------------------------------------------------------------------

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        index = library_model.index_status(resolved_cfg)
        return {
            "serve": True,
            "search": index["ftsReady"],
            "semantic": resolved_cfg.embed.provider != "none" and index["vectorReady"],
            "workspaces": True,
            "ingest": True,
            "jobs": True,
            "jobKinds": job_runner.available_kinds(),
            "paper2any": bool(resolved_cfg.paper2any.mcp_url or resolved_cfg.paper2any.base_url),
        }

    # ------------------------------------------------------------------
    #  GET /api/papers
    # ------------------------------------------------------------------

    @app.get("/api/v1/papers")
    @app.get("/api/papers")
    def list_papers(
        sort: Literal["year_desc", "year_asc", "citations_desc", "title_asc"] = "year_desc",
        year: str | None = None,
        limit: int = Query(500, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        try:
            return library_model.list_papers(
                resolved_cfg,
                sort=sort,
                year=year,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    #  GET /api/papers/{paper_id}
    # ------------------------------------------------------------------

    @app.get("/api/v1/papers/{paper_id}")
    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, Any]:
        pdir = library_model.find_paper_dir(resolved_cfg, paper_id)
        if pdir:
            meta = read_meta(pdir)
            ref = library_model.paper_to_ref(pdir, meta, library_model.indexed_ids(resolved_cfg.index_db))
            md_path = pdir / "paper.md"
            if md_path.exists():
                ref["content"] = md_path.read_text(encoding="utf-8")
            if meta.get("l3_conclusion"):
                ref["l3Conclusion"] = meta["l3_conclusion"]
            return {"paper": ref}
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")

    # ------------------------------------------------------------------
    #  GET /api/v1/papers/{paper_id}/content
    # ------------------------------------------------------------------

    @app.get("/api/v1/papers/{paper_id}/content")
    def get_paper_content(
        paper_id: str,
        layer: int = Query(4, ge=1, le=4),
        lang: str | None = None,
    ) -> dict[str, Any]:
        pdir = library_model.find_paper_dir(resolved_cfg, paper_id)
        if not pdir:
            raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
        meta = read_meta(pdir)
        if layer == 1:
            return {"paperId": meta.get("id") or pdir.name, "layer": 1, "content": meta}
        if layer == 2:
            return {"paperId": meta.get("id") or pdir.name, "layer": 2, "content": meta.get("abstract") or ""}
        if layer == 3:
            return {"paperId": meta.get("id") or pdir.name, "layer": 3, "content": meta.get("l3_conclusion") or ""}
        md_path = pdir / "paper.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Markdown content not found for this paper")
        from scholaraio.services.loader import load_l4

        return {"paperId": meta.get("id") or pdir.name, "layer": 4, "content": load_l4(md_path, lang=lang)}

    # ------------------------------------------------------------------
    #  GET /api/papers/{paper_id}/pdf
    # ------------------------------------------------------------------

    @app.get("/api/v1/papers/{paper_id}/pdf")
    @app.get("/api/papers/{paper_id}/pdf")
    def get_paper_pdf(paper_id: str):
        from fastapi.responses import FileResponse
        pdir = library_model.find_paper_dir(resolved_cfg, paper_id)
        if pdir:
            pdf_path = pdir / "paper.pdf"
            if pdf_path.exists():
                return FileResponse(
                    pdf_path,
                    media_type="application/pdf",
                    filename=f"{pdir.name}.pdf",
                )
            raise HTTPException(status_code=404, detail="PDF file not found for this paper")
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")

    # ------------------------------------------------------------------
    #  GET /api/papers/{paper_id}/images/{filename}
    # ------------------------------------------------------------------

    @app.get("/api/v1/papers/{paper_id}/images/{filename}")
    @app.get("/api/papers/{paper_id}/images/{filename}")
    def get_paper_image(paper_id: str, filename: str):
        import mimetypes
        from fastapi.responses import FileResponse

        if ".." in filename or filename.startswith("/") or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        pdir = library_model.find_paper_dir(resolved_cfg, paper_id)
        if pdir:
            img_path = pdir / "images" / filename
            if img_path.exists():
                media_type, _ = mimetypes.guess_type(filename)
                return FileResponse(img_path, media_type=media_type or "application/octet-stream")
            raise HTTPException(status_code=404, detail="Image not found")
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")

    # ------------------------------------------------------------------
    #  GET /api/stats
    # ------------------------------------------------------------------

    @app.get("/api/v1/stats")
    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        return library_model.get_stats(resolved_cfg)

    # ------------------------------------------------------------------
    #  GET /api/v1/index/status
    # ------------------------------------------------------------------

    @app.get("/api/v1/index/status")
    def get_index_status() -> dict[str, Any]:
        return library_model.index_status(resolved_cfg)

    # ------------------------------------------------------------------
    #  Jobs
    # ------------------------------------------------------------------

    @app.post("/api/v1/jobs", status_code=202)
    @app.post("/api/jobs", status_code=202)
    def create_job(req: CreateJobRequest) -> dict[str, Any]:
        payload = _model_dump(req)
        kind = payload.pop("kind")
        try:
            return {"job": job_runner.submit(kind, resolved_cfg, payload)}
        except JobValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/jobs")
    @app.get("/api/jobs")
    def list_jobs(limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
        return {"jobs": job_runner.list(limit=limit)}

    @app.get("/api/v1/jobs/{job_id}")
    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": job_runner.get(job_id, include_events=True)}
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc

    @app.get("/api/v1/jobs/{job_id}/events")
    @app.get("/api/jobs/{job_id}/events")
    def get_job_events(job_id: str, after: int = Query(0, ge=0)) -> dict[str, Any]:
        try:
            return job_runner.events(job_id, after=after)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc

    @app.post("/api/v1/jobs/{job_id}/cancel")
    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": job_runner.cancel(job_id)}
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc

    # ------------------------------------------------------------------
    #  POST /api/search
    # ------------------------------------------------------------------

    @app.post("/api/v1/search")
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
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        indexed = library_model.indexed_ids(db)
        enriched = []
        for r in results:
            paper_id = r.get("paper_id") or r.get("id", "")
            dir_name = r.get("dir_name", "")
            # md_path non-empty means paper.md exists (hasPdf); avoids per-result stat()
            has_pdf = bool(r.get("md_path"))
            is_indexed = paper_id in indexed

            entry: dict[str, Any] = {
                "id": paper_id,
                "dirName": dir_name,
                "title": r.get("title") or "",
                "authors": r.get("authors") or "",
                "source": r.get("journal") or "",
                "year": int(r.get("year") or 0),
                "citations": str(r.get("citation_count") or "0"),
                "doi": r.get("doi") or "",
                "paperType": r.get("paper_type") or "",
                "abstract": r.get("abstract") or "",
                "isIndexed": is_indexed,
                "hasPdf": has_pdf,
                "status": (
                    "ingested" if has_pdf and is_indexed
                    else "indexing" if has_pdf
                    else "pending"
                ),
                "match": r.get("match"),
                "score": r.get("score"),
            }
            enriched.append(entry)

        return {"results": enriched, "mode": req.mode, "total": len(enriched)}

    # ------------------------------------------------------------------
    #  GET /api/workspaces
    # ------------------------------------------------------------------

    @app.get("/api/v1/workspaces")
    @app.get("/api/workspaces")
    def list_workspaces() -> dict[str, Any]:
        ws_root = resolved_cfg.workspace_dir
        names = ws_mod.list_workspaces(ws_root)
        result = []
        for name in names:
            ws_dir = ws_root / name
            result.append({
                "id": name,
                "name": name,
                "paperCount": ws_mod.paper_count(ws_dir),
                "createdAt": _dir_ctime(ws_dir),
            })
        return {"workspaces": result}

    # ------------------------------------------------------------------
    #  GET /api/workspaces/{name}
    # ------------------------------------------------------------------

    @app.get("/api/v1/workspaces/{name}")
    @app.get("/api/workspaces/{name}")
    def get_workspace(name: str) -> dict[str, Any]:
        if not ws_mod.validate_workspace_name(name):
            raise HTTPException(status_code=400, detail="Invalid workspace name")
        ws_root = resolved_cfg.workspace_dir
        ws_dir = ws_root / name
        if not ws_dir.exists() or not ws_mod.has_paper_index(ws_dir):
            raise HTTPException(status_code=404, detail=f"Workspace not found: {name}")

        entries = ws_mod.show(ws_dir, resolved_cfg.index_db)
        indexed = library_model.indexed_ids(resolved_cfg.index_db)

        papers = []
        for e in entries:
            dir_name = e.get("dir_name", "")
            pdir = resolved_cfg.papers_dir / dir_name if dir_name else None
            if pdir and pdir.exists():
                try:
                    meta = read_meta(pdir)
                    papers.append(library_model.paper_to_ref(pdir, meta, indexed))
                    continue
                except (ValueError, FileNotFoundError):
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
                "hasPdfFile": False,
                "isIndexed": e.get("id", "") in indexed,
                "abstract": "",
                "doi": "",
                "paperType": "",
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

    @app.post("/api/v1/workspaces", status_code=201)
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

    # ------------------------------------------------------------------
    #  POST /api/workspaces/{name}/papers
    # ------------------------------------------------------------------

    @app.post("/api/v1/workspaces/{name}/papers")
    @app.post("/api/workspaces/{name}/papers")
    def add_paper_to_workspace(name: str, req: AddPaperRequest) -> dict[str, Any]:
        if not ws_mod.validate_workspace_name(name):
            raise HTTPException(status_code=400, detail="Invalid workspace name")
        ws_root = resolved_cfg.workspace_dir
        ws_dir = ws_root / name
        if not ws_dir.exists() or not ws_mod.has_paper_index(ws_dir):
            raise HTTPException(status_code=404, detail=f"Workspace not found: {name}")
        ws_mod.add(ws_dir, [req.paperId], resolved_cfg.index_db)
        return {"ok": True}

    # ------------------------------------------------------------------
    #  GET /api/config - read-only config snapshot (keys masked)
    # ------------------------------------------------------------------

    @app.get("/api/v1/config")
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        def mask(key: str) -> str:
            if not key:
                return ""
            return key[:8] + "..." if len(key) > 8 else key[:4] + "..."

        cfg = resolved_cfg
        return {
            "llm": {
                "backend": cfg.llm.backend,
                "model": cfg.llm.model,
                "base_url": cfg.llm.base_url,
                "api_key": mask(cfg.llm.api_key),
            },
            "embed": {
                "provider": cfg.embed.provider,
                "model": cfg.embed.model,
                "source": cfg.embed.source,
                "api_key": mask(cfg.embed.api_key),
            },
            "ingest": {
                "mineru_endpoint": cfg.ingest.mineru_endpoint,
                "mineru_api_key": mask(cfg.ingest.mineru_api_key),
                "s2_api_key": mask(cfg.ingest.s2_api_key),
            },
            "paths": {
                "papers_dir": str(cfg.papers_dir),
                "workspace_dir": str(cfg.workspace_dir),
            },
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
