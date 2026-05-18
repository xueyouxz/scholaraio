"""External reference-manager import orchestration."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from scholaraio.core.config import Config
from scholaraio.core.log import ui as _base_ui
from scholaraio.services.ingest import identifiers, paths, steps
from scholaraio.services.ingest.types import InboxCtx, StepResult

ui = _base_ui


def _pipeline_attr(name: str, fallback):
    from scholaraio.services.ingest import pipeline as pipeline_mod

    return getattr(pipeline_mod, name, fallback)


def _ui(message: str = "") -> None:
    legacy_ui = _pipeline_attr("ui", _base_ui)
    if legacy_ui is not _base_ui:
        legacy_ui(message)
        return
    ui(message)


def import_external(
    records: list,
    cfg: Config,
    *,
    pdf_paths: list[Path | None] | None = None,
    no_api: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """从外部来源（Endnote 等）批量导入论文。

    对每条记录运行 dedup + ingest，最后一次性 embed + index。
    如提供 ``pdf_paths``（与 records 索引对齐），入库时自动复制
    PDF 到论文目录。

    Args:
        records: PaperMetadata 列表。
        cfg: 全局配置。
        pdf_paths: 与 records 对齐的 PDF 路径列表（可选）。
        no_api: 跳过 API 查询。
        dry_run: 预览模式。

    Returns:
        统计字典 ``{"ingested": N, "duplicate": N, "needs_review": N, "failed": N, "skipped": N}``。
    """
    papers_dir = cfg.papers_dir
    pending_dir = _pipeline_attr("_pending_dir", paths.pending_dir)(cfg)
    collect_existing_ids = _pipeline_attr("_collect_existing_ids", identifiers.collect_existing_ids)
    existing_dois, existing_pub_nums, existing_arxiv_ids = collect_existing_ids(papers_dir)

    opts: dict[str, Any] = {"dry_run": dry_run, "no_api": no_api}
    stats: dict[str, int] = {"ingested": 0, "duplicate": 0, "needs_review": 0, "failed": 0, "skipped": 0}
    ingested_jsons: list[Path] = []

    has_api = not no_api and not dry_run
    step_dedup = _pipeline_attr("step_dedup", None)
    step_ingest = _pipeline_attr("step_ingest", None)
    if step_dedup is None or step_ingest is None:
        from scholaraio.services.ingest import pipeline as pipeline_mod

        step_dedup = pipeline_mod.step_dedup
        step_ingest = pipeline_mod.step_ingest

    for idx, meta in enumerate(records):
        _ui(f"\n[{idx + 1}/{len(records)}] {meta.title[:60]}...")

        # Fast DOI dedup check before expensive API calls
        doi = meta.doi.lower().strip() if meta.doi else ""
        if doi and doi in existing_dois:
            _ui(f"Duplicate DOI, skipped: {meta.doi}")
            stats["duplicate"] += 1
            continue

        ctx = InboxCtx(
            pdf_path=None,
            inbox_dir=_pipeline_attr("_inbox_dir", paths.inbox_dir)(cfg),  # not actually used
            papers_dir=papers_dir,
            existing_dois=existing_dois,
            existing_pub_nums=existing_pub_nums,
            existing_arxiv_ids=existing_arxiv_ids,
            cfg=cfg,
            opts=opts,
            pending_dir=pending_dir,
            md_path=None,
            meta=meta,
        )

        # Run dedup (API enrich + DOI check)
        result = step_dedup(ctx)
        if result != StepResult.OK:
            final_status = ctx.status if ctx.status != "pending" else "skipped"
            stats[final_status] += 1
            if has_api and idx < len(records) - 1:
                time.sleep(1.0)
            continue

        # Run ingest
        result = step_ingest(ctx)
        final_status = ctx.status if ctx.status != "pending" else "skipped"
        stats[final_status] += 1
        if final_status == "ingested" and ctx.ingested_json:
            ingested_jsons.append(ctx.ingested_json)

            # Copy PDF to paper directory if available
            pdf_src = pdf_paths[idx] if pdf_paths and idx < len(pdf_paths) else None
            if pdf_src and not dry_run:
                from scholaraio.stores.papers import copy_pdf_to_paper_dir

                paper_d = ctx.ingested_json.parent
                dest_pdf = copy_pdf_to_paper_dir(pdf_src, paper_d)
                _ui(f"  PDF: {dest_pdf.name}")

        if has_api and idx < len(records) - 1:
            time.sleep(1.0)

    _ui(
        f"\nImport completed: {stats['ingested']} ingested | {stats['duplicate']} duplicates | "
        f"{stats['needs_review']} need review | {stats['failed']} failed"
    )

    # Batch embed + index
    if not dry_run and ingested_jsons:
        step_embed = _pipeline_attr("step_embed", steps.step_embed)
        step_index = _pipeline_attr("step_index", steps.step_index)
        step_embed(papers_dir, cfg, {"dry_run": False, "rebuild": False})
        step_index(papers_dir, cfg, {"dry_run": False, "rebuild": False})

    return stats
