from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

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
        assert "https://" not in html
        assert "http://" not in html
        assert "tex-chtml.js" not in html
        assert "MathJax" not in html
        assert "app.js" in html
    finally:
        server.shutdown()
        server.server_close()


def test_library_view_shell_uses_compact_records_and_pdf_controls(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{host}:{port}/", timeout=3) as response:
            html = response.read().decode("utf-8")
        assert 'id="source-copy-button"' in html
        assert 'id="pdf-fullscreen-button"' in html
        assert 'id="detail-subtitle"' not in html
        assert html.index('id="toc-list"') < html.index('id="issue-list"')
    finally:
        server.shutdown()
        server.server_close()


def test_library_view_static_assets_live_inside_package() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    static_dir = _static_dir()

    assert static_dir.is_dir()
    assert static_dir.parent == Path(__file__).resolve().parents[1] / "scholaraio" / "interfaces" / "cli"
    assert (static_dir / "index.html").is_file()
    assert (static_dir / "app.js").is_file()
    assert (static_dir / "styles.css").is_file()


def test_library_view_css_preserves_hidden_attribute_for_pdf_toolbar() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    css = (_static_dir() / "styles.css").read_text(encoding="utf-8")

    assert "[hidden]" in css
    assert "display: none !important" in css


def test_library_view_app_source_copy_fullscreen_and_compact_rows() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  const classes = new Set();
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    disabled: false,
    hidden: false,
    textContent: "",
    className: "",
    children: [],
    classList: {{
      add(name) {{ classes.add(name); }},
      remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
      toggle(name, force) {{
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name);
        else classes.delete(name);
        return enabled;
      }},
    }},
    appendChild(child) {{ this.children.push(child); return child; }},
    append(...items) {{ this.children.push(...items); }},
    removeAttribute(name) {{ delete this[name]; }},
    addEventListener() {{}},
  }};
}}

const elements = new Map();
const tabs = ["main", "proceedings"].map((tab) => {{
  const el = element(`tab-${{tab}}`);
  el.dataset.tab = tab;
  return el;
}});
const document = {{
  body: element("body"),
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll(selector) {{
    if (selector === ".tab") return tabs;
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  navigator: {{ clipboard: {{ writeText: async (value) => {{ context.__copied = value; }} }} }},
  fetch: async () => ({{ ok: true, json: async () => ({{ papers: [], total: 0 }}) }}),
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
(async () => {{
  state.payload.main = {{ root: "/tmp/scholaraio/data/libraries/papers", total: 1, issue_totals: {{}} }};
  renderMetrics();
  await copySourceRoot();
  openPdf({{ pdf_url: "/api/main/pdf?id=paper-1", title: "Paper title" }});
  setPdfFullscreen(true);
  const fullscreenOn = els.tablePanel.classList.contains("is-pdf-fullscreen");
  showRecords();
  const row = {{ paper_id: "paper-1", dir_name: "Doe-2026-Paper", title: "Paper title", has_md: true }};
  state.rows.main = [row];
  renderTable();
  globalThis.__result = {{
    copied: globalThis.__copied,
    sourceCopyLabel: els.sourceCopyButton.textContent,
    fullscreenOn,
    fullscreenAfterBack: els.tablePanel.classList.contains("is-pdf-fullscreen"),
    firstTitleChildren: els.tableBody.children[0].children[0].children[0].children.length,
    metadataLabels: (() => {{
      renderMetadata({{ paper_id: "paper-1", dir_name: "Doe-2026-Paper", title: "Paper title" }});
      return els.metadataGrid.children.filter((child, index) => index % 2 === 0).map((child) => child.textContent);
    }})(),
  }};
}})();
`, context);
setImmediate(() => console.log(JSON.stringify(context.__result)));
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    payload = json.loads(result.stdout)
    assert payload["copied"] == "/tmp/scholaraio/data/libraries/papers"
    assert payload["sourceCopyLabel"] == "Copied"
    assert payload["fullscreenOn"] is True
    assert payload["fullscreenAfterBack"] is False
    assert payload["firstTitleChildren"] == 1
    assert "ID" not in payload["metadataLabels"]


def test_library_view_app_ignores_stale_refresh_and_detail_responses() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  const classes = new Set();
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    disabled: false,
    hidden: false,
    textContent: "",
    title: "",
    className: "",
    children: [],
    classList: {{
      add(name) {{ classes.add(name); }},
      remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
      toggle(name, force) {{
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name);
        else classes.delete(name);
        return enabled;
      }},
    }},
    appendChild(child) {{ this.children.push(child); return child; }},
    append(...items) {{ this.children.push(...items); }},
    removeAttribute(name) {{ delete this[name]; }},
    addEventListener() {{}},
  }};
}}

const elements = new Map();
const tabs = ["main", "proceedings"].map((tab) => {{
  const el = element(`tab-${{tab}}`);
  el.dataset.tab = tab;
  return el;
}});
const document = {{
  body: element("body"),
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll(selector) {{
    if (selector === ".tab") return tabs;
    return [];
  }},
  addEventListener() {{}},
}};
const pending = [];
const context = {{
  document,
  pending,
  navigator: {{ clipboard: {{ writeText: async () => {{}} }} }},
  controlled: false,
  fetch: async (url) => {{
    if (!context.controlled) return {{ ok: true, json: async () => ({{ papers: [], total: 0, issue_totals: {{}} }}) }};
    return new Promise((resolve) => pending.push({{ url, resolve }}));
  }},
  setTimeout,
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
globalThis.__ready = (async () => {{
  controlled = true;
  state.tab = "main";
  state.rows = {{ main: [], proceedings: [] }};
  state.payload = {{ main: null, proceedings: null }};
  const staleRefresh = refreshActive({{ keepSelection: false }});
  state.tab = "proceedings";
  pending.shift().resolve({{ ok: true, json: async () => ({{
    root: "/main",
    total: 1,
    issue_totals: {{}},
    papers: [{{ paper_id: "main-paper", title: "Main paper", has_md: true }}],
  }}) }});
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (pending.length) pending.shift().resolve({{ ok: true, json: async () => ({{ paper_id: "main-paper", title: "Wrong detail" }}) }});
  await staleRefresh;
  const refreshMainRows = state.rows.main.map((row) => row.paper_id);
  const refreshProceedingsRows = state.rows.proceedings.map((row) => row.paper_id);

  state.tab = "main";
  state.rows.main = [
    {{ paper_id: "first", title: "First", has_md: true }},
    {{ paper_id: "second", title: "Second", has_md: true }},
  ];
  state.selected.main = "";
  state.detail = null;
  const staleDetail = selectRow("first");
  await new Promise((resolve) => setTimeout(resolve, 0));
  state.selected.main = "second";
  pending.shift().resolve({{ ok: true, json: async () => ({{ paper_id: "first", title: "First detail" }}) }});
  await staleDetail;

  return {{
    refreshMainRows,
    refreshProceedingsRows,
    activeTab: state.tab,
    selected: state.selected.main,
    detailTitle: els.detailTitle.textContent,
    detail: state.detail,
  }};
}})();
`, context);
context.__ready.then((payload) => console.log(JSON.stringify(payload))).catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    payload = json.loads(result.stdout)
    assert payload["refreshMainRows"] == ["main-paper"]
    assert payload["refreshProceedingsRows"] == []
    assert payload["activeTab"] == "main"
    assert payload["selected"] == "second"
    assert payload["detailTitle"] != "First detail"
    assert payload["detail"] is None


def test_library_view_app_detail_failure_fallback_has_no_commands() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  const classes = new Set();
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    disabled: false,
    hidden: false,
    textContent: "",
    innerHTML: "",
    className: "",
    children: [],
    classList: {{
      add(name) {{ classes.add(name); }},
      remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
      toggle(name, force) {{
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name);
        else classes.delete(name);
        return enabled;
      }},
    }},
    appendChild(child) {{ this.children.push(child); return child; }},
    append(...items) {{ this.children.push(...items); }},
    removeAttribute(name) {{ delete this[name]; }},
    addEventListener() {{}},
  }};
}}

const elements = new Map();
const tabs = ["main", "proceedings"].map((tab) => {{
  const el = element(`tab-${{tab}}`);
  el.dataset.tab = tab;
  return el;
}});
const document = {{
  body: element("body"),
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll(selector) {{
    if (selector === ".tab") return tabs;
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  navigator: {{ clipboard: {{ writeText: async () => {{}} }} }},
  fetch: async () => ({{ ok: false, status: 500, statusText: "Boom" }}),
  setTimeout,
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
globalThis.__ready = (async () => {{
  renderDetail = (detail) => {{ globalThis.__captured = detail; }};
  state.tab = "main";
  state.rows.main = [{{ paper_id: "broken", title: "Broken", has_md: true }}];
  await selectRow("broken");
  return globalThis.__captured;
}})();
`, context);
context.__ready.then((payload) => console.log(JSON.stringify(payload))).catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    payload = json.loads(result.stdout)
    assert payload["title"] == "Detail unavailable"
    assert "commands" not in payload


def test_library_view_app_missing_markdown_status_is_not_clean() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    hidden: false,
    textContent: "",
    className: "",
    classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }},
    appendChild() {{}},
    append() {{}},
    removeAttribute() {{}},
    addEventListener() {{}},
  }};
}}
const elements = new Map();
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll() {{
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  navigator: {{ clipboard: {{ writeText: async () => {{}} }} }},
  fetch: async () => ({{ ok: true, json: async () => ({{ papers: [], total: 0, issue_totals: {{}} }}) }}),
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
globalThis.__result = statusPills({{ has_md: false, issue_counts: {{}} }}).map((pill) => pill[0]);
`, context);
console.log(JSON.stringify(context.__result));
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(result.stdout) == ["No MD"]


def test_library_view_app_renders_markdown_and_math_in_detail_text() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    abstract = json.dumps("Flow **energy** is $E_i = mc^2$. <script>alert(1)</script>")
    conclusion = json.dumps(r"Conclusion with $$\alpha + \beta$$.")
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    hidden: false,
    textContent: "",
    innerHTML: "",
    className: "",
    classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }},
    children: [],
    appendChild(child) {{ this.children.push(child); return child; }},
    append(...items) {{ this.children.push(...items); }},
    removeAttribute() {{}},
    addEventListener() {{}},
  }};
}}
const elements = new Map();
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll() {{
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  __abstract: {abstract},
  __conclusion: {conclusion},
  navigator: {{ clipboard: {{ writeText: async () => {{}} }} }},
  fetch: async () => ({{ ok: true, json: async () => ({{ papers: [], total: 0, issue_totals: {{}} }}) }}),
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
renderDetail({{
  title: "Formula paper",
  abstract: globalThis.__abstract,
  l3_conclusion: globalThis.__conclusion
}});
globalThis.__result = {{
  abstractHtml: els.detailAbstract.innerHTML,
  conclusionHtml: els.detailConclusion.innerHTML,
}};
`, context);
setImmediate(() => console.log(JSON.stringify(context.__result)));
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    payload = json.loads(result.stdout)
    assert "<strong>energy</strong>" in payload["abstractHtml"]
    assert '<span class="math math-inline"' in payload["abstractHtml"]
    assert "E<sub>i</sub> = mc<sup>2</sup>" in payload["abstractHtml"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in payload["abstractHtml"]
    assert '<span class="math math-display"' in payload["conclusionHtml"]
    assert "α + β" in payload["conclusionHtml"]


def test_library_view_app_treats_single_newlines_as_soft_breaks_in_detail_text() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    abstract = json.dumps("We estimate\n$E_i = mc^2$\nfrom extracted features.")
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    hidden: false,
    textContent: "",
    innerHTML: "",
    className: "",
    classList: {{ toggle() {{}}, add() {{}}, remove() {{}} }},
    children: [],
    appendChild(child) {{ this.children.push(child); return child; }},
    append(...items) {{ this.children.push(...items); }},
    removeAttribute() {{}},
    addEventListener() {{}},
  }};
}}
const elements = new Map();
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll() {{
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  __abstract: {abstract},
  navigator: {{ clipboard: {{ writeText: async () => {{}} }} }},
  fetch: async () => ({{ ok: true, json: async () => ({{ papers: [], total: 0, issue_totals: {{}} }}) }}),
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
renderDetail({{
  title: "Wrapped formula paper",
  abstract: globalThis.__abstract,
  l3_conclusion: ""
}});
globalThis.__result = els.detailAbstract.innerHTML;
`, context);
console.log(JSON.stringify(context.__result));
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    html = json.loads(result.stdout)
    assert "<br>" not in html
    assert "We estimate " in html
    assert '<span class="math math-inline"' in html
    assert "E<sub>i</sub> = mc<sup>2</sup>" in html
    assert " from extracted features." in html


def test_library_view_css_only_scrolls_display_math() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    css = (_static_dir() / "styles.css").read_text(encoding="utf-8")

    assert ".math-display" in css
    assert "overflow-x: auto" in css
    assert ".math-inline {\n  display: inline;" in css
    assert "mjx-container" not in css


def test_library_view_tab_switch_resets_stale_type_filter() -> None:
    from scholaraio.interfaces.cli.gui import _static_dir

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for app.js behavior regression")
    app_js = (_static_dir() / "app.js").as_posix()
    script = f"""
const fs = require("fs");
const vm = require("vm");

function element(id) {{
  return {{
    id,
    dataset: {{}},
    value: "",
    checked: false,
    hidden: false,
    textContent: "",
    className: "",
    classList: {{ toggle() {{}} }},
    appendChild() {{}},
    append() {{}},
    removeAttribute() {{}},
    addEventListener() {{}},
  }};
}}

const elements = new Map();
const tabs = ["main", "proceedings"].map((tab) => {{
  const el = element(`tab-${{tab}}`);
  el.dataset.tab = tab;
  return el;
}});
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
  }},
  createElement(tag) {{
    return element(tag);
  }},
  querySelectorAll(selector) {{
    if (selector === ".tab") return tabs;
    return [];
  }},
  addEventListener() {{}},
}};
const context = {{
  document,
  fetch: async () => ({{ ok: true, json: async () => ({{ papers: [], total: 0 }}) }}),
  setInterval: () => 1,
  clearInterval: () => {{}},
  console,
}};
const code = fs.readFileSync({json.dumps(app_js)}, "utf8");
vm.runInNewContext(`${{code}}
state.filters.type = "journal-article";
els.typeFilter.value = "journal-article";
switchTab("proceedings");
globalThis.__result = {{ type: state.filters.type, select: els.typeFilter.value }};
`, context);
console.log(JSON.stringify(context.__result));
"""

    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(result.stdout) == {"type": "", "select": ""}


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


def test_library_view_server_serves_non_ascii_pdf_filename(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    paper_dir = tmp_path / "data" / "libraries" / "papers" / "王-2026-中文论文"
    paper_dir.mkdir(parents=True)
    (paper_dir / "meta.json").write_text(
        json.dumps({"id": "cn-pdf", "title": "中文论文", "authors": ["王"], "year": 2026}, ensure_ascii=False),
        encoding="utf-8",
    )
    (paper_dir / "王-2026-中文论文.pdf").write_bytes(b"%PDF-cn")

    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{host}:{port}/api/main/pdf?id=cn-pdf", timeout=3) as response:
            body = response.read()
            disposition = response.headers["Content-Disposition"]
        assert body == b"%PDF-cn"
        assert 'filename="paper.pdf"' in disposition
        assert "filename*=UTF-8''%E7%8E%8B-2026-%E4%B8%AD%E6%96%87%E8%AE%BA%E6%96%87.pdf" in disposition
    finally:
        server.shutdown()
        server.server_close()


def test_library_view_pdf_errors_do_not_send_ok_before_file_check():
    from scholaraio.interfaces.cli.gui import LibraryViewRequestHandler

    class MissingPdf:
        name = "missing.pdf"

        def stat(self):
            raise FileNotFoundError("missing")

        def open(self, _mode):
            raise AssertionError("open should not run after failed stat")

    class UnreadablePdf:
        name = "locked.pdf"

        def stat(self):
            return SimpleNamespace(st_size=1)

        def open(self, _mode):
            raise PermissionError("locked")

    for pdf_path, expected_status in [
        (MissingPdf(), HTTPStatus.NOT_FOUND),
        (UnreadablePdf(), HTTPStatus.INTERNAL_SERVER_ERROR),
    ]:
        handler = LibraryViewRequestHandler.__new__(LibraryViewRequestHandler)
        statuses: list[HTTPStatus] = []
        handler.command = "GET"
        handler.wfile = io.BytesIO()
        handler.send_response = statuses.append
        handler.send_header = lambda *_args, **_kwargs: None
        handler.end_headers = lambda: None

        def record_error(status, _message, headers=None, *, statuses=statuses):
            statuses.append(status)

        handler._send_error_json = record_error

        with suppress(FileNotFoundError, PermissionError):
            handler._send_pdf(pdf_path)  # type: ignore[arg-type]

        assert HTTPStatus.OK not in statuses
        assert statuses == [expected_status]


def test_pdf_content_disposition_uses_ascii_fallback_and_strips_line_breaks():
    from scholaraio.interfaces.cli.gui import _pdf_content_disposition

    disposition = _pdf_content_disposition('坏\r\nName".pdf')

    assert "\r" not in disposition
    assert "\n" not in disposition
    assert 'filename="paper.pdf"' in disposition
    assert "filename*=UTF-8''%E5%9D%8F__Name_.pdf" in disposition


def test_library_view_rejected_write_methods_close_connection():
    from scholaraio.interfaces.cli.gui import LibraryViewRequestHandler

    handler = LibraryViewRequestHandler.__new__(LibraryViewRequestHandler)
    captured: dict[str, object] = {}
    handler.close_connection = False
    handler.headers = {"Content-Length": "7"}
    handler.rfile = io.BytesIO(b"payload")
    handler._send_error_json = lambda status, message, headers=None: captured.update(
        {"status": status, "message": message, "headers": headers or {}}
    )

    handler.do_POST()

    assert handler.close_connection is True
    assert captured["status"] == HTTPStatus.METHOD_NOT_ALLOWED
    assert captured["headers"] == {"Allow": "GET, HEAD", "Connection": "close"}


def test_browser_url_uses_loopback_for_wildcard_bind_hosts():
    from scholaraio.interfaces.cli.gui import _browser_url

    assert _browser_url("0.0.0.0", 8765) == "http://127.0.0.1:8765"
    assert _browser_url("::", 8765) == "http://127.0.0.1:8765"
    assert _browser_url("", 8765) == "http://127.0.0.1:8765"
    assert _browser_url("127.0.0.1", 8765) == "http://127.0.0.1:8765"
    assert _browser_url("::1", 8765) == "http://[::1]:8765"


def test_library_view_server_head_pdf_does_not_read_body(tmp_path):
    from scholaraio.interfaces.cli.gui import create_library_view_server

    cfg = _build_config({}, tmp_path)
    paper_dir = tmp_path / "data" / "libraries" / "papers" / "Doe-2026-Head-PDF"
    paper_dir.mkdir(parents=True)
    (paper_dir / "meta.json").write_text(
        json.dumps({"id": "head-pdf", "title": "Head PDF", "authors": ["Jane Doe"], "year": 2026}),
        encoding="utf-8",
    )
    (paper_dir / "Doe-2026-Head-PDF.pdf").write_bytes(b"%PDF-head")

    server = create_library_view_server(cfg, host="127.0.0.1", port=0)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(Path, "read_bytes", side_effect=AssertionError("PDF body should not be buffered")):
            request = Request(f"http://{host}:{port}/api/main/pdf?id=head-pdf", method="HEAD")
            with urlopen(request, timeout=3) as response:
                body = response.read()
                content_type = response.headers["Content-Type"]
                length = response.headers["Content-Length"]
        assert body == b""
        assert content_type == "application/pdf"
        assert length == str(len(b"%PDF-head"))
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
