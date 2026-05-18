const POLL_MS = 2200;

const state = {
  tab: "main",
  rows: { main: [], proceedings: [] },
  payload: { main: null, proceedings: null },
  detail: null,
  selected: { main: "", proceedings: "" },
  sortKey: "year",
  sortDir: "desc",
  pdf: null,
  filters: {
    search: "",
    type: "",
    volume: "",
    issues: false,
    missingMd: false,
  },
  pollTimer: null,
};

const els = {
  connectionDot: document.getElementById("connection-dot"),
  connectionLabel: document.getElementById("connection-label"),
  updatedAt: document.getElementById("updated-at"),
  sourceTitle: document.getElementById("source-title"),
  sourceRoot: document.getElementById("source-root"),
  metricTotal: document.getElementById("metric-total"),
  metricErrors: document.getElementById("metric-errors"),
  metricWarnings: document.getElementById("metric-warnings"),
  tableCount: document.getElementById("table-count"),
  recordsToolbarTitle: document.getElementById("records-toolbar-title"),
  recordsView: document.getElementById("records-view"),
  pdfToolbarTitle: document.getElementById("pdf-toolbar-title"),
  pdfBackButton: document.getElementById("pdf-back-button"),
  pdfTitle: document.getElementById("pdf-title"),
  pdfViewer: document.getElementById("pdf-viewer"),
  pdfFrame: document.getElementById("pdf-frame"),
  tableBody: document.getElementById("paper-table-body"),
  emptyState: document.getElementById("empty-state"),
  searchInput: document.getElementById("search-input"),
  typeFilter: document.getElementById("type-filter"),
  volumeFilter: document.getElementById("volume-filter"),
  volumeFilterLabel: document.getElementById("volume-filter-label"),
  filterIssues: document.getElementById("filter-issues"),
  filterMissingMd: document.getElementById("filter-missing-md"),
  refreshButton: document.getElementById("refresh-button"),
  detailTitle: document.getElementById("detail-title"),
  detailSubtitle: document.getElementById("detail-subtitle"),
  metadataGrid: document.getElementById("metadata-grid"),
  issueList: document.getElementById("issue-list"),
  detailAbstract: document.getElementById("detail-abstract"),
  detailConclusion: document.getElementById("detail-conclusion"),
  tocList: document.getElementById("toc-list"),
};

function text(value, fallback = "--") {
  const string = String(value ?? "").trim();
  return string || fallback;
}

function formatDate(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function setConnection(kind, label) {
  els.connectionDot.classList.toggle("live", kind === "live");
  els.connectionDot.classList.toggle("error", kind === "error");
  els.connectionLabel.textContent = label;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function activePayload() {
  return state.payload[state.tab];
}

function activeRows() {
  return state.rows[state.tab] || [];
}

function issueTotal(row) {
  const counts = row.issue_counts || {};
  return Number(counts.error || 0) + Number(counts.warning || 0) + Number(counts.info || 0);
}

function rowMatches(row) {
  const q = state.filters.search.toLowerCase();
  if (q) {
    const haystack = [
      row.title,
      row.authors_text,
      row.doi,
      row.paper_id,
      row.dir_name,
      row.proceeding_title,
    ].join(" ").toLowerCase();
    if (!haystack.includes(q)) return false;
  }
  if (state.filters.type && row.paper_type !== state.filters.type) return false;
  if (state.filters.volume && row.proceeding_title !== state.filters.volume) return false;
  if (state.filters.issues && issueTotal(row) === 0) return false;
  if (state.filters.missingMd && row.has_md) return false;
  return true;
}

function compareRows(a, b) {
  const av = a[state.sortKey] ?? "";
  const bv = b[state.sortKey] ?? "";
  const an = Number(av);
  const bn = Number(bv);
  let result;
  if (Number.isFinite(an) && Number.isFinite(bn)) result = an - bn;
  else result = String(av).localeCompare(String(bv));
  return state.sortDir === "asc" ? result : -result;
}

function filteredRows() {
  return activeRows().filter(rowMatches).sort(compareRows);
}

function buildOptions(select, values, emptyLabel) {
  const current = select.value;
  select.textContent = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = emptyLabel;
  select.appendChild(empty);
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  select.value = values.includes(current) ? current : "";
}

function renderFilters() {
  const rows = activeRows();
  const types = [...new Set(rows.map((row) => row.paper_type).filter(Boolean))].sort();
  const volumes = [...new Set(rows.map((row) => row.proceeding_title).filter(Boolean))].sort();
  buildOptions(els.typeFilter, types, "All types");
  buildOptions(els.volumeFilter, volumes, "All volumes");
  const isProceedings = state.tab === "proceedings";
  els.volumeFilter.hidden = !isProceedings;
  els.volumeFilterLabel.hidden = !isProceedings;
}

function renderMetrics() {
  const payload = activePayload();
  els.sourceTitle.textContent = state.tab === "main" ? "Main Papers" : "Proceedings";
  els.sourceRoot.textContent = payload?.root || "--";
  els.metricTotal.textContent = String(payload?.total ?? "--");
  const totals = payload?.issue_totals || {};
  els.metricErrors.textContent = String(totals.error ?? 0);
  els.metricWarnings.textContent = String(totals.warning ?? 0);
  els.updatedAt.textContent = formatDate(payload?.generated_at);
}

function statusPills(row) {
  const pills = [];
  if (row.has_md) pills.push(["MD", "ok"]);
  else pills.push(["No MD", "severe"]);
  if (issueTotal(row) > 0) {
    const counts = row.issue_counts || {};
    if (counts.error) pills.push([`${counts.error} error`, "severe"]);
    if (counts.warning) pills.push([`${counts.warning} warn`, "warn"]);
  } else {
    pills.push(["clean", "ok"]);
  }
  if (row.has_l3) pills.push(["L3", ""]);
  if (row.toc_count) pills.push([`TOC ${row.toc_count}`, ""]);
  return pills;
}

function renderTable() {
  const rows = filteredRows();
  els.tableBody.textContent = "";
  els.emptyState.hidden = rows.length > 0;
  els.tableCount.textContent = `${rows.length} shown`;
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = state.selected[state.tab] === row.paper_id ? "is-selected" : "";
    tr.addEventListener("click", () => selectRow(row.paper_id));

    const titleCell = document.createElement("td");
    const titleWrap = document.createElement("div");
    titleWrap.className = "title-cell";
    const title = document.createElement("div");
    title.className = "paper-title";
    title.textContent = text(row.title);
    const id = document.createElement("div");
    id.className = "paper-id";
    id.textContent = row.dir_name || row.paper_id;
    titleWrap.append(title, id);
    titleCell.appendChild(titleWrap);
    tr.appendChild(titleCell);

    for (const value of [row.authors_text, row.year, row.paper_type]) {
      const td = document.createElement("td");
      td.textContent = text(value);
      tr.appendChild(td);
    }

    const status = document.createElement("td");
    const pillRow = document.createElement("div");
    pillRow.className = "pill-row";
    for (const [label, kind] of statusPills(row)) {
      const pill = document.createElement("span");
      pill.className = `pill ${kind}`;
      pill.textContent = label;
      pillRow.appendChild(pill);
    }
    if (row.has_pdf && row.pdf_url) {
      const pdfButton = document.createElement("button");
      pdfButton.className = "pill pdf-pill";
      pdfButton.type = "button";
      pdfButton.textContent = "PDF";
      pdfButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openPdf(row);
      });
      pillRow.appendChild(pdfButton);
    }
    status.appendChild(pillRow);
    tr.appendChild(status);
    els.tableBody.appendChild(tr);
  }
}

function renderMetadata(detail) {
  els.metadataGrid.textContent = "";
  const pairs = [
    ["ID", detail.paper_id],
    ["Directory", detail.dir_name],
    ["Authors", detail.authors_text],
    ["Year", detail.year],
    ["Type", detail.paper_type],
    ["Journal", detail.journal],
    ["DOI", detail.doi],
  ];
  if (state.tab === "proceedings") pairs.splice(2, 0, ["Volume", detail.proceeding_title]);
  for (const [label, value] of pairs) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = text(value);
    els.metadataGrid.append(dt, dd);
  }
}

function renderIssues(detail) {
  els.issueList.textContent = "";
  const issues = detail.issues || [];
  if (!issues.length) {
    const empty = document.createElement("div");
    empty.className = "pill ok";
    empty.textContent = "No audit issues";
    els.issueList.appendChild(empty);
    return;
  }
  for (const issue of issues) {
    const item = document.createElement("div");
    item.className = `issue-item ${issue.severity}`;
    const rule = document.createElement("div");
    rule.className = "issue-rule";
    rule.textContent = `${issue.severity}: ${issue.rule}`;
    const message = document.createElement("div");
    message.className = "issue-message";
    message.textContent = issue.message;
    item.append(rule, message);
    els.issueList.appendChild(item);
  }
}

function renderToc(detail) {
  els.tocList.textContent = "";
  const toc = detail.toc || [];
  if (!toc.length) {
    const empty = document.createElement("div");
    empty.className = "pill";
    empty.textContent = "No TOC";
    els.tocList.appendChild(empty);
    return;
  }
  for (const entry of toc) {
    const item = document.createElement("div");
    item.className = "toc-item";
    const title = document.createElement("span");
    title.textContent = `${"#".repeat(Number(entry.level || 1))} ${entry.title || ""}`;
    const line = document.createElement("span");
    line.textContent = entry.line ? `L${entry.line}` : "";
    item.append(title, line);
    els.tocList.appendChild(item);
  }
}

function renderDetail(detail) {
  if (!detail) {
    els.detailTitle.textContent = "Select a record";
    els.detailSubtitle.textContent = "--";
    els.metadataGrid.textContent = "";
    els.issueList.textContent = "";
    els.detailAbstract.textContent = "--";
    els.detailConclusion.textContent = "--";
    els.tocList.textContent = "";
    return;
  }
  els.detailTitle.textContent = text(detail.title);
  els.detailSubtitle.textContent = detail.dir_name || detail.paper_id;
  renderMetadata(detail);
  renderIssues(detail);
  els.detailAbstract.textContent = text(detail.abstract);
  els.detailConclusion.textContent = text(detail.l3_conclusion);
  renderToc(detail);
}

function showRecords() {
  state.pdf = null;
  els.pdfFrame.removeAttribute("src");
  els.recordsToolbarTitle.hidden = false;
  els.refreshButton.hidden = false;
  els.recordsView.hidden = false;
  els.pdfToolbarTitle.hidden = true;
  els.pdfViewer.hidden = true;
}

function openPdf(row) {
  state.pdf = { url: row.pdf_url, title: row.title || row.dir_name || row.paper_id };
  els.pdfTitle.textContent = text(state.pdf.title);
  els.pdfFrame.src = row.pdf_url;
  els.recordsToolbarTitle.hidden = true;
  els.refreshButton.hidden = true;
  els.recordsView.hidden = true;
  els.pdfToolbarTitle.hidden = false;
  els.pdfViewer.hidden = false;
}

async function selectRow(paperId) {
  state.selected[state.tab] = paperId;
  renderTable();
  try {
    const endpoint = state.tab === "main" ? "/api/main/detail" : "/api/proceedings/detail";
    state.detail = await fetchJson(`${endpoint}?id=${encodeURIComponent(paperId)}`);
    renderDetail(state.detail);
    setConnection("live", "Live");
  } catch (err) {
    setConnection("error", "Detail failed");
    renderDetail({ title: "Detail unavailable", abstract: String(err), commands: {} });
  }
}

function chooseDefaultSelection() {
  const selected = state.selected[state.tab];
  const rows = filteredRows();
  if (selected && rows.some((row) => row.paper_id === selected)) return selected;
  return rows[0]?.paper_id || "";
}

async function refreshActive({ keepSelection = true } = {}) {
  const endpoint = state.tab === "main" ? "/api/main/papers" : "/api/proceedings/papers";
  try {
    const payload = await fetchJson(endpoint);
    state.payload[state.tab] = payload;
    state.rows[state.tab] = payload.papers || [];
    if (state.pdf && !state.rows[state.tab].some((row) => row.pdf_url === state.pdf.url)) {
      showRecords();
    }
    renderFilters();
    renderMetrics();
    renderTable();
    setConnection("live", "Live");
    const nextSelection = keepSelection ? chooseDefaultSelection() : filteredRows()[0]?.paper_id || "";
    if (nextSelection) await selectRow(nextSelection);
    else renderDetail(null);
  } catch (err) {
    setConnection("error", "Refresh failed");
    els.tableCount.textContent = String(err);
  }
}

function schedulePoll() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => refreshActive({ keepSelection: true }), POLL_MS);
}

function switchTab(tab) {
  if (state.tab === tab) return;
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === tab);
  });
  state.filters.volume = "";
  els.volumeFilter.value = "";
  showRecords();
  renderDetail(null);
  refreshActive({ keepSelection: true });
}

function bindEvents() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  els.searchInput.addEventListener("input", () => {
    state.filters.search = els.searchInput.value.trim();
    renderTable();
  });
  els.typeFilter.addEventListener("change", () => {
    state.filters.type = els.typeFilter.value;
    renderTable();
  });
  els.volumeFilter.addEventListener("change", () => {
    state.filters.volume = els.volumeFilter.value;
    renderTable();
  });
  els.filterIssues.addEventListener("change", () => {
    state.filters.issues = els.filterIssues.checked;
    renderTable();
  });
  els.filterMissingMd.addEventListener("change", () => {
    state.filters.missingMd = els.filterMissingMd.checked;
    renderTable();
  });
  els.refreshButton.addEventListener("click", () => refreshActive({ keepSelection: true }));
  els.pdfBackButton.addEventListener("click", showRecords);
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else {
        state.sortKey = key;
        state.sortDir = key === "year" ? "desc" : "asc";
      }
      renderTable();
    });
  });
}

bindEvents();
refreshActive({ keepSelection: false });
schedulePoll();
