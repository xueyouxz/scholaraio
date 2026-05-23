"""Local read-only WebUI for browsing ScholarAIO libraries."""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, unquote, urlparse

if TYPE_CHECKING:
    from scholaraio.core.config import Config


def _ui(msg: str = "") -> None:
    try:
        from scholaraio.interfaces.cli import compat as cli_mod
    except ImportError:
        from scholaraio.core.log import ui as log_ui

        log_ui(msg)
        return
    cli_mod.ui(msg)


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "library-view"


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _pdf_content_disposition(filename: str) -> str:
    safe_name = filename.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_").strip()
    if not safe_name:
        safe_name = "paper.pdf"
    try:
        fallback = safe_name.encode("ascii").decode("ascii")
    except UnicodeEncodeError:
        fallback = "paper.pdf"
    fallback = (
        fallback.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_").strip() or "paper.pdf"
    )
    encoded = quote(safe_name, safe="")
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _browser_url(host: str, port: int) -> str:
    browser_host = (host or "127.0.0.1").strip() or "127.0.0.1"
    if browser_host in {"0.0.0.0", "::", "[::]"}:
        browser_host = "127.0.0.1"
    elif ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"
    return f"http://{browser_host}:{port}"


class LibraryViewRequestHandler(BaseHTTPRequestHandler):
    """Request handler configured by :func:`create_library_view_server`."""

    cfg: Config
    static_dir: Path

    server_version = "ScholarAIOReadOnlyGUI/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: object, *, headers: dict[str, str] | None = None) -> None:
        self._send_bytes(status, _json_bytes(payload), "application/json; charset=utf-8", headers=headers)

    def _send_error_json(
        self,
        status: HTTPStatus,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(status, {"error": message, "status": status.value}, headers=headers)

    def _query_id(self) -> str:
        parsed = urlparse(self.path)
        values = parse_qs(parsed.query).get("id") or [""]
        return values[0]

    def _send_pdf(self, pdf_path: Path) -> None:
        try:
            size = pdf_path.stat().st_size
            stream = pdf_path.open("rb")
        except FileNotFoundError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "PDF file not found")
            return
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", _pdf_content_disposition(pdf_path.name))
        self.end_headers()
        if self.command == "HEAD":
            stream.close()
            return
        with stream:
            try:
                shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
            except OSError:
                return

    def _handle_api(self, path: str) -> None:
        from scholaraio.services.library_view import (
            build_main_library_view,
            build_proceedings_library_view,
            get_main_paper_detail,
            get_main_paper_pdf,
            get_proceedings_paper_detail,
            get_proceedings_paper_pdf,
        )

        try:
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "readonly": True})
                return
            if path == "/api/main/papers":
                self._send_json(HTTPStatus.OK, build_main_library_view(self.cfg))
                return
            if path == "/api/main/detail":
                paper_id = self._query_id()
                if not paper_id:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "missing id query parameter")
                    return
                self._send_json(HTTPStatus.OK, get_main_paper_detail(self.cfg, paper_id))
                return
            if path == "/api/main/pdf":
                paper_id = self._query_id()
                if not paper_id:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "missing id query parameter")
                    return
                self._send_pdf(get_main_paper_pdf(self.cfg, paper_id))
                return
            if path == "/api/proceedings/papers":
                self._send_json(HTTPStatus.OK, build_proceedings_library_view(self.cfg))
                return
            if path == "/api/proceedings/detail":
                paper_id = self._query_id()
                if not paper_id:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "missing id query parameter")
                    return
                self._send_json(HTTPStatus.OK, get_proceedings_paper_detail(self.cfg, paper_id))
                return
            if path == "/api/proceedings/pdf":
                paper_id = self._query_id()
                if not paper_id:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "missing id query parameter")
                    return
                self._send_pdf(get_proceedings_paper_pdf(self.cfg, paper_id))
                return
        except KeyError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, f"paper not found: {exc.args[0]}")
            return
        except Exception as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "unknown API route")

    def _serve_static(self, path: str) -> None:
        if path == "/":
            rel = "index.html"
        else:
            rel = unquote(path.lstrip("/"))
        candidate = (self.static_dir / rel).resolve()
        root = self.static_dir.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send_error_json(HTTPStatus.FORBIDDEN, "forbidden path")
            return
        if not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "file not found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif candidate.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif candidate.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, candidate.read_bytes(), content_type)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._handle_api(path)
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        self.close_connection = True
        self._send_error_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "this WebUI is read-only",
            headers={"Allow": "GET, HEAD", "Connection": "close"},
        )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def create_library_view_server(cfg: Config, *, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create a read-only local HTTP server for the library WebUI."""

    static_dir = _static_dir()

    class ConfiguredHandler(LibraryViewRequestHandler):
        pass

    ConfiguredHandler.cfg = cfg
    ConfiguredHandler.static_dir = static_dir
    return ThreadingHTTPServer((host, int(port)), ConfiguredHandler)


def serve_library_view(
    cfg: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the local read-only WebUI until interrupted."""

    server = create_library_view_server(cfg, host=host, port=port)
    url = _browser_url(host, server.server_port)
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    _ui(f"ScholarAIO library WebUI (read-only): {url}")
    _ui("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _ui("Stopping ScholarAIO library WebUI.")
    finally:
        server.server_close()


def cmd_gui(args: argparse.Namespace, cfg: Config) -> None:
    """CLI command handler for the read-only library WebUI."""

    serve_library_view(
        cfg,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )
