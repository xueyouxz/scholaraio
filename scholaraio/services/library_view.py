"""Read-only view models for the local library WebUI."""

from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import quote

from scholaraio.services.audit import Issue, audit_papers
from scholaraio.stores.papers import best_citation, find_pdf, iter_paper_dirs, read_meta
from scholaraio.stores.proceedings import iter_proceedings_dirs, read_json

if TYPE_CHECKING:
    from scholaraio.core.config import Config

_AUDIT_CACHE_TTL_SECONDS = 30.0
_AUDIT_CACHE: dict[str, tuple[float, dict[str, list[dict]]]] = {}
_AUDIT_CACHE_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authors_text(authors: object) -> str:
    if isinstance(authors, str):
        return authors
    if isinstance(authors, (list, tuple)):
        return ", ".join(str(author) for author in authors if author)
    return ""


def _bool_has_text(value: object) -> bool:
    return bool(str(value or "").strip())


def _normalize_paper_type(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", raw)
    text = re.sub(r"[\s_]+", "-", text).lower().strip("-")
    text = re.sub(r"-+", "-", text)
    compact = re.sub(r"[^a-z0-9]", "", text)
    aliases = {
        "article": "journal-article",
        "journalarticle": "journal-article",
        "researcharticle": "journal-article",
        "proceedingsarticle": "conference-paper",
        "conferencearticle": "conference-paper",
        "conferencepaper": "conference-paper",
        "bookchapter": "book-chapter",
    }
    return aliases.get(compact, text)


def _pdf_url(source: str, paper_id: str) -> str:
    return f"/api/{source}/pdf?id={quote(paper_id)}"


def _pdf_fields(source: str, paper_dir: Path, paper_id: str) -> dict:
    pdf = find_pdf(paper_dir)
    return {
        "has_pdf": pdf is not None,
        "pdf_filename": pdf.name if pdf else "",
        "pdf_url": _pdf_url(source, paper_id) if pdf else "",
    }


def _issue_dict(issue: Issue) -> dict:
    return {
        "paper_id": issue.paper_id,
        "severity": issue.severity,
        "rule": issue.rule,
        "message": issue.message,
    }


def _empty_issue_counts() -> dict[str, int]:
    return {"error": 0, "warning": 0, "info": 0}


def _issue_counts(issues: list[dict]) -> dict[str, int]:
    counts = Counter(issue["severity"] for issue in issues)
    result = _empty_issue_counts()
    result.update({key: int(counts.get(key, 0)) for key in result})
    return result


def _main_issue_map(papers_dir: Path) -> dict[str, list[dict]]:
    cache_key = str(papers_dir.resolve())
    now = monotonic()
    with _AUDIT_CACHE_LOCK:
        cached = _AUDIT_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        by_dir: dict[str, list[dict]] = defaultdict(list)
        for issue in audit_papers(papers_dir):
            by_dir[issue.paper_id].append(_issue_dict(issue))
        issue_map = dict(by_dir)
        _AUDIT_CACHE[cache_key] = (now + _AUDIT_CACHE_TTL_SECONDS, issue_map)
        return issue_map


def _invalid_json_issues(paper_id: str, exc: Exception) -> list[dict]:
    return [
        {
            "paper_id": paper_id,
            "severity": "error",
            "rule": "invalid_json",
            "message": f"Failed to parse JSON: {exc}",
        }
    ]


def _metadata_read_issues(paper_id: str, exc: Exception) -> list[dict]:
    if isinstance(exc, ValueError):
        return _invalid_json_issues(paper_id, exc)
    return [
        {
            "paper_id": paper_id,
            "severity": "error",
            "rule": "metadata_unreadable",
            "message": f"Failed to read metadata: {exc}",
        }
    ]


def _main_row(paper_dir: Path, meta: dict, issues: list[dict]) -> dict:
    paper_id = meta.get("id") or paper_dir.name
    toc = meta.get("toc") or []
    md_file = paper_dir / "paper.md"
    raw_type = meta.get("paper_type") or ""
    return {
        "paper_id": paper_id,
        "dir_name": paper_dir.name,
        "title": meta.get("title") or "",
        "authors": meta.get("authors") or [],
        "authors_text": _authors_text(meta.get("authors") or []),
        "year": meta.get("year") or "",
        "journal": meta.get("journal") or "",
        "doi": meta.get("doi") or "",
        "paper_type": _normalize_paper_type(raw_type),
        "paper_type_raw": raw_type,
        "citation_count": best_citation(meta),
        "has_md": md_file.exists(),
        "has_abstract": _bool_has_text(meta.get("abstract")),
        "has_l3": _bool_has_text(meta.get("l3_conclusion")),
        "toc_count": len(toc) if isinstance(toc, list) else 0,
        "issue_counts": _issue_counts(issues),
        "issues": issues,
        **_pdf_fields("main", paper_dir, paper_id),
    }


def build_main_library_view(cfg: Config) -> dict:
    """Return a live read-only table view for the configured main paper library."""
    papers_dir = cfg.papers_dir
    issue_map = _main_issue_map(papers_dir)
    rows: list[dict] = []
    totals = _empty_issue_counts()
    for paper_dir in iter_paper_dirs(papers_dir):
        try:
            meta = read_meta(paper_dir)
        except (ValueError, OSError) as exc:
            issues = _metadata_read_issues(paper_dir.name, exc)
            meta = {"id": paper_dir.name, "title": paper_dir.name}
        else:
            issues = issue_map.get(paper_dir.name, [])
        row = _main_row(paper_dir, meta, issues)
        for key, value in row["issue_counts"].items():
            totals[key] += value
        rows.append(row)

    rows.sort(key=lambda row: (str(row.get("year") or ""), row.get("title") or ""), reverse=True)
    return {
        "source": "main",
        "root": str(papers_dir),
        "generated_at": _now_iso(),
        "total": len(rows),
        "issue_totals": totals,
        "papers": rows,
    }


def _find_main_paper(cfg: Config, paper_id: str, *, include_issues: bool = True) -> tuple[Path, dict, list[dict]]:
    issue_map = _main_issue_map(cfg.papers_dir) if include_issues else {}
    for paper_dir in iter_paper_dirs(cfg.papers_dir):
        try:
            meta = read_meta(paper_dir)
        except (ValueError, OSError) as exc:
            if paper_id == paper_dir.name:
                return (
                    paper_dir,
                    {"id": paper_dir.name, "title": paper_dir.name},
                    _metadata_read_issues(paper_dir.name, exc),
                )
            continue
        current_id = meta.get("id") or paper_dir.name
        if paper_id in {current_id, paper_dir.name}:
            return paper_dir, meta, issue_map.get(paper_dir.name, [])
    raise KeyError(paper_id)


def get_main_paper_detail(cfg: Config, paper_id: str) -> dict:
    """Return detailed read-only metadata for one main-library paper."""
    paper_dir, meta, issues = _find_main_paper(cfg, paper_id)
    row = _main_row(paper_dir, meta, issues)
    return {
        **row,
        "abstract": meta.get("abstract") or "",
        "l3_conclusion": meta.get("l3_conclusion") or "",
        "toc": meta.get("toc") or [],
        "ids": meta.get("ids") or {},
        "source_path": str(paper_dir),
    }


def _proceedings_row(cfg: Config, row: dict, *, meta: dict | None = None, issues: list[dict] | None = None) -> dict:
    paper_dir = cfg.proceedings_dir / row["proceeding_dir"] / "papers" / row["dir_name"]
    meta_path = paper_dir / "meta.json"
    paper_id = row.get("paper_id") or row.get("dir_name") or ""
    row_issues = list(issues or [])
    if meta is None:
        try:
            meta = read_json(meta_path) if meta_path.exists() else {}
        except (ValueError, OSError) as exc:
            meta = {"id": paper_id, "title": paper_id}
            row_issues.extend(_metadata_read_issues(paper_id, exc))
    toc = meta.get("toc") or []
    raw_type = row.get("paper_type") or meta.get("paper_type") or ""
    return {
        "paper_id": paper_id,
        "dir_name": row.get("dir_name") or "",
        "title": row.get("title") or meta.get("title") or "",
        "authors": meta.get("authors") or [],
        "authors_text": row.get("authors") or _authors_text(meta.get("authors") or []),
        "year": row.get("year") or "",
        "journal": row.get("journal") or "",
        "doi": row.get("doi") or "",
        "paper_type": _normalize_paper_type(raw_type),
        "paper_type_raw": raw_type,
        "proceeding_id": row.get("proceeding_id") or "",
        "proceeding_dir": row.get("proceeding_dir") or "",
        "proceeding_title": row.get("proceeding_title") or "",
        "has_md": bool(row.get("md_path")),
        "has_abstract": _bool_has_text(row.get("abstract")),
        "has_l3": _bool_has_text(row.get("conclusion")),
        "toc_count": len(toc) if isinstance(toc, list) else 0,
        "issue_counts": _issue_counts(row_issues),
        "issues": row_issues,
        **_pdf_fields("proceedings", paper_dir, paper_id),
    }


def _iter_proceedings_view_records(cfg: Config):
    for proceeding_dir in iter_proceedings_dirs(cfg.proceedings_dir):
        meta_path = proceeding_dir / "meta.json"
        papers_dir = proceeding_dir / "papers"
        if not meta_path.exists() or not papers_dir.is_dir():
            continue

        proceeding_issues: list[dict] = []
        try:
            proceeding_meta = read_json(meta_path)
        except (ValueError, OSError) as exc:
            proceeding_meta = {"id": proceeding_dir.name, "title": proceeding_dir.name}
            proceeding_issues = _metadata_read_issues(proceeding_dir.name, exc)
        proceeding_title = proceeding_meta.get("title") or proceeding_dir.name
        proceeding_id = proceeding_meta.get("id") or proceeding_dir.name

        try:
            paper_dirs = sorted(papers_dir.iterdir())
        except OSError:
            continue
        for paper_dir in paper_dirs:
            if not paper_dir.is_dir():
                continue
            paper_meta_path = paper_dir / "meta.json"
            if not paper_meta_path.exists():
                continue
            issues = list(proceeding_issues)
            try:
                paper_meta = read_json(paper_meta_path)
            except (ValueError, OSError) as exc:
                paper_meta = {"id": paper_dir.name, "title": paper_dir.name}
                issues.extend(_metadata_read_issues(paper_dir.name, exc))
            row = {
                "paper_id": paper_meta.get("id") or paper_dir.name,
                "title": paper_meta.get("title") or "",
                "authors": _authors_text(paper_meta.get("authors") or []),
                "year": str(paper_meta.get("year") or ""),
                "journal": paper_meta.get("journal") or "",
                "abstract": paper_meta.get("abstract") or "",
                "conclusion": paper_meta.get("l3_conclusion") or "",
                "doi": paper_meta.get("doi") or "",
                "paper_type": paper_meta.get("paper_type") or "",
                "citation_count": "",
                "md_path": str((paper_dir / "paper.md").resolve()) if (paper_dir / "paper.md").exists() else "",
                "dir_name": paper_dir.name,
                "proceeding_id": proceeding_id,
                "proceeding_dir": proceeding_dir.name,
                "proceeding_title": paper_meta.get("proceeding_title") or proceeding_title,
            }
            yield _proceedings_row(cfg, row, meta=paper_meta, issues=issues), paper_dir, paper_meta


def build_proceedings_library_view(cfg: Config) -> dict:
    """Return a live read-only table view for configured proceedings child papers."""
    rows = [row for row, _paper_dir, _meta in _iter_proceedings_view_records(cfg)]
    rows.sort(key=lambda row: (str(row.get("year") or ""), row.get("title") or ""), reverse=True)
    volumes = sorted({row["proceeding_title"] for row in rows if row.get("proceeding_title")})
    totals = _empty_issue_counts()
    for row in rows:
        for key, value in row["issue_counts"].items():
            totals[key] += value
    return {
        "source": "proceedings",
        "root": str(cfg.proceedings_dir),
        "generated_at": _now_iso(),
        "total": len(rows),
        "issue_totals": totals,
        "volumes": volumes,
        "papers": rows,
    }


def _find_proceedings_row(cfg: Config, paper_id: str) -> tuple[dict, Path, dict]:
    for row, paper_dir, meta in _iter_proceedings_view_records(cfg):
        if paper_id in {row["paper_id"], row["dir_name"]}:
            return row, paper_dir, meta
    raise KeyError(paper_id)


def get_proceedings_paper_detail(cfg: Config, paper_id: str) -> dict:
    """Return detailed read-only metadata for one proceedings child paper."""
    row, paper_dir, meta = _find_proceedings_row(cfg, paper_id)
    return {
        **row,
        "abstract": meta.get("abstract") or "",
        "l3_conclusion": meta.get("l3_conclusion") or "",
        "toc": meta.get("toc") or [],
        "ids": meta.get("ids") or {},
        "source_path": str(paper_dir),
    }


def get_main_paper_pdf(cfg: Config, paper_id: str) -> Path:
    """Return the local PDF path for one main-library paper."""
    paper_dir, _meta, _issues = _find_main_paper(cfg, paper_id, include_issues=False)
    pdf = find_pdf(paper_dir)
    if pdf is None:
        raise KeyError(paper_id)
    return pdf


def get_proceedings_paper_pdf(cfg: Config, paper_id: str) -> Path:
    """Return the local PDF path for one proceedings child paper."""
    _row, paper_dir, _meta = _find_proceedings_row(cfg, paper_id)
    pdf = find_pdf(paper_dir)
    if pdf is None:
        raise KeyError(paper_id)
    return pdf
