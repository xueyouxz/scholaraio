"""Inbox-scoped ingest pipeline steps."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from scholaraio.core.log import ui as _base_ui
from scholaraio.services.ingest import assets, cleanup, detection, documents, identifiers, pending, registry
from scholaraio.services.ingest.types import InboxCtx, StepResult

_log = logging.getLogger(__name__)
ui = _base_ui


def _pipeline_attr(name: str, fallback):
    from scholaraio.services.ingest import pipeline as pipeline_mod

    return getattr(pipeline_mod, name, fallback)


def _log_error(message: str, *args) -> None:
    legacy_log = _pipeline_attr("_log", _log)
    legacy_log.error(message, *args)


def _log_debug(message: str, *args) -> None:
    legacy_log = _pipeline_attr("_log", _log)
    legacy_log.debug(message, *args)


def _log_warning(message: str, *args) -> None:
    legacy_log = _pipeline_attr("_log", _log)
    legacy_log.warning(message, *args)


def _ui(message: str = "") -> None:
    legacy_ui = _pipeline_attr("ui", _base_ui)
    if legacy_ui is not _base_ui:
        legacy_ui(message)
        return
    ui(message)


def step_mineru(ctx: InboxCtx) -> StepResult:
    """PDF → Markdown 转换（MinerU）。

    md-only 入库项（无 PDF）自动跳过。已有同名 ``.md`` 时也跳过。
    本地 MinerU 不可达时自动 fallback 到 MinerU 云端 CLI（需配置 ``mineru_api_key`` / token）。
    超长 PDF 会在需要时自动切分后逐段转换再合并。
    本地 MinerU 使用 ``chunk_page_limit``，云端 MinerU 当前按 200 页 / 200MB
    的有效限制做自动切分（官方网页仍可能写 600 页，但 `mineru-open-api`
    现网返回会在 200+ 页时报 ``-60006``）。

    Args:
        ctx: Inbox 上下文，转换后 ``ctx.md_path`` 指向生成的 ``.md``。

    Returns:
        ``StepResult.OK`` 成功, ``StepResult.FAIL`` 失败。
    """
    from scholaraio.providers.mineru import (
        ConvertOptions,
        ConvertResult,
        _convert_long_pdf,
        _convert_long_pdf_cloud,
        _get_pdf_page_count,
        _plan_cloud_chunking,
        check_server,
        convert_pdf,
        is_pdf_validation_error,
        validate_pdf_for_mineru,
    )
    from scholaraio.providers.pdf_fallback import (
        convert_pdf_with_fallback,
        preferred_parser_order,
        prefers_fallback_parser,
    )

    # md-only entry (no PDF): skip MinerU entirely
    if ctx.pdf_path is None:
        if ctx.md_path and (ctx.md_path.exists() or ctx.opts.get("dry_run")):
            _log_debug("no PDF, using existing .md: %s", ctx.md_path.name)
            return StepResult.OK
        _log_error("no PDF and no .md")
        ctx.status = "failed"
        return StepResult.FAIL

    pdf_path = ctx.pdf_path
    md_path = ctx.inbox_dir / (pdf_path.stem + ".md")

    if md_path.exists():
        _log_debug(".md exists, skipping MinerU: %s", md_path.name)
        ctx.md_path = md_path
        return StepResult.OK

    if ctx.opts.get("dry_run"):
        _log_debug("would convert: %s -> %s", pdf_path.name, md_path.name)
        ctx.md_path = md_path
        return StepResult.OK

    mineru_opts = ConvertOptions(
        api_url=ctx.cfg.ingest.mineru_endpoint,
        output_dir=ctx.inbox_dir,
        backend=ctx.cfg.ingest.mineru_backend_local,
        cloud_model_version=ctx.cfg.ingest.mineru_model_version_cloud,
        lang=ctx.cfg.ingest.mineru_lang,
        parse_method=ctx.cfg.ingest.mineru_parse_method,
        formula_enable=ctx.cfg.ingest.mineru_enable_formula,
        table_enable=ctx.cfg.ingest.mineru_enable_table,
        upload_workers=ctx.cfg.ingest.mineru_upload_workers,
        upload_retries=ctx.cfg.ingest.mineru_upload_retries,
        download_retries=ctx.cfg.ingest.mineru_download_retries,
        poll_timeout=ctx.cfg.ingest.mineru_poll_timeout,
    )

    result = None
    fallback_auto_detect = getattr(ctx.cfg.ingest, "pdf_fallback_auto_detect", True)
    fallback_order = preferred_parser_order(
        getattr(ctx.cfg.ingest, "pdf_preferred_parser", "mineru"),
        getattr(ctx.cfg.ingest, "pdf_fallback_order", None),
        auto_detect=fallback_auto_detect,
    )

    if prefers_fallback_parser(getattr(ctx.cfg.ingest, "pdf_preferred_parser", "mineru")):
        ok, parser_name, err = convert_pdf_with_fallback(
            pdf_path,
            md_path,
            parser_order=fallback_order,
            auto_detect=fallback_auto_detect,
        )
        if not ok:
            _log_error("preferred parser chain failed: %s", err)
            ctx.status = "failed"
            return StepResult.FAIL
        _ui(f"Used configured preferred parser: {parser_name}.")
        ctx.md_path = md_path
        return StepResult.OK

    local_mineru_available = check_server(ctx.cfg.ingest.mineru_endpoint)
    cloud_api_key = ""
    if not local_mineru_available:
        cloud_api_key = ctx.cfg.resolved_mineru_api_key()
        if not cloud_api_key:
            _log_warning("MinerU unreachable and no MinerU token, trying fallback parsers")
            ok, parser_name, fallback_err = convert_pdf_with_fallback(
                pdf_path,
                md_path,
                parser_order=fallback_order,
                auto_detect=fallback_auto_detect,
            )
            if not ok:
                _log_error("fallback parsers failed: %s", fallback_err)
                ctx.status = "failed"
                return StepResult.FAIL
            _ui(f"MinerU is unavailable; fell back to {parser_name}.")
            ctx.md_path = md_path
            return StepResult.OK

    validation = validate_pdf_for_mineru(pdf_path)
    if not validation.ok:
        _log_error("%s", validation.error or "PDF validation failed")
        ctx.status = "failed"
        return StepResult.FAIL

    local_chunk_limit = getattr(ctx.cfg.ingest, "chunk_page_limit", 100)
    cloud_chunk_size = 0
    cloud_chunk_reason = ""
    page_count = -1
    is_long = False
    if local_mineru_available:
        page_count = _get_pdf_page_count(pdf_path)
        is_long = page_count > local_chunk_limit
        if is_long:
            _ui(f"Detected a long PDF ({page_count} pages, over the {local_chunk_limit}-page limit); chunking...")
    else:
        is_long, cloud_chunk_size, cloud_chunk_reason = _plan_cloud_chunking(
            pdf_path,
            default_chunk_size=local_chunk_limit,
        )
        if is_long:
            _ui(f"Detected a PDF that needs cloud chunking ({cloud_chunk_reason}); chunking...")

    # Try local MinerU first, fallback to MinerU cloud CLI
    if local_mineru_available:
        if is_long:
            result = _convert_long_pdf(pdf_path, mineru_opts, chunk_size=local_chunk_limit)
        else:
            result = convert_pdf(pdf_path, mineru_opts)
    else:
        from scholaraio.providers.mineru import convert_pdf_cloud

        _log_debug("local MinerU unreachable, using MinerU cloud CLI")
        if is_long:
            try:
                result = _convert_long_pdf_cloud(
                    pdf_path,
                    mineru_opts,
                    api_key=cloud_api_key,
                    cloud_url=ctx.cfg.ingest.mineru_cloud_url,
                    chunk_size=cloud_chunk_size or local_chunk_limit,
                )
            except ImportError as exc:
                _log_warning("cloud split unavailable, trying fallback parsers: %s", exc)
                result = ConvertResult(pdf_path=pdf_path, success=False, error=str(exc))
            except Exception as exc:
                _log_warning("cloud split failed unexpectedly, trying fallback parsers: %s", exc)
                result = ConvertResult(pdf_path=pdf_path, success=False, error=str(exc))
        else:
            result = convert_pdf_cloud(
                pdf_path,
                mineru_opts,
                api_key=cloud_api_key,
                cloud_url=ctx.cfg.ingest.mineru_cloud_url,
            )

    if result is None or not result.success:
        mineru_err = result.error if result is not None else "MinerU unavailable"
        if is_pdf_validation_error(result):
            _log_error("%s", mineru_err)
            ctx.status = "failed"
            return StepResult.FAIL
        _log_warning("MinerU failed, trying fallback parsers: %s", mineru_err)
        ok, parser_name, fallback_err = convert_pdf_with_fallback(
            pdf_path,
            md_path,
            parser_order=fallback_order,
            auto_detect=fallback_auto_detect,
        )
        if not ok:
            _log_error("fallback parsers failed: %s", fallback_err)
            ctx.status = "failed"
            return StepResult.FAIL
        _ui(f"MinerU is unavailable; fell back to {parser_name}.")
        ctx.md_path = md_path
        return StepResult.OK

    ctx.md_path = result.md_path or md_path
    return StepResult.OK


def step_office_convert(ctx: InboxCtx) -> StepResult:
    """Office 文档（DOCX / XLSX / PPTX）→ Markdown 转换（MarkItDown）。

    仅当 ``ctx.opts["office_path"]`` 存在时执行（由 ``_process_inbox`` 在扫描
    Office 文件时注入；非 Office 文件入口时 ``office_path`` 不存在，步骤直接跳过）。
    已有同名 ``.md`` 时跳过转换并直接使用已有文件。

    Args:
        ctx: Inbox 上下文，转换后 ``ctx.md_path`` 指向生成的 ``.md``。

    Returns:
        ``StepResult.OK`` 成功, ``StepResult.FAIL`` 失败。
    """
    office_path: Path | None = ctx.opts.get("office_path")
    if office_path is None:
        # Not an office file entry - skip this step
        return StepResult.OK

    md_path = ctx.inbox_dir / (office_path.stem + ".md")

    if md_path.exists():
        _log_debug(".md exists, skipping office convert: %s", md_path.name)
        ctx.md_path = md_path
        return StepResult.OK

    if ctx.opts.get("dry_run"):
        _log_debug("would convert office: %s -> %s", office_path.name, md_path.name)
        ctx.md_path = md_path
        return StepResult.OK

    try:
        from markitdown import MarkItDown
    except ImportError:
        _log_error("MarkItDown is not installed; cannot convert Office files. Run: pip install scholaraio[office]")
        ctx.status = "failed"
        return StepResult.FAIL

    try:
        md_obj = MarkItDown()
        result = md_obj.convert(str(office_path))
        md_text = result.text_content or ""
        if not md_text.strip():
            _log.warning("Office file content is empty: %s", office_path.name)
        md_path.write_text(md_text, encoding="utf-8")
        ctx.md_path = md_path
        _log_debug("office convert OK: %s -> %s", office_path.name, md_path.name)
        return StepResult.OK
    except Exception as exc:
        _log_error("MarkItDown conversion failed for %s: %s", office_path.name, exc)
        ctx.status = "failed"
        return StepResult.FAIL


def step_extract_doc(ctx: InboxCtx) -> StepResult:
    """从非论文文档提取/生成元数据（LLM 生成标题和摘要）。

    对于缺少标题/摘要的普通文档，使用 LLM 从全文生成，确保检索可用。

    Args:
        ctx: Inbox 上下文，需要 ``ctx.md_path`` 已设置。

    Returns:
        ``StepResult.OK`` 成功, ``StepResult.FAIL`` 失败。
    """
    if ctx.opts.get("dry_run"):
        _log_debug("would extract document metadata from: %s", ctx.md_path.name if ctx.md_path else "?")
        return StepResult.OK

    if not ctx.md_path or not ctx.md_path.exists():
        _log_error("extract_doc failed: no .md file")
        ctx.status = "failed"
        return StepResult.FAIL

    from scholaraio.services.ingest_metadata._doc_extract import extract_document_metadata

    try:
        load_doc_sidecar_metadata = _pipeline_attr(
            "_load_doc_sidecar_metadata",
            documents.load_doc_sidecar_metadata,
        )
        existing_meta = load_doc_sidecar_metadata(ctx.md_path)
        meta = extract_document_metadata(ctx.md_path, ctx.cfg, existing_meta=existing_meta)
    except Exception as e:
        _log_error("document extraction failed: %s", e)
        ctx.status = "failed"
        return StepResult.FAIL

    if not (meta.title or "").strip():
        _log_error("cannot determine document title")
        ctx.status = "failed"
        return StepResult.FAIL

    meta.paper_type = meta.paper_type or "document"
    ctx.meta = meta
    _ui(f"Title: {meta.title[:80]}")
    _ui(f"Type: {meta.paper_type} | Author: {meta.first_author or '?'} | Year: {meta.year or '?'}")
    return StepResult.OK


def step_extract(ctx: InboxCtx) -> StepResult:
    """从 Markdown 头部提取论文元数据。

    使用配置指定的提取器（regex/auto/robust/llm），结果存入 ``ctx.meta``。

    Args:
        ctx: Inbox 上下文，需要 ``ctx.md_path`` 已设置。

    Returns:
        ``StepResult.OK`` 成功, ``StepResult.FAIL`` 失败。
    """
    from scholaraio.services.ingest_metadata.extractor import get_extractor

    if ctx.opts.get("dry_run"):
        _log_debug("would extract metadata from: %s", ctx.md_path.name if ctx.md_path else "?")
        return StepResult.OK

    if not ctx.md_path or not ctx.md_path.exists():
        _log_error("extract failed: no .md file")
        ctx.status = "failed"
        return StepResult.FAIL

    extractor = get_extractor(ctx.cfg)
    meta = extractor.extract(ctx.md_path)
    _ui(f"Title: {(meta.title or '?')[:80]}")
    doi_or_arxiv = meta.doi or (f"arXiv:{meta.arxiv_id}" if meta.arxiv_id else "none")
    _ui(f"Author: {meta.first_author_lastname or '?'} | Year: {meta.year or '?'} | ID: {doi_or_arxiv}")
    ctx.meta = meta
    return StepResult.OK


def step_dedup(ctx: InboxCtx) -> StepResult:
    """API 查询补全 + DOI / 公开号去重检查。

    1. 调用 Crossref / S2 / OpenAlex API 补全元数据
    2. 有 DOI 时检查是否与已入库论文重复
    3. thesis inbox 标记直接放行
    4. patent inbox 标记 paper_type=patent，按公开号去重；无公开号转 pending
    5. 无 DOI 时：检测是否为专利（文本中含公开号），是则按公开号去重并入库
    6. 无 DOI 且非 thesis/patent/book 才转入 configured pending spool

    Args:
        ctx: Inbox 上下文，需要 ``ctx.meta`` 已设置。

    Returns:
        ``StepResult.OK`` 通过, ``StepResult.FAIL`` 重复/无 DOI。
    """
    from scholaraio.services.ingest_metadata import enrich_metadata

    if ctx.opts.get("dry_run"):
        _log_debug("would check dedup and query APIs")
        return StepResult.OK

    if ctx.meta is None:
        _log_error("dedup failed: no metadata")
        ctx.status = "failed"
        return StepResult.FAIL

    move_to_pending = _pipeline_attr("_move_to_pending", pending.move_to_pending)
    detect_patent = _pipeline_attr("_detect_patent", detection.detect_patent)
    detect_thesis = _pipeline_attr("_detect_thesis", detection.detect_thesis)
    detect_book = _pipeline_attr("_detect_book", detection.detect_book)
    normalize_arxiv_id = _pipeline_attr("_normalize_arxiv_id", identifiers.normalize_arxiv_id)

    # Thesis inbox: set paper_type, skip API query and DOI dedup
    if ctx.is_thesis:
        ctx.meta.paper_type = "thesis"
        _log_debug("thesis inbox, skipping API and dedup")
        _ui(f"Thesis: {ctx.meta.title or '?'}")
        return StepResult.OK

    # Patent inbox: set paper_type, skip API query, use publication_number for dedup
    if ctx.is_patent:
        ctx.meta.paper_type = "patent"
        _log_debug("patent inbox, skipping API query")
        _ui(f"Patent: {ctx.meta.title or '?'}")
        # Patent publication number dedup
        pub_num = (ctx.meta.publication_number or "").upper().strip()
        if not pub_num:
            _log_warning("patent inbox but no publication number extracted: %s", ctx.meta.title or "?")
            move_to_pending(
                ctx,
                issue="no_pub_num",
                message="Patent inbox item has no extracted publication number; manual review required",
            )
            ctx.status = "needs_review"
            return StepResult.FAIL
        if ctx.existing_pub_nums and pub_num in ctx.existing_pub_nums:
            existing_json = ctx.existing_pub_nums[pub_num]
            _log_debug("duplicate patent: %s -> %s", pub_num, existing_json.parent.name)
            move_to_pending(
                ctx,
                issue="duplicate",
                message="Patent publication number duplicates an ingested patent",
                extra={"duplicate_of": existing_json.parent.name, "publication_number": pub_num},
            )
            ctx.status = "duplicate"
            return StepResult.FAIL
        return StepResult.OK

    # API query
    if not ctx.opts.get("no_api"):
        _log_debug("querying APIs")
        ctx.meta = enrich_metadata(ctx.meta)
        _ui(f"DOI (after API): {ctx.meta.doi or 'none'}")
    else:
        ctx.meta.extraction_method = "local_only"
        _log_debug("skipping API query (offline mode)")

    # DOI dedup (guard against LLM returning "null"/"None" strings)
    doi = ctx.meta.doi
    if doi and doi.strip().lower() in ("null", "none", "n/a"):
        ctx.meta.doi = ""
        doi = ""
    arxiv_key = normalize_arxiv_id(ctx.meta.arxiv_id)
    if arxiv_key and ctx.existing_arxiv_ids and arxiv_key in ctx.existing_arxiv_ids:
        existing_json = ctx.existing_arxiv_ids[arxiv_key]
        move_to_pending(
            ctx,
            issue="duplicate",
            message="arXiv preprint duplicates an ingested paper",
            extra={"duplicate_of": existing_json.parent.name, "arxiv_id": arxiv_key},
        )
        ctx.status = "duplicate"
        return StepResult.FAIL
    if not doi or not doi.strip():
        # No DOI -> check if patent (by publication number or detection)
        if detect_patent(ctx):
            ctx.meta.paper_type = "patent"
            ctx.is_patent = True
            pub_num = (ctx.meta.publication_number or "").upper().strip()
            if not pub_num:
                # Patent detected but no publication number - needs manual review
                move_to_pending(
                    ctx,
                    issue="no_pub_num",
                    message="Detected as patent but no publication number was extracted; manual review required",
                )
                ctx.status = "needs_review"
                return StepResult.FAIL
            if ctx.existing_pub_nums and pub_num in ctx.existing_pub_nums:
                existing_json = ctx.existing_pub_nums[pub_num]
                move_to_pending(
                    ctx,
                    issue="duplicate",
                    message="Patent publication number duplicates an ingested patent",
                    extra={"duplicate_of": existing_json.parent.name, "publication_number": pub_num},
                )
                ctx.status = "duplicate"
                return StepResult.FAIL
            _ui("Detected patent; ingesting directly without DOI")
            return StepResult.OK
        # No DOI -> LLM thesis detection
        if detect_thesis(ctx):
            ctx.meta.paper_type = "thesis"
            ctx.is_thesis = True
            _ui("Detected thesis; ingesting directly without DOI")
            return StepResult.OK
        # No DOI -> LLM book detection
        if detect_book(ctx):
            ctx.meta.paper_type = "book"
            _ui("Detected book; ingesting directly without DOI")
            return StepResult.OK
        # No DOI -> check arXiv preprint (has arXiv ID from extraction or API)
        if ctx.meta.arxiv_id:
            if not ctx.meta.paper_type:
                ctx.meta.paper_type = "preprint"
            _ui(f"Detected arXiv preprint ({ctx.meta.arxiv_id}); ingesting directly without DOI")
            return StepResult.OK
        # Not thesis/book/patent/arXiv -> move to pending
        _log_debug("no DOI and not thesis/book/patent/arXiv, moving to pending")
        move_to_pending(ctx)
        ctx.status = "needs_review"
        return StepResult.FAIL

    doi_key = ctx.meta.doi.lower().strip()
    if doi_key in ctx.existing_dois:
        existing_json = ctx.existing_dois[doi_key]
        existing_md = existing_json.parent / "paper.md"
        if not existing_md.exists() and ctx.md_path and ctx.md_path.exists():
            existing_dir = existing_json.parent
            if not existing_dir.exists():
                # Stale registry entry (dir was deleted or renamed): treat as new paper
                _log_warning("stale registry entry for DOI %s, dir missing: %s", doi_key, existing_dir)
                return StepResult.OK
            # MD missing from existing paper: restore it automatically
            pdf_stem = ctx.pdf_path.stem if ctx.pdf_path else ""
            md_stem = ctx.md_path.stem if ctx.md_path else ""
            shutil.move(str(ctx.md_path), str(existing_md))
            _log_debug("duplicate (MD missing, restored): %s", existing_md.name)
            repair_abstract = _pipeline_attr("_repair_abstract", documents.repair_abstract)
            cleanup_inbox = _pipeline_attr("_cleanup_inbox", cleanup.cleanup_inbox)
            cleanup_assets = _pipeline_attr("_cleanup_assets", assets.cleanup_assets)
            repair_abstract(existing_json, existing_md, ctx.cfg)
            if ctx.pdf_path and ctx.pdf_path.exists():
                from scholaraio.stores.papers import find_pdf, move_pdf_to_paper_dir

                if find_pdf(existing_dir):
                    move_to_pending(
                        ctx,
                        issue="duplicate",
                        message="DOI duplicates an ingested paper that already has a PDF; review the incoming PDF manually",
                        extra={"duplicate_of": existing_json.parent.name, "doi": doi_key},
                    )
                else:
                    move_pdf_to_paper_dir(ctx.pdf_path, existing_dir)
            cleanup_inbox(ctx.pdf_path, None, dry_run=False)
            cleanup_assets(ctx.inbox_dir, pdf_stem, md_stem)
        else:
            # Normal duplicate: move to pending for user review
            _log_debug("duplicate: DOI %s exists -> %s", ctx.meta.doi, existing_json.parent.name)
            move_to_pending(
                ctx,
                issue="duplicate",
                message="DOI duplicates an ingested paper; handle manually if you need to overwrite it",
                extra={"duplicate_of": existing_json.parent.name, "doi": doi_key},
            )
        ctx.status = "duplicate"
        return StepResult.FAIL

    return StepResult.OK


def step_ingest(ctx: InboxCtx) -> StepResult:
    """将论文正式写入 configured papers library。

    生成标准化文件名 ``{LastName}-{year}-{Title}``，
    写入 JSON 元数据，移动 ``.md`` 文件，清理 inbox。

    Args:
        ctx: Inbox 上下文，需要 ``ctx.meta`` 和 ``ctx.md_path`` 已设置。

    Returns:
        ``StepResult.OK`` 成功, ``StepResult.FAIL`` 失败。
    """
    from scholaraio.services.ingest_metadata import generate_new_stem, write_metadata_json
    from scholaraio.stores.papers import generate_uuid

    if ctx.opts.get("dry_run"):
        _log_debug("would ingest paper to papers_dir")
        ctx.status = "ingested"
        return StepResult.OK

    if ctx.meta is None:
        _log_error("ingest failed: missing meta")
        ctx.status = "failed"
        return StepResult.FAIL

    if not (ctx.meta.title or "").strip() and not (ctx.meta.abstract or "").strip():
        _log_error("ingest failed: no title and no abstract")
        _ui("Skipped: no title or abstract; cannot ingest")
        ctx.status = "failed"
        return StepResult.FAIL

    # Abstract fallback: extract from MD when API didn't return one
    if not ctx.meta.abstract and ctx.md_path and ctx.md_path.exists():
        from scholaraio.services.ingest_metadata import extract_abstract_from_md

        abstract = extract_abstract_from_md(ctx.md_path, ctx.cfg)
        if abstract:
            ctx.meta.abstract = abstract
            _log_debug("abstract backfilled from MD (%d chars)", len(abstract))

    papers_dir = ctx.papers_dir
    papers_dir.mkdir(parents=True, exist_ok=True)
    new_stem = generate_new_stem(ctx.meta)

    # Assign UUID
    ctx.meta.id = generate_uuid()

    # Create per-paper directory
    paper_d = papers_dir / new_stem
    suffix = 2
    while paper_d.exists():
        paper_d = papers_dir / f"{new_stem}-{suffix}"
        suffix += 1

    paper_d.mkdir(parents=True)
    new_json = paper_d / "meta.json"

    write_metadata_json(ctx.meta, new_json)

    if ctx.md_path and ctx.md_path.exists():
        new_md = paper_d / "paper.md"
        shutil.move(str(ctx.md_path), str(new_md))
        if ctx.pdf_path and ctx.pdf_path.exists():
            from scholaraio.stores.papers import move_pdf_to_paper_dir

            move_pdf_to_paper_dir(ctx.pdf_path, paper_d)
        # Move MinerU assets (images, layout.json, etc.) if present
        md_stem = ctx.md_path.stem if ctx.md_path else ""
        pdf_stem = ctx.pdf_path.stem if ctx.pdf_path else ""
        move_assets = _pipeline_attr("_move_assets", assets.move_assets)
        move_assets(ctx.inbox_dir, paper_d, pdf_stem or md_stem, md_stem)
        _ui(f"Ingested: {paper_d.name}/")
        _ui("  meta.json + paper.md" + (" + PDF" if ctx.pdf_path else ""))
    else:
        _ui(f"Ingested (metadata only): {paper_d.name}/")
        _ui("  meta.json")

    if ctx.meta.doi and ctx.meta.doi.strip():
        ctx.existing_dois[ctx.meta.doi.lower().strip()] = new_json
    if ctx.meta.publication_number and ctx.meta.publication_number.strip():
        if ctx.existing_pub_nums is not None:
            ctx.existing_pub_nums[ctx.meta.publication_number.upper().strip()] = new_json
    normalize_arxiv_id = _pipeline_attr("_normalize_arxiv_id", identifiers.normalize_arxiv_id)
    arxiv_key = normalize_arxiv_id(ctx.meta.arxiv_id)
    if arxiv_key and ctx.existing_arxiv_ids is not None:
        ctx.existing_arxiv_ids[arxiv_key] = new_json

    # Update papers_registry immediately so UUID lookup works before rebuild
    update_registry = _pipeline_attr("_update_registry", registry.update_registry)
    update_registry(ctx.cfg, ctx.meta, paper_d.name)

    cleanup_inbox = _pipeline_attr("_cleanup_inbox", cleanup.cleanup_inbox)
    cleanup_inbox(ctx.pdf_path, None, dry_run=False)
    # Clean up original Office source file (DOCX/XLSX/PPTX) if present
    office_src: Path | None = ctx.opts.get("office_path")
    if office_src and office_src.exists():
        try:
            office_src.unlink()
            _log_debug("deleted office source: %s", office_src.name)
        except OSError as exc:
            _log_warning("could not delete office source %s: %s", office_src.name, exc)
    ctx.ingested_json = new_json
    ctx.status = "ingested"
    return StepResult.OK
