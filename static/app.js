"use strict";

// ── Project config (populated from /api/config) ─────────────────────
let PROJECT_COLORS = {};
let PROJECT_NAMES  = {};

// For cycle time: growing is bad (taking longer), shrinking is good
const CT_TREND = {
  growing:             { text: "Growing ↑ — cycle time increasing",  css: "trend-bad"     },
  shrinking:           { text: "Shrinking ↓ — cycle time improving", css: "trend-good"    },
  stable:              { text: "Stable →",                           css: "trend-neutral" },
  "highly variable":   { text: "Highly Variable ⚡",                 css: "trend-warn"    },
  "insufficient data": { text: "Insufficient data",                  css: "trend-neutral" },
};

// For volume: growing means more incoming work
const VOL_TREND = {
  growing:             { text: "Growing ↑",       css: "trend-warn"    },
  shrinking:           { text: "Shrinking ↓",     css: "trend-neutral" },
  stable:              { text: "Stable →",         css: "trend-neutral" },
  "highly variable":   { text: "Highly Variable ⚡", css: "trend-warn"  },
  "insufficient data": { text: "Insufficient data", css: "trend-neutral"},
};

let charts = {};
let allProjects = [];
let jiraBaseUrl = "";

// ── Init ────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  setupTabNav();
  await api("/api/config").then(c => {
    jiraBaseUrl = c.jira_base_url || "";
    Object.entries(c.projects || {}).forEach(([key, p]) => {
      PROJECT_COLORS[key] = p.color;
      PROJECT_NAMES[key]  = p.name;
    });
  }).catch(() => {});
  await initProjects();
  await populateEngineerDropdown();
  await loadOverview();
  checkSyncStatus();
});

// ── Tab navigation ──────────────────────────────────────────────────
function setupTabNav() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.getElementById(`tab-${tab}`).classList.add("active");
      btn.classList.add("active");
      // Lazy-load on first visit
      if (tab === "cycle-time"      && !charts["ct-chart"])  loadCycleTime();
      if (tab === "volume"          && !charts["vol-chart"]) loadVolume();
      if (tab === "velocity"        && !charts["vel-chart"]) loadVelocity();
      if (tab === "stage-durations" && !charts["sd-chart"])  loadStageDurations();
    });
  });
}

// ── API helpers ─────────────────────────────────────────────────────
async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

async function postApi(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

function showError(msg) {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

// ── Chart helpers ───────────────────────────────────────────────────
function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function makeBarChart(id, labels, datasets, opts = {}) {
  destroyChart(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  charts[id] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "top" } },
      scales: { y: { beginAtZero: true } },
      ...opts,
    },
  });
}

function makeHorizontalBarChart(id, labels, data, color, opts = {}) {
  destroyChart(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  charts[id] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "Avg days", data, backgroundColor: color }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
      ...opts,
    },
  });
}

// ── Project selects ─────────────────────────────────────────────────
async function initProjects() {
  try {
    allProjects = await api("/api/projects");
  } catch (e) {
    showError("Failed to load projects: " + e.message);
    allProjects = ["FED", "DIP", "SUP", "OOT", "SSJ"].map(k => ({ key: k, name: PROJECT_NAMES[k] || k }));
  }
  ["ct-project", "vol-project", "vel-project", "sd-project"].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    allProjects.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.key;
      opt.textContent = p.key;
      sel.appendChild(opt);
    });
  });
}

// ── Engineer dropdown ───────────────────────────────────────────────
async function populateEngineerDropdown() {
  try {
    const data = await api("/api/engineers");
    const sel = document.getElementById("eng-select");
    data.engineers.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
  } catch (e) {
    showError("Failed to load engineers: " + e.message);
  }
}

// ── Overview ────────────────────────────────────────────────────────
async function loadOverview() {
  const weeks = document.getElementById("ov-weeks").value;
  const grid = document.getElementById("overview-grid");
  grid.innerHTML = "<p class=\"loading-text\" style=\"padding:var(--xl) 0;text-align:center\">Loading…</p>";

  try {
    const projects = allProjects.length ? allProjects : await api("/api/projects");
    grid.innerHTML = "";

    for (const p of projects) {
      let ctData = { weeks: [], trend: "insufficient data" };
      let volData = { weeks: [], trend: "insufficient data" };
      try { ctData  = await api(`/api/projects/${p.key}/cycle-time?weeks=${weeks}`); } catch (_) {}
      try { volData = await api(`/api/projects/${p.key}/volume?weeks=${weeks}`); } catch (_) {}

      const avgDays = ctData.weeks.length
        ? (ctData.weeks.reduce((a, w) => a + (w.avg_days || 0), 0) / ctData.weeks.length).toFixed(1)
        : "—";
      const avgVol = volData.weeks.length
        ? (volData.weeks.reduce((a, w) => a + w.ticket_count, 0) / volData.weeks.length).toFixed(1)
        : "—";

      const ctBadge  = CT_TREND[ctData.trend]  || CT_TREND["insufficient data"];
      const volBadge = VOL_TREND[volData.trend] || VOL_TREND["insufficient data"];
      const color = PROJECT_COLORS[p.key] || "#6B7280";

      const card = document.createElement("div");
      card.className = "overview-card";
      card.style.borderTop = `4px solid ${color}`;
      const projectUrl = jiraBaseUrl ? `${jiraBaseUrl}/browse/${p.key}` : null;
      const nameText = p.name || PROJECT_NAMES[p.key] || "";
      const nameHtml = projectUrl
        ? `<a href="${projectUrl}" target="_blank" rel="noopener">${nameText}</a>`
        : nameText;
      card.innerHTML = `
        <h3>${p.key}</h3>
        <p class="card-name">${nameHtml}</p>
        <div class="card-metric">
          <span class="metric-label">Avg cycle time (${weeks}w)</span>
          <span class="metric-value">${avgDays === "—" ? "—" : avgDays + "d"}</span>
          <span class="trend-badge ${ctBadge.css}">${ctBadge.text}</span>
        </div>
        <div class="card-metric">
          <span class="metric-label">Avg tickets/week</span>
          <span class="metric-value">${avgVol}</span>
          <span class="trend-badge ${volBadge.css}">${volBadge.text}</span>
        </div>`;
      grid.appendChild(card);
    }

    if (!projects.length) {
      grid.innerHTML = "<p class=\"empty-state\">No projects found. Trigger a sync to populate data.</p>";
    }
  } catch (e) {
    grid.innerHTML = `<p class="empty-state">Failed to load overview: ${e.message}</p>`;
  }
}

// ── Cycle Time ──────────────────────────────────────────────────────
function setChartLoading(canvasId, loading) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrapper = canvas.parentElement;
  if (loading) {
    if (!wrapper.querySelector(".chart-loading")) {
      const div = document.createElement("div");
      div.className = "chart-loading loading-text";
      div.style.cssText = "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%)";
      div.textContent = "Loading…";
      wrapper.appendChild(div);
    }
  } else {
    const el = wrapper.querySelector(".chart-loading");
    if (el) el.remove();
  }
}

async function loadCycleTime() {
  const project = document.getElementById("ct-project").value;
  const weeks   = document.getElementById("ct-weeks").value;
  if (!project) return;

  const badge = document.getElementById("ct-trend-badge");
  const empty = document.getElementById("ct-empty");
  setChartLoading("ct-chart", true);

  try {
    const data = await api(`/api/projects/${project}/cycle-time?weeks=${weeks}`);
    const labels = data.weeks.map(w => w.week);
    const values = data.weeks.map(w => parseFloat((w.avg_days || 0).toFixed(1)));
    const color  = PROJECT_COLORS[project] || "#6B7280";

    setChartLoading("ct-chart", false);
    if (!labels.length) {
      destroyChart("ct-chart");
      badge.style.display = "none";
      empty.style.display = "";
      return;
    }

    empty.style.display = "none";
    closeDrilldown("ct");
    makeBarChart("ct-chart", labels, [{
      label: "Avg Cycle Time (days)",
      data: values,
      backgroundColor: color + "cc",
      borderColor: color,
      borderWidth: 1,
    }], {
      onClick: (event, elements) => {
        if (!elements.length) return;
        const week = labels[elements[0].index];
        loadIssueTable("ct", project, week, "resolved");
      },
      onHover: (event, elements) => {
        event.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
    });

    const b = CT_TREND[data.trend] || CT_TREND["insufficient data"];
    badge.textContent = b.text;
    badge.className = `trend-badge ${b.css}`;
    badge.style.display = "";
  } catch (e) {
    setChartLoading("ct-chart", false);
    showError("Cycle time load failed: " + e.message);
  }
}

// ── Volume ──────────────────────────────────────────────────────────
async function loadVolume() {
  const project = document.getElementById("vol-project").value;
  const weeks   = document.getElementById("vol-weeks").value;
  if (!project) return;

  const badge = document.getElementById("vol-trend-badge");
  const empty = document.getElementById("vol-empty");
  setChartLoading("vol-chart", true);

  try {
    const data = await api(`/api/projects/${project}/volume?weeks=${weeks}`);
    const labels = data.weeks.map(w => w.week);
    const values = data.weeks.map(w => w.ticket_count);
    const color  = PROJECT_COLORS[project] || "#6B7280";

    setChartLoading("vol-chart", false);
    if (!labels.length) {
      destroyChart("vol-chart");
      badge.style.display = "none";
      empty.style.display = "";
      return;
    }

    empty.style.display = "none";
    closeDrilldown("vol");
    makeBarChart("vol-chart", labels, [{
      label: "Tickets Created",
      data: values,
      backgroundColor: color + "cc",
      borderColor: color,
      borderWidth: 1,
    }], {
      onClick: (event, elements) => {
        if (!elements.length) return;
        const week = labels[elements[0].index];
        loadIssueTable("vol", project, week, "created");
      },
      onHover: (event, elements) => {
        event.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
    });

    const b = VOL_TREND[data.trend] || VOL_TREND["insufficient data"];
    badge.textContent = b.text;
    badge.className = `trend-badge ${b.css}`;
    badge.style.display = "";
  } catch (e) {
    setChartLoading("vol-chart", false);
    showError("Volume load failed: " + e.message);
  }
}

// ── Velocity ─────────────────────────────────────────────────────────
async function loadVelocity() {
  const project = document.getElementById("vel-project").value;
  const weeks   = document.getElementById("vel-weeks").value;
  const empty   = document.getElementById("vel-empty");
  const statsBar = document.getElementById("vel-stats");
  if (!project) return;

  closeDrilldown("vel");

  try {
    const data = await api(`/api/projects/${project}/velocity?weeks=${weeks}`);
    const sprints = [...data.sprints].reverse();
    const color   = PROJECT_COLORS[project] || "#6B7280";

    if (!sprints.length) {
      destroyChart("vel-chart");
      empty.style.display = "";
      statsBar.style.display = "none";
      return;
    }

    empty.style.display = "none";

    const s = data.stats;
    document.getElementById("vel-stat-mean").textContent   = s.mean   != null ? s.mean   : "—";
    document.getElementById("vel-stat-median").textContent = s.median != null ? s.median : "—";
    document.getElementById("vel-stat-stddev").textContent = s.std_dev != null ? s.std_dev : "—";
    statsBar.style.display = "";
    const labels    = sprints.map(s => s.name);
    const committed = sprints.map(s => s.committed || 0);
    const completed = sprints.map(s => s.completed || 0);

    makeBarChart("vel-chart", labels, [
      {
        label: "Committed (tickets)",
        data: committed,
        backgroundColor: "#93C5FDcc",
        borderColor: "#93C5FD",
        borderWidth: 1,
      },
      {
        label: "Completed (tickets)",
        data: completed,
        backgroundColor: color + "cc",
        borderColor: color,
        borderWidth: 1,
      },
    ], {
      onClick: (event, elements) => {
        if (!elements.length) return;
        const sprint = sprints[elements[0].index];
        loadSprintIssueTable(sprint.sprint_id, sprint.name);
      },
      onHover: (event, elements) => {
        event.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
    });
  } catch (e) {
    showError("Velocity load failed: " + e.message);
  }
}

async function loadSprintIssueTable(sprintId, sprintName) {
  const panel    = document.getElementById("vel-drilldown");
  const title    = document.getElementById("vel-drilldown-title");
  const tableDiv = document.getElementById("vel-drilldown-table");

  title.textContent = sprintName;
  panel.style.display = "";
  tableDiv.innerHTML = '<p class="loading-text" style="padding:12px 16px">Loading tickets…</p>';

  try {
    const data = await api(`/api/sprints/${encodeURIComponent(sprintId)}/issues`);
    _tableSort = { col: null, asc: true };
    renderSprintIssueTable(tableDiv, data.issues);
  } catch (e) {
    tableDiv.innerHTML = `<p class="empty-state">Failed to load tickets: ${e.message}</p>`;
  }
}

function renderSprintIssueTable(container, issues) {
  if (!issues.length) {
    container.innerHTML = '<p class="empty-state" style="padding:16px">No tickets found.</p>';
    return;
  }

  const cols = [
    { key: "issue_key",      label: "Issue" },
    { key: "summary",        label: "Summary" },
    { key: "assignee",       label: "Assignee" },
    { key: "status",         label: "Status" },
    { key: "priority",       label: "Priority" },
    { key: "completed",      label: "Done" },
    { key: "cycle_time_days", label: "Cycle Time" },
    { key: "resolved_at",    label: "Resolved" },
  ];

  function sorted(col, asc) {
    return [...issues].sort((a, b) => {
      let av = a[col], bv = b[col];
      if (av == null) av = asc ? "\uFFFF" : "";
      if (bv == null) bv = asc ? "\uFFFF" : "";
      if (typeof av === "number" && typeof bv === "number") return asc ? av - bv : bv - av;
      return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }

  function renderCell(c, issue) {
    const val = issue[c.key];
    if (c.key === "issue_key") {
      const href = jiraBaseUrl ? `${jiraBaseUrl}/browse/${val}` : "#";
      return `<td><a href="${href}" target="_blank" rel="noopener">${escapeHtml(val || "")}</a></td>`;
    }
    if (c.key === "completed") {
      return `<td>${val ? "✓" : ""}</td>`;
    }
    if (c.key === "cycle_time_days") {
      return `<td class="cell-num">${val != null ? Number(val).toFixed(1) + "d" : "—"}</td>`;
    }
    if (c.key === "resolved_at") {
      return `<td class="cell-muted">${val ? val.slice(0, 10) : "—"}</td>`;
    }
    if (c.key === "summary") {
      const s = escapeHtml(val || "");
      return `<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${s}">${s}</td>`;
    }
    return `<td>${escapeHtml(String(val || ""))}</td>`;
  }

  function draw(sortCol, sortAsc) {
    const rows  = sorted(sortCol, sortAsc);
    const thead = cols.map(c => {
      const cls = c.key === sortCol ? (sortAsc ? "sort-asc" : "sort-desc") : "";
      return `<th data-col="${c.key}" class="${cls}">${c.label}</th>`;
    }).join("");
    const tbody = rows.map(issue =>
      `<tr>${cols.map(c => renderCell(c, issue)).join("")}</tr>`
    ).join("");
    container.innerHTML = `<table class="data-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
  }

  draw(null, true);

  if (container._sortListener) container.removeEventListener("click", container._sortListener);
  container._sortListener = e => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = th.dataset.col;
    const nextAsc = col === _tableSort.col ? !_tableSort.asc : true;
    _tableSort = { col, asc: nextAsc };
    draw(_tableSort.col, _tableSort.asc);
  };
  container.addEventListener("click", container._sortListener);
}

// ── Engineers ─────────────────────────────────────────────────────────
async function loadEngineerStats() {
  const name  = document.getElementById("eng-select").value;
  const weeks = document.getElementById("eng-weeks").value;
  const prompt = document.getElementById("eng-prompt");
  const cards  = document.getElementById("eng-cards");
  const chartWrapper = document.getElementById("eng-chart-wrapper");

  closeDrilldown("eng");

  if (!name) {
    prompt.style.display = "";
    cards.innerHTML = "";
    chartWrapper.style.display = "none";
    destroyChart("eng-chart");
    return;
  }

  prompt.style.display = "none";

  try {
    const data = await api(`/api/engineers/${encodeURIComponent(name)}/stats?weeks=${weeks}`);
    const { by_project, by_week, avg_per_sprint } = data.stats;

    if (!by_project.length) {
      cards.innerHTML = `<p class="empty-state">No resolved tickets found for ${name} in the last ${weeks} weeks.</p>`;
      chartWrapper.style.display = "none";
      destroyChart("eng-chart");
      return;
    }

    const totalTickets = by_project.reduce((s, p) => s + p.tickets, 0);
    const summaryCard = `
      <div class="stat-card" style="border-top:4px solid var(--text-muted)">
        <div class="stat-card-project" style="color:var(--text-muted)">Overall</div>
        <div class="stat-row"><span>Tickets resolved</span><strong>${totalTickets}</strong></div>
        <div class="stat-row"><span>Avg stories / sprint</span><strong>${avg_per_sprint != null ? avg_per_sprint : "—"}</strong></div>
      </div>`;

    cards.innerHTML = summaryCard + by_project.map(p => {
      const color = PROJECT_COLORS[p.project_key] || "#6B7280";
      return `
        <div class="stat-card">
          <div class="stat-card-project" style="color:${color}">${p.project_key}</div>
          <div class="stat-row"><span>Tickets resolved</span><strong>${p.tickets}</strong></div>
          <div class="stat-row"><span>Avg cycle time</span><strong>${p.avg_cycle_time != null ? p.avg_cycle_time.toFixed(1) + "d" : "—"}</strong></div>
          <div class="stat-row"><span>Story points</span><strong>${p.total_points || 0}</strong></div>
        </div>`;
    }).join("");

    if (by_week.length) {
      chartWrapper.style.display = "";
      const weekData = by_week;
      makeBarChart("eng-chart",
        weekData.map(w => w.week),
        [{
          label: "Tickets Resolved",
          data: weekData.map(w => w.tickets),
          backgroundColor: "#7C3AEDcc",
          borderColor: "#7C3AED",
          borderWidth: 1,
        }],
        {
          onClick: (event, elements) => {
            if (!elements.length) return;
            const week = weekData[elements[0].index].week;
            loadEngineerWeekTable(name, week);
          },
          onHover: (event, elements) => {
            event.native.target.style.cursor = elements.length ? "pointer" : "default";
          },
        }
      );
    } else {
      chartWrapper.style.display = "none";
      destroyChart("eng-chart");
    }
  } catch (e) {
    showError("Engineer stats load failed: " + e.message);
  }
}

async function loadEngineerWeekTable(name, week) {
  const panel    = document.getElementById("eng-drilldown");
  const title    = document.getElementById("eng-drilldown-title");
  const tableDiv = document.getElementById("eng-drilldown-table");

  title.textContent = `${name} — ${week}`;
  panel.style.display = "";
  tableDiv.innerHTML = '<p class="loading-text" style="padding:12px 16px">Loading tickets…</p>';

  try {
    const data = await api(`/api/engineers/${encodeURIComponent(name)}/issues?week=${encodeURIComponent(week)}`);
    _tableSort = { col: null, asc: true };
    renderEngineerIssueTable(tableDiv, data.issues);
  } catch (e) {
    tableDiv.innerHTML = `<p class="empty-state">Failed to load tickets: ${e.message}</p>`;
  }
}

function renderEngineerIssueTable(container, issues) {
  if (!issues.length) {
    container.innerHTML = '<p class="empty-state" style="padding:16px">No tickets found.</p>';
    return;
  }

  const cols = [
    { key: "issue_key",       label: "Issue" },
    { key: "summary",         label: "Summary" },
    { key: "project_key",     label: "Project" },
    { key: "status",          label: "Status" },
    { key: "priority",        label: "Priority" },
    { key: "cycle_time_days", label: "Cycle Time" },
    { key: "resolved_at",     label: "Resolved" },
  ];

  function sorted(col, asc) {
    return [...issues].sort((a, b) => {
      let av = a[col], bv = b[col];
      if (av == null) av = asc ? "\uFFFF" : "";
      if (bv == null) bv = asc ? "\uFFFF" : "";
      if (typeof av === "number" && typeof bv === "number") return asc ? av - bv : bv - av;
      return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }

  function renderCell(c, issue) {
    const val = issue[c.key];
    if (c.key === "issue_key") {
      const href = jiraBaseUrl ? `${jiraBaseUrl}/browse/${val}` : "#";
      return `<td><a href="${href}" target="_blank" rel="noopener">${escapeHtml(val || "")}</a></td>`;
    }
    if (c.key === "cycle_time_days") {
      return `<td class="cell-num">${val != null ? Number(val).toFixed(1) + "d" : "—"}</td>`;
    }
    if (c.key === "resolved_at") {
      return `<td class="cell-muted">${val ? val.slice(0, 10) : "—"}</td>`;
    }
    if (c.key === "project_key") {
      const color = PROJECT_COLORS[val] || "#6B7280";
      return `<td><span style="color:${color};font-weight:600">${escapeHtml(val || "")}</span></td>`;
    }
    if (c.key === "summary") {
      const s = escapeHtml(val || "");
      return `<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${s}">${s}</td>`;
    }
    return `<td>${escapeHtml(String(val || ""))}</td>`;
  }

  function draw(sortCol, sortAsc) {
    const rows  = sorted(sortCol, sortAsc);
    const thead = cols.map(c => {
      const cls = c.key === sortCol ? (sortAsc ? "sort-asc" : "sort-desc") : "";
      return `<th data-col="${c.key}" class="${cls}">${c.label}</th>`;
    }).join("");
    const tbody = rows.map(issue =>
      `<tr>${cols.map(c => renderCell(c, issue)).join("")}</tr>`
    ).join("");
    container.innerHTML = `<table class="data-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
  }

  draw(null, true);

  if (container._sortListener) container.removeEventListener("click", container._sortListener);
  container._sortListener = e => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = th.dataset.col;
    const nextAsc = col === _tableSort.col ? !_tableSort.asc : true;
    _tableSort = { col, asc: nextAsc };
    draw(_tableSort.col, _tableSort.asc);
  };
  container.addEventListener("click", container._sortListener);
}

// ── Stage Durations ───────────────────────────────────────────────────
async function loadStageDurations() {
  const project = document.getElementById("sd-project").value;
  const weeks   = document.getElementById("sd-weeks").value;
  const empty   = document.getElementById("sd-empty");
  if (!project) return;

  closeDrilldown("sd");

  try {
    const data   = await api(`/api/projects/${project}/stage-durations?weeks=${weeks}`);
    const stages = data.stages;
    const color  = PROJECT_COLORS[project] || "#6B7280";

    if (!stages.length) {
      destroyChart("sd-chart");
      empty.style.display = "";
      return;
    }

    empty.style.display = "none";
    makeHorizontalBarChart(
      "sd-chart",
      stages.map(s => s.stage),
      stages.map(s => parseFloat((s.avg_days || 0).toFixed(1))),
      color + "cc",
      {
        onClick: (event, elements) => {
          if (!elements.length) return;
          const stage = stages[elements[0].index].stage;
          loadStageIssueTable(project, stage, weeks);
        },
        onHover: (event, elements) => {
          event.native.target.style.cursor = elements.length ? "pointer" : "default";
        },
      },
    );
  } catch (e) {
    showError("Stage durations load failed: " + e.message);
  }
}

async function loadStageIssueTable(project, stage, weeks) {
  const panel    = document.getElementById("sd-drilldown");
  const title    = document.getElementById("sd-drilldown-title");
  const tableDiv = document.getElementById("sd-drilldown-table");

  title.textContent = `${project} — ${stage}`;
  panel.style.display = "";
  tableDiv.innerHTML = '<p class="loading-text" style="padding:12px 16px">Loading tickets…</p>';

  try {
    const data = await api(`/api/projects/${project}/stage-issues?stage=${encodeURIComponent(stage)}&weeks=${weeks}`);
    _tableSort = { col: null, asc: true };
    renderStageIssueTable(tableDiv, data.issues, stage);
  } catch (e) {
    tableDiv.innerHTML = `<p class="empty-state">Failed to load tickets: ${e.message}</p>`;
  }
}

function renderStageIssueTable(container, issues, stage) {
  if (!issues.length) {
    container.innerHTML = '<p class="empty-state" style="padding:16px">No tickets found.</p>';
    return;
  }

  const cols = [
    { key: "issue_key",   label: "Issue" },
    { key: "summary",     label: "Summary" },
    { key: "assignee",    label: "Assignee" },
    { key: "status",      label: "Status" },
    { key: "priority",    label: "Priority" },
    { key: "stage_days",  label: `Days in stage` },
    { key: "resolved_at", label: "Resolved" },
  ];

  function sorted(col, asc) {
    return [...issues].sort((a, b) => {
      let av = a[col], bv = b[col];
      if (av == null) av = asc ? "\uFFFF" : "";
      if (bv == null) bv = asc ? "\uFFFF" : "";
      if (typeof av === "number" && typeof bv === "number") return asc ? av - bv : bv - av;
      return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }

  function renderCell(c, issue) {
    const val = issue[c.key];
    if (c.key === "issue_key") {
      const href = jiraBaseUrl ? `${jiraBaseUrl}/browse/${val}` : "#";
      return `<td><a href="${href}" target="_blank" rel="noopener">${escapeHtml(val || "")}</a></td>`;
    }
    if (c.key === "stage_days") {
      return `<td class="cell-num">${val != null ? Number(val).toFixed(1) + "d" : "—"}</td>`;
    }
    if (c.key === "resolved_at") {
      return `<td class="cell-muted">${val ? val.slice(0, 10) : "—"}</td>`;
    }
    if (c.key === "summary") {
      const s = escapeHtml(val || "");
      return `<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${s}">${s}</td>`;
    }
    return `<td>${escapeHtml(String(val || ""))}</td>`;
  }

  function draw(sortCol, sortAsc) {
    const rows  = sorted(sortCol, sortAsc);
    const thead = cols.map(c => {
      const cls = c.key === sortCol ? (sortAsc ? "sort-asc" : "sort-desc") : "";
      return `<th data-col="${c.key}" class="${cls}">${c.label}</th>`;
    }).join("");
    const tbody = rows.map(issue =>
      `<tr>${cols.map(c => renderCell(c, issue)).join("")}</tr>`
    ).join("");
    container.innerHTML = `<table class="data-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
  }

  draw(null, true);

  if (container._sortListener) container.removeEventListener("click", container._sortListener);
  container._sortListener = e => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = th.dataset.col;
    const nextAsc = col === _tableSort.col ? !_tableSort.asc : true;
    _tableSort = { col, asc: nextAsc };
    draw(_tableSort.col, _tableSort.asc);
  };
  container.addEventListener("click", container._sortListener);
}

// ── Drill-down table ──────────────────────────────────────────────────
let _tableSort = { col: null, asc: true };

async function loadIssueTable(prefix, project, week, type) {
  const panel = document.getElementById(`${prefix}-drilldown`);
  const title = document.getElementById(`${prefix}-drilldown-title`);
  const tableDiv = document.getElementById(`${prefix}-drilldown-table`);

  const typeLabel = type === "resolved" ? "resolved" : "created";
  title.textContent = `${project} — ${week} — ${typeLabel} tickets`;
  panel.style.display = "";
  tableDiv.innerHTML = '<p class="loading-text" style="padding:12px 16px">Loading tickets…</p>';

  try {
    const data = await api(`/api/projects/${project}/issues?week=${encodeURIComponent(week)}&type=${type}`);
    _tableSort = { col: null, asc: true };
    renderIssueTable(tableDiv, data.issues, type);
  } catch (e) {
    tableDiv.innerHTML = `<p class="empty-state">Failed to load tickets: ${e.message}</p>`;
  }
}

function closeDrilldown(prefix) {
  const panel = document.getElementById(`${prefix}-drilldown`);
  if (panel) panel.style.display = "none";
}

function renderIssueTable(container, issues, type) {
  if (!issues.length) {
    container.innerHTML = '<p class="empty-state" style="padding:16px">No tickets found.</p>';
    return;
  }

  const cols = [
    { key: "issue_key",       label: "Issue"      },
    { key: "summary",         label: "Summary"    },
    { key: "assignee",        label: "Assignee"   },
    { key: "status",          label: "Status"     },
    { key: "priority",        label: "Priority"   },
    { key: "cycle_time_days", label: "Cycle Time" },
    { key: "created_at",      label: "Created"    },
  ];
  if (type === "resolved") {
    cols.push({ key: "resolved_at", label: "Resolved" });
  }

  function sorted(col, asc) {
    return [...issues].sort((a, b) => {
      let av = a[col], bv = b[col];
      if (av == null) av = asc ? "\uFFFF" : "";
      if (bv == null) bv = asc ? "\uFFFF" : "";
      if (typeof av === "number" && typeof bv === "number") return asc ? av - bv : bv - av;
      return asc
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }

  function renderCell(c, issue) {
    const val = issue[c.key];
    if (c.key === "issue_key") {
      const href = jiraBaseUrl ? `${jiraBaseUrl}/browse/${val}` : "#";
      return `<td><a href="${href}" target="_blank" rel="noopener">${escapeHtml(val || "")}</a></td>`;
    }
    if (c.key === "cycle_time_days") {
      return `<td class="cell-num">${val != null ? Number(val).toFixed(1) + "d" : "—"}</td>`;
    }
    if (c.key === "created_at" || c.key === "resolved_at") {
      return `<td class="cell-muted">${val ? val.slice(0, 10) : "—"}</td>`;
    }
    if (c.key === "summary") {
      const s = escapeHtml(val || "");
      return `<td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${s}">${s}</td>`;
    }
    return `<td>${escapeHtml(String(val || ""))}</td>`;
  }

  function draw(sortCol, sortAsc) {
    const rows = sorted(sortCol, sortAsc);
    const thead = cols.map(c => {
      const cls = c.key === sortCol ? (sortAsc ? "sort-asc" : "sort-desc") : "";
      return `<th data-col="${c.key}" class="${cls}">${c.label}</th>`;
    }).join("");
    const tbody = rows.map(issue =>
      `<tr>${cols.map(c => renderCell(c, issue)).join("")}</tr>`
    ).join("");
    container.innerHTML = `<table class="data-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
  }

  draw(null, true);

  if (container._sortListener) {
    container.removeEventListener("click", container._sortListener);
  }
  container._sortListener = e => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = th.dataset.col;
    const nextAsc = col === _tableSort.col ? !_tableSort.asc : true;
    _tableSort = { col, asc: nextAsc };
    draw(_tableSort.col, _tableSort.asc);
  };
  container.addEventListener("click", container._sortListener);
}

// ── Insights ──────────────────────────────────────────────────────────
async function askInsights() {
  const q = document.getElementById("insights-q").value.trim();
  if (!q) return;
  const panel = document.getElementById("insights-answer");
  panel.innerHTML = '<span class="loading-text">Thinking…</span>';
  try {
    const res = await postApi("/api/insights", { question: q });
    panel.innerHTML = escapeHtml(res.answer).replace(/\n/g, "<br>");
  } catch (e) {
    panel.innerHTML = `<span style="color:var(--bad)">Error: ${escapeHtml(e.message)}</span>`;
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Sync ──────────────────────────────────────────────────────────────
async function triggerSync() {
  const btn = document.getElementById("refresh-btn");
  const status = document.getElementById("sync-status");
  btn.disabled = true;
  status.textContent = "⟳ Starting sync…";
  try {
    await postApi("/api/sync", {});
    pollSyncStatus(true);
  } catch (e) {
    showError("Failed to start sync: " + e.message);
    btn.disabled = false;
  }
}

function checkSyncStatus() {
  // One-shot check on load; don't loop
  api("/api/sync/status").then(s => {
    const el = document.getElementById("sync-status");
    if (s.running) {
      pollSyncStatus(true);
    } else if (s.last_run) {
      el.textContent = `Last synced ${new Date(s.last_run).toLocaleString()}`;
      if (s.last_error) {
        el.textContent += " ⚠";
        showError("Last sync error: " + s.last_error);
      }
    } else {
      el.textContent = "Not yet synced";
    }
  }).catch(() => {});
}

function pollSyncStatus(reloadOnDone = false) {
  const el  = document.getElementById("sync-status");
  const btn = document.getElementById("refresh-btn");
  el.textContent = "⟳ Syncing…";

  const interval = setInterval(async () => {
    try {
      const s = await api("/api/sync/status");
      if (s.running) {
        el.textContent = "⟳ Syncing…";
        return;
      }
      clearInterval(interval);
      btn.disabled = false;
      if (s.last_error) {
        el.textContent = "Sync failed ⚠";
        showError("Sync error: " + s.last_error);
      } else {
        el.textContent = s.last_run
          ? `Last synced ${new Date(s.last_run).toLocaleString()}`
          : "Sync complete";
        if (reloadOnDone) {
          await loadOverview();
          // Refresh whichever tab is active
          const active = document.querySelector(".tab-pane.active");
          if (active) {
            const id = active.id;
            if (id === "tab-cycle-time")      { destroyChart("ct-chart");  loadCycleTime();      }
            if (id === "tab-volume")           { destroyChart("vol-chart"); loadVolume();          }
            if (id === "tab-velocity")         { destroyChart("vel-chart"); loadVelocity();        }
            if (id === "tab-stage-durations")  { destroyChart("sd-chart");  loadStageDurations(); }
          }
        }
      }
    } catch (_) { /* ignore transient errors */ }
  }, 3000);
}
