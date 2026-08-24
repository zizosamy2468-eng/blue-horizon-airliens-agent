/**
 * Blue Horizon Platform frontend (Adel)
 * Talks to the platform backend API.
 */

const API_BASE = localStorage.getItem("bh_api_base") || "http://127.0.0.1:5050";

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ---- Navigation ----
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "dashboard") loadDashboard();
    if (btn.dataset.view === "agents") loadAgents();
    if (btn.dataset.view === "hitl") loadHitl();
    if (btn.dataset.view === "tickets") loadTickets();
  });
});

function badge(status) {
  const s = (status || "unknown").toLowerCase();
  return `<span class="badge ${s}">${s}</span>`;
}

// ---- Dashboard ----
async function loadDashboard() {
  const el = document.getElementById("health-status");
  const runsEl = document.getElementById("recent-runs");
  try {
    const health = await api("/api/health");
    el.textContent = `API: ${health.status} (${API_BASE})`;
  } catch (e) {
    el.textContent = `API unreachable at ${API_BASE}: ${e.message}`;
  }
  try {
    const data = await api("/api/agents");
    const runs = data.recent_runs || [];
    runsEl.innerHTML =
      runs.length === 0
        ? `<div class="card">No recent runs (checkpoint store may be offline).</div>`
        : runs
            .map(
              (r) => `
      <div class="card">
        <strong>${r.workflow_type || r.type || "run"}</strong>
        ${badge(r.status)}
        <div style="color:var(--muted);font-size:0.85rem;margin-top:0.35rem">
          ${r.run_id || ""} · flight ${r.flight_number || "—"} · node ${r.current_node || "—"}
        </div>
      </div>`
            )
            .join("");
  } catch {
    runsEl.innerHTML = `<div class="card">Could not load runs.</div>`;
  }
}

// ---- Agents ----
async function loadAgents() {
  const el = document.getElementById("agents-list");
  try {
    const data = await api("/api/agents");
    el.innerHTML = (data.agents || [])
      .map(
        (a) => `
      <div class="card">
        <strong>${a.name}</strong>
        <div style="color:var(--muted);font-size:0.85rem">${a.type} · owner: ${a.owner}</div>
      </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<div class="card">Error: ${e.message}</div>`;
  }
}

// ---- HITL ----
async function loadHitl() {
  const el = document.getElementById("hitl-tasks");
  const detail = document.getElementById("hitl-detail");
  detail.classList.add("hidden");
  try {
    const data = await api("/api/admin/tasks");
    const tasks = data.tasks || [];
    if (!tasks.length) {
      el.innerHTML = `<div class="card">No pending HITL tasks. (Or HITL store offline — you can still decide via run_id below.)</div>
        <div class="card">
          <label>Manual decide — Task ID <input id="manual-task-id" placeholder="task uuid" /></label>
          <label>Run ID <input id="manual-run-id" placeholder="run uuid" /></label>
          <label>Draft / notes <textarea id="manual-comment" rows="2"></textarea></label>
          <div class="actions">
            <button class="btn" onclick="manualDecide('approved')">Approve</button>
            <button class="btn secondary" onclick="manualDecide('changes_requested')">Request changes</button>
          </div>
        </div>`;
      return;
    }
    el.innerHTML = tasks
      .map(
        (t) => `
      <div class="card">
        <strong>${t.title || t.task_type || "Task"}</strong> ${badge(t.status || "open")}
        <div style="color:var(--muted);font-size:0.85rem">${t.task_id} · run ${t.run_id || "—"}</div>
        <div class="actions">
          <button class="btn" onclick='openHitlDetail(${JSON.stringify(t).replace(/'/g, "&#39;")})'>Review</button>
        </div>
      </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<div class="card">Error: ${e.message}</div>`;
  }
}

window.openHitlDetail = function (task) {
  const detail = document.getElementById("hitl-detail");
  const payload = task.payload || task.decision_payload || {};
  const report = payload.draft_report || payload.report || "(no draft attached)";
  detail.classList.remove("hidden");
  detail.innerHTML = `
    <h3>${task.title || "Safety report review"}</h3>
    <pre>${report}</pre>
    <label>Comment <textarea id="hitl-comment" rows="2"></textarea></label>
    <div class="actions">
      <button class="btn" onclick="decide('${task.task_id}', 'approved', '${task.run_id || ""}')">Approve</button>
      <button class="btn secondary" onclick="decide('${task.task_id}', 'changes_requested', '${task.run_id || ""}')">Request changes</button>
    </div>`;
};

window.decide = async function (taskId, decision, runId) {
  const comment = document.getElementById("hitl-comment")?.value || "";
  try {
    await api(`/api/admin/tasks/${taskId}/decide`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        decided_by: "safety_manager",
        comment,
        run_id: runId || undefined,
      }),
    });
    alert(`Decision "${decision}" submitted. Graph will resume from checkpoint.`);
    loadHitl();
  } catch (e) {
    alert("Error: " + e.message);
  }
};

window.manualDecide = async function (decision) {
  const taskId = document.getElementById("manual-task-id")?.value;
  const runId = document.getElementById("manual-run-id")?.value;
  const comment = document.getElementById("manual-comment")?.value || "";
  if (!taskId) return alert("Task ID required");
  try {
    await api(`/api/admin/tasks/${taskId}/decide`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        decided_by: "safety_manager",
        comment,
        run_id: runId || undefined,
      }),
    });
    alert(`Decision "${decision}" submitted.`);
    loadHitl();
  } catch (e) {
    alert("Error: " + e.message);
  }
};

// ---- Tickets ----
async function loadTickets() {
  const el = document.getElementById("tickets-list");
  try {
    const data = await api("/api/admin/tickets");
    const tickets = data.tickets || [];
    if (!tickets.length) {
      el.innerHTML = `<div class="card">No open tickets.</div>`;
      return;
    }
    el.innerHTML = tickets
      .map(
        (t) => `
      <div class="card">
        <strong>${t.error_type || "ticket"}</strong> ${badge(t.status || "open")}
        <div style="font-size:0.85rem;margin:0.35rem 0">${t.error_message || ""}</div>
        <div style="color:var(--muted);font-size:0.8rem">${t.ticket_id} · run ${t.run_id || "—"}</div>
        <div class="actions">
          <button class="btn" onclick="resolveTicket('${t.ticket_id}')">Resolve &amp; resume</button>
        </div>
      </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<div class="card">Error: ${e.message}</div>`;
  }
}

window.resolveTicket = async function (ticketId) {
  const notes = prompt("Resolution notes:", "Evidence corrected / gateway fixed") || "Resolved";
  try {
    await api(`/api/admin/tickets/${ticketId}/resolve`, {
      method: "POST",
      body: JSON.stringify({
        resolved_by: "admin",
        resolution_notes: notes,
        resume: true,
      }),
    });
    alert("Ticket resolved; workflow resume attempted.");
    loadTickets();
  } catch (e) {
    alert("Error: " + e.message);
  }
};

// ---- Safety form ----
document.getElementById("safety-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    agent_type: "safety_incident",
    flight_number: fd.get("flight_number"),
    incident_type: fd.get("incident_type"),
    severity: fd.get("severity"),
    description: fd.get("description") || "",
    has_passenger_impact: fd.get("has_passenger_impact") === "on",
  };
  const out = document.getElementById("safety-result");
  out.classList.remove("hidden");
  out.textContent = "Starting…";
  try {
    const result = await api("/api/agents/start", {
      method: "POST",
      body: JSON.stringify(body),
    });
    out.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// Initial load
loadDashboard();
