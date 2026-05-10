// app.js — Blue Team Console script (includes original features + exec panel)

const $ = (sel) => document.querySelector(sel);
const STORE_KEY = 'BLUE_TOKEN';

function msg(el, s) { if (el) el.textContent = s || ''; }
function loadToken() {
  const t = localStorage.getItem(STORE_KEY) || '';
  $('#token').value = t;
  return t;
}
function saveToken() {
  const t = ($('#token').value || '').trim();
  if (!t) return msg($('#saveMsg'), 'Please enter the token first');
  localStorage.setItem(STORE_KEY, t);
  msg($('#saveMsg'), 'Saved (this browser only)');
  setTimeout(() => msg($('#saveMsg'), ''), 1200);
}

async function apiGet(path) {
  const token = ($('#token').value || '').trim();
  if (!token) throw new Error('Missing token');
  const r = await fetch(path, { headers: { 'x_team_token': token } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.json();
}
async function apiPost(path, body) {
  const token = ($('#token').value || '').trim();
  if (!token) throw new Error('Missing token');
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x_team_token': token },
    body: body ? JSON.stringify(body) : null
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.json();
}

async function refreshMe() {
  try {
    const me = await apiGet('/blue/me');
    $('#me').textContent = JSON.stringify(me, null, 2);
  } catch (e) {
    $('#me').textContent = `Failed to load: ${e}`;
  }
}
async function refreshStatus() {
  try {
    const rows = await apiGet('/blue/last_checks');
    if (!Array.isArray(rows) || rows.length === 0) {
      $('#status').textContent = 'No data yet';
      return;
    }
    $('#status').textContent = rows.map(r => {
      return `${r.service} · ${r.ok ? 'OK' : 'DOWN'}${r.tick != null ? ' · tick ' + r.tick : ''}${r.details ? ' · ' + r.details : ''}`;
    }).join('\n');
  } catch (e) {
    $('#status').textContent = `Failed to load: ${e}`;
  }
}
async function refreshAttacks() {
  const tbody = $('#attacks tbody');
  tbody.innerHTML = '';
  try {
    const rows = await apiGet('/blue/recent_attacks');
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${r.time}</td><td>${r.attacker}</td><td>${r.service}</td><td>${r.verdict}</td><td>${r.flag || ''}</td>`;
      tbody.appendChild(tr);
    }
    if (!rows.length) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="5">None</td>`;
      tbody.appendChild(tr);
    }
  } catch (e) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="5">Failed to load: ${e}</td>`;
    tbody.appendChild(tr);
  }
}
async function doRecheck() {
  msg($('#recheckMsg'), 'Requested. Backend is checking…');
  try {
    await apiPost('/blue/recheck');
    msg($('#recheckMsg'), 'Triggered a global check. Please refresh later.');
    setTimeout(() => { refreshStatus(); refreshAttacks(); }, 1500);
  } catch (e) {
    msg($('#recheckMsg'), `Request failed: ${e}`);
  }
}

// =================== Exec UI ===================
// Frontend logic that talks to vulnbox /exec
const VULNBOX_EXEC_URL = 'http://127.0.0.1:20081/exec'; // change if your port differs

function appendExecLog(text) {
  const log = $('#cmdLog');
  if (!log) return;
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

async function runExecCmd(cmd) {
  const token = ($('#token').value || '').trim();
  if (!token) {
    appendExecLog(`[${new Date().toLocaleTimeString()}] ERROR: missing x_team_token (save your token first)`);
    return;
  }
  appendExecLog(`[${new Date().toLocaleTimeString()}] => ${cmd}`);
  try {
    const resp = await fetch(VULNBOX_EXEC_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x_team_token': token,    // underscore variant
        'x-team-token': token     // hyphen variant (FastAPI header mapping)
      },
      body: JSON.stringify({ cmd })
    });

    let dataText = await resp.text();
    let data = null;
    try { data = JSON.parse(dataText); } catch (e) { /* not JSON; show raw text */ }

    if (!resp.ok) {
      const detail = data && data.detail ? data.detail : dataText;
      appendExecLog(`[ERR ${resp.status}] ${detail}`);
      return;
    }

    // Expected: { rc, out, err, ts }
    if (data && typeof data === 'object') {
      appendExecLog(`[RC ${data.rc}] OUT:\n${data.out || ''}\nERR:\n${data.err || ''}\nTS: ${data.ts || ''}`);
    } else {
      appendExecLog(`[OK] ${dataText}`);
    }
  } catch (e) {
    appendExecLog(`[EXCEPTION] ${e}`);
  }
}

// Sample commands
const SAMPLE_CMDS = [
  'curl -I http://127.0.0.1:80/',
  'curl http://127.0.0.1:80/',
  'ls -la /',
  'cat /etc/hosts'
];

window.addEventListener('DOMContentLoaded', () => {
  loadToken();
  $('#saveToken')?.addEventListener('click', saveToken);
  $('#recheck')?.addEventListener('click', doRecheck);
  refreshMe();
  refreshStatus();
  refreshAttacks();
  // Auto-refresh status & recent attacks every 5s
  setInterval(() => { refreshStatus(); refreshAttacks(); }, 5000);

  // Exec UI bindings
  const sendBtn = $('#cmdSend');
  const clearBtn = $('#cmdClear');
  const sampleBtn = $('#cmdSample');
  const input = $('#cmdInput');

  sendBtn?.addEventListener('click', () => {
    const c = (input.value || '').trim();
    if (!c) return;
    runExecCmd(c);
  });
  input?.addEventListener('keyup', (e) => {
    if (e.key === 'Enter') {
      const c = (input.value || '').trim();
      if (!c) return;
      runExecCmd(c);
    }
  });
  clearBtn?.addEventListener('click', () => {
    $('#cmdLog').textContent = '';
  });
  sampleBtn?.addEventListener('click', () => {
    const s = SAMPLE_CMDS[Math.floor(Math.random() * SAMPLE_CMDS.length)];
    input.value = s;
    runExecCmd(s);
  });
});
