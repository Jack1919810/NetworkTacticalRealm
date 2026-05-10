// ==== Simple log ====
const logBox = () => document.getElementById('msg');
function log(s) {
  const box = logBox();
  if (!box) return;
  const ts = new Date().toTimeString().slice(0, 8);
  box.textContent = `[${ts}] ${s}\n` + box.textContent;
}

// ==== Admin token (with migration) ====
const TOKEN_KEY = 'adm_token';
(function migrateOldKeys() {
  if (!localStorage.getItem(TOKEN_KEY)) {
    const oldKeys = ['ADMIN_TOKEN', 'admin_token', 'token', 'adm'];
    for (const k of oldKeys) {
      const v = localStorage.getItem(k);
      if (v && v.trim()) {
        localStorage.setItem(TOKEN_KEY, v.trim());
        break;
      }
    }
  }
})();
const getToken = () => (localStorage.getItem(TOKEN_KEY) || '').trim();
const setToken = (t) => localStorage.setItem(TOKEN_KEY, (t || '').trim());

// ==== Admin requests wrapper ====
async function postAdmin(path, bodyObj = {}) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Token': getToken(),
    },
    body: JSON.stringify(bodyObj),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => r.statusText);
    throw new Error(`${path} => ${r.status} ${txt}`);
  }
  return r.json().catch(() => ({}));
}

async function safeGetJson(url, fallback = null) {
  try {
    const headers = {};
    if (url.startsWith('/admin/')) headers['X-Admin-Token'] = getToken();
    const r = await fetch(url, { cache: 'no-store', headers });
    if (!r.ok) { log(`${url} => ${r.status}`); return fallback; }
    return await r.json();
  } catch (e) {
    log(`${url} err ${e}`);
    return fallback;
  }
}

// ==== Render: Scoreboard ====
function renderScoreboard(rows) {
  const tbody = document.querySelector('#score tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!Array.isArray(rows) || rows.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="5" style="text-align:center;color:#888">No data</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = document.createElement('tr');
    const sla = r.sla ?? r.sla_points ?? 0;
    const atk = r.atk ?? r.attack_points ?? 0;
    const pts = r.points ?? (sla + atk);
    tr.innerHTML = `
      <td>${r.team ?? r.name ?? ''}</td>
      <td>${r.role ?? ''}</td>
      <td>${sla}</td>
      <td>${atk}</td>
      <td>${pts}</td>
    `;
    tbody.appendChild(tr);
  }
}

// ==== Render: Recent submissions ====
function renderRecent(rows) {
  const tbody = document.querySelector('#subs tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!Array.isArray(rows) || rows.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="6" style="text-align:center;color:#888">No data</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const t = r.created_at ?? r.time ?? '';
    const atk = r.attacker ?? r.attacker_name ?? r.src ?? '';
    const vic = r.victim ?? r.victim_name ?? r.dst ?? '';
    const svc = r.service ?? r.service_name ?? '';
    const vd  = r.verdict ?? r.result ?? '';
    const flag = (r.flag_preview ?? r.flag ?? '').toString().slice(0, 24);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t}</td>
      <td>${atk}</td>
      <td>${vic}</td>
      <td>${svc}</td>
      <td>${vd}</td>
      <td>${flag}</td>
    `;
    tbody.appendChild(tr);
  }
}

// ==== Render: Last checks ====
function renderLastChecks(rows) {
  const box = document.getElementById('lastChecks');
  if (!box) return;
  if (!Array.isArray(rows) || rows.length === 0) {
    box.textContent = 'No data';
    return;
  }
  const html = rows.map(r => {
    const t = r.team ?? r.team_name ?? '';
    const s = r.service ?? r.service_name ?? '';
    const ok = (r.ok ?? r.online ?? false) ? 'OK' : 'DOWN';
    const tick = r.tick ?? r.t ?? '';
    return `<div>${t} · ${s} · ${ok} · tick=${tick}</div>`;
  }).join('');
  box.innerHTML = html;
}

// ==== Periodic refresh ====
async function refreshScore() {
  const data = await safeGetJson('/scoreboard', []);
  renderScoreboard(data);
}
async function refreshRecent() {
  const data = await safeGetJson('/admin/recent_submissions', []);
  renderRecent(data || []);
}
async function refreshChecks() {
  const data = await safeGetJson('/admin/last_checks', []);
  renderLastChecks(data || []);
}
async function tick() {
  try {
    await refreshScore();
    await refreshRecent();
    await refreshChecks();
  } catch (e) {
    log(`tick err: ${e}`);
  } finally {
    setTimeout(tick, 5000);
  }
}

// ==== Toolbar & buttons ====
function bindToolbar() {
  const inp = document.getElementById('adm');
  const saveBtn = document.getElementById('saveBtn');
  const savedMsg = document.getElementById('savedMsg');

  if (inp) inp.value = getToken();

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      setToken((inp?.value || '').trim());
      if (savedMsg) {
        savedMsg.textContent = 'Saved';
        setTimeout(() => (savedMsg.textContent = ''), 1500);
      }
      refreshRecent();
      refreshChecks();
      log('Admin token saved');
    });
  }

  document.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const act = btn.getAttribute('data-action');
      try {
        if (!getToken()) { alert('Please save the admin token first'); return; }
        if (act === 'rotate') {
          const r = await postAdmin('/admin/rotate_now', {});
          log(`rotate_now => ${JSON.stringify(r)}`);
        } else if (act === 'check') {
          const r = await postAdmin('/admin/check_now', {});
          log(`check_now => ${JSON.stringify(r)}`);
        } else if (act === 'soft') {
          const ok = confirm('Confirm soft reset? (Clears stats and rotates flags, DB preserved)');
          if (!ok) return;
          const r = await postAdmin('/admin/reset_soft', {});
          log(`reset_soft => ${JSON.stringify(r)}`);
          refreshScore(); refreshRecent(); refreshChecks();
        } else if (act === 'hard') {
          const ok = confirm('Danger: hard reset will clear the entire database! Continue?');
          if (!ok) return;
          const r = await postAdmin('/admin/reset_hard', {});
          log(`reset_hard => ${JSON.stringify(r)}`);
          refreshScore(); refreshRecent(); refreshChecks();
        }
      } catch (e) {
        log(`${act} err: ${e}`);
        alert(`${act} failed: ${e}`);
      }
    });
  });
}

// Sync across tabs
window.addEventListener('storage', (e) => {
  if (e.key === TOKEN_KEY) {
    const inp = document.getElementById('adm');
    if (inp) inp.value = getToken();
  }
});

// Launch
window.addEventListener('DOMContentLoaded', () => {
  bindToolbar();
  tick();
});
