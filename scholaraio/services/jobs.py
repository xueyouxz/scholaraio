"""In-process job runner for long-running library tasks."""

from __future__ import annotations

import datetime as _dt
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scholaraio.core.config import Config

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
DEFAULT_MAX_EVENTS = 200


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


class JobValidationError(ValueError):
    """Raised when a job request is not valid."""


class JobNotFoundError(KeyError):
    """Raised when a job id is unknown to this runner."""


class JobCancelled(Exception):
    """Raised by a handler that honors cancellation."""


@dataclass
class JobEvent:
    seq: int
    timestamp: str
    level: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
        }


@dataclass
class Job:
    id: str
    kind: str
    title: str
    params: dict[str, Any]
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[JobEvent] = field(default_factory=list)
    future: Future | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "cancelRequested": self.cancel_requested,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "lastEventSeq": self.events[-1].seq if self.events else 0,
        }
        if include_events:
            data["events"] = [event.to_dict() for event in self.events]
        return data


@dataclass
class JobContext:
    job_id: str
    kind: str
    cfg: Config
    params: dict[str, Any]
    cancel_event: threading.Event
    _emit: Callable[[str, str], None]

    def emit(self, message: str, *, level: str = "info") -> None:
        self._emit(level, message)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()


JobHandler = Callable[[JobContext], dict[str, Any] | None]


class JobRunner:
    """Run long-lived library jobs behind a small thread-safe interface."""

    def __init__(
        self,
        *,
        max_workers: int = 1,
        max_jobs: int = 100,
        max_events: int = DEFAULT_MAX_EVENTS,
        handlers: dict[str, JobHandler] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scholaraio-job")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._handlers = handlers or default_handlers()
        self._max_jobs = max_jobs
        self._max_events = max_events

    def submit(self, kind: str, cfg: Config, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        kind = _normalize_kind(kind)
        handler_key = self._resolve_handler(kind, params)
        if handler_key == "pipeline":
            _pipeline_steps(params)
        title = _job_title(kind, params)
        job = Job(
            id=uuid.uuid4().hex,
            kind=kind,
            title=title,
            params=_json_safe_params(params),
        )

        with self._lock:
            self._jobs[job.id] = job
            self._add_event_locked(job, "info", "Job queued")
            self._trim_jobs_locked()
            job.future = self._executor.submit(self._run, job.id, handler_key, cfg, params)
            return job.to_dict(include_events=True)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
            return [job.to_dict(include_events=False) for job in reversed(jobs)]

    def get(self, job_id: str, *, include_events: bool = True) -> dict[str, Any]:
        with self._lock:
            job = self._require_job_locked(job_id)
            return job.to_dict(include_events=include_events)

    def events(self, job_id: str, *, after: int = 0) -> dict[str, Any]:
        with self._lock:
            job = self._require_job_locked(job_id)
            events = [event.to_dict() for event in job.events if event.seq > after]
            return {
                "jobId": job.id,
                "status": job.status,
                "events": events,
                "nextSeq": job.events[-1].seq if job.events else after,
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_job_locked(job_id)
            if job.status in TERMINAL_STATUSES:
                return job.to_dict(include_events=True)
            job.cancel_requested = True
            job.cancel_event.set()
            if job.status == "queued":
                if job.future:
                    job.future.cancel()
                job.status = "cancelled"
                job.finished_at = _now()
                self._add_event_locked(job, "warning", "Job cancelled before start")
            else:
                self._add_event_locked(job, "warning", "Cancellation requested")
            return job.to_dict(include_events=True)

    def available_kinds(self) -> list[str]:
        kinds = set(self._handlers)
        if "pipeline" in self._handlers:
            try:
                from scholaraio.services.ingest.pipeline import PRESETS

                kinds.update(PRESETS)
            except ImportError:
                pass
        return sorted(kinds)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run(self, job_id: str, handler_key: str, cfg: Config, params: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status == "cancelled":
                return
            job.status = "running"
            job.started_at = _now()
            self._add_event_locked(job, "info", "Job started")

        ctx = JobContext(
            job_id=job_id,
            kind=job.kind,
            cfg=cfg,
            params=params,
            cancel_event=job.cancel_event,
            _emit=lambda level, message: self._add_event(job_id, level, message),
        )

        try:
            ctx.check_cancelled()
            result = self._handlers[handler_key](ctx) or {}
        except JobCancelled:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "cancelled"
                job.finished_at = _now()
                self._add_event_locked(job, "warning", "Job cancelled")
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
                job.finished_at = _now()
                self._add_event_locked(job, "error", f"Job failed: {exc}")
        else:
            with self._lock:
                job = self._jobs[job_id]
                if job.status != "cancelled":
                    job.status = "succeeded"
                    job.result = result
                    job.finished_at = _now()
                    self._add_event_locked(job, "info", "Job succeeded")

    def _resolve_handler(self, kind: str, params: dict[str, Any]) -> str:
        if kind in self._handlers:
            return kind
        if "pipeline" in self._handlers:
            try:
                from scholaraio.services.ingest.pipeline import PRESETS
            except ImportError as exc:
                raise JobValidationError("Pipeline jobs are not available") from exc
            if kind in PRESETS:
                params.setdefault("preset", kind)
                return "pipeline"
        raise JobValidationError(f"Unknown job kind: {kind}")

    def _add_event(self, job_id: str, level: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                self._add_event_locked(job, level, message)

    def _add_event_locked(self, job: Job, level: str, message: str) -> None:
        seq = job.events[-1].seq + 1 if job.events else 1
        job.events.append(JobEvent(seq=seq, timestamp=_now(), level=level, message=message))
        if len(job.events) > self._max_events:
            del job.events[: len(job.events) - self._max_events]

    def _require_job_locked(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if not job:
            raise JobNotFoundError(job_id)
        return job

    def _trim_jobs_locked(self) -> None:
        while len(self._jobs) > self._max_jobs:
            old_id, old_job = next(iter(self._jobs.items()))
            if old_job.status not in TERMINAL_STATUSES:
                break
            self._jobs.pop(old_id)


def default_handlers() -> dict[str, JobHandler]:
    return {
        "index": _run_index_job,
        "embed": _run_embed_job,
        "pipeline": _run_pipeline_job,
    }


def _run_index_job(ctx: JobContext) -> dict[str, Any]:
    from scholaraio.services.index import build_index

    papers_dir = _resolve_path(ctx.cfg.papers_dir, ctx.params.get("papersDir") or ctx.params.get("papers_dir"))
    rebuild = _bool(ctx.params.get("rebuild"), default=False)
    ctx.emit(f"{'Rebuild' if rebuild else 'Update'} index: {papers_dir} -> {ctx.cfg.index_db}")
    ctx.check_cancelled()
    count = build_index(papers_dir, ctx.cfg.index_db, rebuild=rebuild)
    return {"indexed": count, "rebuild": rebuild, "papersDir": str(papers_dir), "indexDb": str(ctx.cfg.index_db)}


def _run_embed_job(ctx: JobContext) -> dict[str, Any]:
    try:
        from scholaraio.services.vectors import build_vectors
    except ImportError:
        ctx.emit("Skipping embed job: missing embedding dependencies", level="warning")
        return {"skipped": True, "reason": "missing embed dependencies"}

    papers_dir = _resolve_path(ctx.cfg.papers_dir, ctx.params.get("papersDir") or ctx.params.get("papers_dir"))
    rebuild = _bool(ctx.params.get("rebuild"), default=False)
    ctx.emit(f"{'Rebuild' if rebuild else 'Update'} vector index: {papers_dir} -> {ctx.cfg.index_db}")
    ctx.check_cancelled()
    count = build_vectors(papers_dir, ctx.cfg.index_db, rebuild=rebuild, cfg=ctx.cfg)
    return {"vectors": count, "rebuild": rebuild, "papersDir": str(papers_dir), "indexDb": str(ctx.cfg.index_db)}


def _run_pipeline_job(ctx: JobContext) -> dict[str, Any]:
    from scholaraio.services.ingest.pipeline import run_pipeline

    preset, step_names = _pipeline_steps(ctx.params)

    opts: dict[str, Any] = {
        "dry_run": _bool(ctx.params.get("dryRun", ctx.params.get("dry_run")), default=False),
        "no_api": _bool(ctx.params.get("noApi", ctx.params.get("no_api")), default=False),
        "force": _bool(ctx.params.get("force"), default=False),
        "inspect": _bool(ctx.params.get("inspect"), default=False),
        "max_retries": _int(ctx.params.get("maxRetries", ctx.params.get("max_retries")), default=2, min_value=0),
        "rebuild": _bool(ctx.params.get("rebuild"), default=False),
    }
    if ctx.params.get("inboxDir") or ctx.params.get("inbox_dir"):
        opts["inbox_dir"] = _resolve_path(Path.cwd(), ctx.params.get("inboxDir") or ctx.params.get("inbox_dir"))
    if ctx.params.get("papersDir") or ctx.params.get("papers_dir"):
        opts["papers_dir"] = _resolve_path(ctx.cfg.papers_dir, ctx.params.get("papersDir") or ctx.params.get("papers_dir"))

    ctx.emit(f"Run pipeline: {', '.join(step_names)}")
    ctx.check_cancelled()
    run_pipeline(step_names, ctx.cfg, opts)
    return {
        "preset": preset or None,
        "steps": step_names,
        "rebuild": opts["rebuild"],
        "dryRun": opts["dry_run"],
    }


def _pipeline_steps(params: dict[str, Any]) -> tuple[str, list[str]]:
    from scholaraio.services.ingest.pipeline import PRESETS, STEPS

    preset = str(params.get("preset") or "").strip()
    raw_steps = params.get("steps")
    if preset:
        if preset not in PRESETS:
            raise JobValidationError(f"Unknown pipeline preset: {preset}")
        step_names = list(PRESETS[preset])
    elif isinstance(raw_steps, list) and raw_steps:
        step_names = [str(step).strip() for step in raw_steps if str(step).strip()]
    else:
        raise JobValidationError("Pipeline jobs require preset or steps")

    unknown = [step for step in step_names if step not in STEPS]
    if unknown:
        raise JobValidationError(f"Unknown pipeline steps: {', '.join(unknown)}")
    return preset, step_names


def _normalize_kind(kind: str) -> str:
    kind = str(kind or "").strip().lower()
    if not kind:
        raise JobValidationError("Job kind is required")
    return kind


def _job_title(kind: str, params: dict[str, Any]) -> str:
    if kind == "pipeline" and params.get("preset"):
        return f"pipeline:{params['preset']}"
    if kind == "pipeline" and params.get("steps"):
        return "pipeline:" + ",".join(str(step) for step in params["steps"])
    return kind


def _resolve_path(default: Path, value: object) -> Path:
    if value is None or str(value).strip() == "":
        return default
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else path.resolve()


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(value: object, *, default: int, min_value: int | None = None) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    return parsed


def _json_safe_params(params: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, Path):
            safe[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [str(item) for item in value]
        else:
            safe[key] = str(value)
    return safe
