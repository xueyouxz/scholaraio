from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from scholaraio.core.config import Config, PathsConfig
from scholaraio.serve import create_app
from scholaraio.services.index import build_index
from scholaraio.services.jobs import TERMINAL_STATUSES


def _cfg(tmp_path: Path, papers_dir: Path, db_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            papers_dir=str(papers_dir),
            index_db=str(db_path),
            workspace_dir=str(tmp_path / "workspace"),
        ),
        _root=tmp_path,
    )


def _wait_client_job(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        res = client.get(f"/api/v1/jobs/{job_id}")
        assert res.status_code == 200
        job = res.json()["job"]
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def test_workspace_routes_use_current_refs_papers_layout(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    build_index(tmp_papers, tmp_db, rebuild=True)
    client = TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db)))

    create_res = client.post("/api/v1/workspaces", json={"name": "demo"})
    assert create_res.status_code == 201
    assert (tmp_path / "workspace" / "demo" / "refs" / "papers.json").exists()
    assert not (tmp_path / "workspace" / "demo" / "papers.json").exists()

    empty_res = client.get("/api/v1/workspaces/demo")
    assert empty_res.status_code == 200
    assert empty_res.json()["workspace"]["paperCount"] == 0
    assert empty_res.json()["workspace"]["path"] == str(tmp_path / "workspace" / "demo")

    add_res = client.post("/api/v1/workspaces/demo/papers", json={"paperId": "aaaa-1111"})
    assert add_res.status_code == 200
    assert add_res.json() == {"ok": True}

    current_entries = json.loads(
        (tmp_path / "workspace" / "demo" / "refs" / "papers.json").read_text(encoding="utf-8")
    )
    assert current_entries[0]["id"] == "aaaa-1111"

    list_res = client.get("/api/v1/workspaces")
    assert list_res.status_code == 200
    workspaces = list_res.json()["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["name"] == "demo"
    assert workspaces[0]["path"] == str(tmp_path / "workspace" / "demo")

    show_res = client.get("/api/workspaces/demo")
    assert show_res.status_code == 200
    workspace = show_res.json()["workspace"]
    assert workspace["paperCount"] == 1
    assert workspace["path"] == str(tmp_path / "workspace" / "demo")
    assert workspace["papers"][0]["title"] == "Turbulence modeling in boundary layers"


def test_papers_route_uses_index_read_model_for_pagination(
    tmp_path: Path,
    tmp_papers: Path,
    tmp_db: Path,
) -> None:
    build_index(tmp_papers, tmp_db, rebuild=True)
    client = TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db)))

    res = client.get("/api/v1/papers?sort=year_asc&limit=1&offset=0")

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert len(body["papers"]) == 1
    assert body["papers"][0]["id"] == "aaaa-1111"
    assert body["papers"][0]["abstract"] == "We propose a novel turbulence model for boundary layers."


def test_search_request_validates_limit(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    build_index(tmp_papers, tmp_db, rebuild=True)
    client = TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db)))

    res = client.post("/api/v1/search", json={"query": "turbulence", "limit": 10000})

    assert res.status_code == 422


def test_index_status_reports_readiness(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    build_index(tmp_papers, tmp_db, rebuild=True)
    client = TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db)))

    res = client.get("/api/v1/index/status")

    assert res.status_code == 200
    body = res.json()
    assert body["exists"] is True
    assert body["ftsReady"] is True
    assert body["paperCount"] == 2


def test_jobs_route_runs_index_job_and_exposes_events(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    with TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db))) as client:
        create_res = client.post("/api/v1/jobs", json={"kind": "index", "rebuild": True})
        assert create_res.status_code == 202
        created = create_res.json()["job"]

        job = _wait_client_job(client, created["id"])

        assert job["status"] == "succeeded"
        assert job["result"]["indexed"] == 2
        assert tmp_db.exists()

        list_res = client.get("/api/v1/jobs")
        assert list_res.status_code == 200
        assert list_res.json()["jobs"][0]["id"] == created["id"]

        events_res = client.get(f"/api/v1/jobs/{created['id']}/events?after=0")
        assert events_res.status_code == 200
        messages = [event["message"] for event in events_res.json()["events"]]
        assert "Job queued" in messages
        assert "Job succeeded" in messages

        legacy_res = client.get(f"/api/jobs/{created['id']}")
        assert legacy_res.status_code == 200
        assert legacy_res.json()["job"]["status"] == "succeeded"


def test_jobs_route_rejects_unknown_kind(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    with TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db))) as client:
        res = client.post("/api/v1/jobs", json={"kind": "does-not-exist"})

    assert res.status_code == 400
    assert "Unknown job kind" in res.json()["detail"]


def test_jobs_route_rejects_unknown_pipeline_preset(tmp_path: Path, tmp_papers: Path, tmp_db: Path) -> None:
    with TestClient(create_app(_cfg(tmp_path, tmp_papers, tmp_db))) as client:
        res = client.post("/api/v1/jobs", json={"kind": "pipeline", "preset": "missing"})

    assert res.status_code == 400
    assert "Unknown pipeline preset" in res.json()["detail"]
