from __future__ import annotations

import threading
import time
from pathlib import Path

from scholaraio.core.config import Config, PathsConfig
from scholaraio.services.jobs import JobContext, JobRunner, TERMINAL_STATUSES


def _cfg(tmp_path: Path) -> Config:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir(exist_ok=True)
    return Config(
        paths=PathsConfig(
            papers_dir=str(papers_dir),
            index_db=str(tmp_path / "index.db"),
            workspace_dir=str(tmp_path / "workspace"),
        ),
        _root=tmp_path,
    )


def _wait_for_job(runner: JobRunner, job_id: str) -> dict:
    for _ in range(100):
        job = runner.get(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def test_job_runner_tracks_success_result_and_events(tmp_path: Path) -> None:
    def handler(ctx: JobContext) -> dict:
        ctx.emit("custom work")
        return {"ok": True}

    runner = JobRunner(handlers={"custom": handler})
    try:
        created = runner.submit("custom", _cfg(tmp_path), {"value": 1})
        job = _wait_for_job(runner, created["id"])

        assert job["status"] == "succeeded"
        assert job["result"] == {"ok": True}
        assert [event["message"] for event in job["events"]] == [
            "Job queued",
            "Job started",
            "custom work",
            "Job succeeded",
        ]
    finally:
        runner.shutdown(wait=True)


def test_job_runner_cancels_queued_jobs(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(ctx: JobContext) -> dict:
        started.set()
        release.wait(timeout=2)
        return {"done": True}

    runner = JobRunner(max_workers=1, handlers={"slow": slow})
    try:
        first = runner.submit("slow", _cfg(tmp_path), {})
        assert started.wait(timeout=1)

        second = runner.submit("slow", _cfg(tmp_path), {})
        cancelled = runner.cancel(second["id"])

        assert cancelled["status"] == "cancelled"
        assert cancelled["cancelRequested"] is True
        assert cancelled["events"][-1]["message"] == "Job cancelled before start"

        release.set()
        assert _wait_for_job(runner, first["id"])["status"] == "succeeded"
    finally:
        release.set()
        runner.shutdown(wait=True)
