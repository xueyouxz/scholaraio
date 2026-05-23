"""Line-addressable paper chunk extraction and keyword search."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from scholaraio.stores.papers import iter_paper_dirs, parse_year_range, read_meta


@dataclass(frozen=True)
class PaperChunk:
    chunk_id: str
    paper_id: str
    title: str
    year: str
    journal: str
    paper_type: str
    section_title: str
    seq: int
    start_line: int
    end_line: int
    text: str
    context_text: str
    content_hash: str


_CHUNKS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS paper_chunks USING fts5(
    chunk_id      UNINDEXED,
    paper_id      UNINDEXED,
    dir_name      UNINDEXED,
    title,
    year          UNINDEXED,
    journal       UNINDEXED,
    paper_type    UNINDEXED,
    section_title,
    context_text,
    text,
    start_line    UNINDEXED,
    end_line      UNINDEXED,
    content_hash  UNINDEXED,
    tokenize      = 'unicode61'
);
"""

_CHUNK_HASH_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_chunks_hash (
    paper_id     TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL
);
"""

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")
_REQUIRED_CHUNK_COLUMNS = {
    "chunk_id",
    "paper_id",
    "dir_name",
    "title",
    "year",
    "journal",
    "paper_type",
    "section_title",
    "context_text",
    "text",
    "start_line",
    "end_line",
    "content_hash",
}


def iter_paper_chunks(paper_dir: Path, *, target_chars: int = 4800, meta: dict | None = None) -> list[PaperChunk]:
    """Return searchable chunks for one paper directory."""

    if meta is None:
        meta = read_meta(paper_dir)
    md_path = paper_dir / "paper.md"
    if not md_path.exists():
        return []

    lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    paper_id = meta.get("id") or paper_dir.name
    title = meta.get("title") or paper_dir.name
    year = str(meta.get("year") or "")
    journal = str(meta.get("journal") or "")
    paper_type = str(meta.get("paper_type") or "")
    sections = _sections_from_toc(meta, lines) or _sections_from_headings(title, lines)

    chunks: list[PaperChunk] = []
    seq = 0
    for section_title, start_line, end_line in sections:
        section_lines = lines[start_line - 1 : end_line]
        blocks = _paragraph_blocks(section_lines, start_line)
        for chunk_start, chunk_end, text in _split_blocks(blocks, target_chars=target_chars):
            text = text.strip()
            if not text:
                continue
            seq += 1
            context_text = f"Paper: {title}\nSection: {section_title}"
            content_hash = _hash_text("\n".join([paper_id, section_title, str(chunk_start), str(chunk_end), text]))
            chunks.append(
                PaperChunk(
                    chunk_id=f"{paper_id}:{seq:05d}",
                    paper_id=paper_id,
                    title=title,
                    year=year,
                    journal=journal,
                    paper_type=paper_type,
                    section_title=section_title,
                    seq=seq,
                    start_line=chunk_start,
                    end_line=chunk_end,
                    text=text,
                    context_text=context_text,
                    content_hash=content_hash,
                )
            )
    return chunks


def build_chunk_index(papers_dir: Path, db_path: Path, rebuild: bool = False) -> int:
    """Build or update the FTS5 paper chunk index."""

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        schema_recreated = _ensure_chunk_schema(conn, recreate_stale=True)
        if rebuild:
            conn.execute("DELETE FROM paper_chunks")
            conn.execute("DELETE FROM paper_chunks_hash")

        existing_hashes = {}
        if not rebuild and not schema_recreated:
            existing_hashes = dict(conn.execute("SELECT paper_id, content_hash FROM paper_chunks_hash").fetchall())

        indexed = 0
        seen_paper_ids: set[str] = set()
        for paper_dir in iter_paper_dirs(papers_dir):
            try:
                meta = read_meta(paper_dir)
            except (FileNotFoundError, ValueError):
                continue
            paper_id = meta.get("id") or paper_dir.name
            seen_paper_ids.add(paper_id)
            chunks = iter_paper_chunks(paper_dir, meta=meta)
            paper_hash = _paper_chunks_hash(chunks, dir_name=paper_dir.name)
            if not rebuild and existing_hashes.get(paper_id) == paper_hash:
                continue

            conn.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO paper_chunks
                        (chunk_id, paper_id, dir_name, title, year, journal, paper_type,
                         section_title, context_text,
                         text, start_line, end_line, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.paper_id,
                        paper_dir.name,
                        chunk.title,
                        chunk.year,
                        chunk.journal,
                        chunk.paper_type,
                        chunk.section_title,
                        chunk.context_text,
                        chunk.text,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content_hash,
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO paper_chunks_hash (paper_id, content_hash) VALUES (?, ?)",
                (paper_id, paper_hash),
            )
            indexed += len(chunks)

        stale_ids = set(existing_hashes) - seen_paper_ids
        for paper_id in stale_ids:
            conn.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM paper_chunks_hash WHERE paper_id = ?", (paper_id,))

        conn.commit()
        return indexed
    finally:
        conn.close()


def chunk_search(
    query: str,
    db_path: Path,
    top_k: int = 20,
    *,
    year: str | None = None,
    journal: str | None = None,
    paper_type: str | None = None,
) -> list[dict]:
    """Search indexed paper chunks and return line-addressable snippets."""

    if not db_path.exists():
        raise FileNotFoundError(f"Index file does not exist: {db_path}\nRun `scholaraio index --chunks` first")

    conn = sqlite3.connect(db_path)
    try:
        _ensure_chunk_schema(conn)
        conn.row_factory = sqlite3.Row
        filter_sql, filter_params = _build_filter_clause(year=year, journal=journal, paper_type=paper_type)
        rows = conn.execute(
            f"""
            SELECT chunk_id, paper_id, dir_name, title, year, journal, paper_type,
                   section_title, context_text,
                   text, start_line, end_line,
                   snippet(paper_chunks, 9, '', '', ' ... ', 16) AS snippet
            FROM paper_chunks
            WHERE paper_chunks MATCH ?{filter_sql}
            ORDER BY rank
            LIMIT ?
            """,
            [_safe_query(query), *filter_params, top_k],
        ).fetchall()
        return [_row_to_result(row) for row in rows]
    finally:
        conn.close()


def _sections_from_toc(meta: dict, lines: list[str]) -> list[tuple[str, int, int]]:
    toc = meta.get("toc") or []
    entries: list[tuple[int, str]] = []
    for entry in toc:
        if not isinstance(entry, dict) or not isinstance(entry.get("line"), int):
            continue
        line = int(entry["line"])
        if not 1 <= line <= len(lines):
            continue
        title = str(entry.get("title") or "Untitled section").strip() or "Untitled section"
        entries.append((line, title))
    entries.sort(key=lambda item: item[0])

    sections = []
    for idx, (start, title) in enumerate(entries):
        end = entries[idx + 1][0] - 1 if idx + 1 < len(entries) else len(lines)
        if start <= end:
            sections.append((title, start, end))
    return sections


def _sections_from_headings(title: str, lines: list[str]) -> list[tuple[str, int, int]]:
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        match = _MARKDOWN_HEADING_RE.match(line)
        if match:
            headings.append((idx, match.group(2).strip()))

    if len(headings) > 1 and _normalized_heading(headings[0][1]) == _normalized_heading(title):
        headings = headings[1:]

    if not headings:
        return [("Full text", 1, len(lines))] if lines else []

    sections = []
    for idx, (start, heading_title) in enumerate(headings):
        end = headings[idx + 1][0] - 1 if idx + 1 < len(headings) else len(lines)
        sections.append((heading_title, start, end))
    return sections


def _paragraph_blocks(lines: list[str], base_line: int) -> list[tuple[int, int, str]]:
    blocks = []
    start_line: int | None = None
    current: list[str] = []
    end_line = base_line

    for offset, line in enumerate(lines):
        line_no = base_line + offset
        if line.strip():
            if start_line is None:
                start_line = line_no
            current.append(line)
            end_line = line_no
            continue

        if current and start_line is not None:
            blocks.append((start_line, end_line, "\n".join(current)))
        start_line = None
        current = []

    if current and start_line is not None:
        blocks.append((start_line, end_line, "\n".join(current)))
    return blocks


def _split_blocks(blocks: list[tuple[int, int, str]], *, target_chars: int) -> list[tuple[int, int, str]]:
    chunks = []
    current: list[tuple[int, int, str]] = []
    current_len = 0
    target_chars = max(80, target_chars)

    for block in blocks:
        start, end, text = block
        if len(text) > target_chars and not current:
            chunks.extend(_split_oversized_block(start, end, text, target_chars=target_chars))
            continue

        extra = len(text) + (2 if current else 0)
        if current and current_len + extra > target_chars:
            chunks.append(_merge_blocks(current))
            current = []
            current_len = 0

        current.append((start, end, text))
        current_len += len(text) + (2 if current_len else 0)

    if current:
        chunks.append(_merge_blocks(current))
    return chunks


def _split_oversized_block(
    start_line: int, end_line: int, text: str, *, target_chars: int
) -> list[tuple[int, int, str]]:
    sentences = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(text) if part.strip()]
    if len(sentences) <= 1:
        return [(start_line, end_line, text)]

    chunks = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > target_chars:
            chunks.append((start_line, end_line, " ".join(current)))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence) + 1
    if current:
        chunks.append((start_line, end_line, " ".join(current)))
    return chunks


def _merge_blocks(blocks: list[tuple[int, int, str]]) -> tuple[int, int, str]:
    return blocks[0][0], blocks[-1][1], "\n\n".join(block[2] for block in blocks)


def _paper_chunks_hash(chunks: list[PaperChunk], *, dir_name: str) -> str:
    payload = [
        {
            "chunk_id": chunk.chunk_id,
            "paper_id": chunk.paper_id,
            "dir_name": dir_name,
            "title": chunk.title,
            "year": chunk.year,
            "journal": chunk.journal,
            "paper_type": chunk.paper_type,
            "section_title": chunk.section_title,
            "context_text": chunk.context_text,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "hash": chunk.content_hash,
        }
        for chunk in chunks
    ]
    return _hash_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure_chunk_schema(conn: sqlite3.Connection, *, recreate_stale: bool = False) -> bool:
    has_table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_chunks'").fetchone()
    if has_table:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_chunks)").fetchall()}
        missing = _REQUIRED_CHUNK_COLUMNS - columns
        if missing:
            if not recreate_stale:
                raise FileNotFoundError("Chunk index schema is stale; run `scholaraio index --chunks --rebuild` first")
            conn.execute("DROP TABLE paper_chunks")
            conn.execute("DROP TABLE IF EXISTS paper_chunks_hash")
            conn.execute(_CHUNKS_SCHEMA)
            conn.execute(_CHUNK_HASH_SCHEMA)
            return True
        conn.execute(_CHUNK_HASH_SCHEMA)
        return False

    if not recreate_stale:
        raise FileNotFoundError("Chunk index table does not exist; run `scholaraio index --chunks` first")

    conn.execute(_CHUNKS_SCHEMA)
    conn.execute(_CHUNK_HASH_SCHEMA)
    return True


def _parse_year_filter(year: str) -> tuple[str, list[str]]:
    start, end = parse_year_range(year)
    if start is not None and end is not None:
        if start == end:
            return "year = ?", [str(start)]
        return "year >= ? AND year <= ?", [str(start), str(end)]
    if start is not None:
        return "year >= ?", [str(start)]
    if end is not None:
        return "year <= ?", [str(end)]
    return "1=1", []


def _build_filter_clause(
    year: str | None = None,
    journal: str | None = None,
    paper_type: str | None = None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if year:
        clause, year_params = _parse_year_filter(year)
        clauses.append(clause)
        params.extend(year_params)
    if journal:
        clauses.append("journal LIKE ?")
        params.append(f"%{journal}%")
    if paper_type:
        clauses.append("paper_type LIKE ?")
        params.append(f"%{paper_type}%")
    return "".join(f" AND {clause}" for clause in clauses), params


def _safe_query(query: str) -> str:
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    return " ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)


def _row_to_result(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["start_line"] = int(result["start_line"])
    result["end_line"] = int(result["end_line"])
    if not result.get("snippet"):
        result["snippet"] = _short_snippet(result.get("text") or "")
    return result


def _short_snippet(text: str, max_chars: int = 240) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _normalized_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())
