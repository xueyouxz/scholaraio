from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scholaraio.core.config import _build_config


def test_library_view_api_serves_live_json_and_rejects_writes(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    paper_dir = tmp_path / "data" / "libraries" / "papers" / "Doe-2026-Live"
    paper_dir.mkdir(parents=True)
    (paper_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": "live-paper",
                "title": "Live paper",
                "authors": ["Jane Doe"],
                "year": 2026,
                "journal": "Live Journal",
                "doi": "10.1000/live",
                "abstract": "Live abstract.",
            }
        ),
        encoding="utf-8",
    )
    (paper_dir / "paper.md").write_text("# Live paper\n", encoding="utf-8")

    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{host}:{port}/api/main/papers", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["total"] == 1
        assert payload["papers"][0]["paper_id"] == "live-paper"

        new_dir = tmp_path / "data" / "libraries" / "papers" / "Roe-2026-New"
        new_dir.mkdir()
        (new_dir / "meta.json").write_text(
            json.dumps({"id": "new-paper", "title": "New paper", "authors": ["Pat Roe"], "year": 2026}),
            encoding="utf-8",
        )

        with urlopen(f"http://{host}:{port}/api/main/papers", timeout=3) as response:
            refreshed = json.loads(response.read().decode("utf-8"))
        assert {row["paper_id"] for row in refreshed["papers"]} == {"live-paper", "new-paper"}

        request = Request(f"http://{host}:{port}/api/main/papers", method="POST", data=b"{}")
        try:
            urlopen(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == 405
            assert exc.headers["Allow"] == "GET, HEAD"
            assert "read-only" in exc.read().decode("utf-8")
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("POST unexpectedly succeeded")
    finally:
        server.shutdown()
        server.server_close()


def test_library_view_server_serves_static_console_shell(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{host}:{port}/", timeout=3) as response:
            html = response.read().decode("utf-8")
        assert "ScholarAIO Library" in html
        assert "Main Papers" in html
        assert "Proceedings" in html
        assert "pdf-frame" in html
        assert "Back to records" in html
        assert ">CLI<" not in html
        assert "app.js" in html
    finally:
        server.shutdown()
        server.server_close()


def test_library_view_server_serves_main_pdf_inline(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    paper_dir = tmp_path / "data" / "libraries" / "papers" / "Doe-2026-PDF"
    paper_dir.mkdir(parents=True)
    (paper_dir / "meta.json").write_text(
        json.dumps({"id": "pdf-paper", "title": "PDF paper", "authors": ["Jane Doe"], "year": 2026}),
        encoding="utf-8",
    )
    (paper_dir / "Doe-2026-PDF.pdf").write_bytes(b"%PDF-inline")

    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{host}:{port}/api/main/pdf?id=pdf-paper", timeout=3) as response:
            body = response.read()
            content_type = response.headers["Content-Type"]
            disposition = response.headers["Content-Disposition"]
        assert body == b"%PDF-inline"
        assert content_type == "application/pdf"
        assert "inline" in disposition
        assert "Doe-2026-PDF.pdf" in disposition
    finally:
        server.shutdown()
        server.server_close()


def test_cmd_gui_delegates_to_read_only_server(monkeypatch, tmp_path):
    from scholaraio.interfaces.cli.gui import cmd_gui

    seen = {}

    def fake_serve(cfg, *, host, port, open_browser):
        seen["cfg"] = cfg
        seen["host"] = host
        seen["port"] = port
        seen["open_browser"] = open_browser

    monkeypatch.setattr("scholaraio.interfaces.cli.gui.serve_library_view", fake_serve)
    cfg = _build_config({}, tmp_path)

    cmd_gui(SimpleNamespace(host="127.0.0.1", port=18888, no_open=True), cfg)

    assert seen == {"cfg": cfg, "host": "127.0.0.1", "port": 18888, "open_browser": False}
