"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let META = { algorithms: ["sha256"], default_algorithm: "sha256", data_dir: "/data" };
let jobsCache = [];
let selectedJobId = null;
let pollTimer = null;

// --------------------------------------------------------------------------- //
// API helpers
// --------------------------------------------------------------------------- //
async function api(path, opts = {}) {
  const res = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const msg = (body && body.detail) ? body.detail : `${res.status} ${res.statusText}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

// --------------------------------------------------------------------------- //
// UI utilities
// --------------------------------------------------------------------------- //
function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 4000);
}

function fmtSize(n) {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.label = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${label || "Working…"}`;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.label;
  }
}

function arrow(kind) {
  return kind === "copy" ? "→" : "↔";
}

// --------------------------------------------------------------------------- //
// Tabs
// --------------------------------------------------------------------------- //
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "queue") refreshJobs(true);
  });
});

function goToQueue() {
  document.querySelector('.tab[data-tab="queue"]').click();
}

// --------------------------------------------------------------------------- //
// Result rendering
// --------------------------------------------------------------------------- //
function summaryBar(s) {
  if (!s) return "";
  const chips = [
    `<span class="chip ok">✔ ${s.matching ?? 0} matching</span>`,
  ];
  const diffs = s.differences ?? ((s.mismatching || 0) + (s.only_in_a || 0) + (s.only_in_b || 0));
  if (diffs) chips.push(`<span class="chip bad">✗ ${diffs} differences</span>`);
  if (s.checksum_mismatches) chips.push(`<span class="chip bad">checksum ${s.checksum_mismatches}</span>`);
  if (s.missing_in_b) chips.push(`<span class="chip warn">missing in B ${s.missing_in_b}</span>`);
  if (s.missing_in_a) chips.push(`<span class="chip warn">missing in A ${s.missing_in_a}</span>`);
  if (s.read_errors) chips.push(`<span class="chip bad">read error ${s.read_errors}</span>`);
  // Legacy fallbacks when older jobs lack the new counters
  if (s.checksum_mismatches == null && s.mismatching) {
    chips.push(`<span class="chip bad">✗ ${s.mismatching} mismatching</span>`);
  }
  if (s.missing_in_b == null && s.only_in_a) {
    chips.push(`<span class="chip warn">◀ ${s.only_in_a} only in A</span>`);
  }
  if (s.missing_in_a == null && s.only_in_b) {
    chips.push(`<span class="chip warn">▶ ${s.only_in_b} only in B</span>`);
  }
  return `<div class="summary-bar">${chips.join("")}</div>`;
}

function issueBadge(issue, label) {
  const cls = {
    checksum_mismatch: "bad",
    read_error: "bad",
    missing_in_a: "warn",
    missing_in_b: "warn",
  }[issue] || "";
  return `<span class="issue-badge ${cls}">${esc(label || issue || "—")}</span>`;
}

function fmtSizes(r) {
  const a = r.size_a != null ? fmtSize(r.size_a) : "—";
  const b = r.size_b != null ? fmtSize(r.size_b) : "—";
  if (r.size_a == null && r.size_b == null) return "—";
  if (r.size_a != null && r.size_b == null) return `A: ${a}`;
  if (r.size_b != null && r.size_a == null) return `B: ${b}`;
  if (r.size_a === r.size_b) return a;
  return `A: ${a} · B: ${b}`;
}

function missingWhere(r) {
  const miss = r.missing_from || [];
  if (!miss.length) return "—";
  return miss.map((s) => `Missing in ${s}`).join(", ");
}

function differencesTable(rows) {
  if (!rows || !rows.length) return `<div class="empty">None — all compared files matched.</div>`;
  const body = rows.map((r) => `
    <tr>
      <td>${issueBadge(r.issue, r.issue_label)}</td>
      <td class="path">${esc(r.path)}${r.path_b && r.path_b !== r.path ? ` <span class="ts">↔ ${esc(r.path_b)}</span>` : ""}</td>
      <td class="detail">${esc(r.detail || missingWhere(r))}</td>
      <td class="hash">${esc(r.hash_a ?? "—")}</td>
      <td class="hash">${esc(r.hash_b ?? "—")}</td>
      <td>${fmtSizes(r)}</td>
    </tr>`).join("");
  return `<table class="diff-table">
    <thead><tr>
      <th>Issue</th><th>Path</th><th>Details</th><th>Hash A</th><th>Hash B</th><th>Size</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table>`;
}

function pairTable(rows) {
  if (!rows || !rows.length) return `<div class="empty">None</div>`;
  const body = rows.map((r) => `
    <tr>
      <td class="path">${esc(r.path)}${r.path_b && r.path_b !== r.path ? ` <span class="ts">↔ ${esc(r.path_b)}</span>` : ""}</td>
      <td class="hash">${esc(r.hash_a ?? "—")}</td>
      <td class="hash">${esc(r.hash_b ?? "—")}</td>
      <td>${fmtSizes(r)}</td>
    </tr>`).join("");
  return `<table><thead><tr><th>Path</th><th>Hash A</th><th>Hash B</th><th>Size</th></tr></thead><tbody>${body}</tbody></table>`;
}

function collectDifferences(data) {
  if (data.differences && data.differences.length) return data.differences;
  // Rebuild from legacy fields for older finished jobs
  const out = [];
  for (const r of data.mismatching || []) {
    out.push({
      ...r,
      issue: r.issue || (r.error ? "read_error" : "checksum_mismatch"),
      issue_label: r.issue_label || (r.error ? "Read error" : "Checksum mismatch"),
      detail: r.detail || r.error || "Same path on both sides, but hashes differ",
      missing_from: r.missing_from || [],
    });
  }
  for (const r of data.only_in_a || []) {
    out.push({
      ...r,
      issue: r.issue || "missing_in_b",
      issue_label: r.issue_label || "Missing in B",
      detail: r.detail || "File exists in A but is missing in B",
      missing_from: r.missing_from || ["B"],
      hash_b: r.hash_b ?? null,
      size_b: r.size_b ?? null,
    });
  }
  for (const r of data.only_in_b || []) {
    out.push({
      ...r,
      issue: r.issue || "missing_in_a",
      issue_label: r.issue_label || "Missing in A",
      detail: r.detail || "File exists in B but is missing in A",
      missing_from: r.missing_from || ["A"],
      hash_a: r.hash_a ?? null,
      size_a: r.size_a ?? null,
    });
  }
  return out;
}

function renderResult(data) {
  const s = data.summary || {};
  const diffs = collectDifferences(data);
  let html = summaryBar(s);
  html += `
    <div class="result-group">
      <h3><span class="dot bad"></span>Differences report (${diffs.length})</h3>
      <p class="hint">Checksum mismatch = file on both sides with different hashes. Missing in A/B = path only exists on the other side.</p>
      ${differencesTable(diffs)}
    </div>
    <details class="result-group matching-details">
      <summary><span class="dot ok"></span> Matching files (${s.matching ?? 0})</summary>
      ${pairTable(data.matching || [])}
    </details>`;
  return html;
}

function statusChip(job) {
  const s = job.status;
  if (s === "running") return `<span class="chip warn">running ${Math.round(job.progress || 0)}%</span>`;
  if (s === "queued") return `<span class="chip">queued</span>`;
  if (s === "ok") {
    const sum = job.summary || {};
    const diffs = sum.differences ?? ((sum.mismatching || 0) + (sum.only_in_a || 0) + (sum.only_in_b || 0));
    if (!diffs) return `<span class="chip ok">all match</span>`;
    const parts = [];
    if (sum.checksum_mismatches) parts.push(`${sum.checksum_mismatches} checksum`);
    if (sum.missing_in_b) parts.push(`${sum.missing_in_b} miss B`);
    if (sum.missing_in_a) parts.push(`${sum.missing_in_a} miss A`);
    if (sum.read_errors) parts.push(`${sum.read_errors} error`);
    return `<span class="chip bad">${parts.length ? parts.join(" · ") : `${diffs} diff`}</span>`;
  }
  if (s === "cancelled") return `<span class="chip warn">cancelled</span>`;
  return `<span class="chip bad">error</span>`;
}

function progressBlock(job) {
  if (job.status !== "running" && job.status !== "queued") return "";
  const pct = Math.max(0, Math.min(100, job.progress || 0));
  const counts = job.total_files
    ? `${job.done_files || 0} / ${job.total_files}`
    : (job.status === "queued" ? "waiting…" : "starting…");
  const path = job.current_path
    ? `<div class="current-path">Current: <code>${esc(job.current_path)}</code></div>`
    : "";
  return `
    <div class="progress-wrap">
      <div class="progress-meta"><span>${esc(counts)}</span><span>${Math.round(pct)}%</span></div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      ${path}
    </div>`;
}

// --------------------------------------------------------------------------- //
// Enqueue compare / copy
// --------------------------------------------------------------------------- //
$("#run-compare").addEventListener("click", async () => {
  const btn = $("#run-compare");
  const mode = $('input[name="mode"]:checked').value;
  const path_a = $("#compare-a").value.trim();
  const path_b = $("#compare-b").value.trim();
  if (!path_a || !path_b) return toast("Both paths are required", "error");

  busy(btn, true, "Queuing…");
  try {
    const job = await api("/api/jobs/compare", {
      method: "POST",
      body: JSON.stringify({ mode, path_a, path_b, algorithm: $("#algorithm").value }),
    });
    toast(`Job #${job.id} queued`, "ok");
    await refreshJobs(true);
    goToQueue();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    busy(btn, false);
  }
});

$("#run-copy").addEventListener("click", async () => {
  const btn = $("#run-copy");
  const source = $("#copy-source").value.trim();
  const dest = $("#copy-dest").value.trim();
  if (!source || !dest) return toast("Source and destination are required", "error");

  busy(btn, true, "Queuing…");
  try {
    const job = await api("/api/jobs/copy", {
      method: "POST",
      body: JSON.stringify({
        source, dest,
        algorithm: $("#algorithm").value,
        overwrite: $("#copy-overwrite").checked,
      }),
    });
    toast(`Job #${job.id} queued`, "ok");
    await refreshJobs(true);
    goToQueue();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    busy(btn, false);
  }
});

// --------------------------------------------------------------------------- //
// Jobs / queue
// --------------------------------------------------------------------------- //
async function refreshJobs(forceDetail) {
  try {
    jobsCache = await api("/api/jobs");
    renderJobLists();
    updateBadge();
    schedulePoll();
    if (selectedJobId && forceDetail) {
      await showJobDetail(selectedJobId);
    } else if (selectedJobId) {
      const live = jobsCache.find((j) => j.id === selectedJobId);
      if (live && (live.status === "running" || live.status === "queued")) {
        // Keep detail header fresh without refetching huge results
        const detail = $("#job-detail");
        if (detail.dataset.jobId === String(selectedJobId) && detail.dataset.live === "1") {
          detail.querySelector(".live-progress") &&
            (detail.querySelector(".live-progress").innerHTML = progressBlock(live));
        }
      } else if (live && detailNeedsResult(selectedJobId)) {
        await showJobDetail(selectedJobId);
      }
    }
  } catch (e) {
    toast(e.message, "error");
  }
}

function detailNeedsResult(id) {
  const el = $("#job-detail");
  return el.dataset.jobId === String(id) && el.dataset.live === "1";
}

function updateBadge() {
  const n = jobsCache.filter((j) => j.status === "queued" || j.status === "running").length;
  const badge = $("#queue-badge");
  if (n > 0) {
    badge.textContent = String(n);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function schedulePoll() {
  const active = jobsCache.some((j) => j.status === "queued" || j.status === "running");
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (active) {
    pollTimer = setTimeout(() => refreshJobs(false), 1000);
  }
}

function renderJobLists() {
  const active = jobsCache.filter((j) => j.status === "running");
  const queued = jobsCache.filter((j) => j.status === "queued");
  const finished = jobsCache.filter((j) => !["running", "queued"].includes(j.status));

  $("#active-list").innerHTML = active.length
    ? active.map((j) => jobCard(j)).join("")
    : `<div class="empty">No job running.</div>`;

  $("#queued-list").innerHTML = queued.length
    ? queued.map((j, i) => jobCard(j, { queueIndex: i, queueLen: queued.length })).join("")
    : `<div class="empty">Queue is empty.</div>`;

  $("#finished-list").innerHTML = finished.length
    ? finished.map((j) => jobCard(j)).join("")
    : `<div class="empty">No finished jobs yet.</div>`;

  bindJobActions();
}

function jobCard(job, opts = {}) {
  const selected = selectedJobId === job.id ? " selected" : "";
  let controls = "";
  if (job.status === "queued") {
    const upDis = opts.queueIndex === 0 ? "disabled" : "";
    const downDis = opts.queueIndex === opts.queueLen - 1 ? "disabled" : "";
    controls = `
      <div class="job-actions">
        <button class="ghost icon-btn" data-move="${job.id}" data-dir="up" ${upDis} title="Higher priority">↑</button>
        <button class="ghost icon-btn" data-move="${job.id}" data-dir="down" ${downDis} title="Lower priority">↓</button>
        <button class="danger ghost" data-cancel="${job.id}">Cancel</button>
      </div>`;
  } else if (job.status === "running") {
    controls = `
      <div class="job-actions">
        <button class="danger ghost" data-cancel="${job.id}">Cancel</button>
      </div>`;
  } else {
    controls = `
      <div class="job-actions">
        <button class="danger ghost" data-del="${job.id}">Delete</button>
      </div>`;
  }

  return `
    <div class="history-item${selected}" data-id="${job.id}">
      <div class="history-main" data-detail="${job.id}">
        <div class="line1">
          <span class="badge ${job.kind}">${esc(job.kind)}</span>
          ${statusChip(job)}
          <span class="ts">#${job.id} · ${esc(job.created_at)} · ${esc(job.algorithm)}</span>
        </div>
        <div class="paths">${esc(job.source)} ${arrow(job.kind)} ${esc(job.target)}</div>
        ${progressBlock(job)}
      </div>
      ${controls}
    </div>`;
}

function bindJobActions() {
  $$("[data-detail]").forEach((el) =>
    el.addEventListener("click", () => showJobDetail(Number(el.dataset.detail))));
  $$("[data-cancel]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      cancelJob(Number(el.dataset.cancel));
    }));
  $$("[data-del]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteJob(Number(el.dataset.del));
    }));
  $$("[data-move]").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      moveQueued(Number(el.dataset.move), el.dataset.dir);
    }));
}

async function moveQueued(jobId, dir) {
  const queued = jobsCache.filter((j) => j.status === "queued").map((j) => j.id);
  const i = queued.indexOf(jobId);
  if (i < 0) return;
  const j = dir === "up" ? i - 1 : i + 1;
  if (j < 0 || j >= queued.length) return;
  [queued[i], queued[j]] = [queued[j], queued[i]];
  try {
    jobsCache = await api("/api/jobs/queue", {
      method: "PUT",
      body: JSON.stringify({ ordered_ids: queued }),
    });
    renderJobLists();
    updateBadge();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function cancelJob(id) {
  try {
    await api(`/api/jobs/${id}/cancel`, { method: "POST" });
    toast(`Cancel requested for job #${id}`, "ok");
    await refreshJobs(true);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function deleteJob(id) {
  try {
    await api(`/api/jobs/${id}`, { method: "DELETE" });
    if (selectedJobId === id) {
      selectedJobId = null;
      $("#job-detail").innerHTML = "";
      $("#job-detail").dataset.jobId = "";
    }
    toast(`Deleted job #${id}`, "ok");
    await refreshJobs(false);
  } catch (e) {
    toast(e.message, "error");
  }
}

$("#clear-finished").addEventListener("click", async () => {
  if (!confirm("Delete all finished jobs (ok / error / cancelled)?")) return;
  try {
    const r = await api("/api/jobs", { method: "DELETE" });
    selectedJobId = null;
    $("#job-detail").innerHTML = "";
    toast(`Cleared ${r.deleted} jobs`, "ok");
    await refreshJobs(false);
  } catch (e) {
    toast(e.message, "error");
  }
});

async function showJobDetail(id) {
  selectedJobId = id;
  renderJobLists();
  try {
    const job = await api(`/api/jobs/${id}`);
    const head = `
      <h2>Job #${job.id} <span class="ts">${esc(job.created_at)}</span></h2>
      <p class="paths" style="font-family:var(--mono);color:var(--muted)">
        ${esc(job.source)} ${arrow(job.kind)} ${esc(job.target)}
      </p>
      <div class="summary-bar">${statusChip(job)}</div>
      <div class="live-progress">${progressBlock(job)}</div>`;

    const detail = $("#job-detail");
    detail.dataset.jobId = String(id);

    if (job.status === "running" || job.status === "queued") {
      detail.dataset.live = "1";
      detail.innerHTML = head + `<div class="empty">Results will appear when the job finishes.</div>`;
    } else if (job.status === "error" || job.status === "cancelled") {
      detail.dataset.live = "0";
      detail.innerHTML = head +
        `<div class="result-group"><h3><span class="dot bad"></span>${esc(job.status)}</h3>
         <div class="empty" style="color:var(--bad)">${esc(job.error || "—")}</div></div>`;
    } else {
      detail.dataset.live = "0";
      detail.innerHTML = head + renderResult(job.result || {});
    }
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    toast(e.message, "error");
  }
}

// --------------------------------------------------------------------------- //
// File picker
// --------------------------------------------------------------------------- //
let pickerTarget = null;
let pickerPath = "";

$$("[data-pick]").forEach((btn) =>
  btn.addEventListener("click", (ev) => {
    ev.preventDefault();
    openPicker(btn.dataset.pick);
  }));

$("#picker-close").addEventListener("click", closePicker);
$("#picker").addEventListener("click", (e) => { if (e.target.id === "picker") closePicker(); });
$("#picker-select-dir").addEventListener("click", () => {
  setPicked(pickerPath || "");
  closePicker();
});

function normalizeBrowsePath(path) {
  const p = (path || "").trim();
  if (!p || p === "." || p === "./") return "";
  return p.replace(/^\/+/, "").replace(/\/+$/, "");
}

async function openPicker(targetId) {
  pickerTarget = targetId;
  const current = normalizeBrowsePath($(`#${targetId}`).value);
  let start = "";
  if (current.includes("/")) {
    start = current.split("/").slice(0, -1).join("/");
  } else if (current) {
    // Single segment: open its parent (root) so the item is visible in the list.
    start = "";
  }
  $("#picker").classList.remove("hidden");
  $("#picker-list").innerHTML = `<li class="empty">Loading…</li>`;
  await loadPicker(start);
}

function closePicker() { $("#picker").classList.add("hidden"); }

function setPicked(rel) {
  if (pickerTarget) $(`#${pickerTarget}`).value = normalizeBrowsePath(rel);
}

async function loadPicker(path) {
  try {
    const clean = normalizeBrowsePath(path);
    const data = await api(`/api/browse?path=${encodeURIComponent(clean)}`);
    pickerPath = normalizeBrowsePath(data.path);
    $("#picker-path").textContent = `/${pickerPath}`;
    const items = [];
    if (pickerPath) {
      items.push(`<li data-dir="${esc(normalizeBrowsePath(data.parent))}"><span>📁</span><span class="picker-name">.. (up)</span></li>`);
    }
    for (const e of data.entries || []) {
      if (e.is_dir) {
        items.push(`<li data-dir="${esc(e.rel)}"><span>📁</span><span class="picker-name">${esc(e.name)}</span></li>`);
      } else {
        items.push(`<li data-file="${esc(e.rel)}"><span>📄</span><span class="picker-name">${esc(e.name)}</span><span class="picker-size">${fmtSize(e.size)}</span></li>`);
      }
    }
    const ul = $("#picker-list");
    ul.innerHTML = items.join("") || `<li class="empty">Empty folder</li>`;
    ul.querySelectorAll("[data-dir]").forEach((li) =>
      li.addEventListener("click", () => loadPicker(li.dataset.dir || "")));
    ul.querySelectorAll("[data-file]").forEach((li) =>
      li.addEventListener("click", () => { setPicked(li.dataset.file); closePicker(); }));
  } catch (e) {
    $("#picker-list").innerHTML = `<li class="empty" style="color:var(--bad)">${esc(e.message)}</li>`;
    toast(e.message, "error");
  }
}

// --------------------------------------------------------------------------- //
// Init
// --------------------------------------------------------------------------- //
(async function init() {
  try {
    META = await api("/api/meta");
    const sel = $("#algorithm");
    sel.innerHTML = META.algorithms
      .map((a) => `<option value="${a}" ${a === META.default_algorithm ? "selected" : ""}>${a}</option>`)
      .join("");
    $("#datadir").textContent = `data: ${META.data_dir}`;
    await refreshJobs(false);
  } catch (e) {
    toast(`Failed to load: ${e.message}`, "error");
  }
})();
