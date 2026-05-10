// red_ui/app.js — Adapted for /submit API (x_team_token + {flag}) + local submission history
// ---------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const byId = (id) => document.getElementById(id);
const STORE_KEY = 'RED_TOKEN';

// ==== Basic UI ====
function showMsg(el, text) {
  if (!el) return;
  el.textContent = text || '';
}

function loadToken() {
  const t = localStorage.getItem(STORE_KEY) || '';
  const inp = byId('token');
  if (inp) inp.value = t;
  return t;
}

function saveToken() {
  const inp = byId('token');
  const msg = byId('saveMsg');
  const val = (inp?.value || '').trim();
  if (!val) {
    showMsg(msg, 'Please enter the token first');
    return;
  }
  localStorage.setItem(STORE_KEY, val);
  showMsg(msg, 'Saved locally (this browser only)');
  setTimeout(() => showMsg(msg, ''), 1500);
  // After saving, also refresh history (nice UX if there was a missing-token notice)
  refreshHistory();
}

// ==== Submit FLAG ====
async function submitFlag() {
  const token = (byId('token')?.value || '').trim();
  const flag  = (byId('flag')?.value  || '').trim();
  const out   = byId('submsg');

  if (!token) { showMsg(out, 'Please save the red team token above first'); return; }
  if (!flag)  { showMsg(out, 'Please enter a FLAG'); return; }

  showMsg(out, 'Submitting…');

  try {
    const resp = await fetch('/submit', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        // Important: backend expects x_team_token (underscore)
        'x_team_token': token,
      },
      body: JSON.stringify({ flag }),
    });

    // To be robust, read the body even for non-2xx responses
    let dataText = await resp.text();
    let data = null;
    try { data = JSON.parse(dataText); } catch { /* not JSON, leave as text */ }

    if (!resp.ok) {
      showMsg(out, `HTTP ${resp.status}: ${dataText || 'Submission failed'}`);
      // Also record a failure in local history (for traceability)
      pushLocalHistory({
        ts: new Date().toISOString(),
        status: `http_${resp.status}`,
        flag_preview: (flag || '').toString().slice(0, 16)
      });
      renderHistory(getLocalHistory());
      return;
    }

    if (data && typeof data === 'object') {
      // Expected response: {status: "accepted"|"invalid"|"expired"|"duplicate", points?: number}
      if (data.status === 'accepted') {
        showMsg(out, `✅ Accepted! +${data.points ?? 0} pts`);
      } else if (data.status === 'duplicate') {
        showMsg(out, '⚠️ Duplicate submission (already recorded)');
      } else if (data.status === 'expired') {
        showMsg(out, '⌛ Expired (not a current-tick FLAG)');
      } else if (data.status === 'invalid') {
        showMsg(out, '❌ Invalid FLAG');
      } else {
        showMsg(out, `Response: ${dataText}`);
      }

      // —— Append a local history entry (record accepted/duplicate/expired/invalid)
      pushLocalHistory({
        ts: new Date().toISOString(),
        status: data.status || 'ok',
        flag_preview: (flag || '').toString().slice(0, 16),
        points: data?.points,
        victim: data?.victim || data?.victim_team,
        service: data?.service || data?.service_name
      });
      renderHistory(getLocalHistory());
    } else {
      showMsg(out, dataText || 'Submitted');
      pushLocalHistory({
        ts: new Date().toISOString(),
        status: 'ok',
        flag_preview: (flag || '').toString().slice(0, 16)
      });
      renderHistory(getLocalHistory());
    }
  } catch (e) {
    showMsg(out, `Network or server error: ${e}`);
    pushLocalHistory({
      ts: new Date().toISOString(),
      status: 'network_error',
      flag_preview: (flag || '').toString().slice(0, 16)
    });
    renderHistory(getLocalHistory());
  }
}

// ==== Submission history (localStorage) ====
const HISTORY_KEY = 'SUBMIT_HISTORY_V1';
const HISTORY_MAX = 200;

function getLocalHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); }
  catch { return []; }
}
function saveLocalHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)); } catch {}
}
function pushLocalHistory(entry) {
  const list = getLocalHistory();
  list.unshift(entry);
  if (list.length > HISTORY_MAX) list.length = HISTORY_MAX;
  saveLocalHistory(list);
}
function clearLocalHistory() {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory([]);
}

// Renderer
function fmtTs(ts) {
  try { return new Date(ts).toLocaleString(); } catch { return ts || ""; }
}
function verdictBadge(v) {
  const map = {
    accepted: '✅ Accepted',
    duplicate: '⚠️ Duplicate',
    expired: '⌛ Expired',
    invalid: '❌ Invalid',
    own: 'ℹ️ Own',
    ok: '✅ Processed',
    network_error: '📶 Network error'
  };
  return map[v] || v || '';
}
function renderHistory(list) {
  const box = byId('submit-history');
  if (!box) return;
  if (!Array.isArray(list) || list.length === 0) {
    box.innerHTML = '<li style="color:#777">No records yet</li>';
    return;
  }
  const rows = list.map(it => {
    const ts = fmtTs(it.ts);
    const verdict = verdictBadge(it.status);
    const victim = it.victim || '-';
    const service = it.service || '-';
    const points = (it.points != null ? ` +${it.points}` : '');
    const flagPrev = (it.flag_preview || '').toString();
    return `<li class="hist">
      <span class="ts">${ts}</span>
      <span class="vdg">${verdict}</span>
      <span class="svc">${service}</span>
      <span class="vic">${victim}</span>
      <span class="pts">${points}</span>
      ${flagPrev ? `<code class="fprev">${flagPrev}…</code>` : ''}
    </li>`;
  });
  box.innerHTML = rows.join('');
}
function refreshHistory() {
  renderHistory(getLocalHistory());
}

// ==== Targets hint ====
async function refreshTargets() {
  const box = byId('targets');
  if (!box) return;
  try {
    const resp = await fetch('/scoreboard');
    const arr = await resp.json();
    const blues = (arr || []).filter(r => r.role === 'blue');
    if (!blues.length) {
      box.textContent = 'No blue teams online';
      return;
    }
    // Only show blue team names; port mapping differs per environment, so do not guess here
    box.innerHTML = `<small>Blue teams: ${
      blues.map(b => `<code>${b.team}</code>`).join(', ')
    }</small>`;
  } catch {
    box.textContent = 'Failed to fetch targets';
  }
}

// ==== “Run” button (hint only; no server-side exec) ====
function initRunHint() {
  const runBtn = byId('run');
  const out = byId('output');
  const cmdInput = byId('cmd');
  if (!runBtn || !out) return;
  runBtn.addEventListener('click', () => {
    const cmd = (cmdInput?.value || '').trim();
    const hint =
`(Safety restriction) Server-side command execution is not enabled.
Please run commands locally in your own terminal/PowerShell, for example:

  curl http://127.0.0.1:20081/        # access blue1's vulnbox
  # If your port differs, use your actual port
`;
    out.textContent = cmd ? `You entered: ${cmd}\n\n${hint}` : hint;
  });
}

// ==== Event bindings + init ====
window.addEventListener('DOMContentLoaded', () => {
  loadToken();
  byId('saveToken')?.addEventListener('click', saveToken);
  byId('submit')?.addEventListener('click', submitFlag);
  byId('clear-history')?.addEventListener('click', clearLocalHistory); // will work if this button exists in HTML
  initRunHint();
  refreshTargets();
  refreshHistory(); // ← Render history on load
});
