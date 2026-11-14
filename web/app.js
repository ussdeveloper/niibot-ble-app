'use strict';

/* ============================================================ small helpers */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const TYPE_LABELS = {
  text: 'Text',
  barcode: 'Barcode',
  qr: 'QR code',
  rect: 'Frame',
  line: 'Line',
  image: 'Image',
};

const TYPE_ICONS = {
  text: 'type',
  barcode: 'barcode',
  qr: 'qr-code',
  rect: 'square',
  line: 'minus',
  image: 'image',
};

function uid() {
  return Math.random().toString(16).slice(2, 10);
}

/* Element types whose size is quantised by the printer resolution (see clampCodeSize). */
const CODE_TYPES = new Set(['qr', 'barcode']);

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

function snap(value, free) {
  const step = free ? 0.1 : 0.5;
  return Math.round(value / step) * step;
}

/* grid + alignment snapping */
const SNAP_MM = 0.5;
const GRID_MINOR_MM = 0.5;
const GRID_MAJOR_MM = 5;
const GUIDE_TOLERANCE_PX = 6;

function snapToStep(value, free) {
  if (free || !state.snapToGrid) return round2(value);
  return Math.round(value / SNAP_MM) * SNAP_MM;
}

function alignmentTargets(exceptId) {
  const { width_mm: labelWidth } = state.spec;
  const band = editBand();
  const xs = [
    { value: 0, center: false },
    { value: labelWidth / 2, center: true },
    { value: labelWidth, center: false },
  ];
  const ys = [
    { value: 0, center: false },
    { value: band.center, center: true },
    { value: band.bottom, center: false },
  ];
  for (const element of state.spec.elements) {
    if (element.id === exceptId || element.visible === false) continue;
    if (band.mirror && element.y >= band.bottom - 0.01) continue;
    xs.push(
      { value: element.x, center: false },
      { value: element.x + element.w / 2, center: true },
      { value: element.x + element.w, center: false },
    );
    ys.push(
      { value: element.y, center: false },
      { value: element.y + element.h / 2, center: true },
      { value: element.y + element.h, center: false },
    );
  }
  return { xs, ys };
}

function nearestGuide(points, targets, tolerance) {
  let best = null;
  for (const point of points) {
    for (const target of targets) {
      const delta = target.value - point.value;
      if (Math.abs(delta) > tolerance) continue;
      if (!best || Math.abs(delta) < Math.abs(best.delta)) {
        best = { delta, value: target.value, center: target.center };
      }
    }
  }
  return best;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
  ));
}

/** (Re)render Lucide icons inside a container. */
function refreshIcons(root) {
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    try {
      window.lucide.createIcons({ root: root || document });
      return;
    } catch (_) { /* fall through */ }
  }
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    try { window.lucide.createIcons(); } catch (_) { /* ignore */ }
  }
}

function toast(message, kind = 'info', duration = 0) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  $('#toast').appendChild(node);
  const ms = duration > 0 ? duration : kind === 'error' ? 6000 : 3000;
  if (ms > 0) setTimeout(() => node.remove(), ms);
  return node;
}

async function api(path, method = 'GET', body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    markBackend(false, error.message);
    throw error;
  }
  markBackend(true);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data && data.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      }
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

/* ============================================================ printer queue */
/* Everything that was sent to a printer, newest first, with a running job on top. Each row can
   be repeated (the spec is stored with the job) or removed from the history. */

const QUEUE_STATE_ICONS = {
  running: 'loader',
  done: 'check',
  error: 'alert-triangle',
  cancelled: 'ban',
};

function queueTime(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return sameDay ? time : `${date.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })} ${time}`;
}

function queueTitle(job) {
  if (job.state === 'running') return job.message || 'Printing…';
  if (job.kind === 'sheet') {
    return `Sheet · ${job.copies} ${job.copies === 1 ? 'label' : 'labels'}`;
  }
  if (job.kind === 'windows') return 'Windows print queue';
  return job.sku || 'Label';
}

function renderQueue(payload = state.queue) {
  const list = $('#queue-list');
  if (!list) return;
  const jobs = (payload?.jobs || []);
  const current = payload?.current || null;
  const rows = current ? [current, ...jobs] : jobs;
  const count = $('#queue-count');
  if (count) count.textContent = String(rows.length);
  if (!rows.length) {
    list.innerHTML = '<div class="panel-empty">Nothing printed yet. Jobs appear here and can be repeated or removed.</div>';
    return;
  }
  list.innerHTML = rows.map((job) => {
    const icon = QUEUE_STATE_ICONS[job.state] || 'circle';
    const size = job.width_mm && job.height_mm ? `${round2(job.width_mm)} × ${round2(job.height_mm)} mm` : '';
    const when = queueTime(job.finished_at || job.started_at);
    const meta = [size, when, job.printer].filter(Boolean).join(' · ');
    const progress = job.state === 'running' && job.progress !== undefined
      ? `<div class="queue-progress"><span style="width:${Math.round((job.progress || 0) * 100)}%"></span></div>`
      : '';
    const repeat = job.repeatable && job.state !== 'running'
      ? `<button type="button" class="queue-btn" data-queue-repeat="${job.id}" title="Print this job again"><i data-lucide="repeat"></i></button>`
      : '';
    const remove = job.state === 'running'
      ? `<button type="button" class="queue-btn danger" data-queue-cancel="1" title="Cancel this job"><i data-lucide="x"></i></button>`
      : `<button type="button" class="queue-btn danger" data-queue-remove="${job.id}" title="Remove from the queue"><i data-lucide="x"></i></button>`;
    const error = job.error ? `<div class="queue-error" title="${escapeHtml(job.error)}">${escapeHtml(job.error)}</div>` : '';
    return `<div class="queue-row ${job.state}" data-queue-row="${job.id ?? 'current'}">
      <span class="queue-icon"><i data-lucide="${icon}"></i></span>
      <div class="queue-main">
        <div class="queue-title">${escapeHtml(queueTitle(job))}</div>
        <div class="queue-meta">${escapeHtml(meta)}</div>
        ${progress}${error}
      </div>
      <div class="queue-actions">${repeat}${remove}</div>
    </div>`;
  }).join('');
  refreshIcons(list);
}

async function loadQueue() {
  try {
    state.queue = await api('/api/queue');
  } catch (_) {
    state.queue = { jobs: [], current: null };
  }
  renderQueue();
}

function startQueueWatch() {
  loadQueue();
  if (state.queueTimer) clearInterval(state.queueTimer);
  state.queueTimer = setInterval(loadQueue, 4000);
}

/* Height of the queue section, remembered in the database like every other panel size. */
function applyQueueHeight() {
  const list = $('#queue-list');
  if (!list) return;
  const height = Number(state.queueHeight) || 0;
  if (height > 0) {
    list.classList.add('sized');
    list.style.height = `${height}px`;
  } else {
    list.classList.remove('sized');
    list.style.height = '';
  }
}

function bindQueueSplitter() {
  const splitter = $('#splitter-queue');
  const list = $('#queue-list');
  if (!splitter || !list) return;
  splitter.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = list.getBoundingClientRect().height;
    const panel = document.querySelector('aside.panel').getBoundingClientRect();
    splitter.classList.add('dragging');
    try { splitter.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
    const move = (moveEvent) => {
      const height = Math.max(74, Math.min(panel.height - 220, startHeight - (moveEvent.clientY - startY)));
      state.queueHeight = Math.round(height);
      applyQueueHeight();
    };
    const up = () => {
      splitter.classList.remove('dragging');
      splitter.removeEventListener('pointermove', move);
      splitter.removeEventListener('pointerup', up);
      writePref('queueHeight', state.queueHeight);
    };
    splitter.addEventListener('pointermove', move);
    splitter.addEventListener('pointerup', up);
  });
  splitter.addEventListener('dblclick', () => {
    state.queueHeight = 0;
    applyQueueHeight();
    writePref('queueHeight', 0);
  });
}

async function repeatQueueJob(rowId) {
  try {
    await api(`/api/queue/${rowId}/repeat`, 'POST');
    toast('Printing the job again', 'ok');
    await loadQueue();
    pollJob();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function removeQueueJob(rowId) {
  try {
    state.queue = await api(`/api/queue/${rowId}`, 'DELETE');
    renderQueue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function cancelQueueJob() {
  try {
    await api('/api/cancel', 'POST');
    await loadQueue();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function bindQueueActions() {
  const list = $('#queue-list');
  if (!list) return;
  list.addEventListener('click', (event) => {
    const repeat = event.target.closest('[data-queue-repeat]');
    if (repeat) { repeatQueueJob(repeat.dataset.queueRepeat); return; }
    const remove = event.target.closest('[data-queue-remove]');
    if (remove) { removeQueueJob(remove.dataset.queueRemove); return; }
    if (event.target.closest('[data-queue-cancel]')) cancelQueueJob();
  });
}


/* ============================================================ backend health */
/* The chip in the top-right corner answers one question: can the browser talk to the local
   backend? "Ready" means the last probe succeeded, "Not Ready" means it did not — the printer's
   Bluetooth state is a separate thing and lives in the status bar. */

function markBackend(ok, error = null, payload = null) {
  const backend = state.backend;
  backend.ok = ok;
  backend.at = Date.now();
  if (error) backend.error = error;
  if (payload) {
    backend.version = payload.version || backend.version;
    backend.detail = payload;
    backend.error = null;
  }
  renderBackendChip();
}

function renderBackendChip() {
  const chip = $('#menu-conn');
  const dot = $('#conn-dot');
  const text = $('#conn-text');
  if (!chip || !dot || !text) return;
  const backend = state.backend;
  const ready = backend.ok === true;
  const unknown = backend.ok === null;
  dot.className = `dot ${unknown ? '' : ready ? 'ok' : 'err'}`;
  text.textContent = unknown ? 'Checking…' : ready ? 'Ready' : 'Not Ready';
  chip.classList.toggle('ready', ready);
  chip.classList.toggle('not-ready', backend.ok === false);
  const detail = backend.detail || {};
  chip.title = [
    ready ? 'Backend is answering' : unknown ? 'Contacting the local backend…' : 'The local backend is not answering',
    detail.version ? `version ${detail.version}` : '',
    detail.uptime_s !== undefined ? `up ${Math.round(detail.uptime_s)} s` : '',
    backend.latencyMs !== null ? `last reply ${backend.latencyMs} ms` : '',
    detail.connected ? 'printer connected' : 'no printer connected',
    detail.auto_connect ? `auto-connect: ${detail.auto_connect}` : '',
    backend.error ? `error: ${backend.error}` : '',
  ].filter(Boolean).join('\n');
}

async function pingBackend() {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/api/health', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const payload = await response.json();
    state.backend.latencyMs = Math.round(performance.now() - started);
    markBackend(true, null, payload);
  } catch (error) {
    state.backend.latencyMs = null;
    markBackend(false, error.name === 'AbortError' ? 'no answer within 4 s' : error.message);
  } finally {
    clearTimeout(timer);
  }
}

function startBackendWatch() {
  renderBackendChip();
  pingBackend();
  if (state.backendTimer) clearInterval(state.backendTimer);
  state.backendTimer = setInterval(pingBackend, 3000);
  window.addEventListener('online', pingBackend);
  window.addEventListener('offline', () => markBackend(false, 'browser reports offline'));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pingBackend(); });
}

/* ================================================================== ui prefs */
/* The browser stores nothing. Everything the UI remembers — the working draft, panel
   sizes, view toggles, density, language and the last calibration — is kept in the
   SQLite database (ui_state table) through /api/ui, so a reload, a second window and
   the packaged EXE all see the same state. */

const uiPrefs = {};
let uiPrefsPending = {};
let uiPrefsTimer = null;

let claimedScope = null;

async function loadUiPrefs() {
  try {
    const payload = await api('/api/ui');
    Object.assign(uiPrefs, payload?.keys || {});
    claimedScope = payload?.claimed_scope || null;
  } catch (_) { /* server not up yet — defaults are used, nothing is lost */ }
  await importBrowserPrefs();
}

function readPref(key, fallback = null) {
  return Object.prototype.hasOwnProperty.call(uiPrefs, key) ? uiPrefs[key] : fallback;
}

function writePref(key, value) {
  uiPrefs[key] = value;
  uiPrefsPending[key] = value;
  clearTimeout(uiPrefsTimer);
  uiPrefsTimer = setTimeout(flushPrefs, 350);
}

function flushPrefs() {
  clearTimeout(uiPrefsTimer);
  const patch = uiPrefsPending;
  uiPrefsPending = {};
  if (!Object.keys(patch).length) return Promise.resolve();
  return api('/api/ui', 'POST', patch).then((payload) => {
    if (payload?.keys) Object.assign(uiPrefs, payload.keys);
  }).catch(() => {
    // Keep the change and retry: the database must not silently miss a value.
    uiPrefsPending = { ...patch, ...uiPrefsPending };
    clearTimeout(uiPrefsTimer);
    uiPrefsTimer = setTimeout(flushPrefs, 2000);
  });
}

/* One-time move of anything an older build left in the browser; afterwards the keys are gone. */
const LEGACY_PREF_KEYS = {
  'niibot.draft': 'draft',
  'niibot.panelWidth': 'panelWidth',
  'niibot.sheetHeight': 'sheetHeight',
  'niibot.view': 'view',
  'niibot.density': 'density',
  'niibot.lastModel': 'lastModel',
  'niibot.lastProfile': 'lastProfile',
  'niibot.lang': 'language',
};

/* Nothing may stay in the browser: wipe any leftover from an older build (there is no
   reading of localStorage anywhere else in the app). */
function clearLegacyStorage() {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('niibot.')) localStorage.removeItem(key);
    }
  } catch (_) { /* storage blocked */ }
}

async function importBrowserPrefs() {
  if (readPref('browser_prefs_imported')) { clearLegacyStorage(); return; }
  const patch = {};
  try {
    for (const [storageKey, prefKey] of Object.entries(LEGACY_PREF_KEYS)) {
      const raw = localStorage.getItem(storageKey);
      if (raw === null || uiPrefs[prefKey] !== undefined) continue;
      try { patch[prefKey] = JSON.parse(raw); } catch (_) { patch[prefKey] = raw; }
    }
    for (const storageKey of Object.keys(LEGACY_PREF_KEYS)) localStorage.removeItem(storageKey);
  } catch (_) { /* storage blocked — nothing to move */ }
  patch.browser_prefs_imported = true;
  Object.assign(uiPrefs, patch);
  await api('/api/ui', 'POST', patch).catch(() => {});
  clearLegacyStorage();
}

/* ==================================================================== state */
const state = {
  spec: null,
  options: null,
  printer: null,
  connected: false,
  selectedId: null,
  /* Element the next click would pick — the frame drawn on it is the hint that it is draggable. */
  hoverId: null,
  preview: null,
  previewToken: 0,
  pxPerMm: 4,
  history: [],
  historyIndex: -1,
  labelId: null,
  jobTimer: null,
  statusTimer: null,
  showRegions: true,
  showGrid: false,
  snapToGrid: true,
  view: 'design',
  sheetHeight: 0,
  dragging: false,
  previewPending: false,
  /* element id -> { min_w_mm, min_h_mm, square } for QR/barcode, from the printer's dpi */
  codeLimits: {},
  /* Backend liveness shown in the top-right chip: null = still checking. */
  backend: { ok: null, latencyMs: null, at: null, version: null, error: null, detail: null },
  backendTimer: null,
  /* Print history from /api/queue, newest first, plus the running job. */
  queue: { jobs: [], current: null },
  queueTimer: null,
  catalog: [],
  lastDevices: null,
  printerProtocol: 'auto',
  showAllBle: false,
  printers: [],
  printerInventory: null,
  /* Calibration that was in use when the printer was last connected — kept on screen
     (with a warning) while the printer is away, because an offline printer reports no RFID. */
  lastProfile: null,
  calibrationOffline: false,
  appliedProfileKey: null,
};

const selectedElement = () => state.spec?.elements.find((element) => element.id === state.selectedId) || null;

/* ================================================================== history */
function pushHistory() {
  const snapshot = JSON.stringify(state.spec);
  if (state.history[state.historyIndex] === snapshot) return;
  state.history = state.history.slice(0, state.historyIndex + 1);
  state.history.push(snapshot);
  if (state.history.length > 80) state.history.shift();
  state.historyIndex = state.history.length - 1;
  scheduleAutosave();
}

/* ---------------------------------------------------------------- autosave */
let autosaveTimer = null;

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(saveDraft, 1200);
}

/* One working draft per printer type: a B1 label makes no sense on a 96 px D11s. */
function draftKey() {
  const scope = state.printer?.device_scope || state.deviceScope || '';
  return scope ? `draft:${scope}` : 'draft';
}

function saveDraft() {
  if (!state.spec) return;
  writePref(draftKey(), {
    at: Date.now(),
    labelId: state.labelId,
    view: state.view,
    spec: state.spec,
  });
}

function clearDraft() {
  clearTimeout(autosaveTimer);
  if (!Object.prototype.hasOwnProperty.call(uiPrefs, draftKey())) return;
  writePref(draftKey(), null);
  flushPrefs();
}

/* The draft used to be global; give it to the first printer type that is in use. */
function adoptLegacyDraft() {
  if (claimDone || uiPrefs.draft === undefined) return;
  // The pre-scope library belonged to the first Niimbot printer (the only family the old
  // build supported), so never hand it to a Fichero/LuckPrinter unit.
  const target = claimedScope || (String(state.deviceScope || '').startsWith('niimbot:') ? state.deviceScope : '');
  if (!target) return;
  claimDone = true;
  const key = `draft:${target}`;
  if (uiPrefs[key] === undefined) writePref(key, uiPrefs.draft);
  writePref('draft', null);
  flushPrefs();
}

let claimDone = false;

function restoreDraft() {
  const draft = readPref(draftKey());
  if (!draft || !draft.spec || !Array.isArray(draft.spec.elements)) return false;
  setSpec(draft.spec, { labelId: draft.labelId || null });
  // Design is the default view: never open in the sheet (the switch is one click away).
  if (state.view !== 'design') switchView('design');
  return true;
}

function undo() {
  const current = JSON.stringify(state.spec);
  if (state.history[state.historyIndex] !== current) {
    state.history = state.history.slice(0, state.historyIndex + 1);
    state.history.push(current);
    state.historyIndex = state.history.length - 1;
  }
  if (state.historyIndex <= 0) return;
  state.historyIndex -= 1;
  state.spec = JSON.parse(state.history[state.historyIndex]);
  syncSpec();
  buildInspector();
}

function redo() {
  if (state.historyIndex >= state.history.length - 1) return;
  state.historyIndex += 1;
  state.spec = JSON.parse(state.history[state.historyIndex]);
  syncSpec();
  buildInspector();
}

/* ===================================================================== spec */
function normalizeSpec(spec) {
  const normalized = Object.assign(
    { name: 'Label', width_mm: 40, height_mm: 30, gap_mm: 2, label_type: 1, density: 3, copies: 1, elements: [] },
    spec,
  );
  normalized.elements = (normalized.elements || []).map((element) => Object.assign(
    {
      id: uid(),
      type: 'text',
      x: 0, y: 0, w: 30, h: 8,
      rotation: 0,
      text: '',
      font: 'Arial',
      font_size_pt: 9,
      bold: false,
      italic: false,
      align: 'left',
      valign: 'middle',
      line_spacing: 1.15,
      auto_fit: true,
      barcode_format: 'code128',
      show_text: false,
      ec_level: 'M',
      fill: false,
      thickness_mm: 0.3,
      image_id: null,
    },
    element,
    { id: element.id || uid() },
  ));
  normalized.sheet = Object.assign(
    { columns: [], rows: [], mirror: false, mirror_flip: false },
    normalized.sheet || {},
  );
  normalized.sheet.columns = (normalized.sheet.columns || []).map((column) => Object.assign(
    { id: column.id || uid(), element_id: column.element_id, name: column.name || '' },
    column,
  ));
  normalized.sheet.rows = (normalized.sheet.rows || []).map((row) => Object.assign({}, row));
  return normalized;
}

function setSpec(spec, options = {}) {
  state.spec = normalizeSpec(spec);
  if (!options.keepSelection) state.selectedId = null;
  state.labelId = options.labelId ?? null;
  state.codeLimits = {};
  pushHistory();
  syncSpec();
  buildInspector();
  refreshCodeLimits();
}

/** Re-read the printable minimum for every code in the label (dpi may have changed too). */
function refreshCodeLimits() {
  const codes = (state.spec?.elements || []).filter((element) => CODE_TYPES.has(element.type));
  const live = new Set(codes.map((element) => element.id));
  for (const id of Object.keys(state.codeLimits)) {
    if (!live.has(id)) delete state.codeLimits[id];
  }
  if (!codes.length) return;
  Promise.all(codes.map((element) => ensureCodeLimit(element))).then(() => {
    if (state.selectedId) buildInspector();
  });
}

function syncSpec() {
  renderOverlay();
  schedulePreview();
  if (state.view === 'sheet') renderSheet();
}

/* ===================================================== size / paper geometry */
function printheadWidthMm() {
  const info = state.printer?.info;
  if (state.connected && info && info.printhead_px && info.dpi) {
    return Math.floor(((info.printhead_px * 25.4) / info.dpi) * 2) / 2;
  }
  return null;
}

function applySize(width, height, extra = {}) {
  const limit = printheadWidthMm();
  let nextWidth = Number(width);
  if (limit && nextWidth > limit) {
    nextWidth = limit;
    toast(`This model prints up to ${limit} mm — the label width was clamped.`);
  }
  state.spec.width_mm = nextWidth;
  state.spec.height_mm = Number(height);
  Object.assign(state.spec, extra);
  clampElements();
  syncSpec();
  layoutStage();
  pushHistory();
}

function clampElements() {
  const { width_mm: width, height_mm: height } = state.spec;
  let moved = 0;
  for (const element of state.spec.elements) {
    const x = Math.min(Math.max(0, element.x), Math.max(0, width - 1));
    const y = Math.min(Math.max(0, element.y), Math.max(0, height - 1));
    const w = Math.min(element.w, Math.max(1, width - x));
    const h = Math.min(element.h, Math.max(1, height - y));
    if (w !== element.w || h !== element.h || x !== element.x || y !== element.y) moved += 1;
    element.w = w;
    element.h = h;
    element.x = x;
    element.y = y;
  }
  if (moved) toast(`Fitted ${moved} element(s) to the new format.`);
}

/* Load the workspace that belongs to the printer that just connected. */
function switchToDeviceScope(payload) {
  adoptLegacyDraft();
  if (restoreDraft()) {
    toast(`Workspace: ${payload?.info?.model_name || state.deviceScope}`, 'ok');
    requestPreview();
    return;
  }
  setSpec(state.options?.default_spec || state.spec);
  const profile = payload?.profile;
  if (profile) {
    applySize(profile.width_mm, profile.height_mm, {
      gap_mm: profile.gap_mm ?? state.spec.gap_mm,
      label_type: profile.label_type ?? state.spec.label_type,
    });
  } else {
    const dpi = Number(payload?.info?.dpi) || 203;
    const headMm = ((Number(payload?.info?.printhead_px) || 384) / dpi) * 25.4;
    applySize(Math.max(5, round2(Math.min(state.spec.width_mm, headMm))), state.spec.height_mm, {});
  }
  saveDraft();
  toast(`New workspace for ${payload?.info?.model_name || state.deviceScope}`, 'ok');
  requestPreview();
}

function applyKnownFormat(paper) {
  if (!paper || !paper.detected_width_mm || !paper.detected_height_mm) return false;
  const same = Math.abs(state.spec.width_mm - paper.detected_width_mm) < 0.05
    && Math.abs(state.spec.height_mm - paper.detected_height_mm) < 0.05;
  if (same) return false;
  applySize(paper.detected_width_mm, paper.detected_height_mm, {
    gap_mm: paper.detected_gap_mm ?? state.spec.gap_mm,
    label_type: paper.detected_label_type ?? state.spec.label_type,
  });
  const source = paper.geometry_source === 'catalog' ? 'from the NIIMBOT catalogue' : 'from the printer';
  toast(`Format ${source}: ${paper.detected_width_mm} × ${paper.detected_height_mm} mm`, 'ok');
  return true;
}

function gapInfoMm() {
  const paper = state.printer?.paper || {};
  if (paper.detected_gap_mm !== undefined && paper.detected_gap_mm !== null) return Number(paper.detected_gap_mm);
  if (paper.catalog?.gap_mm) return Number(paper.catalog.gap_mm);
  if (state.spec && typeof state.spec.gap_mm === 'number') return Number(state.spec.gap_mm);
  return 0;
}

function effectiveLabelType() {
  const paper = state.printer?.paper || {};
  if (paper.label_type !== undefined && paper.label_type !== null) return Number(paper.label_type);
  return Number(state.spec?.label_type ?? 1);
}

const hasGapPaper = () => ![3, 11].includes(effectiveLabelType());

function paperGeometry() {
  const paper = state.printer?.paper || {};
  const info = state.printer?.info || {};
  const catalog = paper.catalog || null;
  const rows = Math.max(1, Number(catalog?.rows || 1));
  const headMm = state.connected && info.dpi && info.printhead_px ? (info.printhead_px * 25.4) / info.dpi : null;
  return { rows, catalog, typeName: paper.label_type_name || null, gapMm: gapInfoMm(), headMm };
}

/* ================================================================== preview */
let previewTimer = null;

function schedulePreview() {
  clearTimeout(previewTimer);
  if (state.dragging) {
    state.previewPending = true;
    return;
  }
  previewTimer = setTimeout(requestPreview, 100);
}

async function requestPreview() {
  const token = ++state.previewToken;
  try {
    const result = await api('/api/preview', 'POST', { spec: state.spec, mirror: mirrorRequest() });
    if (token !== state.previewToken) return;
    state.preview = result;
    // The canvas must show exactly the bitmap the printer receives — never a smoothed stand-in.
    $('#preview').src = `data:image/png;base64,${result.png_print || result.png}`;
    renderWarnings(result.warnings || []);
    layoutStage();
  } catch (error) {
    if (token === state.previewToken) toast(error.message, 'error');
  }
}

function renderWarnings(warnings) {
  const container = $('#warnings');
  container.innerHTML = '';
  for (const warning of warnings) {
    const node = document.createElement('div');
    node.className = 'warning';
    node.textContent = warning;
    container.appendChild(node);
  }
}

/* =================================================================== layout */
function layoutStage() {
  if (!state.spec) return;
  const wrap = $('#canvas-wrap');
  const gapMm = state.showRegions && hasGapPaper() ? gapInfoMm() : 0;
  const availableWidth = Math.max(120, wrap.clientWidth - 48);
  const availableHeight = Math.max(120, wrap.clientHeight - 48);
  const labelShare = state.spec.height_mm / Math.max(1e-6, state.spec.height_mm + Math.max(0, gapMm));
  const ratio = state.spec.width_mm / Math.max(0.1, state.spec.height_mm);

  let height = availableHeight * labelShare;
  let width = height * ratio;
  if (width > availableWidth) {
    width = availableWidth;
    height = width / ratio;
  }
  width = Math.max(60, Math.floor(width));
  height = Math.max(30, Math.floor(height));

  const stage = $('#stage');
  stage.style.width = `${width}px`;
  stage.style.height = `${height}px`;
  state.pxPerMm = width / state.spec.width_mm;

  const gapEl = $('#gap-preview');
  const gapPx = Math.round(gapMm * state.pxPerMm);
  if (gapMm > 0 && gapPx >= 6) {
    gapEl.classList.remove('hidden');
    gapEl.style.height = `${gapPx}px`;
    gapEl.style.width = `${width}px`;
    $('#gap-preview-label').textContent = `gap ${round2(gapMm)} mm`;
  } else {
    gapEl.classList.add('hidden');
  }

  renderPaperRegions();
  renderStageMeta();
  applyGridSize();
  renderOverlay();
}

function applyGridSize() {
  const layer = $('#grid-layer');
  if (!layer) return;
  layer.hidden = !state.showGrid;
  if (!state.showGrid || !state.spec) return;
  const px = state.pxPerMm || 4;
  // Draw the grid the snapping actually uses: 0.5 mm cells, 5 mm lines heavier.
  const minor = px * GRID_MINOR_MM >= 4.5 ? GRID_MINOR_MM : px >= 4.5 ? 1 : 0;
  layer.style.setProperty('--grid-x', `${minor ? minor * px : px}px`);
  layer.style.setProperty('--grid-y', `${minor ? minor * px : px}px`);
  layer.style.setProperty('--grid-major-x', `${GRID_MAJOR_MM * px}px`);
  layer.style.setProperty('--grid-major-y', `${GRID_MAJOR_MM * px}px`);
}

function renderStageMeta() {
  const meta = $('#stage-meta');
  if (!meta || !state.spec) return;
  const spec = state.spec;
  const geo = paperGeometry();
  const paper = state.printer?.paper || {};
  /* Headline chip = the roll's name when it has one, otherwise the label (print page) size.
     The sticker size only matters inside the calibrator, so it lives in the tooltip. */
  const rollProfile = state.printer?.profile;
  const hasSticker = Number(rollProfile?.sticker_w_mm) > 0 && Number(rollProfile?.sticker_h_mm) > 0;
  const stickerText = hasSticker
    ? `${round2(rollProfile.sticker_w_mm)} × ${round2(rollProfile.sticker_h_mm)} mm` : null;
  const pageText = `${round2(spec.width_mm)} × ${round2(spec.height_mm)} mm`;
  const typedName = String(rollProfile?.name || '').trim();
  const nameIsPlaceholder = !typedName
    || /^\d+$/.test(typedName)
    || typedName === String(paper.barcode || '')
    || typedName === stickerText
    || typedName === pageText;
  const headline = nameIsPlaceholder ? pageText : typedName;
  const headlineTitle = `Click to calibrate${stickerText ? ` · sticker ${stickerText}` : ''} · print page ${pageText}`;
  const chips = [];
  if (state.calibrationOffline && rollProfile) {
    chips.push(`<span class="meta-chip warn" id="meta-calib-offline"
      title="The printer is disconnected, so the roll code cannot be read — showing the calibration of the last roll used. It is not synced with the printer.">
      <i data-lucide="alert-triangle"></i> offline calibration</span>`);
  }
  chips.push(`<button type="button" class="meta-chip" id="meta-roll-size" title="${escapeHtml(headlineTitle)}">
    <b>${escapeHtml(headline)}</b></button>`);
  if (geo.typeName) chips.push(`<span class="meta-chip type">paper: ${escapeHtml(geo.typeName)}</span>`);
  // Reachable off-line too: the calibrator can be edited and stored for the remembered roll.
  /* "Correct calibration" only when a calibration is actually stored for this roll. */
  const calibrated = !!state.printer?.profile;
  chips.push(`<button type="button" class="meta-chip${calibrated ? '' : ' warn'}" id="meta-calibrate"
    title="${calibrated ? 'Item zones of this roll — reopen the calibrator any time'
      : 'No calibration for this roll yet — draw its item zones or pick one from the library'}">
    <i data-lucide="ruler"></i> ${calibrated ? 'Correct calibration' : 'Calibrate this roll'}
  </button>`);
  if (rollProfile && rollProfile.width_mm && rollProfile.height_mm) {
    const headLimit = printheadWidthMm() || Math.max(rollProfile.width_mm, rollProfile.height_mm);
    const rollA = { w: Math.min(rollProfile.width_mm, headLimit), h: rollProfile.height_mm };
    const closeTo = (a, b) => Math.abs(a - b) <= 0.6;
    const same = (closeTo(spec.width_mm, rollA.w) && closeTo(spec.height_mm, rollA.h))
      || (closeTo(spec.width_mm, rollA.h) && closeTo(spec.height_mm, rollA.w));
    if (!same) {
      chips.push(`<button type="button" class="meta-chip bad" id="meta-fit-roll"
        title="The label is a different size than the roll stored in the calibrator — press to fit it">
        <i data-lucide="maximize"></i> roll ${round2(rollProfile.width_mm)} × ${round2(rollProfile.height_mm)} mm · fit
      </button>`);
    }
  }
  if (geo.rows > 1) chips.push(`<span class="meta-chip">${geo.rows} regions × ${round2(spec.height_mm / geo.rows)} mm</span>`);
  if (geo.rows > 1) {
    const on = !!spec.sheet?.mirror;
    chips.push(`<button type="button" class="meta-chip mirror ${on ? 'active' : ''}" id="meta-mirror"
      title="Edit only the first region — the other ${geo.rows - 1} adapt automatically at print time">
      <i data-lucide="flip-vertical-2"></i> Mirror ${geo.rows}×
    </button>`);
  }
  const cells = profileCells();
  if (cells.length > 1) {
    const onCells = !!spec.sheet?.cells_mirror;
    chips.push(`<button type="button" class="meta-chip mirror ${onCells ? 'active' : ''}" id="meta-cells-mirror"
      title="Design inside cell 1 of the calibrated roll — cells 2..${cells.length} are filled with the same content">
      <i data-lucide="copy"></i> Mirror cells ${cells.length}×
    </button>`);
  }
  if (geo.gapMm > 0 && hasGapPaper()) chips.push(`<span class="meta-chip">gap ${round2(geo.gapMm)} mm</span>`);
  chips.push(`<button type="button" class="meta-chip" id="meta-orient"
    title="Change the orientation — turn the design with the label or just swap the canvas">
    <i data-lucide="rotate-cw"></i> Orientation
  </button>`);
  if (geo.headMm) {
    const fits = spec.width_mm <= geo.headMm + 0.05;
    chips.push(`<span class="meta-chip ${fits ? 'ok' : 'bad'}">head ${geo.headMm.toFixed(1)} mm ${fits ? '✓ printable' : '— clipped'}</span>`);
  }
  meta.innerHTML = chips.join('');
  refreshIcons(meta);
  const sizeChip = $('#meta-roll-size', meta);
  if (sizeChip) sizeChip.onclick = () => openRollSizeDialog();
  const calibrateChip = $('#meta-calibrate', meta);
  if (calibrateChip) calibrateChip.onclick = () => openRollSizeDialog();
  const orientChip = $('#meta-orient', meta);
  if (orientChip) orientChip.onclick = () => openOrientationDialog();
  const cellsMirrorChip = $('#meta-cells-mirror', meta);
  if (cellsMirrorChip) cellsMirrorChip.onclick = () => toggleCellsMirror();
  const fitChip = $('#meta-fit-roll', meta);
  if (fitChip) {
    fitChip.onclick = () => {
      const target = state.printer?.profile;
      if (!target) return;
      applySize(target.width_mm, target.height_mm, { gap_mm: target.gap_mm ?? gapInfoMm() });
      schedulePreview();
      toast(`Label fitted to the roll: ${round2(state.spec.width_mm)} × ${round2(state.spec.height_mm)} mm`, 'ok');
    };
  }
}

/* A roll the printer/catalogue cannot describe: size it, then outline where each item
   on the label can be printed. One *item zone* is one printable item and may consist of
   several outlines (rectangle, circle, ellipse) that merge into a single area. */
function openRollSizeDialog(options = {}) {
  const paper = state.printer?.paper || {};
  const rollName = paper.sku || paper.catalog?.name || paper.barcode || 'this roll';
  /* ``options.profile`` edits a calibration picked from the library (a copy for this roll). */
  const profile = options.profile || state.printer?.profile || null;
  const currentBarcode = () => state.printer?.paper?.barcode
    || String(profile?.key || '').replace(/^bc:/, '') || '';
  /* A friendly name for this roll (the barcode is a poor label everywhere in the UI). */
  const codeOnly = (value) => !value || /^\d+$/.test(String(value).trim());
  const rollLabel = options.profile
    ? calibrationName(options.profile)
    : !codeOnly(profile?.name) ? String(profile.name)
      : !codeOnly(paper.catalog?.name) ? String(paper.catalog.name) : '';
  const presets = (state.options?.paper_presets || []).map((preset) => ({
    label: preset.name || `${round2(preset.width_mm)} × ${round2(preset.height_mm)} mm`,
    width: preset.width_mm,
    height: preset.height_mm,
    rows: preset.rows || 1,
  }));

  let rows = Math.max(1, Number(profile?.rows || paper.catalog?.rows) || 1);
  let cols = Math.max(1, Number(profile?.cols || paper.catalog?.cols) || 1);
  let rowSpace = Math.max(0, Number(profile?.row_space_mm) || 0);
  let colSpace = Math.max(0, Number(profile?.col_space_mm) || 0);
  /* The main size is one sticker; the print page follows from it and the grid. */
  let stickerW = Number(profile?.sticker_w_mm) || 0;
  let stickerH = Number(profile?.sticker_h_mm) || 0;
  if (!stickerW || !stickerH) {
    stickerW = round2((Number(profile?.width_mm) || Number(state.spec?.width_mm) || 40) / cols);
    stickerH = round2((Number(profile?.height_mm) || Number(state.spec?.height_mm) || 30) / rows);
  }
  let width = stickerW;
  let height = stickerH;
  const pageSize = () => {
    width = Math.max(1, round2(cols * stickerW + (cols - 1) * colSpace));
    height = Math.max(1, round2(rows * stickerH + (rows - 1) * rowSpace));
    return { width, height };
  };
  pageSize();
  const stickerLabel = () => `${round2(stickerW)} × ${round2(stickerH)} mm`;
  let gap = Number(profile?.gap_mm ?? gapInfoMm()) || 0;

  const cleanShape = (shape) => ({
    kind: shape.kind === 'circle' ? 'circle' : 'rect',
    x: Number(shape.x) || 0,
    y: Number(shape.y) || 0,
    w: Math.max(0.4, Number(shape.w) || 0.4),
    h: Math.max(0.4, Number(shape.h) || 0.4),
  });

  /* Stored regions carry their outlines; older ones only have the single box. */
  const zoneFromRegion = (region, index) => {
    const list = Array.isArray(region.shapes) && region.shapes.length
      ? region.shapes.map(cleanShape)
      : region.shape === 'line'
        ? []
        : [cleanShape({
          kind: region.shape === 'circle' ? 'circle' : 'rect',
          x: region.x, y: region.y, w: region.w, h: region.h,
        })];
    return {
      label: region.label || `Item ${index + 1}`,
      thickness: Number(region.thickness_mm) || 0.3,
      shapes: list.filter((shape) => shape.w > 0.4 && shape.h > 0.4),
    };
  };

  let zones = (profile?.regions || []).map(zoneFromRegion).filter((zone) => zone.shapes.length);
  let autoZone = !(profile?.regions || []).length;

  const defaultZone = () => {
    const margin = Math.min(0.5, Math.min(stickerW, stickerH) / 8);
    return {
      label: 'Item 1',
      thickness: 0.3,
      shapes: [{
        kind: 'rect',
        x: round2(margin),
        y: round2(margin),
        w: round2(Math.max(1, stickerW - margin * 2)),
        h: round2(Math.max(1, stickerH - margin * 2)),
      }],
    };
  };
  if (!zones.length) zones = [defaultZone()];

  let selection = new Set([0]);
  let activeShape = null;
  let tool = 'select';
  let zoom = 1;
  const view = { scale: 4, ox: 0, oy: 0 };

  const body = document.createElement('div');
  body.className = 'calibrator';
  body.innerHTML = `
    <div class="cal-col cal-col-left">
      <div class="hint">The B1 cannot report the label size over Bluetooth and
        <code>${escapeHtml(rollName)}</code> is not in the catalogue on this machine.
        Set the size of <strong>one sticker</strong>, outline where it can print, and everything is
        remembered for this roll code.</div>

      <div class="cal-section">
        <div class="cal-section-head">Sticker</div>
        <div class="field">
          <label for="cal-name">Roll name</label>
          <input id="cal-name" data-key="cal-name" value="${escapeHtml(rollLabel)}"
            placeholder="${escapeHtml(stickerLabel())}" autocomplete="off">
        </div>
        <div class="grid-2">
          ${field('Sticker width (mm)', numberInput('cal-width', round2(stickerW), 0.5, 2, 108))}
          ${field('Sticker height (mm)', numberInput('cal-height', round2(stickerH), 0.5, 2, 300))}
        </div>
        <div class="grid-2">
          ${field('Label gap (mm)', numberInput('cal-gap', round2(gap), 0.5, 0, 20))}
          ${presets.length ? `<div class="field"><label for="cal-preset">Common size</label>
            <select id="cal-preset"><option value="">— pick one —</option>
              ${presets.map((preset, index) => `<option value="${index}">${escapeHtml(preset.label)}${preset.rows > 1 ? ` · ${preset.rows} rows` : ''}</option>`).join('')}
            </select></div>` : '<div></div>'}
        </div>
        <div class="hint" id="cal-page"></div>
        <div class="field">
          <label for="cal-library">Start from a saved calibration</label>
          <select id="cal-library"><option value="">— draw a new one —</option></select>
        </div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Item zones</div>
        <div class="grid-2">
          ${field('Rows', numberInput('cal-rows', rows, 1, 1, 12))}
          ${field('Columns', numberInput('cal-cols', cols, 1, 1, 12))}
        </div>
        <div class="grid-2">
          ${field('Row spacing (mm)', numberInput('cal-rowspace', rowSpace, 0.5, 0, 50))}
          ${field('Column spacing (mm)', numberInput('cal-colspace', colSpace, 0.5, 0, 50))}
        </div>
        <div class="cal-toolbar">
          <button type="button" class="btn" data-cal="grid"><i data-lucide="grid-2x2"></i> Grid of zones</button>
          <button type="button" class="btn" data-cal="add"><i data-lucide="plus"></i> Add item</button>
          <button type="button" class="btn" data-cal="single"><i data-lucide="square"></i> One item</button>
        </div>
        <div class="hint">Rows and columns rebuild the zones evenly across the whole label.</div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Draw</div>
        <div class="cal-tools" role="group" aria-label="Drawing tool">
          <button type="button" class="cal-tool on" data-tool="select"><i data-lucide="mouse-pointer-2"></i> Select</button>
          <button type="button" class="cal-tool" data-tool="rect"><i data-lucide="square"></i> Rectangle</button>
          <button type="button" class="cal-tool" data-tool="square"><i data-lucide="box"></i> Square</button>
          <button type="button" class="cal-tool" data-tool="circle"><i data-lucide="circle"></i> Circle</button>
        </div>
        <div class="cal-toolbar">
          <button type="button" class="btn" data-cal="delete"><i data-lucide="trash-2"></i> Delete</button>
          <button type="button" class="btn" data-cal="undo" title="Undo (Ctrl+Z)"><i data-lucide="undo-2"></i></button>
          <button type="button" class="btn" data-cal="redo" title="Redo (Ctrl+Y)"><i data-lucide="redo-2"></i></button>
        </div>
        <div class="hint">Drag on the label to add a zone. Right-click a zone to add a second outline to the
          same item, duplicate it, or copy it onto every zone. Snapping: 0.5 mm (Alt = 0.1 mm free).</div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Selected item</div>
        <div class="cal-editor" id="cal-editor"></div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Align <span class="cal-selcount" id="cal-selcount"></span></div>
        <div class="cal-align" id="cal-align">
          <button type="button" class="cal-a" data-align="left" title="Align left edges">Left</button>
          <button type="button" class="cal-a" data-align="hcenter" title="Centre horizontally">Centre H</button>
          <button type="button" class="cal-a" data-align="right" title="Align right edges">Right</button>
          <button type="button" class="cal-a" data-align="top" title="Align top edges">Top</button>
          <button type="button" class="cal-a" data-align="vmiddle" title="Centre vertically">Middle V</button>
          <button type="button" class="cal-a" data-align="bottom" title="Align bottom edges">Bottom</button>
          <button type="button" class="cal-a wide" data-align="dist-h" title="Equal horizontal spacing">Distribute H</button>
          <button type="button" class="cal-a wide" data-align="dist-v" title="Equal vertical spacing">Distribute V</button>
        </div>
        <div class="hint">Shift+click zones to select several.</div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Print check</div>
        <div class="hint">Prints the borders of every zone on the roll — nothing else. Use it to see whether the
          outlines land where the label really is.</div>
        <label class="cal-check"><input type="checkbox" data-zone="border" checked> Draw the label border as well</label>
        <div class="cal-toolbar">
          <button type="button" class="btn primary" data-cal="outline-print"><i data-lucide="printer"></i> Print zone borders</button>
        </div>
      </div>
    </div>

    <div class="cal-col cal-col-right">
      <div class="cal-canvas-wrap" id="cal-canvas-wrap">
        <canvas id="cal-canvas"></canvas>
      </div>
      <div class="cal-zoombar">
        <button type="button" class="btn" data-zoom="out">−</button>
        <span class="cal-zoom-label" id="cal-zoom-label">100%</span>
        <button type="button" class="btn" data-zoom="in">+</button>
        <button type="button" class="btn" data-zoom="fit">Fit</button>
        <span class="hint">wheel = zoom · middle-drag = pan · click = select</span>
      </div>
      <div class="cal-zonechips" id="cal-zonechips"></div>
      <div class="cal-summary" id="cal-summary"></div>
    </div>
  `;

  const footer = document.createElement('div');
  footer.style.cssText = 'display:flex;align-items:center;gap:10px;width:100%';
  footer.innerHTML = `
    <div class="cal-status" id="cal-status"></div>
    <div class="row" style="gap:8px;flex:0 0 auto">
      <button class="btn" data-cal-cancel>Cancel</button>
      <button class="btn primary" data-cal-save><i data-lucide="ruler"></i> Save for this roll</button>
    </div>`;

  const modal = openModal({
    id: 'roll-size',
    title: 'Calibrate this roll',
    icon: 'ruler',
    width: 900,
    height: 820,
    body,
    footer,
  });

  /* Print and preview of the zone borders live next to the close button. */
  const titlebar = modal.querySelector('.modal-titlebar');
  const titleActions = document.createElement('div');
  titleActions.className = 'modal-actions';
  titleActions.innerHTML = `
    <button type="button" class="modal-icon" data-title-action="print"
      title="Print the zone borders on this roll (calibration check)">
      <i data-lucide="printer"></i></button>`;
  titlebar.insertBefore(titleActions, titlebar.querySelector('.modal-close'));
  refreshIcons(titleActions);

  /* Which roll this calibration belongs to — code and tag serial from the printer. */
  const renderCalStatus = () => {
    const status = $('#cal-status', footer);
    if (!status) return;
    const info = state.printer?.info || {};
    const paperNow = state.printer?.paper || {};
    const bits = [];
    const barcode = currentBarcode();
    if (barcode) bits.push(`<span class="cal-status-item"><em>roll</em> <b>${escapeHtml(barcode)}</b></span>`);
    const serial = paperNow.serial || info.serial;
    if (serial) bits.push(`<span class="cal-status-item"><em>SN</em> <b>${escapeHtml(serial)}</b></span>`);
    bits.push(`<span class="cal-status-item">${stickerLabel()} · page ${round2(width)} × ${round2(height)} mm</span>`);
    if (!state.connected) bits.push('<span class="cal-status-item bad">printer off-line</span>');
    status.innerHTML = bits.join('');
  };

  const wrap = $('#cal-canvas-wrap', body);
  const canvas = $('#cal-canvas', body);
  const ctx = canvas.getContext('2d');
  const editor = $('#cal-editor', body);
  const summary = $('#cal-summary', body);
  const chipsBox = $('#cal-zonechips', body);
  const zoomLabel = $('#cal-zoom-label', body);
  const silCanvas = document.createElement('canvas');
  const silCtx = silCanvas.getContext('2d');
  const eroCanvas = document.createElement('canvas');
  const eroCtx = eroCanvas.getContext('2d');
  const bandCanvas = document.createElement('canvas');
  const bandCtx = bandCanvas.getContext('2d');

  const COLOUR = { teal: '#0a8f92', violet: '#7c4dd6', navy: '#001a4d', warn: '#d1663a' };
  let dpr = 1;
  let cssW = 480;
  let cssH = 420;

  /* ------------------------------------------------------------- geometry */
  const zoneBounds = (zone) => {
    const left = Math.min(...zone.shapes.map((shape) => shape.x));
    const top = Math.min(...zone.shapes.map((shape) => shape.y));
    const right = Math.max(...zone.shapes.map((shape) => shape.x + shape.w));
    const bottom = Math.max(...zone.shapes.map((shape) => shape.y + shape.h));
    return { x: left, y: top, w: right - left, h: bottom - top };
  };
  const translateZone = (zone, dx, dy) => zone.shapes.forEach((shape) => {
    shape.x += dx;
    shape.y += dy;
  });
  const scaleZone = (zone, from, to) => {
    const sx = from.w > 0.01 ? to.w / from.w : 1;
    const sy = from.h > 0.01 ? to.h / from.h : 1;
    zone.shapes.forEach((shape) => {
      shape.x = to.x + (shape.x - from.x) * sx;
      shape.y = to.y + (shape.y - from.y) * sy;
      shape.w = Math.max(0.4, shape.w * sx);
      shape.h = Math.max(0.4, shape.h * sy);
    });
  };
  const pointInShape = (shape, x, y) => {
    if (x < shape.x || x > shape.x + shape.w || y < shape.y || y > shape.y + shape.h) return false;
    if (shape.kind !== 'circle') return true;
    const rx = shape.w / 2;
    const ry = shape.h / 2;
    const dx = (x - (shape.x + rx)) / Math.max(1e-6, rx);
    const dy = (y - (shape.y + ry)) / Math.max(1e-6, ry);
    return dx * dx + dy * dy <= 1;
  };
  const hitZone = (x, y) => {
    for (let index = zones.length - 1; index >= 0; index -= 1) {
      if (zones[index].shapes.some((shape) => pointInShape(shape, x, y))) return index;
    }
    // The gaps between an item's outlines still belong to that item.
    for (let index = zones.length - 1; index >= 0; index -= 1) {
      const box = zoneBounds(zones[index]);
      if (x >= box.x - 0.5 && x <= box.x + box.w + 0.5 && y >= box.y - 0.5 && y <= box.y + box.h + 0.5) {
        return index;
      }
    }
    return -1;
  };
  const hitShape = (zone, x, y) => {
    for (let index = zone.shapes.length - 1; index >= 0; index -= 1) {
      if (pointInShape(zone.shapes[index], x, y)) return index;
    }
    return -1;
  };

  const cssX = (mm) => view.ox + mm * view.scale;
  const cssY = (mm) => view.oy + mm * view.scale;
  const mmX = (px) => (px - view.ox) / view.scale;
  const mmY = (px) => (px - view.oy) / view.scale;

  const snapMm = (value, free) => {
    const step = free ? 0.1 : 0.5;
    return Math.round(value / step) * step;
  };

  const readSize = () => {
    const read = (name, fallback) => Number($(`[data-key="cal-${name}"]`, body)?.value) || fallback;
    stickerW = Math.max(2, read('width', stickerW));
    stickerH = Math.max(2, read('height', stickerH));
    rows = Math.max(1, Math.min(12, Math.round(read('rows', rows))));
    cols = Math.max(1, Math.min(12, Math.round(read('cols', cols))));
    gap = Math.max(0, Number($('[data-key="cal-gap"]', body)?.value) || 0);
    rowSpace = Math.max(0, Number($('[data-key="cal-rowspace"]', body)?.value) || 0);
    colSpace = Math.max(0, Number($('[data-key="cal-colspace"]', body)?.value) || 0);
    pageSize();
    renderPageNote();
  };

  /* The main size is one sticker — the print page is always the grid of them. */
  const renderPageNote = () => {
    const note = $('#cal-page', body);
    if (!note) return;
    const headLimit = printheadWidthMm();
    const over = headLimit && width > headLimit + 0.05
      ? ` · <strong>${round2(width - headLimit)} mm wider than the printhead</strong>` : '';
    const grid = cols * rows === 1 ? 'one sticker'
      : `${cols} across × ${rows} down`;
    note.innerHTML = `Print page: <strong>${round2(width)} × ${round2(height)} mm</strong> (${grid})${over}`;
  };

  /* -------------------------------------------------------------- history */
  let history = [JSON.stringify(zones)];
  let historyIndex = 0;
  const commit = () => {
    const snapshot = JSON.stringify(zones);
    if (history[historyIndex] !== snapshot) {
      history = history.slice(0, historyIndex + 1);
      history.push(snapshot);
      if (history.length > 80) history.shift();
      historyIndex = history.length - 1;
    }
    draw();
  };
  const clampSelection = () => {
    const valid = [...selection].filter((index) => zones[index]);
    if (!valid.length && zones.length) valid.push(0);
    selection = new Set(valid);
    const first = valid[0];
    if (activeShape !== null && (!zones[first] || !zones[first].shapes[activeShape])) activeShape = null;
  };
  const stepHistory = (delta) => {
    const next = historyIndex + delta;
    if (next < 0 || next >= history.length) return;
    historyIndex = next;
    zones = JSON.parse(history[historyIndex]);
    clampSelection();
    renderInspector();
    draw();
  };

  const selectedIndices = () => [...selection].filter((index) => zones[index]).sort((a, b) => a - b);

  /* ------------------------------------------------------------- toolbar */
  const setTool = (next) => {
    tool = next;
    $$('.cal-tool', body).forEach((button) => button.classList.toggle('on', button.dataset.tool === tool));
    canvas.style.cursor = tool === 'select' ? 'default' : 'crosshair';
  };

  const applyGrid = () => {
    readSize();
    // One zone per sticker, laid out exactly where the stickers sit on the page.
    const zoneW = Math.max(1, stickerW);
    const zoneH = Math.max(1, stickerH);
    zones = [];
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        zones.push({
          label: rows * cols > 1 ? `R${row + 1}C${col + 1}` : 'Item 1',
          thickness: 0.3,
          shapes: [{
            kind: 'rect',
            x: round2(col * (zoneW + colSpace)),
            y: round2(row * (zoneH + rowSpace)),
            w: round2(zoneW),
            h: round2(zoneH),
          }],
        });
      }
    }
    autoZone = false;
    selection = new Set(zones.length ? [0] : []);
    activeShape = null;
    renderInspector();
    commit();
    toast(`${zones.length} item zone${zones.length === 1 ? '' : 's'} laid out`, 'ok');
  };

  const singleZone = () => {
    readSize();
    zones = [defaultZone()];
    autoZone = true;
    selection = new Set([0]);
    activeShape = null;
    renderInspector();
    commit();
  };

  const addZone = () => {
    readSize();
    const box = {
      x: round2(Math.max(0, (stickerW - stickerW * 0.8) / 2)),
      y: round2(Math.max(0, (stickerH - stickerH * 0.8) / 2)),
      w: round2(stickerW * 0.8),
      h: round2(stickerH * 0.8),
    };
    zones.push({ label: `Item ${zones.length + 1}`, thickness: 0.3, shapes: [{ kind: 'rect', ...box }] });
    autoZone = false;
    selection = new Set([zones.length - 1]);
    activeShape = null;
    renderInspector();
    commit();
  };

  const addShapeToZone = (index, kind) => {
    const zone = zones[index];
    if (!zone) return;
    const box = zoneBounds(zone);
    // The same area as the item — the new outline starts exactly on top of the existing one,
    // then its handles shape the second lobe of the item.
    zone.shapes.push({ kind, x: box.x, y: box.y, w: box.w, h: box.h });
    selection = new Set([index]);
    activeShape = zone.shapes.length - 1;
    renderInspector();
    commit();
    toast(kind === 'circle'
      ? 'Circle added — it outlines the same item; drag its handles to shape the second area'
      : 'Rectangle added — it outlines the same item; drag its handles to shape the second area');
  };

  const removeShape = (index, order) => {
    const zone = zones[index];
    if (!zone) return;
    zone.shapes.splice(order, 1);
    if (!zone.shapes.length) zones.splice(index, 1);
    activeShape = null;
    clampSelection();
    renderInspector();
    commit();
  };

  const deleteSelection = () => {
    const targets = selectedIndices().sort((a, b) => b - a);
    if (!targets.length) return;
    targets.forEach((index) => zones.splice(index, 1));
    activeShape = null;
    clampSelection();
    renderInspector();
    commit();
  };

  const duplicateZone = (index) => {
    const zone = zones[index];
    if (!zone) return;
    const box = zoneBounds(zone);
    const copy = {
      label: `${zone.label} copy`,
      thickness: zone.thickness,
      shapes: zone.shapes.map((shape) => ({
        ...shape,
        x: round2(Math.min(width - shape.w, shape.x + 1)),
        y: round2(Math.min(height - shape.h, shape.y + 1)),
      })),
    };
    if (box.w <= 0) return;
    zones.push(copy);
    selection = new Set([zones.length - 1]);
    activeShape = null;
    renderInspector();
    commit();
  };

  /* Copy this item's outlines (scaled) onto every other zone. */
  const mirrorZone = (index) => {
    const source = zones[index];
    if (!source) return;
    const from = zoneBounds(source);
    zones.forEach((zone, other) => {
      if (other === index) return;
      const to = zoneBounds(zone);
      zone.shapes = source.shapes.map((shape) => ({
        kind: shape.kind,
        x: to.x + ((shape.x - from.x) / Math.max(0.01, from.w)) * to.w,
        y: to.y + ((shape.y - from.y) / Math.max(0.01, from.h)) * to.h,
        w: Math.max(0.4, (shape.w / Math.max(0.01, from.w)) * to.w),
        h: Math.max(0.4, (shape.h / Math.max(0.01, from.h)) * to.h),
      }));
    });
    commit();
    toast(`Item ${index + 1} copied onto ${zones.length - 1} other zone${zones.length === 2 ? '' : 's'}`, 'ok');
  };

  const alignSelection = (mode) => {
    const indices = selectedIndices();
    if (indices.length < 2) return;
    const boxes = new Map(indices.map((index) => [index, zoneBounds(zones[index])]));
    const list = () => [...boxes.values()];
    const left = Math.min(...list().map((box) => box.x));
    const right = Math.max(...list().map((box) => box.x + box.w));
    const top = Math.min(...list().map((box) => box.y));
    const bottom = Math.max(...list().map((box) => box.y + box.h));
    const centreX = (left + right) / 2;
    const centreY = (top + bottom) / 2;

    /* Put a zone's own top-left at (x, y) — always inside the label, so nothing can jump off screen. */
    const place = (index, x, y) => {
      const zone = zones[index];
      const box = boxes.get(index);
      if (!zone || !box) return;
      const dx = Math.min(width - (box.x + box.w), Math.max(-box.x, x - box.x));
      const dy = Math.min(height - (box.y + box.h), Math.max(-box.y, y - box.y));
      translateZone(zone, round2(dx), round2(dy));
    };

    if (mode === 'dist-h' || mode === 'dist-v') {
      const horizontal = mode === 'dist-h';
      const size = (box) => (horizontal ? box.w : box.h);
      const start = (box) => (horizontal ? box.x : box.y);
      const sorted = [...indices].sort((a, b) => start(boxes.get(a)) - start(boxes.get(b)));
      const used = sorted.reduce((sum, index) => sum + size(boxes.get(index)), 0);
      const labelSpan = horizontal ? width : height;
      /* Equal gaps between the items AND equal margins at the label edges — that is what
         "distributed across the sticker" means; only fall back to the group's own span when
         the items are too big to fit with margins. */
      const fitsLabel = labelSpan - used >= 0.5 * (sorted.length + 1);
      const origin = fitsLabel ? 0 : Math.min(...sorted.map((index) => start(boxes.get(index))));
      const extent = fitsLabel ? labelSpan
        : Math.max(...sorted.map((index) => start(boxes.get(index)) + size(boxes.get(index)))) - origin;
      const count = sorted.length;
      const margin = fitsLabel ? (extent - used) / (count + 1) : 0;
      const between = fitsLabel ? margin : Math.max(0, (extent - used) / Math.max(1, count - 1));
      let cursor = origin + margin;
      sorted.forEach((index) => {
        const box = boxes.get(index);
        if (horizontal) place(index, cursor, box.y);
        else place(index, box.x, cursor);
        cursor += size(box) + between;
      });
      commit();
      renderInspector();
      draw();
      return;
    }

    indices.forEach((index) => {
      const box = boxes.get(index);
      if (mode === 'left') place(index, left, box.y);
      if (mode === 'right') place(index, right - box.w, box.y);
      if (mode === 'hcenter') place(index, centreX - box.w / 2, box.y);
      if (mode === 'top') place(index, box.x, top);
      if (mode === 'bottom') place(index, box.x, bottom - box.h);
      if (mode === 'vmiddle') place(index, box.x, centreY - box.h / 2);
    });
    commit();
    renderInspector();
    draw();
  };

  /* Guide lines taken from the label size: the centre and the quarters of the sticker. */
  const guideXs = () => [width / 2, width / 4, (3 * width) / 4];
  const guideYs = () => [height / 2, height / 4, (3 * height) / 4];
  const SNAP_GUIDE_PX = 7;
  /* Which guide is currently holding a dragged edge (drawn highlighted). */
  let activeGuides = { x: null, y: null };

  const guideTolerance = () => SNAP_GUIDE_PX / Math.max(1, view.scale);

  /** Nearest guide to *value* within the tolerance — ``null`` when nothing is close. */
  const nearestGuide = (value, guides, tolerance) => {
    let best = null;
    guides.forEach((guide) => {
      const delta = Math.abs(guide - value);
      if (delta <= tolerance && (!best || delta < best.delta)) best = { guide, delta };
    });
    return best;
  };

  /** Snap the whole box (left / centre / right and top / middle / bottom) onto the guides. */
  const snapBoxToGuides = (box, dx, dy, tolerance) => {
    // ``box`` is the dragged footprint: left/top plus right/bottom (no w/h properties).
    const boxW = box.right - box.x;
    const boxH = box.bottom - box.y;
    const candidatesX = [box.x + dx, box.x + dx + boxW / 2, box.right + dx];
    const candidatesY = [box.y + dy, box.y + dy + boxH / 2, box.bottom + dy];
    let pickX = null;
    candidatesX.forEach((anchor) => {
      const hit = nearestGuide(anchor, guideXs(), tolerance);
      if (hit && (!pickX || hit.delta < pickX.delta)) pickX = { ...hit, anchor };
    });
    let pickY = null;
    candidatesY.forEach((anchor) => {
      const hit = nearestGuide(anchor, guideYs(), tolerance);
      if (hit && (!pickY || hit.delta < pickY.delta)) pickY = { ...hit, anchor };
    });
    activeGuides = { x: pickX?.guide ?? null, y: pickY?.guide ?? null };
    return {
      dx: dx + (pickX ? pickX.guide - pickX.anchor : 0),
      dy: dy + (pickY ? pickY.guide - pickY.anchor : 0),
      hitX: !!pickX,
      hitY: !!pickY,
    };
  };

  /* ------------------------------------------------------------- drawing */
  const pathShapes = (context, shapes) => {
    context.beginPath();
    shapes.forEach((shape) => {
      const x = cssX(shape.x);
      const y = cssY(shape.y);
      const w = Math.max(1, shape.w * view.scale);
      const h = Math.max(1, shape.h * view.scale);
      if (shape.kind === 'circle') context.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
      else context.rect(x, y, w, h);
    });
  };

  const identity = (context) => context.setTransform(1, 0, 0, 1, 0, 0);
  const cssTransform = (context) => context.setTransform(dpr, 0, 0, dpr, 0, 0);

  /* Outline of the *merged* area: silhouette minus a version eroded by half the border,
     so two overlapping outlines read as one item instead of two shapes. */
  const zoneRing = (shapes, colour, thickness) => {
    identity(silCtx);
    silCtx.clearRect(0, 0, silCanvas.width, silCanvas.height);
    cssTransform(silCtx);
    silCtx.fillStyle = colour;
    pathShapes(silCtx, shapes);
    silCtx.fill();

    identity(eroCtx);
    eroCtx.clearRect(0, 0, eroCanvas.width, eroCanvas.height);
    eroCtx.drawImage(silCanvas, 0, 0);
    eroCtx.globalCompositeOperation = 'destination-in';
    const radius = Math.max(1, (thickness * view.scale * dpr) / 2);
    for (let step = 0; step < 16; step += 1) {
      const angle = (step / 16) * Math.PI * 2;
      eroCtx.drawImage(silCanvas, Math.cos(angle) * radius, Math.sin(angle) * radius);
    }
    eroCtx.globalCompositeOperation = 'source-over';

    identity(bandCtx);
    bandCtx.clearRect(0, 0, bandCanvas.width, bandCanvas.height);
    cssTransform(bandCtx);
    bandCtx.fillStyle = colour;
    pathShapes(bandCtx, shapes);
    bandCtx.fill();
    identity(bandCtx);
    bandCtx.globalCompositeOperation = 'destination-out';
    bandCtx.drawImage(eroCanvas, 0, 0);
    bandCtx.globalCompositeOperation = 'source-over';
  };

  const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
  const handlePoint = (box, spot) => ({
    x: cssX(spot.includes('w') ? box.x : spot.includes('e') ? box.x + box.w : box.x + box.w / 2),
    y: cssY(spot.includes('n') ? box.y : spot.includes('s') ? box.y + box.h : box.y + box.h / 2),
  });
  const handleMmPoint = (box, spot) => ({
    x: spot.includes('w') ? box.x : spot.includes('e') ? box.x + box.w : box.x + box.w / 2,
    y: spot.includes('n') ? box.y : spot.includes('s') ? box.y + box.h : box.y + box.h / 2,
  });

  const paintHandles = (box) => {
    ctx.save();
    HANDLES.forEach((spot) => {
      const point = handlePoint(box, spot);
      ctx.fillStyle = '#fff';
      ctx.strokeStyle = COLOUR.violet;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.rect(point.x - 4, point.y - 4, 8, 8);
      ctx.fill();
      ctx.stroke();
    });
    ctx.restore();
  };

  const draw = () => {
    cssTransform(ctx);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = '#eef3f8';
    ctx.fillRect(0, 0, cssW, cssH);

    // the label page
    ctx.save();
    ctx.shadowColor = 'rgba(0, 26, 77, 0.22)';
    ctx.shadowBlur = 12;
    ctx.shadowOffsetY = 3;
    ctx.fillStyle = '#fff';
    ctx.fillRect(cssX(0), cssY(0), width * view.scale, height * view.scale);
    ctx.restore();
    ctx.strokeStyle = 'rgba(0, 26, 77, 0.28)';
    ctx.lineWidth = 1;
    ctx.strokeRect(cssX(0) + 0.5, cssY(0) + 0.5, width * view.scale - 1, height * view.scale - 1);

    /* The grid matches the snap: 0.5 mm cells (1 mm when they would be too dense), 5 mm heavier. */
    const gridLines = (step, colour, weight) => {
      if (step <= 0) return;
      ctx.save();
      ctx.strokeStyle = colour;
      ctx.lineWidth = weight;
      ctx.beginPath();
      for (let i = 1; i * step < width - 1e-6; i += 1) {
        const x = i * step;
        ctx.moveTo(cssX(x), cssY(0));
        ctx.lineTo(cssX(x), cssY(height));
      }
      for (let i = 1; i * step < height - 1e-6; i += 1) {
        const y = i * step;
        ctx.moveTo(cssX(0), cssY(y));
        ctx.lineTo(cssX(width), cssY(y));
      }
      ctx.stroke();
      ctx.restore();
    };
    const minorStep = view.scale * 0.5 >= 6 ? 0.5 : view.scale >= 6 ? 1 : 0;
    gridLines(minorStep, 'rgba(0, 26, 77, 0.045)', 1);
    gridLines(5, 'rgba(0, 26, 77, 0.13)', 1);

    /* Centre and quarter guides — the lines the drag snaps to. */
    const drawGuide = (value, vertical, strong, active) => {
      ctx.save();
      ctx.setLineDash(active ? [] : strong ? [6, 4] : [3, 4]);
      ctx.lineWidth = active ? 1.6 : strong ? 1.2 : 1;
      ctx.strokeStyle = active ? '#e87a2a' : strong ? 'rgba(139, 92, 246, 0.55)' : 'rgba(139, 92, 246, 0.26)';
      ctx.beginPath();
      if (vertical) {
        ctx.moveTo(cssX(value), cssY(0));
        ctx.lineTo(cssX(value), cssY(height));
      } else {
        ctx.moveTo(cssX(0), cssY(value));
        ctx.lineTo(cssX(width), cssY(value));
      }
      ctx.stroke();
      ctx.restore();
    };
    const centreGuideX = width / 2;
    const centreGuideY = height / 2;
    guideXs().forEach((value) => {
      const isCentre = Math.abs(value - centreGuideX) < 1e-6;
      drawGuide(value, true, isCentre, activeGuides.x !== null && Math.abs(activeGuides.x - value) < 1e-6);
    });
    guideYs().forEach((value) => {
      const isCentre = Math.abs(value - centreGuideY) < 1e-6;
      drawGuide(value, false, isCentre, activeGuides.y !== null && Math.abs(activeGuides.y - value) < 1e-6);
    });

    // printhead limit
    const headLimit = printheadWidthMm();
    if (headLimit && width > headLimit + 0.05) {
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = COLOUR.warn;
      ctx.beginPath();
      ctx.moveTo(cssX(headLimit), cssY(0));
      ctx.lineTo(cssX(headLimit), cssY(height));
      ctx.stroke();
      ctx.restore();
    }

    zones.forEach((zone, index) => {
      const selected = selection.has(index);
      const colour = selected ? COLOUR.violet : COLOUR.teal;
      zoneRing(zone.shapes, colour, Math.max(0.15, Number(zone.thickness) || 0.3));
      identity(ctx);
      ctx.globalAlpha = selected ? 0.16 : 0.09;
      ctx.drawImage(silCanvas, 0, 0);
      ctx.globalAlpha = 1;
      ctx.drawImage(bandCanvas, 0, 0);
      cssTransform(ctx);

      const box = zoneBounds(zone);
      ctx.fillStyle = colour;
      ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
      const label = String(index + 1);
      const badgeX = cssX(box.x) + 3;
      const badgeY = cssY(box.y) + 12;
      if (view.scale * box.w > 14 && view.scale * box.h > 14) {
        ctx.fillText(label, badgeX + 1, badgeY + 1);
      }
    });

    if (marquee) {
      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = COLOUR.navy;
      ctx.fillStyle = 'rgba(0, 26, 77, 0.06)';
      const x = cssX(Math.min(marquee.x0, marquee.x1));
      const y = cssY(Math.min(marquee.y0, marquee.y1));
      const w = Math.abs(marquee.x1 - marquee.x0) * view.scale;
      const h = Math.abs(marquee.y1 - marquee.y0) * view.scale;
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      ctx.restore();
    }

    if (draft) {
      const shape = { kind: draft.kind, x: Math.min(draft.x0, draft.x1), y: Math.min(draft.y0, draft.y1),
        w: Math.abs(draft.x1 - draft.x0), h: Math.abs(draft.y1 - draft.y0) };
      ctx.save();
      ctx.setLineDash([5, 3]);
      ctx.strokeStyle = COLOUR.navy;
      ctx.fillStyle = 'rgba(0, 26, 77, 0.08)';
      slotPath(ctx, shape);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }

    // handles for the single selected zone, or for the outline being shaped
    const indices = selectedIndices();
    if (indices.length === 1) {
      const zone = zones[indices[0]];
      if (activeShape !== null && zone.shapes[activeShape]) paintHandles(zone.shapes[activeShape]);
      else paintHandles(zoneBounds(zone));
      if (activeShape !== null && zone.shapes[activeShape]) {
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = COLOUR.violet;
        const box = zone.shapes[activeShape];
        ctx.strokeRect(cssX(box.x) + 0.5, cssY(box.y) + 0.5, box.w * view.scale, box.h * view.scale);
        ctx.restore();
      }
    }

    renderChips();
    renderSummary();
  };

  /* One shape as a canvas path (used for the draw preview). */
  function slotPath(context, shape) {
    const x = cssX(shape.x);
    const y = cssY(shape.y);
    const w = Math.max(1, shape.w * view.scale);
    const h = Math.max(1, shape.h * view.scale);
    context.beginPath();
    if (shape.kind === 'circle') context.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
    else context.rect(x, y, w, h);
  }

  const renderSummary = () => {
    const outlines = zones.reduce((sum, zone) => sum + zone.shapes.length, 0);
    summary.textContent = `sticker ${stickerLabel()}`
      + ` · page ${round2(width)} × ${round2(height)} mm`
      + ` · ${zones.length} item${zones.length === 1 ? '' : 's'}`
      + ` · ${outlines} outline${outlines === 1 ? '' : 's'}`;
    renderCalStatus();
  };

  const renderChips = () => {
    const selCount = $('#cal-selcount', body);
    if (selCount) selCount.textContent = selection.size > 1 ? `${selection.size} selected` : '';
    if (!chipsBox) return;
    chipsBox.innerHTML = zones.length
      ? zones.map((zone, index) => {
        const box = zoneBounds(zone);
        const kind = zone.shapes.length > 1 ? `${zone.shapes.length} outlines`
          : zone.shapes[0].kind === 'circle' ? 'circle' : 'rect';
        return `<button type="button" class="cal-chip${selection.has(index) ? ' on' : ''}" data-chip="${index}">
          <b>${index + 1}</b><span>${kind} ${round2(box.w)}×${round2(box.h)}</span>
          <i class="cal-chip-x" data-chip-del="${index}" title="Delete this zone">✕</i></button>`;
      }).join('')
      : '<span class="hint">No zones yet — press Grid of zones, or drag on the label.</span>';
  };

  /* ----------------------------------------------------------- inspector */
  const renderInspector = () => {
    const indices = selectedIndices();
    if (!indices.length) {
      editor.innerHTML = '<div class="hint">Nothing selected — click an item zone, or draw a new one.</div>';
      return;
    }
    if (indices.length > 1) {
      editor.innerHTML = `<div class="cal-editor-head">${indices.length} item zones selected</div>
        <div class="hint">Drag any of them to move the group, or align them with the buttons below.</div>`;
      return;
    }
    const index = indices[0];
    const zone = zones[index];
    const box = zoneBounds(zone);
    const shapes = zone.shapes;
    editor.innerHTML = `
      <div class="cal-editor-head">Item ${index + 1} of ${zones.length}</div>
      <div class="field">
        <label for="cal-zname">Zone name</label>
        <input id="cal-zname" data-zone="name" value="${escapeHtml(zone.label || '')}" placeholder="Item ${index + 1}">
      </div>
      <div class="grid-4">
        ${field('X (mm)', numberInput('cal-x', round2(box.x), 0.5, 0, 400))}
        ${field('Y (mm)', numberInput('cal-y', round2(box.y), 0.5, 0, 400))}
        ${field('W (mm)', numberInput('cal-w', round2(box.w), 0.5, 0.5, 400))}
        ${field('H (mm)', numberInput('cal-h', round2(box.h), 0.5, 0.5, 400))}
      </div>
      <div class="cal-outlines">
        ${shapes.map((shape, order) => `
          <button type="button" class="cal-outline${activeShape === order ? ' on' : ''}" data-outline="${order}"
            title="Edit outline ${order + 1} on its own">
            <em>${shape.kind === 'circle' ? '◯' : '▭'}</em><b>${order + 1}</b>
            <span>${round2(shape.w)}×${round2(shape.h)}</span>
            ${shapes.length > 1 ? `<i class="cal-outline-x" data-outline-del="${order}" title="Remove this outline">✕</i>` : ''}
          </button>`).join('')}
      </div>
      <div class="cal-shapes" role="group" aria-label="Add an outline to this item">
        <button type="button" class="cal-shape" data-add="rect">+ Rectangle</button>
        <button type="button" class="cal-shape" data-add="circle">+ Circle</button>
      </div>
      <div class="grid-2">
        ${field('Border (mm)', numberInput('cal-thickness', round2(zone.thickness ?? 0.3), 0.05, 0.1, 1.5))}
        <div class="hint" style="align-self:end">Border weight of the guide and the check print.</div>
      </div>`;
    refreshIcons(editor);
  };

  /* ---------------------------------------------------------- interaction */
  let dragState = null;
  let draft = null;
  let marquee = null;
  let menu = null;

  const cssPoint = (event) => {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const mmPoint = (event) => {
    const point = cssPoint(event);
    return { x: mmX(point.x), y: mmY(point.y) };
  };
  const handleAt = (point, box) => HANDLES.find((spot) => {
    const target = handlePoint(box, spot);
    return Math.abs(target.x - point.x) <= 7 && Math.abs(target.y - point.y) <= 7;
  });

  const closeMenu = () => {
    if (menu) menu.remove();
    menu = null;
  };

  const openZoneMenu = (index, event) => {
    closeMenu();
    const zone = zones[index];
    if (!zone) return;
    menu = document.createElement('div');
    menu.className = 'cal-menu';
    menu.innerHTML = `
      <button type="button" data-menu="rect"><i data-lucide="square"></i> Add rectangle to this item</button>
      <button type="button" data-menu="circle"><i data-lucide="circle"></i> Add circle to this item</button>
      <div class="cal-menu-sep"></div>
      <button type="button" data-menu="shapes"><i data-lucide="spline"></i> Edit the outlines (${zone.shapes.length})</button>
      <button type="button" data-menu="duplicate"><i data-lucide="copy"></i> Duplicate this item</button>
      <button type="button" data-menu="mirror"><i data-lucide="copy-check"></i> Copy onto every other item</button>
      <div class="cal-menu-sep"></div>
      <button type="button" data-menu="delete" class="danger"><i data-lucide="trash-2"></i> Delete this item</button>`;
    document.body.appendChild(menu);
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(window.innerWidth - rect.width - 8, event.clientX)}px`;
    menu.style.top = `${Math.min(window.innerHeight - rect.height - 8, event.clientY)}px`;
    refreshIcons(menu);
    menu.addEventListener('click', (menuEvent) => {
      const action = menuEvent.target.closest('[data-menu]')?.dataset.menu;
      if (!action) return;
      closeMenu();
      if (action === 'rect' || action === 'circle') addShapeToZone(index, action);
      if (action === 'shapes') {
        selection = new Set([index]);
        activeShape = zone.shapes.length > 1 ? 1 : 0;
        renderInspector();
        draw();
      }
      if (action === 'duplicate') duplicateZone(index);
      if (action === 'mirror') mirrorZone(index);
      if (action === 'delete') {
        selection = new Set([index]);
        deleteSelection();
      }
    });
  };

  const closeMenuOutside = (event) => {
    if (!document.body.contains(canvas)) {
      document.removeEventListener('pointerdown', closeMenuOutside, true);
      return;
    }
    if (menu && !menu.contains(event.target)) closeMenu();
  };
  document.addEventListener('pointerdown', closeMenuOutside, true);
  const onKeydown = (event) => {
    if (!document.body.contains(canvas)) {
      document.removeEventListener('keydown', onKeydown, true);
      return;
    }
    if (event.key === 'Escape') {
      if (menu) {
        closeMenu();
        event.stopPropagation();
        return;
      }
      if (activeShape !== null) {
        activeShape = null;
        renderInspector();
        draw();
        event.stopPropagation();
      }
      return;
    }
    const tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      event.stopPropagation();
      stepHistory(event.shiftKey ? 1 : -1);
      return;
    }
    if (mod && event.key.toLowerCase() === 'y') {
      event.preventDefault();
      event.stopPropagation();
      stepHistory(1);
      return;
    }
    if (event.key === 'Delete') {
      if (!selectedIndices().length) return;
      event.preventDefault();
      event.stopPropagation();
      deleteSelection();
      return;
    }
    const step = event.shiftKey ? 1 : 0.5;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const move = moves[event.key];
    if (!move) return;
    const indices = selectedIndices();
    if (!indices.length) return;
    event.preventDefault();
    event.stopPropagation();
    indices.forEach((index) => translateZone(zones[index], move[0], move[1]));
    commit();
    renderInspector();
  };
  document.addEventListener('keydown', onKeydown, true);

  canvas.addEventListener('contextmenu', (event) => {
    const point = mmPoint(event);
    const index = hitZone(point.x, point.y);
    if (index < 0) return;
    event.preventDefault();
    selection = new Set([index]);
    activeShape = null;
    renderInspector();
    draw();
    openZoneMenu(index, event);
  });

  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    const point = cssPoint(event);
    const beforeX = mmX(point.x);
    const beforeY = mmY(point.y);
    zoom = Math.min(8, Math.max(0.5, zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
    applyView();
    view.ox = point.x - beforeX * view.scale;
    view.oy = point.y - beforeY * view.scale;
    draw();
  }, { passive: false });

  canvas.addEventListener('pointerdown', (event) => {
    closeMenu();
    const point = cssPoint(event);
    const spot = mmPoint(event);
    if (event.button === 1) {
      dragState = { mode: 'pan', startX: point.x, startY: point.y, ox: view.ox, oy: view.oy };
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;

    if (tool !== 'select') {
      draft = {
        kind: tool === 'circle' ? 'circle' : 'rect',
        square: tool === 'square',
        x0: snapMm(spot.x, event.altKey),
        y0: snapMm(spot.y, event.altKey),
        x1: snapMm(spot.x, event.altKey),
        y1: snapMm(spot.y, event.altKey),
      };
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }

    const indices = selectedIndices();
    if (indices.length === 1) {
      const zone = zones[indices[0]];
      const box = activeShape !== null && zone.shapes[activeShape] ? zone.shapes[activeShape] : zoneBounds(zone);
      const hit = handleAt(point, box);
      if (hit) {
        const grabbed = handleMmPoint(box, hit);
        dragState = {
          mode: 'resize',
          spot: hit,
          index: indices[0],
          shape: activeShape,
          origin: { ...box },
          // Cursor offset from the handle: the edge follows the pointer instead of jumping to it.
          grab: { x: spot.x - grabbed.x, y: spot.y - grabbed.y },
          // Snapshot, so scaling always works from the original outlines instead of compounding.
          shapes: zone.shapes.map((item) => ({ ...item })),
        };
        canvas.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
    }

    const index = hitZone(spot.x, spot.y);
    if (index < 0) {
      marquee = { x0: spot.x, y0: spot.y, x1: spot.x, y1: spot.y };
      if (!event.shiftKey) {
        selection = new Set();
        activeShape = null;
        renderInspector();
        draw();
      }
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }

    if (event.shiftKey) {
      if (selection.has(index)) selection.delete(index);
      else selection.add(index);
      activeShape = null;
      renderInspector();
      draw();
      return;
    }

    if (!selection.has(index)) {
      selection = new Set([index]);
      activeShape = null;
      renderInspector();
    }

    // Alt+click picks the individual outline of a multi-outline item.
    if (event.altKey && zones[index].shapes.length > 1) {
      const order = hitShape(zones[index], spot.x, spot.y);
      activeShape = order >= 0 ? order : 0;
    }

    const origins = selectedIndices().map((target) => ({
      index: target,
      shapes: zones[target].shapes.map((shape) => ({ ...shape })),
    }));
    // Bounds of everything that is being dragged, so the group can be kept on the label.
    const moving = origins.flatMap((entry) => entry.shapes);
    const bounds = {
      x: Math.min(...moving.map((shape) => shape.x)),
      y: Math.min(...moving.map((shape) => shape.y)),
      right: Math.max(...moving.map((shape) => shape.x + shape.w)),
      bottom: Math.max(...moving.map((shape) => shape.y + shape.h)),
    };
    dragState = {
      mode: 'move',
      index,
      shape: activeShape,
      startX: spot.x,
      startY: spot.y,
      bounds,
      origins,
    };
    canvas.setPointerCapture(event.pointerId);
    draw();
    event.preventDefault();
  });

  canvas.addEventListener('pointermove', (event) => {
    const spot = mmPoint(event);
    if (!dragState && !draft && !marquee) {
      // cursor feedback
      const indices = selectedIndices();
      if (indices.length === 1) {
        const zone = zones[indices[0]];
        const box = activeShape !== null && zone.shapes[activeShape] ? zone.shapes[activeShape] : zoneBounds(zone);
        const overHandle = handleAt(cssPoint(event), box);
        canvas.style.cursor = overHandle ? `${overHandle}-resize` : (hitZone(spot.x, spot.y) >= 0 ? 'move' : 'default');
      } else {
        canvas.style.cursor = hitZone(spot.x, spot.y) >= 0 ? 'move' : 'default';
      }
      return;
    }

    if (dragState?.mode === 'pan') {
      const point = cssPoint(event);
      view.ox = dragState.ox + (point.x - dragState.startX);
      view.oy = dragState.oy + (point.y - dragState.startY);
      draw();
      return;
    }

    if (draft) {
      draft.x1 = snapMm(spot.x, event.altKey);
      draft.y1 = snapMm(spot.y, event.altKey);
      if (draft.square) {
        const size = Math.max(Math.abs(draft.x1 - draft.x0), Math.abs(draft.y1 - draft.y0));
        draft.x1 = draft.x0 + Math.sign(draft.x1 - draft.x0 || 1) * size;
        draft.y1 = draft.y0 + Math.sign(draft.y1 - draft.y0 || 1) * size;
      }
      draw();
      return;
    }

    if (marquee) {
      marquee.x1 = spot.x;
      marquee.y1 = spot.y;
      draw();
      return;
    }

    if (dragState?.mode === 'move') {
      const free = event.altKey;
      const bounds = dragState.bounds;
      // Keep the dragged outlines on the label, snap to the centre/quarter guides first,
      // then fall back to the 0.5 mm grid.
      let rawX = Math.min(width - bounds.right, Math.max(-bounds.x, spot.x - dragState.startX));
      let rawY = Math.min(height - bounds.bottom, Math.max(-bounds.y, spot.y - dragState.startY));
      const guided = free ? { hitX: false, hitY: false } : snapBoxToGuides(bounds, rawX, rawY, guideTolerance());
      if (!free) {
        rawX = Math.min(width - bounds.right, Math.max(-bounds.x, guided.dx));
        rawY = Math.min(height - bounds.bottom, Math.max(-bounds.y, guided.dy));
      } else {
        activeGuides = { x: null, y: null };
      }
      const snapAxis = (value, axis) => (
        (axis === 'x' ? guided.hitX : guided.hitY) ? value : snapMm(value, free)
      );
      if (dragState.shape !== null) {
        // move the single outline inside its item
        const current = zones[dragState.index].shapes[dragState.shape];
        const origin = dragState.origins.find((item) => item.index === dragState.index)?.shapes[dragState.shape];
        if (current && origin) {
          current.x = snapAxis(origin.x + rawX, 'x');
          current.y = snapAxis(origin.y + rawY, 'y');
        }
      } else {
        dragState.origins.forEach((entry) => {
          const zone = zones[entry.index];
          entry.shapes.forEach((shape, order) => {
            zone.shapes[order].x = snapAxis(shape.x + rawX, 'x');
            zone.shapes[order].y = snapAxis(shape.y + rawY, 'y');
          });
        });
      }
      draw();
      return;
    }

    if (dragState?.mode === 'resize') {
      const zone = zones[dragState.index];
      if (!zone) return;
      const origin = dragState.origin;
      const free = event.altKey;
      const cursor = { x: spot.x - dragState.grab.x, y: spot.y - dragState.grab.y };
      let left = origin.x;
      let right = origin.x + origin.w;
      let top = origin.y;
      let bottom = origin.y + origin.h;
      const tolerance = guideTolerance();
      const guideX = free ? null : nearestGuide(cursor.x, guideXs(), tolerance);
      const guideY = free ? null : nearestGuide(cursor.y, guideYs(), tolerance);
      activeGuides = { x: guideX?.guide ?? null, y: guideY?.guide ?? null };
      const edgeX = (value) => (guideX ? guideX.guide : snapMm(value, free));
      const edgeY = (value) => (guideY ? guideY.guide : snapMm(value, free));
      if (dragState.spot.includes('w')) left = Math.max(0, edgeX(cursor.x));
      if (dragState.spot.includes('e')) right = Math.min(width, edgeX(cursor.x));
      if (dragState.spot.includes('n')) top = Math.max(0, edgeY(cursor.y));
      if (dragState.spot.includes('s')) bottom = Math.min(height, edgeY(cursor.y));
      left = Math.min(left, right - 1);
      right = Math.max(right, left + 1);
      top = Math.min(top, bottom - 1);
      bottom = Math.max(bottom, top + 1);
      const next = { x: left, y: top, w: right - left, h: bottom - top };
      if (dragState.shape !== null) {
        const shape = zone.shapes[dragState.shape];
        if (shape) Object.assign(shape, next);
      } else {
        // Scale the outlines from the snapshot taken at pointerdown.
        const sx = origin.w > 0.01 ? next.w / origin.w : 1;
        const sy = origin.h > 0.01 ? next.h / origin.h : 1;
        dragState.shapes.forEach((shape, order) => {
          const target = zone.shapes[order];
          if (!target) return;
          target.x = next.x + (shape.x - origin.x) * sx;
          target.y = next.y + (shape.y - origin.y) * sy;
          target.w = Math.max(0.4, shape.w * sx);
          target.h = Math.max(0.4, shape.h * sy);
        });
      }
      draw();
    }
  });

  const finishPointer = (event) => {
    activeGuides = { x: null, y: null };
    if (dragState?.mode === 'pan') {
      dragState = null;
      draw();
      return;
    }
    if (draft) {
      const shape = {
        kind: draft.kind,
        x: round2(Math.min(draft.x0, draft.x1)),
        y: round2(Math.min(draft.y0, draft.y1)),
        w: round2(Math.abs(draft.x1 - draft.x0)),
        h: round2(Math.abs(draft.y1 - draft.y0)),
      };
      const fresh = shape.w < 1.5 || shape.h < 1.5;
      if (fresh) {
        // A plain click with a draw tool puts a reasonable zone in the middle.
        const side = Math.min(width, height) * 0.4;
        shape.w = round2(draft.kind === 'circle' ? side : Math.min(shape.w || width * 0.6, width));
        shape.h = round2(draft.kind === 'circle' ? side : Math.min(shape.h || height * 0.5, height));
        shape.x = round2(Math.max(0, Math.min(width - shape.w, (shape.x || width / 2) - shape.w / 2)));
        shape.y = round2(Math.max(0, Math.min(height - shape.h, (shape.y || height / 2) - shape.h / 2)));
      }
      zones.push({ label: `Item ${zones.length + 1}`, thickness: 0.3, shapes: [shape] });
      autoZone = false;
      selection = new Set([zones.length - 1]);
      activeShape = null;
      draft = null;
      renderInspector();
      commit();
      setTool('select');
      return;
    }
    if (marquee) {
      const left = Math.min(marquee.x0, marquee.x1);
      const right = Math.max(marquee.x0, marquee.x1);
      const top = Math.min(marquee.y0, marquee.y1);
      const bottom = Math.max(marquee.y0, marquee.y1);
      const picked = event?.shiftKey ? new Set(selection) : new Set();
      zones.forEach((zone, index) => {
        const box = zoneBounds(zone);
        if (box.x < right && box.x + box.w > left && box.y < bottom && box.y + box.h > top) picked.add(index);
      });
      selection = picked;
      marquee = null;
      renderInspector();
      draw();
      return;
    }
    if (dragState) {
      dragState = null;
      renderInspector();
      commit();
    }
  };
  canvas.addEventListener('pointerup', finishPointer);
  canvas.addEventListener('pointercancel', finishPointer);
  canvas.addEventListener('dblclick', (event) => {
    const spot = mmPoint(event);
    const index = hitZone(spot.x, spot.y);
    if (index < 0) return;
    const zone = zones[index];
    selection = new Set([index]);
    if (zone.shapes.length > 1) {
      const order = hitShape(zone, spot.x, spot.y);
      activeShape = order >= 0 ? order : (activeShape === null ? 0 : null);
    } else {
      activeShape = activeShape === null ? 0 : null;
    }
    renderInspector();
    draw();
  });

  /* ---------------------------------------------------------------- view */
  const fitScale = () => Math.min(
    (cssW - 34) / Math.max(1, width),
    (cssH - 34) / Math.max(1, height),
  );

  function applyView() {
    const base = fitScale();
    view.scale = Math.max(0.6, base * zoom);
    if (zoomLabel) zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
  }

  const centreView = () => {
    applyView();
    view.ox = (cssW - width * view.scale) / 2;
    view.oy = (cssH - height * view.scale) / 2;
    draw();
  };

  const resizeCanvas = () => {
    const rect = wrap.getBoundingClientRect();
    dpr = Math.min(3, window.devicePixelRatio || 1);
    cssW = Math.max(260, Math.round(rect.width));
    cssH = Math.max(240, Math.round(rect.height));
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    [canvas, silCanvas, eroCanvas, bandCanvas].forEach((item) => {
      item.width = Math.round(cssW * dpr);
      item.height = Math.round(cssH * dpr);
    });
    centreView();
  };

  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(() => {
      if (!document.body.contains(canvas)) {
        observer.disconnect();
        return;
      }
      const rect = wrap.getBoundingClientRect();
      if (Math.abs(rect.width - cssW) > 2 || Math.abs(rect.height - cssH) > 2) resizeCanvas();
    });
    observer.observe(wrap);
  }

  /* -------------------------------------------------------------- events */
  $$('[data-tool]', body).forEach((button) => {
    button.onclick = () => setTool(button.dataset.tool);
  });
  $$('[data-zoom]', body).forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.zoom;
      if (action === 'fit') {
        zoom = 1;
        centreView();
        return;
      }
      const point = { x: cssW / 2, y: cssH / 2 };
      const beforeX = mmX(point.x);
      const beforeY = mmY(point.y);
      zoom = Math.min(8, Math.max(0.5, zoom * (action === 'in' ? 1.25 : 1 / 1.25)));
      applyView();
      view.ox = point.x - beforeX * view.scale;
      view.oy = point.y - beforeY * view.scale;
      draw();
    };
  });
  $$('[data-cal]', body).forEach((button) => {
    button.onclick = async () => {
      const action = button.dataset.cal;
      if (action === 'grid') applyGrid();
      else if (action === 'add') addZone();
      else if (action === 'single') singleZone();
      else if (action === 'delete') deleteSelection();
      else if (action === 'undo') stepHistory(-1);
      else if (action === 'redo') stepHistory(1);
      else if (action === 'outline-print') await printOutline();
    };
  });
  $$('[data-align]', body).forEach((button) => {
    button.onclick = () => alignSelection(button.dataset.align);
  });

  editor.addEventListener('click', (event) => {
    const add = event.target.closest('[data-add]');
    if (add) {
      addShapeToZone(selectedIndices()[0], add.dataset.add);
      return;
    }
    const remove = event.target.closest('[data-outline-del]');
    if (remove) {
      removeShape(selectedIndices()[0], Number(remove.dataset.outlineDel));
      return;
    }
    const outline = event.target.closest('[data-outline]');
    if (outline) {
      const order = Number(outline.dataset.outline);
      activeShape = activeShape === order ? null : order;
      renderInspector();
      draw();
    }
  });

  editor.addEventListener('change', (event) => {
    const key = event.target.dataset?.key;
    const name = event.target.dataset?.zone;
    const index = selectedIndices()[0];
    const zone = zones[index];
    if (name === 'name' && zone) {
      zone.label = event.target.value.trim();
      renderChips();
      return;
    }
    if (!key || !zone) return;
    const box = zoneBounds(zone);
    if (key === 'cal-w' || key === 'cal-h') {
      const value = Math.max(0.5, Number(event.target.value) || 0.5);
      const next = { ...box };
      if (key === 'cal-w') next.w = value;
      else next.h = value;
      scaleZone(zone, box, next);
      commit();
      renderInspector();
    }
    if (key === 'cal-thickness') {
      zone.thickness = Math.max(0.1, Math.min(1.5, Number(event.target.value) || 0.3));
      draw();
    }
  });

  editor.addEventListener('input', (event) => {
    const key = event.target.dataset?.key;
    if (key !== 'cal-x' && key !== 'cal-y') return;
    const zone = zones[selectedIndices()[0]];
    if (!zone) return;
    const box = zoneBounds(zone);
    if (key === 'cal-x') translateZone(zone, Math.max(-box.x, Number(event.target.value) - box.x), 0);
    if (key === 'cal-y') translateZone(zone, 0, Math.max(-box.y, Number(event.target.value) - box.y));
    commit();
  });

  if (chipsBox) {
    chipsBox.addEventListener('click', (event) => {
      const remove = event.target.closest('[data-chip-del]');
      if (remove) {
        selection = new Set([Number(remove.dataset.chipDel)]);
        deleteSelection();
        return;
      }
      const chip = event.target.closest('[data-chip]');
      if (!chip) return;
      const index = Number(chip.dataset.chip);
      if (event.shiftKey) {
        if (selection.has(index)) selection.delete(index);
        else selection.add(index);
      } else {
        selection = new Set([index]);
      }
      activeShape = null;
      renderInspector();
      draw();
    });
  }

  body.addEventListener('input', (event) => {
    const key = event.target.dataset?.key;
    if (!key || !key.startsWith('cal-')) return;
    if (['cal-rows', 'cal-cols', 'cal-rowspace', 'cal-colspace'].includes(key)) {
      applyGrid();
      return;
    }
    if (key === 'cal-width' || key === 'cal-height') {
      readSize();
      if (autoZone) {
        zones = [defaultZone()];
        selection = new Set([0]);
        activeShape = null;
        renderInspector();
      }
      const placeholder = $('#cal-name', body);
      if (placeholder && !placeholder.value.trim()) placeholder.placeholder = stickerLabel();
      centreView();
    }
  });

  const presetSelect = $('#cal-preset', body);
  if (presetSelect) {
    presetSelect.onchange = () => {
      const preset = presets[Number(presetSelect.value)];
      if (!preset) return;
      $('[data-key="cal-width"]', body).value = preset.width;
      $('[data-key="cal-height"]', body).value = preset.height;
      $('[data-key="cal-rows"]', body).value = preset.rows || 1;
      readSize();
      if (preset.rows > 1) {
        applyGrid();
      } else {
        singleZone();
      }
    };
  }

  /* ------------------------------------------------------- print outline */
  const outlinePayload = () => {
    readSize();
    return {
      width_mm: width,
      height_mm: height,
      label_border: !!$('[data-zone="border"]', body)?.checked,
      stroke_mm: 0.3,
      regions: zones.map((zone, index) => {
        const box = zoneBounds(zone);
        const shapes = zone.shapes.map((shape) => ({
          kind: shape.kind,
          x: round2(shape.x),
          y: round2(shape.y),
          w: round2(shape.w),
          h: round2(shape.h),
        }));
        return {
          shape: shapes.length === 1 ? shapes[0].kind : 'mixed',
          x: round2(box.x),
          y: round2(box.y),
          w: round2(box.w),
          h: round2(box.h),
          label: zone.label || `Item ${index + 1}`,
          angle: 0,
          thickness_mm: Number(zone.thickness) || 0.3,
          shapes,
        };
      }),
    };
  };

  async function printOutline() {
    if (!state.connected) {
      toast('Connect the printer first', 'error');
      return;
    }
    const button = $('[data-cal="outline-print"]', body);
    if (button) button.disabled = true;
    try {
      await api('/api/roll-format/print', 'POST', outlinePayload());
      renderJobStatus({ state: 'running', progress: 0, message: 'Zone outline accepted…' });
      pollJob();
      toast('Printing the zone borders — check where they land on the label', 'ok');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  titleActions.addEventListener('click', (event) => {
    if (event.target.closest('[data-title-action="print"]')) printOutline();
  });

  /* -------------------------------------------------- saved calibrations */
  /* Loading a stored calibration replaces what is drawn here, so it can be reused for this roll
     without editing anything — or used as the starting point for small changes. */
  const applyProfileToDialog = (saved) => {
    const sticker = calibrationSticker(saved);
    stickerW = Math.max(2, sticker.w);
    stickerH = Math.max(2, sticker.h);
    rows = Math.max(1, sticker.rows);
    cols = Math.max(1, sticker.cols);
    rowSpace = Math.max(0, Number(saved.row_space_mm) || 0);
    colSpace = Math.max(0, Number(saved.col_space_mm) || 0);
    pageSize();
    const values = {
      'cal-width': round2(stickerW),
      'cal-height': round2(stickerH),
      'cal-rows': rows,
      'cal-cols': cols,
      'cal-rowspace': round2(rowSpace),
      'cal-colspace': round2(colSpace),
      'cal-gap': round2(Number(saved.gap_mm) || 0),
      'cal-name': calibrationName(saved),
    };
    Object.entries(values).forEach(([key, value]) => {
      const input = $(`[data-key="${key}"]`, body);
      if (input) input.value = value;
    });
    const loaded = (saved.regions || []).map(zoneFromRegion).filter((zone) => zone.shapes.length);
    if (loaded.length) zones = loaded;
    autoZone = false;
    selection = new Set(zones.length ? [0] : []);
    activeShape = null;
    history = [JSON.stringify(zones)];
    historyIndex = 0;
    renderPageNote();
    renderInspector();
    centreView();
    renderCalStatus();
    toast(`Loaded „${calibrationName(saved)}” — ${zones.length} item${zones.length === 1 ? '' : 's'} · Save for this roll to keep it`, 'ok');
  };

  api('/api/profiles').then((payload) => {
    const select = $('#cal-library', body);
    if (!select) return;
    const list = (payload.profiles || []).filter((item) => Array.isArray(item.regions) && item.regions.length);
    if (!list.length) {
      select.disabled = true;
      select.options[0].textContent = '— no saved calibrations yet —';
      return;
    }
    list.forEach((item, index) => {
      const sticker = calibrationSticker(item);
      const option = document.createElement('option');
      option.value = String(index);
      const name = calibrationName(item);
      const size = `${sticker.w} × ${sticker.h} mm`;
      option.textContent = `${name && name !== size ? `${name} · ` : ''}${size} · `
        + `${item.regions.length} zone${item.regions.length === 1 ? '' : 's'}`;
      select.appendChild(option);
    });
    select.onchange = () => {
      const item = list[Number(select.value)];
      select.value = '';
      if (item) applyProfileToDialog(item);
    };
  }).catch(() => { /* the list simply stays empty */ });

  /* ---------------------------------------------------------------- save */
  $('[data-cal-cancel]', footer).onclick = () => closeModal('roll-size');
  $('[data-cal-save]', footer).onclick = async () => {
    const targetBarcode = currentBarcode();
    if (!targetBarcode) {
      toast('Connect the printer (or insert the roll) so the calibration can be stored for it', 'error');
      return;
    }
    readSize();
    const typedName = ($('[data-key="cal-name"]', body)?.value || '').trim();
    /* No name typed: the roll is described by its sticker size, not by the RFID code. */
    const savedName = typedName || stickerLabel();
    const payloadRegions = outlinePayload().regions;
    const layoutRows = Math.max(1, new Set(zones.map((zone) => Math.round(zoneBounds(zone).y))).size);
    const layoutCols = Math.max(1, new Set(zones.map((zone) => Math.round(zoneBounds(zone).x))).size);
    closeModal('roll-size');
    try {
      const payload = await api('/api/roll-format', 'POST', {
        barcode: targetBarcode,
        name: savedName,
        width_mm: width,
        height_mm: height,
        rows: layoutRows,
        cols: layoutCols,
        gap_mm: gap,
        label_type: state.printer?.paper?.label_type ?? paper.label_type ?? state.spec.label_type,
        capacity: state.printer?.paper?.capacity ?? paper.capacity ?? null,
        regions: payloadRegions,
        sticker_w_mm: stickerW,
        sticker_h_mm: stickerH,
        row_space_mm: rowSpace,
        col_space_mm: colSpace,
      });
      applyState(payload);
      applySize(width, height, { gap_mm: gap, label_type: paper.label_type ?? state.spec.label_type });
      try {
        const catalog = await api('/api/catalog');
        state.catalog = catalog.catalog || [];
      } catch (_) { /* the list refreshes with the settings tab */ }
      toast(`${savedName} — sticker ${stickerLabel()}, print page ${round2(width)} × ${round2(height)} mm,`
        + ` ${zones.length} item${zones.length === 1 ? '' : 's'} remembered`, 'ok');
      renderPaperRegions();
      schedulePreview();
      if (isModalOpen('settings')) renderSettingsTab();
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  renderInspector();
  renderPageNote();
  setTool('select');
  /* Measure right away — requestAnimationFrame does not run in a background tab, and the canvas
     must never be left blank. The frame callback then catches late layout changes. */
  resizeCanvas();
  requestAnimationFrame(() => {
    resizeCanvas();
    refreshIcons(body);
  });
}

/* ==================================================== calibration library */
/** Small preview of a stored calibration: the page with its item zones drawn on it. */
function calibrationThumb(profile, maxWidth = 168) {
  const width = Number(profile?.width_mm) || 1;
  const height = Number(profile?.height_mm) || 1;
  const scale = Math.min(maxWidth / width, 96 / height);
  const regions = (profile?.regions || []).filter((region) => Number(region.w) > 0.2 && Number(region.h) > 0.2);
  const parts = [];
  regions.forEach((region) => {
    const outlines = Array.isArray(region.shapes) && region.shapes.length
      ? region.shapes
      : [{
        kind: region.shape === 'circle' ? 'circle' : 'rect',
        x: region.x, y: region.y, w: region.w, h: region.h,
      }];
    outlines.forEach((shape) => {
      const style = 'fill="rgba(0,194,194,0.16)" stroke="#0a8f92" stroke-width="1"';
      if (shape.kind === 'circle') {
        parts.push(`<ellipse cx="${((shape.x + shape.w / 2) * scale).toFixed(2)}"
          cy="${((shape.y + shape.h / 2) * scale).toFixed(2)}"
          rx="${((shape.w / 2) * scale).toFixed(2)}" ry="${((shape.h / 2) * scale).toFixed(2)}" ${style}/>`);
      } else {
        parts.push(`<rect x="${(shape.x * scale).toFixed(2)}" y="${(shape.y * scale).toFixed(2)}"
          width="${(shape.w * scale).toFixed(2)}" height="${(shape.h * scale).toFixed(2)}" ${style}/>`);
      }
    });
  });
  const w = (width * scale).toFixed(2);
  const h = (height * scale).toFixed(2);
  return `<svg class="calib-thumb" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="${w}" height="${h}" fill="#ffffff" stroke="#b9c7d6" stroke-width="1"/>
    ${parts.join('')}
  </svg>`;
}

/** Sticker size of a stored calibration (older records only have the page size). */
function calibrationSticker(profile) {
  const cols = Math.max(1, Number(profile?.cols) || 1);
  const rows = Math.max(1, Number(profile?.rows) || 1);
  const w = Number(profile?.sticker_w_mm) || (Number(profile?.width_mm) || 0) / cols;
  const h = Number(profile?.sticker_h_mm) || (Number(profile?.height_mm) || 0) / rows;
  return { w: round2(w), h: round2(h), cols, rows };
}

function calibrationName(profile) {
  const sticker = calibrationSticker(profile);
  const name = String(profile?.name || '').trim();
  const code = String(profile?.key || '').replace(/^bc:/, '');
  if (!name || name === code || /^\d+$/.test(name)) return `${sticker.w} × ${sticker.h} mm`;
  return name;
}

/**
 * Every calibration in the database, with a preview of its zones. Opens on its own when a roll
 * with no stored calibration is inserted, and from View → Calibration library.
 */
async function openCalibrationPicker({ reason = 'browse' } = {}) {
  const paper = state.printer?.paper || {};
  const barcode = paper.barcode || String(state.printer?.profile?.key || '').replace(/^bc:/, '') || '';
  let profiles = [];
  try {
    const payload = await api('/api/profiles');
    profiles = (payload.profiles || []).filter((profile) => Array.isArray(profile.regions) && profile.regions.length);
  } catch (error) {
    toast(error.message, 'error');
    return;
  }
  profiles.sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0));

  const body = document.createElement('div');
  body.className = 'calib-picker';
  const unknown = reason === 'unknown';
  body.innerHTML = `
    <div class="hint">${unknown
      ? `The database has no calibration for roll <code>${escapeHtml(barcode || 'unknown')}</code>. `
        + 'Create one, or pick a calibration you already have — it is copied onto this roll.'
      : 'Calibrations stored in the database. Pick one to use for the inserted roll, or edit it.'}</div>
    ${profiles.length ? `<div class="calib-grid">${profiles.map((profile, index) => {
    const sticker = calibrationSticker(profile);
    const zones = profile.regions.length;
    const current = profile.key === state.printer?.profile?.key;
    return `<div class="calib-card${current ? ' current' : ''}" data-calib="${index}">
        <div class="calib-preview">${calibrationThumb(profile)}</div>
        <div class="calib-title">${escapeHtml(calibrationName(profile))}${current ? ' <em>in use</em>' : ''}</div>
        <div class="calib-meta">sticker ${sticker.w} × ${sticker.h} mm
          · page ${round2(profile.width_mm)} × ${round2(profile.height_mm)} mm
          · ${zones} ${zones === 1 ? 'zone' : 'zones'}</div>
        <div class="calib-actions">
          <button type="button" class="btn primary" data-use="${index}"${barcode ? '' : ' disabled'}>Use</button>
          <button type="button" class="btn" data-edit="${index}">Edit</button>
        </div>
      </div>`;
  }).join('')}</div>`
    : '<div class="hint">No calibrations stored yet.</div>'}`;

  const footer = document.createElement('div');
  footer.className = 'row';
  footer.style.cssText = 'justify-content:flex-end;gap:8px';
  footer.innerHTML = `
    <button class="btn" data-calib-cancel>Cancel</button>
    <button class="btn primary" data-calib-new><i data-lucide="ruler"></i> New calibration</button>`;

  const modal = openModal({
    id: 'calibration-picker',
    title: unknown ? 'No calibration for this roll' : 'Calibration library',
    icon: 'library',
    width: 720,
    height: 560,
    body,
    footer,
  });
  fitModalToContent(modal, { min: 320, max: 700 });

  const useProfile = async (profile) => {
    if (!barcode) {
      toast('Connect the printer so the calibration can be stored for this roll', 'error');
      return;
    }
    const sticker = calibrationSticker(profile);
    const cols = Math.max(1, sticker.cols);
    const rows = Math.max(1, sticker.rows);
    try {
      const payload = await api('/api/roll-format', 'POST', {
        barcode,
        name: calibrationName(profile),
        width_mm: Number(profile.width_mm),
        height_mm: Number(profile.height_mm),
        rows,
        cols,
        gap_mm: Number(profile.gap_mm) || 0,
        label_type: paper.label_type ?? state.spec?.label_type ?? 1,
        capacity: paper.capacity ?? null,
        regions: profile.regions,
        sticker_w_mm: sticker.w,
        sticker_h_mm: sticker.h,
        row_space_mm: Number(profile.row_space_mm) || 0,
        col_space_mm: Number(profile.col_space_mm) || 0,
      });
      closeModal('calibration-picker');
      applyState(payload);
      applySize(Number(profile.width_mm), Number(profile.height_mm), {
        gap_mm: Number(profile.gap_mm) || 0,
        label_type: paper.label_type ?? state.spec?.label_type,
      });
      renderPaperRegions();
      schedulePreview();
      toast(`Calibration „${calibrationName(profile)}” applied to roll ${barcode}`, 'ok');
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  body.addEventListener('click', (event) => {
    const use = event.target.closest('[data-use]');
    if (use) {
      useProfile(profiles[Number(use.dataset.use)]);
      return;
    }
    const edit = event.target.closest('[data-edit]');
    if (edit) {
      const profile = profiles[Number(edit.dataset.edit)];
      closeModal('calibration-picker');
      openRollSizeDialog({ profile });
    }
  });

  footer.querySelector('[data-calib-cancel]').onclick = () => closeModal('calibration-picker');
  footer.querySelector('[data-calib-new]').onclick = () => {
    closeModal('calibration-picker');
    openRollSizeDialog();
  };
}

function toggleMirror() {
  if (!state.spec) return;
  const sheet = state.spec.sheet || (state.spec.sheet = { columns: [], rows: [], mirror: false, mirror_flip: false });
  sheet.mirror = !sheet.mirror;
  if (!sheet.mirror) sheet.mirror_flip = false;
  pushHistory();
  renderStageMeta();
  renderOverlay();
  schedulePreview();
  if (state.view === 'sheet') renderSheet();
  toast(sheet.mirror
    ? `Mirror on — edit the first region only (${sheetRegions()} regions filled automatically)`
    : 'Mirror off — every region is edited separately');
}

function renderPaperRegions() {
  const layer = $('#paper-regions');
  if (!layer || !state.spec) return;
  layer.innerHTML = '';
  if (!state.showRegions) return;
  const spec = state.spec;
  const geo = paperGeometry();

  if (geo.rows > 1) {
    for (let index = 1; index < geo.rows; index += 1) {
      const top = (index / geo.rows) * 100;
      const divider = document.createElement('div');
      divider.className = 'row-divider';
      divider.style.top = `${top}%`;
      layer.appendChild(divider);
      const tagEl = document.createElement('div');
      tagEl.className = 'row-tag';
      tagEl.style.top = `${top}%`;
      tagEl.textContent = `cut ${index}/${geo.rows}`;
      layer.appendChild(tagEl);
    }
  }
  if (geo.headMm && spec.width_mm > geo.headMm + 0.05) {
    const cut = document.createElement('div');
    cut.className = 'head-cut';
    cut.style.left = `${(geo.headMm / spec.width_mm) * 100}%`;
    layer.appendChild(cut);
  }

  // Item zones drawn in the calibrator for this roll: show where content can go.
  const cells = state.printer?.profile?.regions || [];
  cells.forEach((cell, index) => {
    const isLine = cell.shape === 'line';
    const cellX = Math.max(0, Math.min(Number(cell.x || 0), spec.width_mm));
    const cellY = Math.max(0, Math.min(Number(cell.y || 0), spec.height_mm));
    const cellW = isLine
      ? Number(cell.w || 0)
      : Math.min(Number(cell.w || 0), spec.width_mm - cellX);
    const cellH = isLine
      ? Number(cell.h || 0.8)
      : Math.min(Number(cell.h || 0), spec.height_mm - cellY);
    if (cellW <= 0.2 || cellH <= 0.2) return;
    const outlines = (Array.isArray(cell.shapes) ? cell.shapes : [])
      .filter((shape) => Number(shape.w) > 0.2 && Number(shape.h) > 0.2);
    const shapeClass = cell.shape === 'circle' ? ' circle' : cell.shape === 'line' ? ' line' : '';
    const current = index === 0 && !!state.spec?.sheet?.cells_mirror;
    const box = document.createElement('div');
    box.className = `paper-cell${shapeClass}${current ? ' editable' : ''}`;
    box.style.left = `${(cellX / spec.width_mm) * 100}%`;
    box.style.top = `${(cellY / spec.height_mm) * 100}%`;
    box.style.width = `${(cellW / spec.width_mm) * 100}%`;
    box.style.height = `${(cellH / spec.height_mm) * 100}%`;
    if (cell.shape === 'line' && Number(cell.angle || 0)) {
      box.style.transform = `rotate(${Number(cell.angle)}deg)`;
      box.style.transformOrigin = '0 50%';
    }
    if (outlines.length > 1) {
      // One item, several outlines: the wrapper is the item, the children are its shape.
      box.classList.add('multi');
      outlines.forEach((shape) => {
        const leaf = document.createElement('i');
        leaf.className = `paper-outline${shape.kind === 'circle' ? ' circle' : ''}`;
        leaf.style.left = `${((Number(shape.x) - cellX) / cellW) * 100}%`;
        leaf.style.top = `${((Number(shape.y) - cellY) / cellH) * 100}%`;
        leaf.style.width = `${(Number(shape.w) / cellW) * 100}%`;
        leaf.style.height = `${(Number(shape.h) / cellH) * 100}%`;
        box.appendChild(leaf);
      });
    }
    box.insertAdjacentHTML('beforeend', `<b>${index + 1}</b>`);
    box.title = `${cell.label || `Item ${index + 1}`} — ${round2(cell.w)} × ${round2(cell.h)} mm`
      + (outlines.length > 1 ? ` (${outlines.length} outlines)` : '');
    layer.appendChild(box);
  });
}

/* ================================================================== overlay */
function elementTitle(element) {  if (element.label) return element.label;
  if (element.type === 'text') return (element.text || 'text').split('\n')[0].slice(0, 22) || 'text';
  if (element.type === 'barcode') return element.text ? `Code ${element.text.slice(0, 16)}` : 'Barcode';
  if (element.type === 'qr') return element.text ? `QR ${element.text.slice(0, 16)}` : 'QR code';
  if (element.type === 'image') return 'Image';
  return TYPE_LABELS[element.type];
}

/* Vertical area the user can actually edit. With Mirror on, only the first region is
   designed by hand — the remaining ones are generated, so centring, snapping and dragging
   must stay inside that band instead of the whole label. */
function editBand() {
  const regions = sheetRegions();
  const mirror = !!state.spec?.sheet?.mirror && regions > 1;
  const height = Number(state.spec?.height_mm) || 0;
  const size = mirror ? height / regions : height;
  return { mirror, top: 0, bottom: size, height: size, center: size / 2 };
}

function isMirrorLocked(element) {
  const band = editBand();
  if (!band.mirror) return false;
  return element.y >= band.bottom - 0.01; // anchored in a generated region
}

function crossesCut(element) {
  const band = editBand();
  return band.mirror && element.y < band.bottom - 0.01 && element.y + element.h > band.bottom + 0.01;
}

function renderOverlay() {  if (!state.spec) return;  const overlay = $('#overlay');
  overlay.innerHTML = '';
  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;

  for (const element of state.spec.elements) {
    const box = document.createElement('div');
    const hidden = element.visible === false;
    const locked = isMirrorLocked(element);
    const crossing = !locked && crossesCut(element);
    const hovered = element.id === state.hoverId && element.id !== state.selectedId;
    box.className = `element-box${element.id === state.selectedId ? ' selected' : ''}${hovered ? ' hover' : ''}${hidden ? ' hidden-el' : ''}${locked ? ' locked' : ''}${crossing ? ' crosses' : ''}`;
    box.dataset.name = elementTitle(element) || element.type;
    box.dataset.id = element.id;
    box.style.left = `${(element.x / labelWidth) * 100}%`;
    box.style.top = `${(element.y / labelHeight) * 100}%`;
    box.style.width = `${(element.w / labelWidth) * 100}%`;
    box.style.height = `${(element.h / labelHeight) * 100}%`;

    if (element.visible === false) box.title = 'Hidden layer — it will not be printed';
    if (locked) box.title = 'Region generated by Mirror — edit the first region';
    if (crossing) box.title = 'Mirror: this element crosses the cut — the part below it is clipped when printing';

    const limit = codeLimit(element);
    if (limit && limit.min_w_mm > 0
      && (element.w < limit.min_w_mm - 0.01 || (limit.min_h_mm > 0 && element.h < limit.min_h_mm - 0.01))) {
      box.classList.add('code-too-small');
      box.title = `Too small to print — needs at least ${limit.min_w_mm} × ${limit.min_h_mm} mm at ${limit.dpi} dpi`;
    } else if (limit && limit.recommended_w_mm > 0
      && (element.w < limit.recommended_w_mm - 0.01
        || (limit.recommended_h_mm > 0 && element.h < limit.recommended_h_mm - 0.01))) {
      box.classList.add('code-risky');
      box.title = `Tight for scanners — no room for the quiet zone. ${limit.recommended_w_mm} mm is safe.`;
    }

    const handle = document.createElement('div');
    handle.className = 'handle';
    box.appendChild(handle);
    overlay.appendChild(box);
  }
  renderLayers();
  updateFloatingToolbar();
}

/* ================================================================ layers */
const thumbCache = { src: null, img: null };

/* Per-element renders: a layer thumbnail (and the dragged artwork) must show the element
   alone, not whatever else happens to sit in the same rectangle of the label. */
const elementImages = new Map();

function elementSignature(element) {
  const fields = [
    element.type, element.text, element.font, element.font_size_pt, element.bold, element.italic,
    element.align, element.valign, element.line_spacing, element.auto_fit, element.rotation,
    element.w, element.h, element.fill, element.thickness_mm, element.barcode_format,
    element.show_text, element.ec_level, element.image_id, element.visible,
  ];
  return `${element.id}:${fields.map((value) => (value === undefined || value === null ? '' : String(value))).join('|')}`;
}

function elementImage(element) {
  const key = elementSignature(element);
  let entry = elementImages.get(key);
  if (!entry) {
    entry = { img: null, src: null, box: null };
    elementImages.set(key, entry);
    if (elementImages.size > 160) {
      const oldest = elementImages.keys().next().value;
      if (oldest && oldest !== key) elementImages.delete(oldest);
    }
    api('/api/element-preview', 'POST', { spec: state.spec, element_id: element.id })
      .then((result) => {
        const image = new Image();
        image.onload = () => {
          entry.img = image;
          entry.src = image.src;
          entry.box = result.element_box || null;
          renderLayers();
        };
        image.src = `data:image/png;base64,${result.png}`;
        entry.box = result.element_box || null;
      })
      .catch(() => { elementImages.delete(key); });
  }
  return entry;
}
function previewImage() {
  if (!state.preview) return null;
  const src = state.preview.png;
  if (thumbCache.src !== src) {
    thumbCache.src = src;
    thumbCache.img = new Image();
    thumbCache.img.onload = () => renderLayers();
    thumbCache.img.src = `data:image/png;base64,${src}`;
  }
  return thumbCache.img && thumbCache.img.complete ? thumbCache.img : null;
}

function drawLayerThumb(canvas, element) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cssW = 40;
  const cssH = 28;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, cssW, cssH);

  const finish = () => {
    ctx.strokeStyle = 'rgba(0,26,77,0.14)';
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, cssW - 1, cssH - 1);
  };

  const img = previewImage();
  const entry = elementImage(element);
  const own = entry?.img;
  if (own && own.naturalWidth) {
    const pad = 3;
    const scale = Math.min((cssW - pad * 2) / own.naturalWidth, (cssH - pad * 2) / own.naturalHeight);
    const drawW = Math.max(1, own.naturalWidth * scale);
    const drawH = Math.max(1, own.naturalHeight * scale);
    ctx.imageSmoothingEnabled = scale < 1;
    ctx.drawImage(
      own,
      Math.round((cssW - drawW) / 2),
      Math.round((cssH - drawH) / 2),
      drawW,
      drawH,
    );
    return finish();
  }
  if (!img || !state.spec || !img.naturalWidth) return finish();
  const { width_mm: W, height_mm: H } = state.spec;
  const labelW = img.naturalWidth;
  const labelH = img.naturalHeight;

  // 1. preview crop, exactly the layer area
  const sx = Math.max(0, Math.floor((element.x / W) * labelW));
  const sy = Math.max(0, Math.floor((element.y / H) * labelH));
  const sw = Math.max(1, Math.min(labelW - sx, Math.ceil((element.w / W) * labelW)));
  const sh = Math.max(1, Math.min(labelH - sy, Math.ceil((element.h / H) * labelH)));

  const probe = document.createElement('canvas');
  probe.width = sw;
  probe.height = sh;
  const pctx = probe.getContext('2d', { willReadFrequently: true });
  pctx.imageSmoothingEnabled = false;
  pctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);

  // 2. trim the empty background — the thumbnail shows the element, not the label fragment
  let ink = null;
  try {
    const data = pctx.getImageData(0, 0, sw, sh).data;
    let minX = sw, minY = sh, maxX = -1, maxY = -1;
    for (let y = 0; y < sh; y += 1) {
      for (let x = 0; x < sw; x += 1) {
        if (data[(y * sw + x) * 4] < 128) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX >= minX && maxY >= minY) ink = { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  } catch (_) { /* canvas unreadable — keep the full crop */ }

  if (!ink) {
    // empty layer — show the element type instead
    ctx.fillStyle = 'rgba(0, 26, 77, 0.28)';
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText((TYPE_LABELS[element.type] || '?').slice(0, 1).toUpperCase(), cssW / 2, cssH / 2);
    return finish();
  }

  const pad = 3;
  const scale = Math.min((cssW - pad * 2) / ink.w, (cssH - pad * 2) / ink.h);
  const drawW = Math.max(1, ink.w * scale);
  const drawH = Math.max(1, ink.h * scale);
  ctx.imageSmoothingEnabled = scale < 1;
  ctx.drawImage(
    img,
    sx + ink.x,
    sy + ink.y,
    ink.w,
    ink.h,
    Math.round((cssW - drawW) / 2),
    Math.round((cssH - drawH) / 2),
    drawW,
    drawH,
  );
  finish();
}

function renderLayers() {
  const list = $('#layer-list');
  const elements = state.spec ? state.spec.elements : [];
  $('#layer-count').textContent = String(elements.length);
  list.innerHTML = '';
  if (!elements.length) {
    list.innerHTML = '<div class="empty">No layers yet</div>';
    return;
  }

  const panel = [...elements].reverse();
  panel.forEach((element, panelIndex) => {
    const row = document.createElement('div');
    row.className = `layer-row${element.id === state.selectedId ? ' selected' : ''}${element.visible === false ? ' hidden-layer' : ''}`;
    row.draggable = true;
    row.dataset.id = element.id;
    row.dataset.index = String(panelIndex);

    const eye = document.createElement('button');
    eye.className = 'layer-eye';
    eye.title = element.visible === false ? 'Show layer' : 'Hide layer';
    eye.innerHTML = `<i data-lucide="${element.visible === false ? 'eye-off' : 'eye'}"></i>`;
    eye.onclick = (event) => {
      event.stopPropagation();
      toggleElementVisible(element);
    };
    row.appendChild(eye);

    const thumb = document.createElement('canvas');
    thumb.className = 'layer-thumb';
    thumb.title = TYPE_LABELS[element.type] || element.type;
    row.appendChild(thumb);
    drawLayerThumb(thumb, element);

    const name = document.createElement('div');
    name.className = 'layer-name';
    name.innerHTML = `<span class="layer-kind"><i data-lucide="${TYPE_ICONS[element.type] || 'square'}"></i></span>
      <span class="layer-text">${escapeHtml(elementTitle(element))}</span>`;
    row.appendChild(name);

    name.ondblclick = (event) => {
      event.stopPropagation();
      const input = document.createElement('input');
      input.className = 'layer-rename';
      input.value = elementTitle(element);
      name.querySelector('.layer-text').replaceWith(input);
      input.focus();
      input.select();
      const commit = () => {
        const value = input.value.trim();
        element.label = value || null;
        pushHistory();
        renderOverlay();
      };
      input.onblur = commit;
      input.onkeydown = (keyEvent) => {
        if (keyEvent.key === 'Enter') { keyEvent.preventDefault(); input.blur(); }
        if (keyEvent.key === 'Escape') { keyEvent.preventDefault(); renderLayers(); }
      };
    };

    const grip = document.createElement('span');
    grip.className = 'layer-grip';
    grip.innerHTML = '<i data-lucide="grip-vertical"></i>';
    row.appendChild(grip);

    row.onclick = () => {
      state.selectedId = element.id;
      renderOverlay();
      buildInspector();
    };

    row.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('text/plain', element.id);
      event.dataTransfer.effectAllowed = 'move';
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('dragging'));
    row.addEventListener('dragover', (event) => {
      event.preventDefault();
      row.classList.add('drop-target');
    });
    row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
    row.addEventListener('drop', (event) => {
      event.preventDefault();
      row.classList.remove('drop-target');
      const draggedId = event.dataTransfer.getData('text/plain');
      if (draggedId && draggedId !== element.id) reorderLayer(draggedId, panelIndex);
    });

    list.appendChild(row);
  });
  refreshIcons(list);
}

function reorderLayer(draggedId, panelIndex) {
  const elements = state.spec.elements;
  const fromIndex = elements.findIndex((element) => element.id === draggedId);
  if (fromIndex < 0) return;
  const [moved] = elements.splice(fromIndex, 1);
  const targetIndex = Math.max(0, Math.min(elements.length, elements.length - panelIndex));
  elements.splice(targetIndex, 0, moved);
  pushHistory();
  renderOverlay();
  schedulePreview();
}

/* ============================================================== inspector */
function field(label, html) {
  return `<div class="field"><label>${label}</label>${html}</div>`;
}

function numberInput(key, value, step = 0.5, min = null, max = null) {
  const attrs = [
    `data-key="${key}"`,
    'type="number"',
    `step="${step}"`,
    min !== null ? `min="${min}"` : '',
    max !== null ? `max="${max}"` : '',
    `value="${value ?? 0}"`,
  ].join(' ');
  return `<input ${attrs}>`;
}

function selectInput(key, value, options) {
  return `<select data-key="${key}">${options
    .map(([optionValue, label]) => `<option value="${optionValue}" ${optionValue === value ? 'selected' : ''}>${label}</option>`)
    .join('')}</select>`;
}

function checkInput(key, label, checked) {
  return `<label class="check"><input type="checkbox" data-key="${key}" ${checked ? 'checked' : ''}> ${label}</label>`;
}

/* ------------------------------------------------------------ image cropping */
const imageMetaCache = new Map();

async function imageMeta(imageId) {
  if (!imageId) return null;
  if (imageMetaCache.has(imageId)) return imageMetaCache.get(imageId);
  try {
    const meta = await api(`/api/uploads/${imageId}/meta`);
    imageMetaCache.set(imageId, meta);
    return meta;
  } catch (_) {
    return null;
  }
}

function applyImageCrop(element, mode) {
  if (mode === 'fill') {
    element.image_fit = 'cover';
    element.image_zoom = 1;
    element.image_offset_x = 0;
    element.image_offset_y = 0;
  } else if (mode === 'centre') {
    element.image_offset_x = 0;
    element.image_offset_y = 0;
  } else if (mode === 'reset') {
    element.image_fit = 'contain';
    element.image_zoom = 1;
    element.image_offset_x = 0;
    element.image_offset_y = 0;
  }
}

/* Tells the user what the crop does to print quality, measured against this printer's dpi. */
async function renderCropQuality(element) {
  const target = $('#crop-quality');
  if (!target || !element.image_id) return;
  const meta = await imageMeta(element.image_id);
  if (!meta || !meta.width_px || !meta.height_px) {
    target.textContent = 'Source resolution unknown.';
    return;
  }
  if (!document.body.contains(target)) return;
  const printerDpi = Number(state.printer?.info?.dpi) || 203;
  const fit = element.image_fit === 'cover' ? 'cover' : element.image_fit === 'stretch' ? 'stretch' : 'contain';
  const zoom = Math.max(0.05, Number(element.image_zoom) || 1);
  let dpi;
  if (fit === 'stretch') {
    dpi = Math.min(25.4 * meta.width_px / Math.max(0.1, element.w), 25.4 * meta.height_px / Math.max(0.1, element.h));
  } else {
    const scale = fit === 'cover'
      ? Math.max(element.w / meta.width_px, element.h / meta.height_px)
      : Math.min(element.w / meta.width_px, element.h / meta.height_px);
    dpi = 25.4 * zoom / Math.max(1e-6, scale);
  }
  const rounded = Math.round(dpi);
  const verdict = rounded >= printerDpi * 1.4
    ? 'sharp'
    : rounded >= printerDpi
      ? 'matches the printhead'
      : 'soft — the picture is upscaled';
  target.textContent = `Source ${meta.width_px} × ${meta.height_px} px · ~${rounded} dpi in this box (printer ${printerDpi} dpi) — ${verdict}`;
  target.classList.toggle('warn-text', rounded < printerDpi);
}

function buildInspector() {
  const card = $('#inspector-card');  const element = selectedElement();
  if (!element) {
    card.innerHTML = '<div class="panel-empty">Select an element on the label to edit it. Drag it to move, use the bottom-right handle to resize.</div>';
    return;
  }

  const parts = [];
  parts.push('<div class="grid-2">');
  parts.push(field('X (mm)', numberInput('x', round2(element.x))));
  parts.push(field('Y (mm)', numberInput('y', round2(element.y))));
  parts.push(field('Width (mm)', numberInput('w', round2(element.w))));
  parts.push(field('Height (mm)', numberInput('h', round2(element.h))));
  parts.push('</div>');
  parts.push(field('Rotation', selectInput('rotation', String(element.rotation), [
    ['0', '0°'], ['90', '90°'], ['180', '180°'], ['270', '270°'],
  ])));

  if (element.type === 'text') {
    parts.push(field('Text', `<textarea data-key="text">${escapeHtml(element.text || '')}</textarea>`));
    parts.push(field('Font', `<button type="button" class="btn wide font-pick" data-font-pick="1" style="font-family:${fontCss(element.font)}">
      <i data-lucide="type"></i><span>${escapeHtml(element.font)}</span><i data-lucide="chevron-down"></i>
    </button>`));
    parts.push('<div class="grid-2">');
    parts.push(field('Size (pt)', numberInput('font_size_pt', element.font_size_pt, 0.5, 1, 200)));
    parts.push(field('Line height', numberInput('line_spacing', element.line_spacing, 0.05, 0.6, 3)));
    parts.push('</div>');
    parts.push('<div class="grid-2">');
    parts.push(field('Align', selectInput('align', element.align, [['left', 'left'], ['center', 'centre'], ['right', 'right']])));
    parts.push(field('Vertical', selectInput('valign', element.valign, [['top', 'top'], ['middle', 'middle'], ['bottom', 'bottom']])));
    parts.push('</div>');
    parts.push('<div class="row" style="margin-top:8px;gap:12px">');
    parts.push(checkInput('bold', 'Bold', element.bold));
    parts.push(checkInput('italic', 'Italic', element.italic));
    parts.push(checkInput('auto_fit', 'Auto fit', element.auto_fit));
    parts.push('</div>');
  }

  if (element.type === 'barcode') {
    parts.push(field('Data', `<input data-key="text" value="${escapeHtml(element.text || '')}">`));
    const formats = (state.options?.barcode_formats || ['code128'])
      .map((format) => `<option value="${format}" ${format === element.barcode_format ? 'selected' : ''}>${String(format).toUpperCase()}</option>`)
      .join('');
    parts.push(field('Format', `<select data-key="barcode_format">${formats}</select>`));
    parts.push(`<div class="row" style="margin-top:8px">${checkInput('show_text', 'Text under code', element.show_text)}</div>`);
  }

  if (element.type === 'qr') {
    parts.push(field('Data', `<textarea data-key="text">${escapeHtml(element.text || '')}</textarea>`));
    parts.push(field('Error correction', selectInput('ec_level', element.ec_level, [
      ['L', 'L (7%)'], ['M', 'M (15%)'], ['Q', 'Q (25%)'], ['H', 'H (30%)'],
    ])));
  }

  if (CODE_TYPES.has(element.type)) parts.push(codeLimitHint(element));

  if (element.type === 'rect') {
    parts.push(field('Line width (mm)', numberInput('thickness_mm', element.thickness_mm, 0.1, 0.1, 3)));
    parts.push(`<div class="row" style="margin-top:8px">${checkInput('fill', 'Fill', element.fill)}</div>`);
  }

  if (element.type === 'line') {
    parts.push(field('Thickness (mm)', numberInput('thickness_mm', element.thickness_mm, 0.1, 0.1, 3)));
  }

  if (element.type === 'image') {
    parts.push(field('Image', `<button id="btn-pick-image" class="btn wide">${element.image_id ? 'Change image' : 'Pick image'}</button>`));
    if (element.image_id) {
      parts.push(`<div class="crop-preview"><img class="thumb" src="/api/uploads/${element.image_id}" alt="image"></div>`);
      parts.push(field('Fill mode', selectInput('image_fit', element.image_fit, [
        ['cover', 'Cover — crop to the box'],
        ['contain', 'Contain — whole picture'],
        ['stretch', 'Stretch — squash to the box'],
      ])));
      parts.push(field('Zoom', numberInput('image_zoom', element.image_zoom ?? 1, 1.0, 0.1, 8, 0.05)));
      parts.push(`<div class="grid-2">
        ${field('Crop X (mm)', numberInput('image_offset_x', element.image_offset_x, 0, -200, 200, 0.1))}
        ${field('Crop Y (mm)', numberInput('image_offset_y', element.image_offset_y, 0, -200, 200, 0.1))}
      </div>`);
      parts.push(`<div class="row crop-actions">
        <button type="button" class="btn" data-crop="fill">Fill box</button>
        <button type="button" class="btn" data-crop="centre">Centre</button>
        <button type="button" class="btn" data-crop="reset">Reset</button>
      </div>`);
      parts.push(`<div class="row" style="margin-top:8px">${checkInput('image_dither', 'Dither photo', element.image_dither)}</div>
        <div class="hint">Dithering keeps shades and gradients; leave it off for logos and line art.</div>`);
      parts.push(`<div class="hint" id="crop-quality" data-image-id="${escapeHtml(element.image_id)}">Checking the source resolution…</div>`);
    }
  }

  card.innerHTML = parts.join('');

  const pick = $('#btn-pick-image');
  if (pick) {
    pick.onclick = () => {
      state.pendingImageTarget = element.id;
      $('#file-input').click();
    };
  }

  if (element.type === 'image' && element.image_id) {
    $$('[data-crop]', card).forEach((button) => {
      button.onclick = () => {
        applyImageCrop(element, button.dataset.crop);
        pushHistory();
        renderOverlay();
        buildInspector();
        schedulePreview();
      };
    });
    renderCropQuality(element);
  }

  const fontPick = $('[data-font-pick]', card);
  if (fontPick) fontPick.onclick = () => openFontPicker(element);

  const fitCode = $('[data-fit-code]', card);
  if (fitCode) {
    fitCode.onclick = () => {
      syncCodeLimit(element, { safe: true });
      pushHistory();
    };
  }
}

function onInspectorInput(event) {
  const key = event.target.dataset.key;
  if (!key) return;
  const element = selectedElement();
  if (!element) return;
  let value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
  if (event.target.type === 'number') value = Number(value);
  if (key === 'rotation' || key === 'font_size_pt') value = Number(value);
  element[key] = value;
  if (key === 'y' || key === 'h') {
    // Mirror prints only the first region — do not let the fields push the element into a generated one.
    const band = editBand();
    element.h = Math.max(2, Math.min(element.h, band.height));
    element.y = Math.max(0, Math.min(element.y, Math.max(0, band.height - element.h)));
    updateGeometryFields();
  }
  if ((key === 'w' || key === 'h') && CODE_TYPES.has(element.type)) {
    const fitted = clampCodeSize(element, element.w, element.h);
    if (fitted.limited) {
      element.w = round2(Math.min(fitted.w, state.spec.width_mm - element.x));
      element.h = round2(fitted.h);
      updateGeometryFields();
    }
  }
  if (CODE_TYPES.has(element.type) && ['text', 'barcode_format', 'ec_level'].includes(key)) {
    // A longer payload needs more modules, hence a bigger box.
    scheduleCodeLimitSync(element);
  }
  renderOverlay();
  schedulePreview();
}

function updateGeometryFields() {
  const element = selectedElement();
  if (!element) return;
  for (const key of ['x', 'y', 'w', 'h']) {
    const input = $(`#inspector-card [data-key="${key}"]`);
    if (input && document.activeElement !== input) input.value = round2(element[key]);
  }
}

/* ============================================== QR / barcode size floor
   A code is a grid of whole modules and the printhead can only place whole dots, so below a
   certain size the modules smear into their neighbours and no scanner can read the result.
   The server knows the module count and the printer dpi, so it owns the limit; the editor
   refuses to go under it instead of printing an unreadable label. */

function codeLimitKey(element) {
  return [element.type, element.text || '', element.ec_level || '', element.barcode_format || ''].join('\u0001');
}

function codeLimit(element) {
  if (!element || !CODE_TYPES.has(element.type)) return null;
  const entry = state.codeLimits[element.id];
  return entry && entry.key === codeLimitKey(element) ? entry : null;
}

async function ensureCodeLimit(element) {
  if (!element || !CODE_TYPES.has(element.type)) return null;
  const key = codeLimitKey(element);
  const cached = state.codeLimits[element.id];
  if (cached && cached.key === key) return cached;
  state.codeLimits[element.id] = { key, pending: true };
  let result = { key };
  try {
    result = Object.assign({ key }, await api('/api/code-size', 'POST', {
      kind: element.type,
      text: element.text || '',
      ec_level: element.ec_level || 'M',
      barcode_format: element.barcode_format || 'code128',
    }));
  } catch (_) {
    /* Without a limit the editor stays unclamped; the renderer still warns. */
  }
  state.codeLimits[element.id] = result;
  return result;
}

/** Size a code element must keep. QR codes stay square — a stretched matrix does not scan. */
function clampCodeSize(element, w, h, { square = true } = {}) {
  const limit = codeLimit(element);
  if (!limit || !(limit.min_w_mm > 0)) return { w, h, limited: false };
  if (limit.square && square) {
    const side = Math.max(w, h, limit.min_w_mm);
    return { w: side, h: side, limited: true };
  }
  return { w: Math.max(w, limit.min_w_mm), h: Math.max(h, limit.min_h_mm || 0), limited: true };
}

/** Grow the element when the data or dpi needs more room than the box has. */
async function syncCodeLimit(element, { grow = true, safe = false } = {}) {
  if (!element || !CODE_TYPES.has(element.type)) return;
  const limit = await ensureCodeLimit(element);
  if (!limit || !(limit.min_w_mm > 0)) return;
  if (grow) {
    const band = editBand();
    const fitted = safe
      ? { w: Math.max(element.w, limit.recommended_w_mm || 0), h: Math.max(element.h, limit.recommended_h_mm || 0) }
      : clampCodeSize(element, element.w, element.h, { square: false });
    const maxW = Math.max(2, state.spec.width_mm - Math.min(element.x, Math.max(0, state.spec.width_mm - 2)));
    const w = round2(Math.min(fitted.w, maxW));
    const h = round2(Math.min(fitted.h, band.height));
    if (w !== round2(element.w) || h !== round2(element.h)) {
      element.w = w;
      element.h = h;
      element.y = round2(Math.max(0, Math.min(element.y, band.height - h)));
      updateGeometryFields();
      renderOverlay();
      schedulePreview();
    }
  }
  if (state.selectedId === element.id) buildInspector();
}

let codeLimitTimer = null;
function scheduleCodeLimitSync(element) {
  clearTimeout(codeLimitTimer);
  codeLimitTimer = setTimeout(() => { syncCodeLimit(element); }, 250);
}

/** Orange frame + tooltip while a code sits under its printable minimum. */
function paintCodeLimitHint(element) {
  const limit = codeLimit(element);
  if (!limit || !(limit.min_w_mm > 0)) return;
  const box = document.querySelector(`#overlay .element-box[data-id="${element.id}"]`);
  if (!box) return;
  const tooSmall = element.w < limit.min_w_mm - 0.01 || (element.h < (limit.min_h_mm || 0) - 0.01);
  box.classList.toggle('code-too-small', tooSmall);
  box.title = tooSmall
    ? `Too small to print — needs at least ${limit.min_w_mm} × ${limit.min_h_mm} mm at ${limit.dpi} dpi`
    : '';
}

/** Inspector line: the smallest box that still scans on the attached printer. */
function codeLimitHint(element) {
  const limit = codeLimit(element);
  if (!limit) {
    return '<div class="hint" id="code-limit">Checking the printable size for this printer…</div>';
  }
  if (!(limit.min_w_mm > 0)) return '';
  const hard = limit.square ? `${limit.min_w_mm} × ${limit.min_h_mm} mm` : `${limit.min_w_mm} mm wide`;
  const comfy = limit.square
    ? `${limit.recommended_w_mm} mm square`
    : `${limit.recommended_w_mm} mm wide`;
  const modules = `${limit.modules} modules · ${limit.hard_module_mm} mm minimum per module at ${limit.dpi} dpi`;
  const impossible = element.w < limit.min_w_mm - 0.01 || (limit.min_h_mm > 0 && element.h < limit.min_h_mm - 0.01);
  const risky = !impossible && (element.w < limit.recommended_w_mm - 0.01
    || (limit.recommended_h_mm > 0 && element.h < limit.recommended_h_mm - 0.01));
  if (impossible) {
    return `<div class="hint warn" id="code-limit">Too small to print — ${hard} is the floor on this printer
      <button type="button" class="btn" data-fit-code="1">Fit</button>
      <br><span class="mono">${modules}</span></div>`;
  }
  if (risky) {
    return `<div class="hint warn-text" id="code-limit">Tight for scanners — there is no room for the quiet zone yet,
      so modules may print one dot wide. ${comfy} is safe
      <button type="button" class="btn" data-fit-code="1">Fit</button>
      <br><span class="mono">${modules}</span></div>`;
  }
  return `<div class="hint" id="code-limit">Scannable from <b>${hard}</b>, safe at <b>${comfy}</b>
    <br><span class="mono">${modules}</span></div>`;
}

/* ========================================================= floating toolbar */
function updateFloatingToolbar() {
  const bar = $('#floating-toolbar');
  const element = selectedElement();
  if (!bar) return;
  if (!element) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  $('#ft-label').textContent = elementTitle(element);
  layoutFloatingToolbar();
}

function layoutFloatingToolbar() {
  const bar = $('#floating-toolbar');
  const wrap = $('#canvas-wrap');
  if (!bar || !wrap) return;
  // Fixed spot above the canvas — the bar must not follow the dragged element.
  const wrapRect = wrap.getBoundingClientRect();
  const host = bar.offsetParent && bar.offsetParent.getBoundingClientRect
    ? bar.offsetParent.getBoundingClientRect()
    : { left: 0, top: 0 };
  bar.style.left = `${Math.round(wrapRect.left - host.left + wrapRect.width / 2)}px`;
  bar.style.top = `${Math.max(4, Math.round(wrapRect.top - host.top + 10))}px`;
}

/* ============================================================== element ops */
function deleteSelected() {
  if (!state.selectedId) return;
  state.spec.elements = state.spec.elements.filter((element) => element.id !== state.selectedId);
  state.selectedId = null;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
}

function duplicateSelected() {
  const element = selectedElement();
  if (!element) return;
  const copy = Object.assign(clone(element), { id: uid(), x: element.x + 2, y: element.y + 2 });
  state.spec.elements.push(copy);
  state.selectedId = copy.id;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
}

function toggleElementVisible(element) {
  element.visible = element.visible === false;
  pushHistory();
  renderOverlay();
  schedulePreview();
  buildInspector();
}

function moveLayer(id, direction) {
  const elements = state.spec.elements;
  const index = elements.findIndex((element) => element.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= elements.length) return;
  const [element] = elements.splice(index, 1);
  elements.splice(target, 0, element);
  pushHistory();
  renderOverlay();
  schedulePreview();
}

function alignSelected(axis) {
  const element = selectedElement();
  if (!element) return;
  const band = editBand();
  if (axis === 'h') element.x = Math.max(0, round2((state.spec.width_mm - element.w) / 2));
  if (axis === 'v') {
    const offset = (band.height - element.h) / 2;
    element.y = Math.max(0, round2(Math.min(offset, Math.max(0, band.height - element.h))));
  }
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
}

function rotateSelected() {
  const element = selectedElement();
  if (!element) return;
  element.rotation = (element.rotation + 90) % 360;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
}

function addElement(type) {
  const { width_mm: width } = state.spec;
  const band = editBand();
  const height = band.height;
  const base = { id: uid(), type, x: 1, y: 1, w: Math.min(width - 2, 28), h: Math.min(height - 2, 9) };
  if (type === 'text') Object.assign(base, { text: 'New text', font_size_pt: 10, align: 'center' });
  if (type === 'barcode') Object.assign(base, { text: '1234567890', h: Math.min(10, height - 2), w: Math.min(width - 2, 38) });
  if (type === 'qr') Object.assign(base, { text: 'https://', w: Math.min(16, width - 2), h: Math.min(16, height - 2) });
  if (type === 'rect') Object.assign(base, { x: 0.5, y: 0.5, w: width - 1, h: height - 1, thickness_mm: 0.4 });
  if (type === 'line') Object.assign(base, { w: width - 2, h: Math.min(2, height - 2), thickness_mm: 0.4 });
  if (type === 'image') {
    // Whole picture by default; "Fill box" in the inspector crops it deliberately.
    Object.assign(base, { w: Math.min(20, width - 2), h: Math.min(20, height - 2), image_fit: 'contain' });
  }
  state.spec.elements.push(base);
  state.selectedId = base.id;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
  if (CODE_TYPES.has(type)) syncCodeLimit(base);
  if (type === 'image') {
    state.pendingImageTarget = base.id;
    $('#file-input').click();
  }
}

async function uploadImage(file) {
  const data = new FormData();
  data.append('file', file);
  const response = await fetch('/api/upload', { method: 'POST', body: data });
  if (!response.ok) throw new Error('Could not upload the image');
  const result = await response.json();
  const target = state.spec.elements.find((element) => element.id === state.pendingImageTarget);
  if (target) {
    target.image_id = result.id;
    state.selectedId = target.id;
    pushHistory();
    renderOverlay();
    buildInspector();
    schedulePreview();
  }
}

function nudge(dx, dy, event) {
  const element = selectedElement();
  if (!element) return;
  const band = editBand();
  const step = event.shiftKey ? 0.2 : 1;
  element.x = Math.max(0, Math.min(state.spec.width_mm - element.w, element.x + dx * step));
  element.y = Math.max(0, Math.min(Math.max(0, band.height - element.h), element.y + dy * step));
  renderOverlay();
  updateGeometryFields();
  schedulePreview();
}

/* ================================================================ pointers */
let drag = null;
const guideEls = { v: null, h: null };

function ensureGuideEls() {
  const layer = $('#guides');
  if (!layer) return;
  if (!guideEls.v || !guideEls.v.isConnected) {
    guideEls.v = document.createElement('div');
    guideEls.v.className = 'guide v';
    guideEls.v.hidden = true;
    layer.appendChild(guideEls.v);
  }
  if (!guideEls.h || !guideEls.h.isConnected) {
    guideEls.h = document.createElement('div');
    guideEls.h.className = 'guide h';
    guideEls.h.hidden = true;
    layer.appendChild(guideEls.h);
  }
}

function paintGuides(vertical, horizontal) {
  ensureGuideEls();
  if (!guideEls.v || !state.spec) return;
  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;
  if (vertical) {
    guideEls.v.hidden = false;
    guideEls.v.style.left = `${(vertical.value / labelWidth) * 100}%`;
    guideEls.v.classList.toggle('center', !!vertical.center);
  } else {
    guideEls.v.hidden = true;
  }
  if (horizontal) {
    guideEls.h.hidden = false;
    guideEls.h.style.top = `${(horizontal.value / labelHeight) * 100}%`;
    guideEls.h.classList.toggle('center', !!horizontal.center);
  } else {
    guideEls.h.hidden = true;
  }
}

function paintBox(box, element) {
  if (!box || !state.spec) return;
  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;
  box.style.left = `${(element.x / labelWidth) * 100}%`;
  box.style.top = `${(element.y / labelHeight) * 100}%`;
  box.style.width = `${(element.w / labelWidth) * 100}%`;
  box.style.height = `${(element.h / labelHeight) * 100}%`;
}

/* Content of the dragged element is a crop of the label preview that travels with
   the pointer, so the artwork visibly moves instead of only the selection frame. */
function paintElementGhost(ghost, element) {
  const entry = ghost?.__own;
  const stage = $('#stage');
  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;
  if (!entry?.img?.naturalWidth || !entry.box || !stage || !labelWidth || !labelHeight) return false;
  const frameW = stage.clientWidth * (element.w / labelWidth);
  const frameH = stage.clientHeight * (element.h / labelHeight);
  const scaleX = frameW / entry.box.w;
  const scaleY = frameH / entry.box.h;
  ghost.style.backgroundSize = `${entry.img.naturalWidth * scaleX}px ${entry.img.naturalHeight * scaleY}px`;
  ghost.style.backgroundPosition = `${-entry.box.x * scaleX}px ${-entry.box.y * scaleY}px`;
  return true;
}

function makeDragGhost(element) {
  const preview = $('#preview');
  const stage = $('#stage');
  if (!stage) return null;
  const ghost = document.createElement('div');
  ghost.className = 'drag-ghost';

  // Prefer the element's own render: the lifted artwork must not contain neighbours.
  const own = elementImage(element);
  if (own?.src) {
    ghost.dataset.mode = 'element';
    ghost.__own = own;
    ghost.style.backgroundImage = `url(${own.src})`;
    ghost.style.backgroundRepeat = 'no-repeat';
    if (!paintElementGhost(ghost, element)) {
      ghost.style.backgroundSize = '100% 100%';
      ghost.style.backgroundPosition = 'center';
    }
  } else if (preview && preview.src && state.preview?.png) {
    ghost.style.backgroundImage = `url(${preview.src})`;
    ghost.style.backgroundRepeat = 'no-repeat';
    ghost.style.backgroundSize = `${stage.clientWidth}px ${stage.clientHeight}px`;
    ghost.style.backgroundPosition = `${-element.x * state.pxPerMm}px ${-element.y * state.pxPerMm}px`;
  } else {
    return null;
  }

  stage.appendChild(ghost);
  stage.classList.add('lifting');
  return ghost;
}

function paintGhost(ghost, element, drag) {
  if (!ghost || !state.spec) return;
  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;
  ghost.style.left = `${(element.x / labelWidth) * 100}%`;
  ghost.style.top = `${(element.y / labelHeight) * 100}%`;
  ghost.style.width = `${(element.w / labelWidth) * 100}%`;
  ghost.style.height = `${(element.h / labelHeight) * 100}%`;

  if (ghost.dataset.mode === 'element') {
    // Keep the artwork exactly inside the frame, also while resizing it.
    paintElementGhost(ghost, element);
    return;
  }
  if (drag && drag.mode === 'resize' && drag.ghostOrigin) {
    // stretch the crop together with the frame
    const stage = $('#stage');
    const sx = (element.w / Math.max(0.01, drag.ghostOrigin.w));
    const sy = (element.h / Math.max(0.01, drag.ghostOrigin.h));
    ghost.style.backgroundSize = `${stage.clientWidth * sx}px ${stage.clientHeight * sy}px`;
    ghost.style.backgroundPosition = `${-element.x * state.pxPerMm * sx}px ${-element.y * state.pxPerMm * sy}px`;
  }
}

function dropDragGhost(ghost) {
  if (ghost) ghost.remove();
  $('#stage')?.classList.remove('lifting');
}

/* ---------------------------------------------------------------- picking
   Element boxes sit on top of each other, so the browser's own hit test only ever
   reaches the front-most one. Picking is done here instead: every element under the
   cursor is collected back-to-front, an unfilled frame is caught on its border only
   (so whatever it frames stays reachable), and Ctrl+click walks deeper into the stack.
   The hover frame shows the element a click would take, so it is clear what will move. */

function labelPoint(clientX, clientY) {
  // Element boxes are positioned against the stage (they map 0..label size in mm).
  const surface = $('#stage') || $('#preview');
  if (!surface || !state.spec) return null;
  const rect = surface.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return null;
  return {
    x: ((clientX - rect.left) / rect.width) * state.spec.width_mm,
    y: ((clientY - rect.top) / rect.height) * state.spec.height_mm,
  };
}

function elementUnderPoint(element, x, y) {
  if (x < element.x || x > element.x + element.w || y < element.y || y > element.y + element.h) {
    return false;
  }
  // An outline frame must not swallow the artwork it surrounds: only its border answers.
  if (element.type === 'rect' && !element.fill) {
    const band = Math.max(1.6, (Number(element.thickness_mm) || 0.3) + 0.8);
    if (x > element.x + band && x < element.x + element.w - band
      && y > element.y + band && y < element.y + element.h - band) {
      return false;
    }
  }
  return true;
}

/* Every element under the cursor, front-most first. */
function elementStackAt(clientX, clientY) {
  const point = labelPoint(clientX, clientY);
  if (!point) return [];
  return state.spec.elements
    .filter((element) => elementUnderPoint(element, point.x, point.y))
    .reverse();
}

function pickFromStack(stack, deep) {
  if (!stack.length) return null;
  if (!deep) return stack[0];
  // Ctrl steps one element towards the back, wrapping to the front, so the whole stack is reachable.
  // With nothing selected the step starts from the front element — a plain click already gives that one.
  const base = state.selectedId ? stack.findIndex((element) => element.id === state.selectedId) : 0;
  return stack[((base < 0 ? 0 : base) + 1) % stack.length];
}

function deepPickKey(event) {
  return event.ctrlKey || event.metaKey;
}

function setHover(element, stack) {
  const id = element ? element.id : null;
  const overlay = $('#overlay');
  if (overlay) {
    overlay.querySelectorAll('.element-box.hover').forEach((box) => box.classList.remove('hover'));
    if (id) {
      const box = overlay.querySelector(`.element-box[data-id="${id}"]`);
      if (box) {
        // Overlapping elements: the counter tells how deep the frame is (Ctrl+click goes deeper).
        const depth = stack && stack.length > 1 ? ` \u00b7 ${stack.indexOf(element) + 1}/${stack.length}` : '';
        box.dataset.name = `${elementTitle(element) || element.type}${depth}`;
        box.classList.add('hover');
      }
    }
    $('#stage')?.classList.toggle('picking', !!id);
  }
  state.hoverId = id;
}

function isStageTarget(target) {
  return !!target?.closest?.('.element-box')
    || ['stage', 'preview', 'grid-layer', 'guides', 'overlay'].includes(target?.id);
}

function updateHover(event) {
  if (drag || modalStack.length) return;
  if (!isStageTarget(event.target)) { setHover(null); return; }
  const stack = elementStackAt(event.clientX, event.clientY);
  setHover(pickFromStack(stack, deepPickKey(event)), stack);
}

function onPointerDown(event) {
  if (event.button !== 0) return;
  const box = event.target.closest('.element-box');
  if (!box) {
    if (event.target.id === 'stage' || event.target.id === 'preview' || event.target.id === 'grid-layer' || event.target.id === 'guides') {
      state.selectedId = null;
      renderOverlay();
      buildInspector();
    }
    return;
  }
  // Ctrl+click reaches the element underneath the one in front; the hover frame already shows it.
  const stack = isStageTarget(event.target) ? elementStackAt(event.clientX, event.clientY) : [];
  const picked = pickFromStack(stack, deepPickKey(event));
  const element = picked
    || state.spec.elements.find((item) => item.id === box.dataset.id);
  if (!element) return;
  if (isMirrorLocked(element)) {
    toast('Mirror: this region is generated automatically — edit the first region.');
    return;
  }
  state.selectedId = element.id;
  setHover(null);
  renderOverlay();
  buildInspector();
  if (CODE_TYPES.has(element.type)) syncCodeLimit(element);
  pushHistory();

  const freshBox = $('#overlay').querySelector(`.element-box[data-id="${element.id}"]`);
  const rect = (freshBox || box).getBoundingClientRect();
  // Resize needs a corner grab on a box that is actually big enough to have a corner —
  // thin elements (lines, 1 mm strips) stay draggable everywhere.
  const nearCorner = event.clientX >= rect.right - 14 && event.clientY >= rect.bottom - 14;
  const bigEnough = rect.width >= 26 && rect.height >= 18;
  drag = {
    mode: nearCorner && bigEnough ? 'resize' : 'move',
    element,
    box: freshBox,
    origin: { x: element.x, y: element.y, w: element.w, h: element.h },
    startX: event.clientX,
    startY: event.clientY,
    targets: alignmentTargets(element.id),
    moved: false,
  };
  if (drag.box) {
    drag.box.classList.add('dragging');
    try { drag.box.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
  }
  drag.ghost = makeDragGhost(element);
  if (drag.ghost) {
    drag.ghostOrigin = { w: element.w, h: element.h };
    paintGhost(drag.ghost, element, drag);
  }
  state.dragging = true;
  event.preventDefault();
}

function onPointerMove(event) {
  if (!drag) return;
  const element = drag.element;
  const { width_mm: labelWidth } = state.spec;
  const band = editBand();
  const free = event.altKey;
  const tolerance = GUIDE_TOLERANCE_PX / Math.max(1, state.pxPerMm);
  let dx = (event.clientX - drag.startX) / state.pxPerMm;
  let dy = (event.clientY - drag.startY) / state.pxPerMm;
  if (event.shiftKey) {
    if (Math.abs(dx) > Math.abs(dy)) dy = 0;
    else dx = 0;
  }
  if (Math.abs(dx) > 0.01 || Math.abs(dy) > 0.01) drag.moved = true;

  let vertical = null;
  let horizontal = null;

  if (drag.mode === 'move') {
    let x = snapToStep(drag.origin.x + dx, free);
    let y = snapToStep(drag.origin.y + dy, free);
    vertical = nearestGuide([{ value: x }, { value: x + element.w / 2 }, { value: x + element.w }], drag.targets.xs, tolerance);
    horizontal = nearestGuide([{ value: y }, { value: y + element.h / 2 }, { value: y + element.h }], drag.targets.ys, tolerance);
    if (vertical && state.snapToGrid && !free) x += vertical.delta;
    if (horizontal && state.snapToGrid && !free) y += horizontal.delta;
    element.x = round2(Math.min(labelWidth - element.w, Math.max(0, x)));
    element.y = round2(Math.min(Math.max(0, band.height - element.h), Math.max(0, y)));
  } else {
    let w = Math.max(2, snapToStep(drag.origin.w + dx, free));
    let h = Math.max(2, snapToStep(drag.origin.h + dy, free));
    vertical = nearestGuide([{ value: element.x + w }], drag.targets.xs, tolerance);
    horizontal = nearestGuide([{ value: element.y + h }], drag.targets.ys, tolerance);
    if (vertical && state.snapToGrid && !free) w += vertical.delta;
    if (horizontal && state.snapToGrid && !free) h += horizontal.delta;
    const limited = clampCodeSize(element, w, h);
    if (limited.limited) {
      // Blocks shrinking past the point where the modules stop being printable.
      w = limited.w;
      h = limited.h;
      drag.limitHit = true;
    } else if (drag.limitHit) {
      drag.limitHit = false;
    }
    element.w = round2(Math.max(2, Math.min(labelWidth - element.x, w)));
    element.h = round2(Math.max(2, Math.min(band.height - element.y, h)));
    paintCodeLimitHint(element);
  }

  paintBox(drag.box, element);
  paintGhost(drag.ghost, element, drag);
  paintGuides(vertical, horizontal);
  updateGeometryFields();
  schedulePreview();
}

function onPointerUp() {
  if (!drag) return;
  if (drag.box) drag.box.classList.remove('dragging');
  dropDragGhost(drag.ghost);
  const moved = drag.moved;
  drag = null;
  state.dragging = false;
  paintGuides(null, null);
  renderOverlay();
  if (moved) {
    buildInspector();
    layoutFloatingToolbar();
    if (state.previewPending) {
      state.previewPending = false;
      schedulePreview();
    }
  }
}

/* The language switcher lives in i18n.js; the choice is remembered in the database. */
document.addEventListener('niibot:language-changed', (event) => {
  if (event.detail?.persist === false) return;
  const code = event.detail?.code;
  if (code) writePref('language', code);
});

/* ------------------------------------------------------- context menu (status bar) */
function showContextMenu(event, items) {
  document.querySelectorAll('.ctx-menu').forEach((node) => node.remove());
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.innerHTML = items.map((item) => (item.sep
    ? '<div class="ctx-sep"></div>'
    : `<button type="button" data-key="${escapeHtml(item.key)}"${item.disabled ? ' disabled' : ''}>
         <i data-lucide="${item.icon || 'circle'}"></i>
         <span>${escapeHtml(item.label)}</span>
         ${item.hint ? `<em>${escapeHtml(item.hint)}</em>` : ''}
       </button>`)).join('');
  document.body.appendChild(menu);
  refreshIcons(menu);
  const rect = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(6, Math.min(window.innerWidth - rect.width - 8, event.clientX))}px`;
  menu.style.top = `${Math.max(6, event.clientY - rect.height - 6)}px`;

  const close = () => {
    menu.remove();
    document.removeEventListener('pointerdown', outside, true);
  };
  const outside = (event_) => { if (!menu.contains(event_.target)) close(); };
  document.addEventListener('pointerdown', outside, true);
  menu.onclick = async (event_) => {
    const button = event_.target.closest('button[data-key]');
    if (!button) return;
    const item = items.find((entry) => entry.key === button.dataset.key);
    close();
    if (item && item.run) await item.run();
  };
  return menu;
}

/* Right-click on the connection chip: pick the printer to print on. Every printer keeps its
   own labels, templates and calibrations, so switching also switches the library. */
async function openPrinterSwitcher(event) {
  // No scan here: every printer the app knows is either already connected (the background loop
  // keeps them alive) or listed as known — so the menu opens at once and switching is instant.
  let payload = { printers: state.printers || [], current: null };
  try {
    payload = await api(`/api/printers${state.showAllBle ? '?all=true' : ''}`);
    state.printers = payload.printers || [];
    state.printerInventory = payload;
  } catch (_) { /* fall back to what we already know */ }
  const printers = payload.printers || [];
  const items = printers.map((printer) => {
    const connected = !!printer.connected;
    const active = !!printer.active;
    return {
      key: printer.address,
      icon: active ? 'bluetooth-connected' : connected ? 'bluetooth' : printer.available ? 'signal' : 'bluetooth-off',
      label: `${printer.model_name || printer.name || printer.address}${active ? ' ✓' : ''}`,
      hint: [
        PROTOCOL_LABELS[printer.protocol] || printer.protocol,
        active ? 'printing now' : connected ? 'connected — click to switch' : printer.available ? 'in range — click to connect' : 'known, not in range',
        !connected && printer.last_error ? printer.last_error : '',
      ].filter(Boolean).join(' · '),
      disabled: active,
      run: () => (connected
        ? activatePrinter(printer)
        : connect(printer.address, 'auto', printer.model_name || printer.name || printer.address)),
    };
  });
  if (printers.length) items.push({ sep: true });
  items.push({
    key: 'connect-all',
    icon: 'plug',
    label: 'Connect all printers in range',
    run: () => connectAllPrinters(),
  });
  items.push({ key: 'scan', icon: 'search', label: 'Scan for printers…', run: () => { openSettings('printer'); } });
  showContextMenu(event, items);
}

/** Switch to a printer that is already connected — no Bluetooth work, so it is instant. */
async function activatePrinter(printer) {
  const label = printer.model_name || printer.name || printer.address;
  const spinner = toast(`Switching to ${label}…`, 'info', 0);
  const started = performance.now();
  try {
    const payload = await api('/api/activate', 'POST', { address: printer.address });
    applyState(payload);
    const ms = Math.round(performance.now() - started);
    spinner?.remove();
    toast(`Now printing on ${label} (${ms} ms)`, 'ok');
  } catch (error) {
    spinner?.remove();
    toast(`Could not switch to ${label}: ${error.message}`, 'error');
  }
}

async function connectAllPrinters() {
  const spinner = toast('Connecting every printer in range…', 'info', 0);
  try {
    const payload = await api('/api/auto-connect', 'POST');
    applyState(payload);
    const count = (payload.connections || []).length;
    spinner?.remove();
    toast(`${count} printer${count === 1 ? '' : 's'} connected`, 'ok');
  } catch (error) {
    spinner?.remove();
    toast(error.message, 'error');
  }
}

/* =============================================================== status bar */
/** The connection chip alone — also refreshed while disconnected, so the auto-connect
    status is visible without re-rendering the whole panel every few seconds. */
function updateConnectionChip(payload = state.printer || {}) {
  const connBox = $('#sb-conn');
  const connText = $('#sb-conn-text');
  if (!connBox || !connText) return;
  const info = payload.info || {};
  const title = 'Connection — right-click to choose the printer (each printer keeps its own labels, templates and calibrations)';
  const connections = payload.connections || [];
  const others = connections.filter((connection) => !connection.active);
  if (state.connected) {
    connBox.classList.add('ok');
    connBox.classList.remove('err');
    const protocol = payload.protocol === 'luckprinter' ? 'LuckPrinter' : 'Niimbot';
    const alsoOn = others.length
      ? ` · +${others.length} connected (${others.map((c) => c.model_name || c.name).join(', ')})`
      : '';
    connText.textContent = `${info.model_name || 'printer'} · ${protocol} · ${payload.link?.name || payload.link?.address || ''}${alsoOn}`.trim();
    connBox.title = [
      title,
      connections.length > 1 ? `connected: ${connections.length}` : '',
      ...connections.map((c) => `${c.active ? '▶' : '·'} ${c.model_name || c.name} (${c.address})`),
    ].filter(Boolean).join('\n');
    return;
  }
  connBox.classList.remove('ok');
  connBox.classList.add('err');
  const auto = payload.auto_connect || {};
  const remembered = payload.last_name || payload.last_address || '';
  const busy = auto.enabled && auto.status && !/^(idle|not connected)/i.test(auto.status);
  connText.textContent = busy ? `not connected · ${auto.status}` : 'not connected';
  connBox.title = [
    title,
    remembered ? `remembered: ${remembered}` : '',
    auto.enabled ? 'auto-connect is on' : 'auto-connect is off',
    auto.status ? `status: ${auto.status}` : '',
  ].filter(Boolean).join('\n');
}

function renderStatusBar() {
  const payload = state.printer || {};
  const paper = payload.paper || {};
  const info = payload.info || {};

  updateConnectionChip(payload);

  const paperBits = [];
  if (paper.lid_closed === false) paperBits.push('lid open');
  if (paper.paper_inserted === false) paperBits.push('no paper');
  if (paper.barcode) paperBits.push(`RFID ${paper.barcode}`);
  if (paper.label_type_name) paperBits.push(paper.label_type_name);
  if (paper.remaining !== null && paper.remaining !== undefined) paperBits.push(`${paper.remaining} left`);
  else if (paper.capacity && paper.used !== null) paperBits.push(`${paper.used}/${paper.capacity}`);
  $('#sb-paper-text').textContent = paperBits.length ? paperBits.join(' · ') : (state.connected ? 'no roll data' : 'no paper');
  $('#sb-paper').classList.toggle('err', paper.lid_closed === false || paper.paper_inserted === false);

  $('#sb-format-text').textContent = state.spec
    ? `${round2(state.spec.width_mm)} × ${round2(state.spec.height_mm)} mm`
    : '—';

  const temp = $('#sb-temp');
  if (paper.temperature !== null && paper.temperature !== undefined) {
    temp.innerHTML = `<i data-lucide="thermometer"></i><span>${paper.temperature}°</span>`;
  } else {
    temp.innerHTML = '';
  }
    refreshIcons($('.statusbar'));
}

function renderJobStatus(job) {
  const box = $('#sb-job');
  if (!job) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $('#sb-progress-bar').style.width = `${Math.round((job.progress || 0) * 100)}%`;
  $('#sb-job-text').textContent = job.message || job.state;
}

/* ============================================================== new label */
function paperTemplateMatch(detected, spec) {
  if (!detected || !spec) return false;
  return Math.abs((spec.width_mm || 0) - detected.w) <= 0.6
    && Math.abs((spec.height_mm || 0) - detected.h) <= 0.6;
}

async function openNewLabelDialog() {
  const paper = state.printer?.paper || {};
  const detected = paper.detected_width_mm && paper.detected_height_mm
    ? { w: paper.detected_width_mm, h: paper.detected_height_mm }
    : (paper.catalog ? { w: paper.catalog.width_mm, h: paper.catalog.height_mm } : null);

  const templates = (state.options?.templates || []).slice();
  const body = document.createElement('div');
  body.className = 'tpl-picker';
  const paperLine = detected
    ? `<span class="tpl-paper"><i data-lucide="tag"></i> ${round2(detected.w)} × ${round2(detected.h)} mm`
      + `${paper.label_type_name ? ` · ${escapeHtml(paper.label_type_name)}` : ''}`
      + `${paper.sku || paper.catalog?.name ? ` · ${escapeHtml(paper.sku || paper.catalog.name)}` : ''}</span>`
    : '<span class="tpl-paper warn"><i data-lucide="alert-triangle"></i> No roll detected — connect the printer to filter templates</span>';

  body.innerHTML = `
    <div class="tpl-head">
      ${paperLine}
      <label class="sheet-toggle"><input type="checkbox" id="tpl-all"><span>Show all sizes</span></label>
    </div>
    <div class="tpl-grid" id="tpl-grid"></div>
  `;

  const footer = document.createElement('div');
  footer.className = 'row';
  footer.style.cssText = 'justify-content:flex-end';
  footer.innerHTML = '<button class="btn" data-tpl-cancel>Cancel</button>';

  const modal = openModal({ id: 'new-label', title: 'New label', icon: 'file-plus', width: 760, height: 620, body, footer });

  const grid = $('#tpl-grid', body);
  const showAll = $('#tpl-all', body);

  const fitModalHeight = () => {
    const titleH = $('.modal-titlebar', modal)?.offsetHeight || 0;
    const footH = $('.modal-footer', modal)?.offsetHeight || 0;
    const headH = $('.tpl-head', body)?.offsetHeight || 0;
    const bodyStyle = getComputedStyle(body);
    const bodyBox = getComputedStyle(body.closest('.modal-body') || body);
    const gridStyle = getComputedStyle(grid);
    const modalStyle = getComputedStyle(modal);
    const modalBorder = parseFloat(modalStyle.borderTopWidth) + parseFloat(modalStyle.borderBottomWidth);
    const bodyPad = parseFloat(bodyBox.paddingTop) + parseFloat(bodyBox.paddingBottom);
    const gridPad = parseFloat(gridStyle.paddingTop) + parseFloat(gridStyle.paddingBottom);
    const rowGap = parseFloat(gridStyle.rowGap) || 0;
    const pickerGap = parseFloat(bodyStyle.rowGap) || 0;
    const cards = [...grid.children].filter((node) => node.classList.contains('tpl-card'));
    const cols = Math.max(1, gridStyle.gridTemplateColumns.split(' ').filter(Boolean).length);
    const rows = Math.max(1, Math.ceil(cards.length / cols));
    const cardH = cards.length ? Math.max(...cards.map((card) => card.offsetHeight)) : 160;
    const gridH = rows * cardH + (rows - 1) * rowGap + gridPad;
    const wanted = Math.ceil(titleH + headH + footH + bodyPad + pickerGap + gridH + modalBorder);
    modal.style.height = `${Math.min(Math.max(wanted, 300), Math.min(680, window.innerHeight - 60))}px`;
    modal.style.top = `${Math.max(16, Math.round((window.innerHeight - wanted) / 2))}px`;
  };

  const applyTemplate = (spec, label) => {
    const next = JSON.parse(JSON.stringify(spec));
    next.sheet = Object.assign({ columns: [], rows: [], mirror: false, mirror_flip: false }, next.sheet || {});
    setSpec(next);
    state.labelId = null;
    if (detected) {
      applySize(detected.w, detected.h, {
        gap_mm: paper.detected_gap_mm ?? next.gap_mm,
        label_type: paper.detected_label_type ?? next.label_type,
      });
    }
    closeModal('new-label');
    toast(`New label from “${label}”`, 'ok');
  };

  const cards = [
    {
      key: 'empty',
      name: 'Empty label',
      spec: { name: 'Label', width_mm: detected?.w || 40, height_mm: detected?.h || 30, gap_mm: 2, label_type: 1, density: 3, copies: 1, elements: [] },
      empty: true,
    },
    ...templates.map((template, index) => ({
      key: template.id || `tpl-${index}`,
      name: template.name || 'Template',
      spec: template.spec,
      saved: template.source === 'saved',
    })),
  ];

  const paint = () => {
    const anyMatch = cards.some((card) => !card.empty && paperTemplateMatch(detected, card.spec));
    const all = !!showAll.checked || !anyMatch;
    if (!anyMatch && !showAll.checked) showAll.checked = true;
    const visible = cards.filter((card) => all || !detected || card.empty || paperTemplateMatch(detected, card.spec));
    grid.innerHTML = visible.map((card) => `
      <button class="tpl-card" data-tpl-key="${escapeHtml(card.key)}">
        <div class="tpl-thumb" data-tpl-thumb="${escapeHtml(card.key)}"></div>
        <div class="tpl-name">${escapeHtml(card.name)}</div>
        <div class="tpl-meta">
          ${card.empty ? 'Blank canvas' : `${round2(card.spec.width_mm)} × ${round2(card.spec.height_mm)} mm`}
          ${!card.empty && detected && paperTemplateMatch(detected, card.spec) ? '<span class="tpl-badge ok">fits roll</span>' : ''}
          ${card.saved ? '<span class="tpl-badge">saved</span>' : ''}
        </div>
      </button>`).join('') || '<div class="empty">No templates match this roll — tick “Show all sizes”.</div>';

    $$('.tpl-card', grid).forEach((button) => {
      const card = visible.find((item) => item.key === button.dataset.tplKey);
      button.onclick = () => applyTemplate(card.spec, card.name);
    });

    // previews (server-rendered, lazily)
    visible.forEach((card) => {
      const slot = $(`[data-tpl-thumb="${card.key}"]`, grid);
      if (!slot) return;
      api('/api/spec-preview', 'POST', { spec: card.spec, max_width: 240 })
        .then((result) => {
          slot.innerHTML = `<img src="data:image/png;base64,${result.png}" alt="${escapeHtml(card.name)}">`;
        })
        .catch(() => { slot.innerHTML = '<i data-lucide="file-question"></i>'; refreshIcons(slot); });
    });
    refreshIcons(grid);
    setTimeout(fitModalHeight, 0);
  };
  showAll.onchange = paint;
  $('[data-tpl-cancel]', footer).onclick = () => closeModal('new-label');
  paint();
}

/* ============================================================= icons + fonts */
const LUCIDE_VERSION = '0.544.0';
const iconLib = { ready: false, loading: null, lucide: [], mdi: [], mdiVersion: '', mdiBuilt: '', lucideVersion: LUCIDE_VERSION };

function kebabCase(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([0-9])([A-Za-z])/g, '$1-$2')
    .toLowerCase();
}

async function loadIconLibrary() {
  if (iconLib.ready) return iconLib;
  if (iconLib.loading) return iconLib.loading;
  iconLib.loading = (async () => {
    const icons = (window.lucide && window.lucide.icons) || {};
    iconLib.lucide = Object.keys(icons).map((key) => ({
      source: 'lucide',
      name: kebabCase(key),
      node: icons[key],
      search: kebabCase(key).replace(/-/g, ' '),
    }));
    try {
      const response = await fetch('/static/vendor/mdi-icons.json?v=7.4.47');
      const data = await response.json();
      const tags = data.tags || {};
      iconLib.mdiVersion = data.version || '';
      iconLib.mdiBuilt = data.built || '';
      iconLib.lucideVersion = (window.lucide && window.lucide.version) || LUCIDE_VERSION;
      iconLib.mdi = (data.icons || []).map(([name, path]) => ({
        source: 'mdi',
        name,
        path,
        search: `${name.replace(/-/g, ' ')} ${tags[name] || ''}`,
      }));
    } catch (error) {
      toast(`Could not load the Material Design Icons library: ${error.message}`, 'error');
    }
    iconLib.ready = true;
    return iconLib;
  })();
  return iconLib.loading;
}

function iconSvgMarkup(icon, extra = '') {
  if (icon.source === 'mdi') {
    return `<svg viewBox="0 0 24 24" fill="currentColor" ${extra}><path d="${icon.path}"/></svg>`;
  }
  const children = (icon.node || [])
    .map(([tag, attrs]) => {
      const serialized = Object.entries(attrs || {})
        .map(([key, value]) => `${key}="${String(value).replace(/"/g, '&quot;')}"`)
        .join(' ');
      return `<${tag} ${serialized}/>`;
    })
    .join('');
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${children}</svg>`;
}

function printheadPx() {
  return Math.max(96, Number(state.printer?.info?.printhead_px) || 384);
}

async function rasterizeIcon(icon, sizePx = printheadPx()) {
  const svg = iconSvgMarkup(icon, `xmlns="http://www.w3.org/2000/svg" width="${sizePx}" height="${sizePx}" color="#000000"`);
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  const image = new Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error('Could not render the icon'));
    image.src = url;
  });
  const canvas = document.createElement('canvas');
  canvas.width = sizePx;
  canvas.height = sizePx;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(image, 0, 0, sizePx, sizePx);
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not encode the PNG'))), 'image/png');
  });
}

async function insertIcon(icon) {
  const blob = await rasterizeIcon(icon);
  const data = new FormData();
  data.append('file', new File([blob], `${icon.name}.png`, { type: 'image/png' }));
  const response = await fetch('/api/upload', { method: 'POST', body: data });
  if (!response.ok) throw new Error('Could not upload the icon');
  const uploaded = await response.json();

  const { width_mm: labelWidth, height_mm: labelHeight } = state.spec;
  const size = Math.max(4, Math.round(Math.min(labelWidth, labelHeight) * 0.35 * 2) / 2);
  const element = {
    id: uid(),
    type: 'image',
    x: round2(Math.max(0, (labelWidth - size) / 2)),
    y: round2(Math.max(0, (labelHeight - size) / 2)),
    w: size,
    h: size,
    rotation: 0,
    visible: true,
    label: icon.name.replace(/-/g, ' '),
    image_id: uploaded.id,
    // An icon must stay whole: resizing the box letterboxes it instead of cropping it.
    image_fit: 'contain',
  };
  state.spec.elements.push(element);
  state.selectedId = element.id;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
  toast(`Icon added: ${icon.name.replace(/-/g, ' ')}`);
}

function openIconPicker() {
  const body = document.createElement('div');
  body.className = 'icon-picker';
  body.innerHTML = `
    <div class="icon-toolbar">
      <input id="icon-search" type="search" placeholder="Search icons (e.g. coffee, arrow, user…)" autocomplete="off">
      <div class="seg" id="icon-sources">
        <button data-source="all" class="active">All</button>
        <button data-source="lucide">Lucide</button>
        <button data-source="mdi">Material</button>
      </div>
      <span class="icon-count" id="icon-count">—</span>
    </div>
    <div class="icon-grid" id="icon-grid"><div class="empty">Loading the icon library…</div></div>
  `;

  // The dataset read-out lives in the modal footer so it hugs and fills the bottom edge.
  const statusBar = document.createElement('div');
  statusBar.className = 'icon-status modal-status';
  statusBar.id = 'icon-status';

  openModal({
    id: 'icons',
    title: 'Icons & cliparts',
    icon: 'shapes',
    width: Math.min(1520, Math.max(760, window.innerWidth - 96)),
    height: Math.min(1000, Math.max(520, window.innerHeight - 96)),
    body,
    footer: statusBar,
  });

  const grid = $('#icon-grid', body);
  const search = $('#icon-search', body);
  const countLabel = $('#icon-count', body);
  const state_ = { source: 'all', query: '', shown: 0, results: [] };
  const PAGE = 200;

  const paintStatus = () => {
    const lucide = iconLib.lucide.length;
    const mdi = iconLib.mdi.length;
    const total = lucide + mdi;
    const shown = state_.results.length;
    const filtered = state_.query.trim() || state_.source !== 'all';
    statusBar.innerHTML = `
      <span class="is-item"><i data-lucide="feather"></i> Lucide <b>${lucide.toLocaleString('pl-PL')}</b> <span class="is-dim">v${escapeHtml(iconLib.lucideVersion || '—')}</span></span>
      <span class="is-sep"></span>
      <span class="is-item"><i data-lucide="shapes"></i> Material Design <b>${mdi.toLocaleString('pl-PL')}</b> <span class="is-dim">v${escapeHtml(iconLib.mdiVersion || '—')}</span></span>
      <span class="is-sep"></span>
      <span class="is-item"><i data-lucide="layers"></i> total <b>${total.toLocaleString('pl-PL')}</b></span>
      ${filtered ? `<span class="is-sep"></span><span class="is-item is-accent">filtered <b>${shown.toLocaleString('pl-PL')}</b></span>` : ''}
      <span class="is-spacer"></span>
      <span class="is-item is-dim">dataset: ${escapeHtml(iconLib.mdiBuilt || '—')}</span>
    `;
    refreshIcons(statusBar);
  };

  const paint = () => {
    const query = state_.query.trim().toLowerCase();
    const pool = [];
    if (state_.source !== 'mdi') pool.push(...iconLib.lucide);
    if (state_.source !== 'lucide') pool.push(...iconLib.mdi);
    state_.results = query
      ? pool.filter((icon) => icon.search.includes(query) || icon.name.includes(query))
      : pool;
    countLabel.textContent = `${state_.results.length} icons`;
    grid.innerHTML = '';
    state_.shown = 0;
    appendPage();
    paintStatus();
  };

  const appendPage = () => {
    const slice = state_.results.slice(state_.shown, state_.shown + PAGE);
    if (!slice.length) {
      if (!state_.results.length) grid.innerHTML = '<div class="empty">No results</div>';
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const icon of slice) {
      const cell = document.createElement('button');
      cell.className = 'icon-cell';
      cell.dataset.name = icon.name;
      cell.title = `${icon.name}  (${icon.source === 'mdi' ? 'Material' : 'Lucide'})`;
      cell.innerHTML = `${iconSvgMarkup(icon)}<span>${escapeHtml(icon.name.replace(/-/g, ' '))}</span>`;
      cell.onclick = async () => {
        cell.disabled = true;
        try {
          await insertIcon(icon);
          closeModal('icons');
        } catch (error) {
          toast(error.message, 'error');
          cell.disabled = false;
        }
      };
      fragment.appendChild(cell);
    }
    grid.appendChild(fragment);
    state_.shown += slice.length;
  };

  grid.addEventListener('scroll', () => {
    if (grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 120) appendPage();
  });

  let timer = null;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state_.query = search.value; paint(); }, 120);
  });

  $$('#icon-sources button', body).forEach((button) => {
    button.onclick = () => {
      state_.source = button.dataset.source;
      $$('#icon-sources button', body).forEach((entry) => entry.classList.toggle('active', entry === button));
      paint();
    };
  });

  loadIconLibrary()
    .then(() => paint())
    .catch((error) => toast(error.message, 'error'));

  paintStatus();
  setTimeout(() => search.focus(), 60);
}

function fontCss(family) {
  return `'${String(family).replace(/'/g, "\\'")}', sans-serif`;
}

function openFontPicker(element) {
  const catalog = state.options?.font_catalog?.length
    ? state.options.font_catalog
    : (state.options?.fonts || ['Arial']).map((name) => ({ name, styles: ['regular'], curated: true }));

  const body = document.createElement('div');
  body.className = 'font-picker';
  body.innerHTML = `
    <div class="icon-toolbar">
      <input id="font-search" type="search" placeholder="Search fonts…" autocomplete="off">
      <span class="icon-count" id="font-count">${catalog.length} families</span>
    </div>
    <div class="font-list" id="font-list"></div>
  `;

  // The hint is a status bar, so it sits in the footer and fills the bottom edge.
  const statusBar = document.createElement('div');
  statusBar.className = 'font-hint modal-status';
  statusBar.innerHTML = 'Drop your own .ttf/.otf files into <code>data\\fonts</code> — they appear here after a refresh.';

  openModal({ id: 'fonts', title: 'Fonts', icon: 'type', width: 620, height: 560, body, footer: statusBar });

  const list = $('#font-list', body);
  const search = $('#font-search', body);

  const paint = (query = '') => {
    const needle = query.trim().toLowerCase();
    const items = catalog.filter((entry) => !needle || entry.name.toLowerCase().includes(needle));
    $('#font-count', body).textContent = `${items.length} families`;
    list.innerHTML = items.map((entry) => {
      const selected = entry.name === element.font;
      const badges = ['bold', 'italic', 'bold-italic']
        .filter((style) => (entry.styles || []).includes(style))
        .map((style) => (style === 'bold' ? 'B' : style === 'italic' ? 'I' : 'BI'))
        .join(' ');
      return `<button class="font-row${selected ? ' selected' : ''}" data-font="${escapeHtml(entry.name)}">
        <span class="font-sample" style="font-family:${fontCss(entry.name)}">Aa Bb Cc 123</span>
        <span class="font-name">${escapeHtml(entry.name)}</span>
        <span class="font-styles">${badges}</span>
      </button>`;
    }).join('') || '<div class="empty">No results</div>';

    $$('.font-row', list).forEach((row) => {
      row.onclick = () => {
        element.font = row.dataset.font;
        pushHistory();
        renderOverlay();
        buildInspector();
        schedulePreview();
        closeModal('fonts');
      };
    });
  };

  paint();
  search.addEventListener('input', () => paint(search.value));
  setTimeout(() => search.focus(), 60);
}

/* =============================================================== sheet view */
const VALUE_TYPES = new Set(['text', 'barcode', 'qr']);
const TYPE_NAMES = { text: 'Text', barcode: 'Barcode', qr: 'QR', image: 'Image', rect: 'Frame', line: 'Line' };

function sheetRegions() {
  const geo = paperGeometry();
  return Math.max(1, Number(geo.rows || 1));
}

function profileCells() {
  const cells = state.printer?.profile?.regions || [];
  return cells.filter((cell) => Number(cell.w) > 0.5 && Number(cell.h) > 0.5);
}

function mirrorRequest() {
  const sheet = state.spec?.sheet;
  const cells = profileCells();
  if (sheet?.cells_mirror && cells.length > 1) {
    return {
      regions: cells.length,
      flip: !!sheet.mirror_flip,
      cells: cells.map((cell) => ({
        shape: cell.shape || 'rect',
        x: Number(cell.x) || 0,
        y: Number(cell.y) || 0,
        w: Number(cell.w),
        h: Number(cell.h),
      })),
    };
  }
  const regions = sheetRegions();
  if (!sheet?.mirror || regions < 2) return null;
  return { regions, flip: !!sheet.mirror_flip };
}

/* With calibrated cells the user can design in cell 1 only and let the rest be filled. */
function toggleCellsMirror() {
  if (!state.spec) return;
  const sheet = state.spec.sheet || (state.spec.sheet = { columns: [], rows: [], mirror: false, mirror_flip: false });
  sheet.cells_mirror = !sheet.cells_mirror;
  pushHistory();
  renderStageMeta();
  renderOverlay();
  renderPaperRegions();
  schedulePreview();
  const cells = profileCells();
  if (!sheet.cells_mirror) {
    toast('Mirror cells off — every cell is printed as designed');
    return;
  }
  const first = cells[0];
  const outside = state.spec.elements.filter((element) => element.visible !== false && (
    element.x + element.w / 2 < Number(first.x)
    || element.x + element.w / 2 > Number(first.x) + Number(first.w)
    || element.y + element.h / 2 < Number(first.y)
    || element.y + element.h / 2 > Number(first.y) + Number(first.h)
  ));
  if (outside.length) {
    toast(`Mirror cells on — but ${outside.length} element(s) sit outside cell 1, so only what is inside cell 1 is repeated.`, 'error');
  } else {
    toast(`Mirror cells on — design inside cell 1, the other ${cells.length - 1} will match`, 'ok');
  }
}

function valueElements() {
  return state.spec.elements.filter((element) => VALUE_TYPES.has(element.type));
}

function syncSheetColumns() {
  const sheet = state.spec.sheet;
  const byElement = new Map(sheet.columns.map((column) => [column.element_id, column]));
  const columns = valueElements().map((element) => {
    const existing = byElement.get(element.id);
    if (existing) return existing;
    return {
      id: uid(),
      element_id: element.id,
      name: element.type === 'text'
        ? (element.text || 'Tekst').split('\n')[0].slice(0, 24) || 'Tekst'
        : element.type === 'barcode' ? 'Kod kreskowy' : 'Kod QR',
    };
  });
  sheet.columns = columns;
  return columns;
}

function switchView(view) {
  state.view = view;
  applySplit();
  $$('.editor-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.view === view));
  if (view === 'sheet') {
    renderSheet();
  } else {
    updateFloatingToolbar();
    layoutStage();
  }
}

function applySplit() {
  const isSheet = state.view === 'sheet';
  const split = isSheet && state.sheetHeight > 0;
  $('#splitter-sheet').hidden = !isSheet;
  $('#sheet-pane').hidden = !isSheet;
  $('#canvas-wrap').hidden = isSheet && !split;
  $('.stage-meta').hidden = isSheet && !split;
  $('#warnings').hidden = isSheet;
  const pane = $('#sheet-pane');
  pane.style.height = split ? `${state.sheetHeight}px` : '';
  pane.style.flex = split ? '0 0 auto' : '';
  if (split) {
    state.dragging = false;
    layoutStage();
  }
}

function bindSplitters() {
  const panelWidth = Number(readPref('panelWidth'));
  if (panelWidth >= 240 && panelWidth <= 640) {
    document.documentElement.style.setProperty('--panel-w', `${panelWidth}px`);
  }
  const sheetHeight = Number(readPref('sheetHeight'));
  if (sheetHeight > 120) state.sheetHeight = sheetHeight;

  const resizer = $('#panel-resizer');
  resizer.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--panel-w'), 10) || 320;
    resizer.classList.add('dragging');
    try { resizer.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
    const move = (moveEvent) => {
      const width = Math.max(240, Math.min(620, startWidth - (moveEvent.clientX - startX)));
      document.documentElement.style.setProperty('--panel-w', `${width}px`);
      layoutStage();
      layoutFloatingToolbar();
    };
    const up = () => {
      resizer.classList.remove('dragging');
      resizer.removeEventListener('pointermove', move);
      resizer.removeEventListener('pointerup', up);
      const width = getComputedStyle(document.documentElement).getPropertyValue('--panel-w');
      writePref('panelWidth', parseInt(width, 10));
    };
    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', up);
  });

  const sheetSplitter = $('#splitter-sheet');
  const setSheetHeight = (height) => {
    state.sheetHeight = height;
    writePref('sheetHeight', height);
    applySplit();
  };
  sheetSplitter.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    const editor = $('.editor').getBoundingClientRect();
    sheetSplitter.classList.add('dragging');
    try { sheetSplitter.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
    const move = (moveEvent) => {
      const raw = editor.bottom - moveEvent.clientY;
      const height = Math.max(0, Math.min(editor.height - 140, raw));
      state.sheetHeight = height < 120 ? 0 : Math.round(height);
      applySplit();
    };
    const up = () => {
      sheetSplitter.classList.remove('dragging');
      sheetSplitter.removeEventListener('pointermove', move);
      sheetSplitter.removeEventListener('pointerup', up);
      writePref('sheetHeight', state.sheetHeight);
    };
    sheetSplitter.addEventListener('pointermove', move);
    sheetSplitter.addEventListener('pointerup', up);
  });
  sheetSplitter.addEventListener('dblclick', () => setSheetHeight(0));
}

function renderSheet() {
  const table = $('#sheet-table');
  if (!table || !state.spec) return;
  const sheet = state.spec.sheet;
  const columns = syncSheetColumns();
  const regions = sheetRegions();
  const elements = valueElements();

  const headCells = columns.map((column, index) => {
    const element = state.spec.elements.find((item) => item.id === column.element_id);
    const type = element ? element.type : 'text';
    const typeName = TYPE_NAMES[type] || type;
    return `<th class="sheet-col" draggable="true" data-col-index="${index}">
      <div class="col-head">
        <span class="col-type" title="${escapeHtml(typeName)} · ${escapeHtml(element ? elementTitle(element) : '')}">
          <i data-lucide="${TYPE_ICONS[type] || 'type'}"></i>
        </span>
        <span class="col-title" data-col-title="${column.id}" title="Double-click to rename, drag to reorder">${escapeHtml(column.name)}</span>
        <button class="col-del" data-col-del="${column.id}" title="Remove column from the sheet"><i data-lucide="x"></i></button>
      </div>
    </th>`;
  }).join('');

  const currentCells = columns.map((column) => {
    const element = state.spec.elements.find((item) => item.id === column.element_id);
    return `<td><input class="cell-value" data-cell-current="${column.id}" value="${escapeHtml(element ? element.text || '' : '')}"></td>`;
  }).join('');

  const rows = sheet.rows.map((row, index) => {
    const cells = columns.map((column) => `<td><input class="cell-value" data-cell-row="${index}" data-cell-col="${column.id}" value="${escapeHtml(row[column.id] ?? '')}"></td>`).join('');
    return `<tr class="sheet-row" data-row="${index}">
      <th class="row-head"><span class="row-num">${index + 2}</span>
        <button class="row-del" data-row-del="${index}" title="Delete row"><i data-lucide="x"></i></button>
      </th>
      ${cells}
    </tr>`;
  }).join('');

  table.innerHTML = `
    <thead><tr><th class="row-head sheet-corner"><i data-lucide="hash"></i></th>${headCells}</tr></thead>
    <tbody>
      <tr class="sheet-row current">
        <th class="row-head"><span class="row-num current">1</span></th>
        ${currentCells}
      </tr>
      ${rows}
    </tbody>`;

  const mirrorBox = $('#sheet-mirror');
  mirrorBox.checked = !!sheet.mirror;
  mirrorBox.disabled = regions < 2;
  $('#sheet-flip').checked = !!sheet.mirror_flip;
  $('#sheet-flip').disabled = regions < 2 || !sheet.mirror;
  $('#sheet-mirror-wrap').classList.toggle('disabled', regions < 2);

  const total = (sheet.rows.length + 1) * Math.max(1, state.spec.copies || 1);
  $('#sheet-print').innerHTML = `<i data-lucide="printer"></i> Print sheet (${total})`;
  $('#sheet-hint').textContent = regions > 1
    ? `This roll has ${regions} regions per template. Mirror: edit the first one only, the print fills the rest${sheet.mirror ? ' — on' : ''} (${regions} × per row).`
    : 'Row 1 is the current label — editing it updates the design right away. The other rows print in order.';
  refreshIcons(table);
  refreshIcons($('#sheet-toolbar') || document);

  // sorting: column list in the toolbar
  const sortSelect = $('#sheet-sort');
  const previousSort = sortSelect.value;
  sortSelect.innerHTML = '<option value="">Sort…</option>'
    + columns.map((column) => `<option value="${column.id}">${escapeHtml(column.name)}</option>`).join('');
  sortSelect.value = columns.some((column) => column.id === previousSort) ? previousSort : '';

  // drag & drop column order
  $$('.sheet-col', table).forEach((th) => {
    th.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('text/plain', String(Number(th.dataset.colIndex)));
      event.dataTransfer.effectAllowed = 'move';
      th.classList.add('dragging');
    });
    th.addEventListener('dragend', () => th.classList.remove('dragging'));
    th.addEventListener('dragover', (event) => { event.preventDefault(); th.classList.add('drop-target'); });
    th.addEventListener('dragleave', () => th.classList.remove('drop-target'));
    th.addEventListener('drop', (event) => {
      event.preventDefault();
      th.classList.remove('drop-target');
      const from = Number(event.dataTransfer.getData('text/plain'));
      const to = Number(th.dataset.colIndex);
      if (Number.isFinite(from) && Number.isFinite(to) && from !== to) moveSheetColumn(from, to);
    });

    // double-click on the name — inline rename
    const title = $('.col-title', th);
    title.addEventListener('dblclick', () => {
      const column = state.spec.sheet.columns.find((item) => item.id === title.dataset.colTitle);
      if (!column) return;
      const input = document.createElement('input');
      input.className = 'col-name';
      input.value = column.name;
      title.replaceWith(input);
      input.focus();
      input.select();
      const commit = () => {
        const value = input.value.trim();
        if (value) column.name = value;
        pushHistory();
        renderSheet();
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); input.blur(); }
        if (event.key === 'Escape') { event.preventDefault(); renderSheet(); }
      });
    });
  });
}

function moveSheetColumn(fromIndex, toIndex) {
  const columns = state.spec.sheet.columns;
  if (fromIndex < 0 || fromIndex >= columns.length || toIndex < 0 || toIndex >= columns.length) return;
  const [moved] = columns.splice(fromIndex, 1);
  columns.splice(toIndex, 0, moved);
  pushHistory();
  renderSheet();
}

function sortSheetRows(columnId, direction) {
  const sheet = state.spec.sheet;
  if (!columnId) return;
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  sheet.rows.sort((left, right) => {
    const a = (left[columnId] ?? '').toString();
    const b = (right[columnId] ?? '').toString();
    return direction === 'desc' ? collator.compare(b, a) : collator.compare(a, b);
  });
  pushHistory();
  renderSheet();
}

function readSheetColumns() {
  return state.spec.sheet.columns;
}

function removeSheetColumn(columnId) {
  const sheet = state.spec.sheet;
  sheet.columns = sheet.columns.filter((column) => column.id !== columnId);
  sheet.rows.forEach((row) => { delete row[columnId]; });
}

async function importSheetFile(file) {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch('/api/sheet/import', { method: 'POST', body: form });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  const data = await response.json();
  const sheet = state.spec.sheet;
  syncSheetColumns();

  const byName = new Map(sheet.columns.map((column) => [column.name.trim().toLowerCase(), column]));
  const header = (data.header || []).map((cell, index) => (cell || `Column ${index + 1}`).toString());
  let skipped = 0;

  const mapping = header.map((name) => {
    const existing = byName.get(name.trim().toLowerCase());
    if (existing) return existing.id;
    const bound = new Set(sheet.columns.map((column) => column.element_id));
    const free = valueElements().find((element) => !bound.has(element.id));
    if (!free) {
      skipped += 1;
      return null;
    }
    const column = { id: uid(), element_id: free.id, name: name.trim() };
    sheet.columns.push(column);
    byName.set(name.trim().toLowerCase(), column);
    return column.id;
  });

  sheet.rows = (data.rows || []).map((row) => {
    const record = {};
    mapping.forEach((columnId, index) => {
      if (columnId) record[columnId] = (row[index] ?? '').toString();
    });
    return record;
  });

  pushHistory();
  renderSheet();
  schedulePreview();
  toast(
    skipped
      ? `Imported ${data.rows_count} rows — ${skipped} column(s) skipped (no free element to bind)`
      : `Imported ${data.rows_count} rows from ${file.name}`,
    skipped ? 'error' : 'ok',
  );
}

function addSheetColumn() {  const sheet = state.spec.sheet;
  const bound = new Set(sheet.columns.map((column) => column.element_id));
  const next = valueElements().find((element) => !bound.has(element.id));
  if (!next) {
    toast('Every text element is already a column — re-bind a column in the header instead.', 'error');
    return;
  }
  sheet.columns.push({
    id: uid(),
    element_id: next.id,
    name: next.type === 'text' ? (next.text || 'Tekst').split('\n')[0].slice(0, 24) : 'Kod',
  });
  pushHistory();
  renderSheet();
}

function bindSheetControls() {
  $$('.editor-tab').forEach((tab) => {
    tab.onclick = () => switchView(tab.dataset.view);
  });

  $('#sheet-add-row').onclick = () => {
    state.spec.sheet.rows.push({});
    pushHistory();
    renderSheet();
  };
  $('#sheet-add-column').onclick = () => addSheetColumn();
  $('#sheet-clear').onclick = () => {
    if (!state.spec.sheet.rows.length) return;
    state.spec.sheet.rows = [];
    pushHistory();
    renderSheet();
  };
  $('#sheet-print').onclick = () => printSheet();
  $('#sheet-sort').onchange = (event) => {
    const columnId = event.target.value;
    if (columnId) sortSheetRows(columnId, state.sheetSortDir || 'asc');
  };
  $('#sheet-sort-dir').onclick = () => {
    state.sheetSortDir = (state.sheetSortDir || 'asc') === 'asc' ? 'desc' : 'asc';
    const button = $('#sheet-sort-dir');
    button.innerHTML = `<i data-lucide="${state.sheetSortDir === 'asc' ? 'arrow-down-a-z' : 'arrow-up-z-a'}"></i>`;
    refreshIcons(button);
    const columnId = $('#sheet-sort').value;
    if (columnId) sortSheetRows(columnId, state.sheetSortDir);
  };
  $('#sheet-import').onclick = () => $('#sheet-file').click();
  $('#sheet-file').onchange = async (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    try {
      await importSheetFile(file);
    } catch (error) {
      toast(error.message, 'error');
    }
  };
  $('#sheet-mirror').onchange = (event) => {
    state.spec.sheet.mirror = event.target.checked;
    if (!state.spec.sheet.mirror) state.spec.sheet.mirror_flip = false;
    pushHistory();
    renderSheet();
    schedulePreview();
  };
  $('#sheet-flip').onchange = (event) => {
    state.spec.sheet.mirror_flip = event.target.checked;
    pushHistory();
    schedulePreview();
  };

  const table = $('#sheet-table');
  table.addEventListener('focusin', (event) => {
    if (event.target.matches('input')) pushHistory();
  });
  table.addEventListener('input', (event) => {
    const input = event.target;
    if (input.matches('[data-cell-current]')) {
      paintCellToElement(input.dataset.cellCurrent, input.value);
      renderOverlay();
      schedulePreview();
    } else if (input.matches('[data-cell-row]')) {
      const row = state.spec.sheet.rows[Number(input.dataset.cellRow)];
      if (row) row[input.dataset.cellCol] = input.value;
    } else if (input.matches('.col-name')) {
      const column = state.spec.sheet.columns.find((item) => item.id === input.dataset.col);
      if (column) column.name = input.value;
    }
  });
  table.addEventListener('change', () => { /* element binding is fixed per column now */ });
  table.addEventListener('click', (event) => {
    const delCol = event.target.closest('[data-col-del]');
    if (delCol) {
      removeSheetColumn(delCol.dataset.colDel);
      pushHistory();
      renderSheet();
      return;
    }
    const delRow = event.target.closest('[data-row-del]');
    if (delRow) {
      state.spec.sheet.rows.splice(Number(delRow.dataset.rowDel), 1);
      pushHistory();
      renderSheet();
    }
  });
}

function paintCellToElement(columnId, value) {
  const column = state.spec.sheet.columns.find((item) => item.id === columnId);
  if (!column) return;
  const element = state.spec.elements.find((item) => item.id === column.element_id);
  if (!element) return;
  element.text = value;
}

function printSheet() {
  openPrintDialog({ fromSheet: true });
}

/* =================================================================== modals */
const modalStack = [];
function openModal({ id, title, icon = 'settings', width, height, body, footer, onMount }) {
  closeModal(id);
  const layer = document.createElement('div');
  layer.className = 'modal-layer';
  layer.dataset.modal = id;

  const modal = document.createElement('div');
  modal.className = 'modal';
  if (width) modal.style.width = `${width}px`;
  if (height) modal.style.height = `${height}px`;

  const offset = modalStack.length * 24;
  modal.style.left = `${Math.max(16, (window.innerWidth - (width || 760)) / 2 + offset)}px`;
  modal.style.top = `${Math.max(16, (window.innerHeight - (height || 560)) / 2 + offset)}px`;

  modal.innerHTML = `
    <div class="modal-titlebar">
      <i data-lucide="${icon}"></i>
      <span>${escapeHtml(title)}</span>
      <div class="spacer"></div>
      <button class="modal-close" data-close="${escapeHtml(id)}" title="Close"><i data-lucide="x"></i></button>
    </div>
    <div class="modal-body"></div>
    ${footer ? '<div class="modal-footer"></div>' : ''}
  `;
  const wrapper = $('.modal-body', modal);
  // A body that already owns its own padding/layout (tabs + content, scrollable content) replaces
  // the wrapper instead of nesting inside it, so the padding is not applied twice.
  if (body.classList.contains('modal-body') || body.classList.contains('modal-content')) {
    wrapper.replaceWith(body);
  } else {
    wrapper.appendChild(body);
  }
  if (footer) $('.modal-footer', modal).appendChild(footer);

  layer.appendChild(modal);
  $('#modal-root').appendChild(layer);
  refreshIcons(modal);

  // drag by title bar
  const titlebar = $('.modal-titlebar', modal);
  let dragState = null;
  titlebar.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.modal-close')) return;
    dragState = {
      startX: event.clientX,
      startY: event.clientY,
      left: parseFloat(modal.style.left) || 0,
      top: parseFloat(modal.style.top) || 0,
    };
    titlebar.setPointerCapture(event.pointerId);
  });
  titlebar.addEventListener('pointermove', (event) => {
    if (!dragState) return;
    const nextLeft = dragState.left + (event.clientX - dragState.startX);
    const nextTop = dragState.top + (event.clientY - dragState.startY);
    modal.style.left = `${Math.max(-40, Math.min(window.innerWidth - 80, nextLeft))}px`;
    modal.style.top = `${Math.max(0, Math.min(window.innerHeight - 40, nextTop))}px`;
  });
  titlebar.addEventListener('pointerup', (event) => {
    dragState = null;
    try { titlebar.releasePointerCapture(event.pointerId); } catch (_) { /* ignore */ }
  });

  layer.addEventListener('pointerdown', (event) => {
    if (event.target === layer) closeModal(id);
  });
  $('.modal-close', modal).onclick = () => closeModal(id);
  modalStack.push({ id, layer, modal });
  if (onMount) onMount(modal);
  return modal;
}

/* Shrink a modal to the height its content actually needs (compact dialogs such as Print). */
function fitModalToContent(modal, { min = 180, max = 640 } = {}) {
  const body = modal?.querySelector(':scope > .modal-body');
  if (!body) return;
  const border = (parseFloat(getComputedStyle(modal).borderTopWidth) || 0) * 2;
  const titleH = modal.querySelector('.modal-titlebar')?.offsetHeight || 0;
  const footH = modal.querySelector('.modal-footer')?.offsetHeight || 0;
  const previous = { height: body.style.height, flex: body.style.flex };
  body.style.flex = '0 0 auto';
  body.style.height = 'auto';
  const contentH = body.scrollHeight;
  body.style.height = previous.height;
  body.style.flex = previous.flex;
  const height = Math.min(
    Math.max(Math.ceil(titleH + footH + contentH + border), min),
    Math.min(max, window.innerHeight - 60),
  );
  modal.style.height = `${height}px`;
  modal.style.top = `${Math.max(16, Math.round((window.innerHeight - height) / 2))}px`;
}

function closeModal(id) {  const index = modalStack.findIndex((entry) => entry.id === id);
  if (index < 0) return;
  const [entry] = modalStack.splice(index, 1);
  entry.layer.remove();
}

function isModalOpen(id) {
  return modalStack.some((entry) => entry.id === id);
}

/** Small single-field dialog in the shared modal style; resolves to the text or null. */
function askText({ title, label, value = '', okLabel = 'Save', icon = 'pencil', hint = '' }) {
  return new Promise((resolve) => {
    const id = `ask-${uid()}`;
    const body = document.createElement('div');
    body.className = 'ask-body';
    body.innerHTML = `
      <label class="ask-label">${escapeHtml(label)}</label>
      <input class="ask-input" id="${id}-input" value="${escapeHtml(value)}" autocomplete="off">
      ${hint ? `<div class="hint" style="margin-top:6px">${escapeHtml(hint)}</div>` : ''}
    `;
    const footer = document.createElement('div');
    footer.className = 'row';
    footer.style.cssText = 'justify-content:flex-end;gap:8px';
    footer.innerHTML = `
      <button class="btn" data-ask-cancel>Cancel</button>
      <button class="btn primary" data-ask-ok>${escapeHtml(okLabel)}</button>
    `;

    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
      closeModal(id);
    };

    openModal({ id, title, icon, width: 460, height: hint ? 232 : 208, body, footer });

    const input = $(`#${id}-input`);
    input.focus();
    input.select();
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); finish(input.value.trim() || null); }
      if (event.key === 'Escape') { event.preventDefault(); finish(null); }
    });
    $('[data-ask-ok]', footer).onclick = () => finish(input.value.trim() || null);
    $('[data-ask-cancel]', footer).onclick = () => finish(null);
  });
}

/* =============================================================== settings UI */
const SETTINGS_TABS = [
  { id: 'printer', label: 'Printer', icon: 'bluetooth' },
  { id: 'paper', label: 'Paper & roll', icon: 'tag' },
  { id: 'windows', label: 'Windows printing', icon: 'monitor' },
  { id: 'catalog', label: 'Format catalogue', icon: 'database' },
  { id: 'stats', label: 'Statistics', icon: 'list' },
  { id: 'about', label: 'About', icon: 'info' },
];

let settingsTab = 'printer';

function openSettings(tab) {
  settingsTab = tab || settingsTab || 'printer';
  if (isModalOpen('settings')) {
    renderSettingsTab();
    return;
  }

  const body = document.createElement('div');
  body.className = 'modal-body flush';
  body.innerHTML = `
    <nav class="modal-tabs" id="settings-tabs">
      ${SETTINGS_TABS.map((item) => `
        <button class="modal-tab" data-tab="${item.id}">
          <i data-lucide="${item.icon}"></i><span>${item.label}</span>
        </button>`).join('')}
    </nav>
    <div class="modal-content" id="settings-content"></div>
  `;
  const footer = document.createElement('div');
  footer.innerHTML = '<span class="hint" id="settings-hint">Settings are stored in the SQLite database.</span>';

  const modal = openModal({
    id: 'settings',
    title: 'Settings',
    icon: 'settings',
    width: 820,
    height: 600,
    body,
    footer,
  });

  $$('.modal-tab', modal).forEach((button) => {
    button.onclick = () => {
      settingsTab = button.dataset.tab;
      renderSettingsTab();
    };
  });
  renderSettingsTab();
}

function renderSettingsTab() {
  const modal = modalStack.find((entry) => entry.id === 'settings');
  if (!modal) return;
  $$('.modal-tab', modal.modal).forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === settingsTab);
  });
  const content = $('#settings-content', modal.modal);
  const renderers = {
    printer: renderPrinterTab,
    paper: renderPaperTab,
    windows: renderWindowsTab,
    catalog: renderCatalogTab,
    stats: renderStatsTab,
    about: renderAboutTab,
  };
  content.innerHTML = (renderers[settingsTab] || renderAboutTab)();
  refreshIcons(content);
  bindSettingsActions(content);
  if (settingsTab === 'printer') {
    bindProtocolSelect(content);
    if (!state.printers || Date.now() - printersLoadedAt > 20000) setTimeout(() => loadPrinters(), 50);
  }
  if (settingsTab === 'paper') bindPaperTabInputs(content);
  if (settingsTab === 'windows') loadFitInto(content);
  if (settingsTab === 'stats') loadStats(content);
}

/* The protocol select in Settings → Printer: the value is stored server-side so the
   auto-connect and the Windows queue use the same driver. */
function bindProtocolSelect(content) {
  const showsAll = $('#show-all-ble', content);
  if (showsAll) {
    showsAll.onchange = () => {
      state.showAllBle = showsAll.checked;
      writePref('showAllBle', state.showAllBle);
      loadPrinters(true);
    };
  }
  const select = $('#protocol-select', content);
  if (!select) return;
  select.onchange = async () => {
    const protocol = select.value;
    try {
      const geometry = await api('/api/settings/device', 'POST', {
        dpi: Number($('#geom-dpi', content)?.value || 0),
        printhead_px: Number($('#geom-head', content)?.value || 0),
        print_task: $('#geom-task', content)?.value || '',
        protocol,
      });
      state.printerProtocol = geometry.protocol || protocol;
      toast(`Protocol: ${PROTOCOL_LABELS[state.printerProtocol] || state.printerProtocol}`, 'ok');
    } catch (error) {
      toast(error.message, 'error');
    }
  };
}

function bindSettingsActions(content) {
  content.onclick = async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    try {
      if (action === 'scan') await scanDevices(event.target.closest('[data-action]'));
      if (action === 'connect') {
        const button = event.target.closest('[data-address]');
        const protocol = button?.dataset.protocol || state.printerProtocol || 'niimbot';
        await connect(button.dataset.address, protocol);
      }
      if (action === 'disconnect') await disconnectPrinter();
      if (action === 'refresh') await refreshPrinter();
      if (action === 'test-print') await testPrint();
      if (action === 'calibrate') await calibratePrinter();
      if (action === 'detect') await detectSize();
      if (action === 'apply-format') applyDetectedFormat();
      if (action === 'save-roll') await saveRollSize();
      if (action === 'save-fit') await saveFit();
      if (action === 'install-info') showInstallCommand(content);
      if (action === 'import-catalog') await importCatalog(event.target.closest('[data-action]'));
      if (action === 'roll-format') openRollSizeDialog();
      if (action === 'device-save') await saveDeviceGeometry(content);
      if (action === 'scan-printers') await loadPrinters(true);
      if (action === 'diagnose') await diagnoseDevice(event.target.closest('[data-address]').dataset.address);
      if (action === 'feed-now' || action === 'feed') await feedPaper(document.querySelector('#feed-mm')?.value);
      if (action === 'use-format') {
        const index = Number(event.target.closest('[data-index]').dataset.index);
        const item = state.catalog[index];
        if (item) {
          applySize(item.width_mm, item.height_mm, { label_type: item.paper_type ?? state.spec.label_type });
          toast(`Set to ${item.width_mm} × ${item.height_mm} mm`, 'ok');
        }
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  };
}

/* Two protocol families are supported: Niimbot's framed protocol and the plain
   LuckPrinter bytes used by AiYin/Fichero/Karsten units (96 px head, 203 dpi). */
const PROTOCOL_LABELS = {
  niimbot: 'Niimbot',
  luckprinter: 'LuckPrinter (Fichero / AiYin)',
};
const LUCK_PRINTER_HINTS = ['fichero', 'd11s', 'd12', 'aiyin', 'karsten', 'luckp', 'dp_d1'];

function detectProtocol(name) {
  const lower = String(name || '').toLowerCase();
  return LUCK_PRINTER_HINTS.some((hint) => lower.includes(hint)) ? 'luckprinter' : 'niimbot';
}

/* Some printers (clones / OEM units) are not in the model library. When we cannot look the
   geometry up, the dpi and printhead width have to be entered by hand — everything else in the
   app (canvas, print packing, PDF rasterising) follows these two numbers. */
let deviceSettings = null;

function loadDeviceSettings(force) {
  if (deviceSettings && !force) return Promise.resolve(deviceSettings);
  return api('/api/settings/device')
    .then((payload) => {
      deviceSettings = payload;
      return payload;
    })
    .catch(() => null);
}

function renderGeometryOverride(info) {
  if (state.connected && !deviceSettings) loadDeviceSettings().then(() => renderSettingsTab());
  const override = deviceSettings?.override || {};
  const detected = deviceSettings?.detected || {};
  const unknown = state.connected && detected.known === false;
  const note = unknown
    ? `<div class="hint warn">This printer is not in the model library, so the detected geometry may be wrong.
        Set the resolution and the printhead width by hand.</div>`
    : '';
  return `
    <div class="geom-override">
      <h4>Printhead override</h4>
      ${note}
      <div class="grid-2" style="margin-top:6px">
        <label>Resolution (dpi)<input type="number" id="geom-dpi" min="100" max="1200" step="1"
          placeholder="${detected.dpi || 203}" value="${override.dpi || ''}"></label>
        <label>Printhead width (px)<input type="number" id="geom-head" min="32" max="2048" step="1"
          placeholder="${detected.printhead_px || 384}" value="${override.printhead_px || ''}"></label>
      </div>
      <div class="geom-row">
        <label>Print task<select id="geom-task">
          <option value="">Auto</option>
          <option value="b1" ${override.print_task === 'b1' ? 'selected' : ''}>B1 / legacy</option>
          <option value="v4" ${override.print_task === 'v4' ? 'selected' : ''}>V4</option>
        </select></label>
        <button class="btn" data-action="device-save"><i data-lucide="save"></i> Apply</button>
      </div>
      <div class="hint">Leave empty for automatic. Changes apply to the live connection immediately.</div>
      <h4 style="margin-top:12px">Paper advance</h4>
      <div class="hint">The Fichero/Karsten family does not walk to the tear bar by itself — the vendor app
        sends the distance explicitly. Set how far the label should advance after printing (0 = leave it to
        the printer) and use <em>Feed now</em> to try it out.</div>
      <div class="geom-row">
        <label>Advance after print (mm)<input type="number" id="feed-mm" min="0" max="50" step="0.5"
          value="${Number(deviceSettings?.feed_mm || 0)}"></label>
        <button class="btn" data-action="feed"><i data-lucide="arrow-down-to-line"></i> Feed now</button>
      </div>
    </div>`;
}

async function saveDeviceGeometry(content) {
  const dpi = Number($('#geom-dpi', content)?.value || 0);
  const printhead_px = Number($('#geom-head', content)?.value || 0);
  const print_task = $('#geom-task', content)?.value || '';
  const feed_mm = Number($('#feed-mm', content)?.value || 0);
  const payload = await api('/api/settings/device', 'POST', { dpi, printhead_px, print_task, feed_mm });
  deviceSettings = payload;
  await refreshState();
  renderSettingsTab();
  toast(
    payload?.detected?.dpi
      ? `Device geometry: ${payload.detected.dpi} dpi · ${payload.detected.printhead_px} px`
      : 'Device geometry updated',
    'ok',
  );
}

/* ------------------------------------------------------------ printer inventory */
/* The list is the merged view from the server: printers that were connected at least once
   (each with its own library) and printers that are advertising right now. A light scan runs
   in the background when the list is opened, so both units show up without pressing anything. */
let printersLoadedAt = 0;
let printersLoading = false;

async function loadPrinters(force = false) {
  if (printersLoading) return;
  printersLoading = true;
  const query = state.showAllBle ? '?all=true' : '';
  const paint = () => {
    if (settingsTab === 'printer' && modalStack.some((entry) => entry.id === 'settings')) renderSettingsTab();
  };
  try {
    // First paint from what the server already knows — no waiting for the radio.
    const quick = await api(`/api/printers${query}`);
    state.printers = quick.printers || [];
    state.printerInventory = quick;
    paint();
    if (force || Date.now() - printersLoadedAt > 20000) {
      try {
        await api('/api/scan', 'POST', { timeout: 4, show_all: true });
      } catch (_) { /* Bluetooth off — the known printers still list */ }
      const payload = await api(`/api/printers${query}`);
      state.printers = payload.printers || [];
      state.printerInventory = payload;
      printersLoadedAt = Date.now();
      paint();
    }
  } catch (_) { /* keep the previous list */ }
  printersLoading = false;
}

/* One row per printer: everything that was ever connected (it owns a library) plus whatever
   is advertising right now. The protocol is detected, never chosen by hand. */
function renderPrinterRows() {
  const rows = (state.printers || []).map((printer) => {
    const stage = printer.connected ? 'connected' : printer.available ? 'in range' : 'known';
    return `
      <div class="device">
        <div>
          <strong>${escapeHtml(printer.model_name || printer.name || printer.address)}</strong>
          <span class="device-tag ${printer.connected ? 'known' : 'other'}">${stage}</span>
          <span class="device-tag other">${escapeHtml(PROTOCOL_LABELS[printer.protocol] || printer.protocol || '?')}</span>
          ${printer.known ? '<span class="device-tag known">own library</span>' : ''}
          <small>${escapeHtml(printer.address)}${printer.scope ? ` · ${escapeHtml(printer.scope)}` : ''}</small>
        </div>
        <div class="row" style="gap:6px">
          ${printer.available || printer.connected
            ? `<button class="btn" data-action="diagnose" data-address="${escapeHtml(printer.address)}" title="Inspect services and notification traffic"><i data-lucide="stethoscope"></i></button>`
            : ''}
          ${printer.connected
            ? '<span class="dot ok"></span>'
            : `<button class="btn" data-action="connect" data-address="${escapeHtml(printer.address)}" data-protocol="auto">Connect</button>`}
        </div>
      </div>`;
  }).join('');
  return rows || '<div class="empty">No printer yet — switch it on and press “Scan Bluetooth”.</div>';
}

function renderPrinterTab() {
  const payload = state.printer || {};
  const list = renderPrinterRows();

  const info = payload.info || {};
  return `
    <section>
      <h3>Connection</h3>
      <div class="row">
        <button class="btn primary" data-action="scan"><i data-lucide="bluetooth"></i> Scan Bluetooth</button>
        <button class="btn" data-action="refresh" ${state.connected ? '' : 'disabled'}><i data-lucide="refresh-cw"></i> Refresh</button>
        <button class="btn" data-action="test-print" ${state.connected ? '' : 'disabled'}><i data-lucide="scan-line"></i> Test print</button>
        <button class="btn danger" data-action="disconnect" ${state.connected ? '' : 'disabled'}><i data-lucide="power"></i> Disconnect</button>
      </div>
      <label class="inline-check"><input type="checkbox" id="show-all-ble" ${state.showAllBle ? 'checked' : ''}>
        Show all Bluetooth devices (not only printers)</label>
      <div class="protocol-row">
        <label>Protocol<select id="protocol-select">
          <option value="auto" ${state.printerProtocol === 'auto' ? 'selected' : ''}>Automatic (recommended)</option>
          <option value="niimbot" ${state.printerProtocol === 'niimbot' ? 'selected' : ''}>Niimbot (B1, B1 Pro, …)</option>
          <option value="luckprinter" ${state.printerProtocol === 'luckprinter' ? 'selected' : ''}>LuckPrinter (Fichero, Karsten, AiYin)</option>
        </select></label>
        <span class="hint" id="protocol-hint">Automatic: the protocol follows the device name, and the other one is tried when the connection fails.</span>
      </div>
      <div class="device-list" style="margin-top:10px">${list}</div>
      <div class="hint" style="margin-top:6px">Every Bluetooth LE device nearby is listed — printers that are not from the Niimbot family show up as
        <em>BLE device</em>. The test print sends a small sample using the detected paper format — the quickest way to verify the connection and the print quality.
        A printer that never appears here is either off, already paired to a phone, or speaks classic Bluetooth (SPP) only, which Windows exposes as a serial port
        rather than a BLE device.</div>
    </section>
    <section>
      <h3>Model</h3>
      <div class="status-line"><span>Model</span><span>${escapeHtml(info.model_name || '—')}</span></div>
      <div class="status-line"><span>Model ID</span><span>${info.model_id ?? '—'}</span></div>
      <div class="status-line"><span>Resolution</span><span>${info.dpi ? `${info.dpi} dpi` : '—'}</span></div>
      <div class="status-line"><span>Printhead</span><span>${info.printhead_px ? `${info.printhead_px} px` : '—'}</span></div>
      <div class="status-line"><span>Firmware</span><span>${escapeHtml(info.firmware || '—')}</span></div>
      <div class="status-line"><span>Protocol</span><span>${info.protocol_version ?? '—'}</span></div>
      <div class="status-line"><span>Serial number</span><span class="mono">${escapeHtml(info.serial || '—')}</span></div>
      ${renderGeometryOverride(info)}
    </section>
    <section>
      <h3>Service</h3>
      <div class="row">
        <button class="btn" data-action="calibrate" ${state.connected ? '' : 'disabled'}><i data-lucide="scan-line"></i> Paper calibration</button>
        <button class="btn" id="btn-test-settings" ${state.connected ? '' : 'disabled'}><i data-lucide="printer"></i> Test print</button>
      </div>
      <div class="hint" style="margin-top:6px">Calibration feeds a few labels — use it when the print is offset against the cuts.</div>
    </section>
  `;
}

function renderPaperTab() {
  const paper = state.printer?.paper || {};
  const detected = paper.detected_width_mm && paper.detected_height_mm
    ? { w: paper.detected_width_mm, h: paper.detected_height_mm, gap: paper.detected_gap_mm, type: paper.detected_label_type }
    : null;
  const flags = [
    ['Lid', paper.lid_closed === true ? '<span class="ok-text">closed</span>' : paper.lid_closed === false ? '<span class="err-text">open</span>' : '—'],
    ['Paper', paper.paper_inserted === true ? '<span class="ok-text">detected</span>' : paper.paper_inserted === false ? '<span class="err-text">missing</span>' : '—'],
    ['RFID tag', paper.rfid_ok === true ? '<span class="ok-text">read</span>' : paper.rfid_ok === false ? '<span class="warn-text">missing</span>' : '—'],
    ['Type', escapeHtml(paper.label_type_name || '—')],
    ['Roll code', `<span class="mono">${escapeHtml(paper.barcode || '—')}</span>`],
    ['Serial number', `<span class="mono">${escapeHtml(paper.serial || '—')}</span>`],
  ];
  if (paper.capacity) {
    const approx = paper.capacity_estimated ? '≈' : '';
    flags.push(['Roll usage', `${paper.used ?? 0} / ${approx}${paper.capacity}${paper.remaining !== null && paper.remaining !== undefined ? ` · ${approx}${paper.remaining} left` : ''}`]);
  }
  if (paper.battery !== null && paper.battery !== undefined) {
    const levels = ['0%', '25%', '50%', '75%', '100%'];
    flags.push(['Battery', paper.battery <= 4 ? `${levels[paper.battery]} <span class="hint">(level ${paper.battery})</span>` : `${paper.battery}%`]);
  }
  if (paper.temperature !== null && paper.temperature !== undefined) flags.push(['Temperature', `${paper.temperature}`]);

  const profile = state.printer?.profile;
  const catalogEntry = paper.catalog;

  return `
    <section>
      <h3>Paper status</h3>
      ${flags.map(([label, value]) => `<div class="status-line"><span>${label}</span><span>${value}</span></div>`).join('')}
    </section>
    <section>
      <h3>Format</h3>
      ${detected ? `<div class="status-line"><span>${paper.geometry_source === 'catalog' ? 'From the catalogue' : 'From the printer'}</span><span class="ok-text">${detected.w} × ${detected.h} mm</span></div>` : ''}
      ${catalogEntry?.name ? `<div class="status-line"><span>Symbol</span><span class="mono">${escapeHtml(catalogEntry.name)}</span></div>` : ''}
      <div class="row" style="margin-top:8px">
        <button class="btn" data-action="detect" ${state.connected ? '' : 'disabled'}><i data-lucide="scan-line"></i> Detect format</button>
        <button class="btn primary" data-action="apply-format" ${detected ? '' : 'disabled'}>Use detected</button>
      </div>
      <div class="grid-2" style="margin-top:10px">
        <div class="field"><label for="roll-width">Width (mm)</label><input type="number" id="roll-width" step="0.5" value="${round2(state.spec.width_mm)}"></div>
        <div class="field"><label for="roll-height">Height (mm)</label><input type="number" id="roll-height" step="0.5" value="${round2(state.spec.height_mm)}"></div>
      </div>
      <div class="row" style="margin-top:8px">
        <button class="btn" data-action="save-roll" ${paper.barcode || paper.uuid ? '' : 'disabled'}><i data-lucide="database"></i> Save size for this roll</button>
      </div>
      ${profile ? `<div class="hint" style="margin-top:6px">Saved profile: ${profile.width_mm} × ${profile.height_mm} mm</div>` : ''}
    </section>
    <section>
      <h3>Design</h3>
      <div class="grid-2">
        <div class="field"><label for="label-width">Width (mm)</label><input type="number" id="label-width" min="5" max="110" step="0.5" value="${round2(state.spec.width_mm)}"></div>
        <div class="field"><label for="label-height">Height (mm)</label><input type="number" id="label-height" min="5" max="300" step="0.5" value="${round2(state.spec.height_mm)}"></div>
      </div>
      <div class="field"><label for="label-type">Paper type</label>
        <select id="label-type">${(state.options?.label_types || []).map((type) => `<option value="${type.value}" ${Number(state.spec.label_type) === Number(type.value) ? 'selected' : ''}>${escapeHtml(type.name)}</option>`).join('')}</select>
      </div>
      <div class="grid-2">
        <div class="field"><label for="label-gap">Gap (mm)</label><input type="number" id="label-gap" min="0" max="20" step="0.5" value="${state.spec.gap_mm}"></div>
        <div class="field"><label for="label-preset">Ready-made size</label>
          <select id="label-preset"><option value="">— choose —</option>${(state.options?.paper_presets || []).map((preset, index) => `<option value="${index}">${escapeHtml(preset.name)}</option>`).join('')}</select>
        </div>
      </div>
    </section>
  `;
}

function bindPaperTabInputs(root) {
  const width = $('#label-width', root);
  if (width) {
    width.oninput = () => {
      state.spec.width_mm = Math.max(5, Number(width.value) || 5);
      clampElements();
      layoutStage();
      schedulePreview();
    };
  }
  const height = $('#label-height', root);
  if (height) {
    height.oninput = () => {
      state.spec.height_mm = Math.max(5, Number(height.value) || 5);
      clampElements();
      layoutStage();
      schedulePreview();
    };
  }
  const gap = $('#label-gap', root);
  if (gap) gap.oninput = () => { state.spec.gap_mm = Math.max(0, Number(gap.value) || 0); schedulePreview(); };
  const type = $('#label-type', root);
  if (type) type.onchange = () => { state.spec.label_type = Number(type.value); schedulePreview(); };
  const preset = $('#label-preset', root);
  if (preset) {
    preset.onchange = () => {
      if (preset.value === '') return;
      const item = state.options.paper_presets[Number(preset.value)];
      if (item) applySize(item.width_mm, item.height_mm, { gap_mm: item.gap_mm, label_type: item.label_type });
      preset.value = '';
    };
  }
}

function renderWindowsTab() {
  return `
    <section>
      <h3>Dopasowanie obrazu z Windows</h3>
      <div class="field">
        <label for="fit-mode">Tryb</label>
        <select id="fit-mode">
          <option value="contain">Fit whole page (no crop)</option>
          <option value="cover">Fill and crop</option>
          <option value="stretch">Stretch to format</option>
        </select>
      </div>
      <div class="grid-2">
        <div class="field"><label for="fit-dx">Offset X (mm)</label><input type="number" id="fit-dx" step="0.5" value="0"></div>
        <div class="field"><label for="fit-dy">Offset Y (mm)</label><input type="number" id="fit-dy" step="0.5" value="0"></div>
        <div class="field"><label for="fit-rot">Rotation</label><select id="fit-rot"><option value="0">0°</option><option value="90">90°</option><option value="180">180°</option><option value="270">270°</option></select></div>
        <div class="field"><label for="fit-scale">Skala</label><input type="number" id="fit-scale" step="0.05" value="1"></div>
        <div class="field"><label for="fit-margin">Margines (mm)</label><input type="number" id="fit-margin" step="0.5" value="0"></div>
        <div class="field"><label for="fit-threshold">Threshold (0–255)</label><input type="number" id="fit-threshold" step="5" value="128"></div>
      </div>
      <div class="row" style="margin-top:8px">
        <label class="check"><input type="checkbox" id="fit-dither"> dithering (photos)</label>
        <label class="check"><input type="checkbox" id="fit-invert"> negatyw</label>
      </div>
      <div class="row" style="margin-top:10px">
        <button class="btn primary" data-action="save-fit"><i data-lucide="save"></i> Save fit</button>
      </div>
    </section>
    <section>
      <h3>Kolejka Windows (IPP)</h3>
      <div class="hint">The printer shows up as a regular queue — no kernel driver involved.
        Registration needs one-time administrator rights.</div>
      <div class="row" style="margin-top:8px">
        <button class="btn" data-action="install-info"><i data-lucide="terminal"></i> Show install command</button>
      </div>
      <div id="driver-command"></div>
    </section>
  `;
}

function renderCatalogTab() {
  const items = state.catalog || [];
  const paper = state.printer?.paper || {};
  const current = items.find((item) => item.barcode && item.barcode === paper.barcode);
  const rollName = paper.sku || paper.catalog?.name || paper.barcode || '';
  return `
    <section>
      <h3>Roll in the printer</h3>
      ${state.connected ? `
        <div class="device">
          <div>
            <strong>${rollName ? escapeHtml(rollName) : 'no roll data'}</strong>
            <small>${current
              ? `${round2(current.width_mm)} × ${round2(current.height_mm)} mm${current.rows > 1 ? ` · ${current.rows} rows` : ''} · ${current.source}`
              : 'the printer cannot report this roll — set its size once'}</small>
          </div>
          <button class="btn primary" data-action="roll-format"><i data-lucide="ruler"></i> ${current ? 'Edit size' : 'Set size'}</button>
        </div>` : '<div class="hint">Connect the printer to see the roll and its format.</div>'}
    </section>
    <section>
      <h3>Formats in the database (${items.length})</h3>
      <div class="hint">Built-in formats, formats imported from the NIIMBOT app and sizes you entered by hand.
        A roll is recognised by the product barcode stored in its RFID tag.</div>
      <div class="device-list" style="margin-top:10px;max-height:280px">
        ${items.length ? items.map((item, index) => `
          <div class="device">
            <div>
              <strong>${round2(item.width_mm)} × ${round2(item.height_mm)} mm${item.rows > 1 ? ` · ${item.rows} rows` : ''}</strong>
              <small>${escapeHtml(item.sku || item.name || '')}${item.capacity ? ` · ${item.capacity} labels` : ''} · ${item.source}${item.barcode ? ` · ${item.barcode}` : ''}</small>
            </div>
            <button class="btn" data-action="use-format" data-index="${index}">Use</button>
          </div>`).join('') : '<div class="empty">No formats</div>'}
      </div>
    </section>
    <section>
      <h3>Import</h3>
      <div class="row">
        <button class="btn" data-action="import-catalog"><i data-lucide="download"></i> Import from the NIIMBOT app</button>
      </div>
      <div class="hint" style="margin-top:6px">Reads every roll format the vendor app has cached locally
        (barcode → size, rows, capacity). Rolls the vendor app never saw are not in that cache — enter those once
        with <strong>Set size</strong> above and they stay in this database.</div>
    </section>
  `;
}

function renderStatsTab() {
  return '<section><h3>Statistics</h3><div id="stats-body"><div class="empty">loading…</div></div></section>';
}

function renderAboutTab() {
  return `
    <section>
      <h3>Niibot Studio</h3>
      <div class="hint" style="margin-bottom:10px">Standalone panel for the Niimbot B1 printer over Bluetooth LE.
        Dane trzymane lokalnie w jednej bazie SQLite — bez chmury.</div>
      <div class="status-line"><span>Protocol</span><span>Niimbot BLE (0x55 0x55 … 0xAA 0xAA)</span></div>
      <div class="status-line"><span>Formats</span><span>${(state.catalog || []).length} stored</span></div>
      <div class="status-line"><span>Interface</span><span>Lucide + Roboto, VS Code layout</span></div>
      <div class="status-line"><span>Windows printer</span><span>IPP queue (Microsoft IPP Class Driver)</span></div>
      <h4>Shortcuts</h4>
      <div class="status-line"><span>Undo / redo</span><span class="mono">Ctrl+Z / Ctrl+Y</span></div>
      <div class="status-line"><span>Duplicate / delete</span><span class="mono">Ctrl+D / Del</span></div>
      <div class="status-line"><span>Nudge</span><span class="mono">arrows (Shift = 0.2 mm)</span></div>
      <div class="status-line"><span>Print</span><span class="mono">Ctrl+P</span></div>
    </section>
  `;
}

/* ================================================================ menus */
const MENUS = {
  file: {
    label: 'File',
    items: [
      { icon: 'file-plus', label: 'New label', run: () => openNewLabelDialog() },
      { icon: 'folder-open', label: 'Load label…', run: () => openLabelsModal() },
      { icon: 'save', label: 'Save label', key: 'Ctrl+S', run: () => saveLabel() },
      { icon: 'save-all', label: 'Save label as…', key: 'Ctrl+Shift+S', run: () => saveLabelAs() },
      { icon: 'layout-template', label: 'Save as template…', run: () => saveAsTemplate() },
      { sep: true },
      { icon: 'download', label: 'Export PNG', run: () => exportPng() },
      { sep: true },
      { icon: 'printer', label: 'Print', key: 'Ctrl+P', run: () => printLabel() },
      { icon: 'scan-line', label: 'Test print', run: () => testPrint(), disabled: () => !state.connected },
      { sep: true },
      { icon: 'x', label: 'Close window', run: () => window.close() },
    ],
  },
  edit: {
    label: 'Edit',
    items: [
      { icon: 'undo-2', label: 'Undo', key: 'Ctrl+Z', run: () => undo() },
      { icon: 'redo-2', label: 'Redo', key: 'Ctrl+Y', run: () => redo() },
      { sep: true },
      { icon: 'copy', label: 'Duplicate element', key: 'Ctrl+D', run: () => duplicateSelected(), disabled: () => !state.selectedId },
      { icon: 'trash-2', label: 'Delete element', key: 'Del', run: () => deleteSelected(), disabled: () => !state.selectedId },
      { sep: true },
      { icon: 'align-center-horizontal', label: 'Centre horizontally', run: () => alignSelected('h'), disabled: () => !state.selectedId },
      { icon: 'align-center-vertical', label: 'Centre vertically', run: () => alignSelected('v'), disabled: () => !state.selectedId },
      { sep: true },
      { icon: 'shapes', label: 'Insert icon / clipart…', run: () => openIconPicker() },
      {
        icon: 'type',
        label: 'Font…',
        run: () => { const element = selectedElement(); if (element && element.type === 'text') openFontPicker(element); },
        disabled: () => selectedElement()?.type !== 'text',
      },
    ],
  },
  view: {
    label: 'View',
    items: [
      { icon: 'grid-3x3', label: 'Grid on label', key: 'G', checked: () => state.showGrid, run: () => toggleGrid() },
      { icon: 'magnet', label: 'Snap to grid', key: 'S', checked: () => state.snapToGrid, run: () => toggleSnap() },
      { icon: 'crop', label: 'Print regions', checked: () => state.showRegions, run: () => toggleRegions() },
      { sep: true },
      { icon: 'rotate-cw', label: 'Orientation…', run: () => openOrientationDialog() },
      { icon: 'library', label: 'Calibration library…', run: () => openCalibrationPicker().catch(() => {}) },
      { sep: true },
      { icon: 'scan-line', label: 'Detect roll format', run: () => detectSize(), disabled: () => !state.connected },
      { icon: 'refresh-cw', label: 'Refresh printer state', run: () => refreshPrinter(), disabled: () => !state.connected },
    ],
  },
  settings: {
    label: 'Settings',
    items: [
      { icon: 'settings', label: 'Settings…', key: 'Ctrl+,', run: () => openSettings('printer') },
      { icon: 'tag', label: 'Paper & roll…', run: () => openSettings('paper') },
      { icon: 'monitor', label: 'Print from Windows…', run: () => openSettings('windows') },
      { sep: true },
      { icon: 'download', label: 'Import NIIMBOT catalogue', run: () => importCatalog() },
      { icon: 'list', label: 'Statistics…', run: () => openSettings('stats') },
    ],
  },
  help: {
    label: 'Help',
    items: [
      { icon: 'info', label: 'About…', run: () => openSettings('about') },
      { icon: 'book-open', label: 'Documentation (README)', run: () => window.open('/../README.md', '_blank') },
    ],
  },
};

let openMenuKey = null;

function toggleMenu(key) {
  const existing = $('#menu-dropdown');
  if (openMenuKey === key) {
    existing.hidden = true;
    openMenuKey = null;
    $$('.menu-item').forEach((item) => item.classList.remove('active'));
    return;
  }
  openMenuKey = key;
  $$('.menu-item').forEach((item) => item.classList.toggle('active', item.dataset.menu === key));
  const menu = MENUS[key];
  if (!menu) return;
  existing.innerHTML = menu.items.map((item, index) => {
    if (item.sep) return '<div class="md-sep"></div>';
    const disabled = item.disabled ? item.disabled() : false;
    const checked = item.checked ? item.checked() : false;
    return `<button data-index="${index}" ${disabled ? 'disabled' : ''} class="${checked ? 'checked' : ''}">
      <i data-lucide="${item.icon || 'circle'}"></i>
      <span>${escapeHtml(item.label)}</span>
      ${item.key ? `<span class="md-key">${item.key}</span>` : ''}
    </button>`;
  }).join('');
  const anchor = $(`.menu-item[data-menu="${key}"]`).getBoundingClientRect();
  existing.style.left = `${anchor.left}px`;
  existing.style.top = `${anchor.bottom + 4}px`;
  existing.hidden = false;
  refreshIcons(existing);
  $$('#menu-dropdown button').forEach((button) => {
    button.onclick = () => {
      const item = menu.items[Number(button.dataset.index)];
      existing.hidden = true;
      openMenuKey = null;
      $$('.menu-item').forEach((entry) => entry.classList.remove('active'));
      if (item && item.run) item.run();
    };
  });
}

function toggleRegions() {
  state.showRegions = !state.showRegions;
  layoutStage();
  toast(state.showRegions ? 'Print regions: on' : 'Print regions: off');
}

function saveViewPrefs() {
  writePref('view', {
    showGrid: state.showGrid,
    snapToGrid: state.snapToGrid,
    showRegions: state.showRegions,
  });
}

function loadViewPrefs() {
  const queueHeight = Number(readPref('queueHeight', 0));
  state.queueHeight = Number.isFinite(queueHeight) && queueHeight > 0 ? Math.round(queueHeight) : 0;
  applyQueueHeight();
  const saved = readPref('view');
  if (!saved) return;
  if (typeof saved.showGrid === 'boolean') state.showGrid = saved.showGrid;
  if (typeof saved.snapToGrid === 'boolean') state.snapToGrid = saved.snapToGrid;
  if (typeof saved.showRegions === 'boolean') state.showRegions = saved.showRegions;
}

function syncViewToggles() {
  $('#btn-grid')?.classList.toggle('active', state.showGrid);
  $('#btn-snap')?.classList.toggle('active', state.snapToGrid);
}

function toggleGrid(force) {
  state.showGrid = typeof force === 'boolean' ? force : !state.showGrid;
  applyGridSize();
  syncViewToggles();
  saveViewPrefs();
  toast(state.showGrid ? 'Grid: on' : 'Grid: off');
}

function toggleSnap(force) {
  state.snapToGrid = typeof force === 'boolean' ? force : !state.snapToGrid;
  syncViewToggles();
  saveViewPrefs();
  toast(state.snapToGrid ? 'Snap to grid: on' : 'Snapping off (Alt = free movement)');
}

/* ================================================================ actions */
async function scanDevices(button) {
  const previous = button ? button.innerHTML : null;
  if (button) {
    button.disabled = true;
    button.innerHTML = '<i data-lucide="loader"></i> Skanowanie…';
    refreshIcons(button);
  }
  try {
    const { devices } = await api('/api/scan', 'POST', { timeout: 6, show_all: true });
    state.lastDevices = devices;
    renderSettingsTab();
    if (!devices.length) {
      toast('No Bluetooth device found — is the printer switched on? A printer that only offers classic Bluetooth (SPP) never appears in this list.', 'error');
    }
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.innerHTML = previous;
      refreshIcons(button);
    }
  }
}

/* Inspect an unknown BLE device: services, characteristics and whatever it says on its
   notifications. This is how a Fichero/Karsten/OEM printer gets identified — the report can
   be copied out with one button. */
async function diagnoseDevice(address) {
  const body = document.createElement('div');
  body.className = 'modal-body';
  body.innerHTML = `<div class="hint"><span class="spinner"></span> Listening to ${escapeHtml(address)} for a few seconds…</div>`;
  const footer = document.createElement('div');
  footer.innerHTML = `
    <span class="hint" id="diagnose-verdict"></span>
    <button class="btn" data-diagnose="copy" disabled><i data-lucide="copy"></i> Copy report</button>
    <button class="btn" data-diagnose="close">Close</button>`;
  const modal = openModal({
    id: 'diagnose',
    title: 'Device diagnostics',
    icon: 'stethoscope',
    width: 760,
    height: 520,
    body,
    footer,
  });
  refreshIcons(modal);

  let report = '';
  footer.querySelector('[data-diagnose="close"]').onclick = () => closeModal('diagnose');
  footer.querySelector('[data-diagnose="copy"]').onclick = async (event) => {
    try {
      await navigator.clipboard.writeText(report);
      toast('Report copied', 'ok');
    } catch (_) {
      toast('Copy failed — select the text and copy it manually.', 'error');
    }
  };

  try {
    const result = await api('/api/device/diagnose', 'POST', { address, seconds: 6 });
    const lines = [result.report, '', `notifications ${result.notifications.length} (${result.bytes} bytes)`];
    for (const item of result.notifications.slice(0, 60)) {
      lines.push(`  ${String(item.at).padStart(6)}s  ${item.uuid}  ${item.hex}`);
    }
    report = lines.join('\n');
    body.innerHTML = `<pre class="diagnose-report">${escapeHtml(report)}</pre>`;
    footer.querySelector('#diagnose-verdict').textContent = result.verdict;
    footer.querySelector('[data-diagnose="copy"]').disabled = false;
  } catch (error) {
    report = `diagnose failed: ${error.message}`;
    body.innerHTML = `<div class="hint">${escapeHtml(error.message)}</div>`;
    footer.querySelector('#diagnose-verdict').textContent = 'Device did not answer.';
  }
}

/* Push the label out to the tear bar — also the way to find the right advance value. */
async function feedPaper(millimetres) {
  const mm = Number(millimetres || 6);
  try {
    const result = await api('/api/printer/feed', 'POST', { mm });
    toast(`Fed ${result.fed_mm} mm`, 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function disconnectPrinter() {
  const payload = await api('/api/disconnect', 'POST');
  applyState(payload);
  toast('Disconnected');
}

async function refreshPrinter() {
  const payload = await api('/api/refresh', 'POST');
  applyState(payload);
}

async function calibratePrinter() {
  toast('Calibration — the printer will feed a few labels…');
  const payload = await api('/api/calibrate', 'POST');
  applyState(payload);
  toast('Calibration finished', 'ok');
}

function applyDetectedFormat() {
  const paper = state.printer?.paper || {};
  if (paper.detected_width_mm && paper.detected_height_mm) {
    applySize(paper.detected_width_mm, paper.detected_height_mm, {
      gap_mm: paper.detected_gap_mm ?? state.spec.gap_mm,
      label_type: paper.detected_label_type ?? state.spec.label_type,
    });
    toast('Detected format applied', 'ok');
  }
}

async function saveRollSize() {
  const paper = state.printer?.paper || {};
  const width = Number($('#roll-width')?.value) || state.spec.width_mm;
  const height = Number($('#roll-height')?.value) || state.spec.height_mm;
  const key = paper.barcode ? `bc:${paper.barcode}` : (paper.uuid ? `uid:${paper.uuid}` : null);
  if (!key) throw new Error('The roll has no identifier yet');
  await api('/api/profiles', 'POST', {
    key,
    name: `${width} × ${height} mm`,
    width_mm: width,
    height_mm: height,
    gap_mm: state.spec.gap_mm,
    label_type: state.spec.label_type,
    capacity: paper.capacity || null,
  });
  await refreshPrinter();
  toast('Roll size saved', 'ok');
}

async function loadFitInto(root) {
  try {
    const { fit } = await api('/api/fit');
    const set = (id, value) => { const node = $(`#${id}`, root); if (node) node.value = value; };
    set('fit-mode', fit.mode || 'contain');
    set('fit-dx', fit.offset_x_mm ?? 0);
    set('fit-dy', fit.offset_y_mm ?? 0);
    set('fit-rot', String(fit.rotation ?? 0));
    set('fit-scale', fit.scale ?? 1);
    set('fit-margin', fit.margin_mm ?? 0);
    set('fit-threshold', fit.threshold ?? 128);
    const dither = $('#fit-dither', root);
    if (dither) dither.checked = Boolean(fit.dither);
    const invert = $('#fit-invert', root);
    if (invert) invert.checked = Boolean(fit.invert);
  } catch (_) { /* ignore */ }
}

async function saveFit() {
  await api('/api/fit', 'POST', {
    mode: $('#fit-mode').value,
    offset_x_mm: Number($('#fit-dx').value) || 0,
    offset_y_mm: Number($('#fit-dy').value) || 0,
    rotation: Number($('#fit-rot').value) || 0,
    scale: Number($('#fit-scale').value) || 1,
    margin_mm: Number($('#fit-margin').value) || 0,
    threshold: Number($('#fit-threshold').value) || 128,
    dither: $('#fit-dither').checked,
    invert: $('#fit-invert').checked,
  });
  toast('Windows print fitting saved', 'ok');
}

async function showInstallCommand(root) {
  const info = await api('/api/driver');
  const box = $('#driver-command', root);
  if (!box) return;
  box.innerHTML = `<pre># as administrator, in the application folder:
powershell -ExecutionPolicy Bypass -File install-printer.ps1

# or manually:
${escapeHtml(info.install_command)}</pre>`;
}

async function importCatalog(button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = '<i data-lucide="loader"></i> Reading the cache…';
    refreshIcons(button);
  }
  try {
    const result = await api('/api/catalog/import', 'POST');
    state.catalog = result.catalog;
    applyState(result.state);
    renderSettingsTab();
    toast(result.imported ? `Imported ${result.imported} format(s)` : 'No new formats found', result.imported ? 'ok' : 'error');
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.innerHTML = '<i data-lucide="download"></i> Import from the NIIMBOT app';
      refreshIcons(button);
    }
  }
}

async function loadStats(root) {
  const body = $('#stats-body', root);
  if (!body) return;
  try {
    const stats = await api('/api/stats?recent=6');
    const rows = [];
    rows.push(`<div class="status-line"><span>Labels printed</span><span>${stats.labels || 0}</span></div>`);
    rows.push(`<div class="status-line"><span>Jobs</span><span>${stats.done || 0} ok · ${stats.failed || 0} failed</span></div>`);
    rows.push(`<div class="status-line"><span>Formats in the database</span><span>${stats.formats || 0}</span></div>`);
    rows.push(`<div class="status-line"><span>Szablony</span><span>${stats.templates || 0}</span></div>`);
    if (stats.last_ok) rows.push(`<div class="status-line"><span>Last print</span><span>${new Date(stats.last_ok * 1000).toLocaleString('pl-PL')}</span></div>`);
    for (const item of (stats.by_sku || []).slice(0, 4)) {
      rows.push(`<div class="status-line"><span class="mono">${escapeHtml(String(item.sku))}</span><span>${item.labels}</span></div>`);
    }
    if ((stats.recent || []).length) {
      rows.push('<h4>Ostatnie</h4>');
      for (const job of stats.recent) {
        const when = new Date(job.started_at * 1000).toLocaleString('pl-PL');
        rows.push(`<div class="status-line"><span>${when}</span><span>${job.state} · ${job.copies} szt.</span></div>`);
      }
    }
    body.innerHTML = rows.join('');
  } catch (_) {
    body.innerHTML = '<div class="empty">no data yet</div>';
  }
}

function sheetDataRows() {
  return (state.spec?.sheet?.rows || []).length;
}

const DENSITY_LABELS = ['faintest', 'lighter', 'standard', 'darker', 'darkest'];

/* Density is the printer's own heat setting (the vendor calls it "print density", range 1-5).
   The last value the user picked wins; otherwise follow what the printer reports. */
function storedDensity() {
  const clamp = (value) => (Number.isFinite(value) ? Math.max(1, Math.min(5, Math.round(value))) : null);
  const savedDensity = readPref('density');
  if (savedDensity !== null && String(savedDensity).trim() !== '') {
    const saved = clamp(Number(savedDensity));
    if (saved) return saved;
  }
  const device = clamp(Number(state.printer?.info?.density));
  if (device) return device;
  return clamp(Number(state.spec?.density)) || 3;
}

function rememberDensity(value) {
  writePref('density', value);
}

/* One place that starts a print: asks for the copy count and, when the sheet holds more rows
   than the edited one, whether to run the whole sheet (each row × copies). */
function openPrintDialog({ fromSheet = false } = {}) {
  if (!state.connected) {
    toast('Connect the printer first.', 'error');
    return;
  }
  const extraRows = sheetDataRows();
  const sheetRows = extraRows + 1; // the edited label is row 1
  const hasSheet = extraRows > 0;
  const defaults = Math.max(1, Math.min(99, Number(state.spec.copies) || 1));

  const body = document.createElement('div');
  body.className = 'print-dialog';
  body.innerHTML = `
    <div class="print-row">
      <label class="print-label" for="print-copies">Copies</label>
      <input class="print-copies" id="print-copies" type="number" min="1" max="99" step="1" value="${defaults}">
    </div>
    <div class="print-row">
      <span class="print-label">Density</span>
      <div class="density-group" id="print-density" role="group" aria-label="Print density">
        ${[1, 2, 3, 4, 5].map((value) => `<button type="button" class="density-step" data-density="${value}">${value}</button>`).join('')}
      </div>
      <span class="print-density-hint" id="print-density-hint"></span>
    </div>
    ${hasSheet ? `<label class="check"><input type="checkbox" id="print-from-sheet" ${fromSheet ? 'checked' : ''}>
      <span>Print the sheet</span> <span class="print-rows">(${sheetRows} rows)</span></label>` : ''}
    <div class="print-summary" id="print-summary"></div>
  `;

  const footer = document.createElement('div');
  footer.className = 'row';
  footer.style.cssText = 'justify-content:flex-end;gap:8px';
  footer.innerHTML = `
    <button class="btn" data-print-cancel>Cancel</button>
    <button class="btn primary" data-print-ok><i data-lucide="printer"></i> Print</button>`;

  const modal = openModal({
    id: 'print',
    title: 'Print',
    icon: 'printer',
    width: 430,
    height: hasSheet ? 356 : 304,
    body,
    footer,
  });
  fitModalToContent(modal, { min: 196, max: 460 });

  const copiesInput = $('#print-copies', body);
  const sheetToggle = $('#print-from-sheet', body);
  const summary = $('#print-summary', body);
  const mirrorOn = !!mirrorRequest();
  const densityHint = $('#print-density-hint', body);
  const densityButtons = $$('.density-step', body);
  let density = storedDensity();

  const paintDensity = () => {
    densityButtons.forEach((button) => {
      const on = Number(button.dataset.density) === density;
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (densityHint) densityHint.textContent = t(DENSITY_LABELS[density - 1] || '');
  };

  densityButtons.forEach((button) => {
    button.onclick = () => {
      density = Number(button.dataset.density);
      paintDensity();
      paint();
    };
  });

  const paint = () => {
    const copies = Math.max(1, Math.min(99, Number(copiesInput.value) || 1));
    const useSheet = !!sheetToggle?.checked;
    const rows = useSheet ? sheetRows : 1;
    const total = rows * copies;
    const parts = [
      useSheet ? `${rows} rows × ${copies} ${copies === 1 ? 'copy' : 'copies'}` : `${copies} ${copies === 1 ? 'copy' : 'copies'} of this label`,
      `${total} ${total === 1 ? 'label' : 'labels'}`,
      `density ${density}`,
    ];
    if (mirrorOn) parts.push('mirror fills the extra regions');
    summary.textContent = parts.join(' · ');
  };
  copiesInput.addEventListener('input', paint);
  if (sheetToggle) sheetToggle.addEventListener('change', paint);
  paintDensity();
  paint();

  const start = async () => {
    const copies = Math.max(1, Math.min(99, Number(copiesInput.value) || 1));
    const useSheet = !!sheetToggle?.checked;
    state.spec.copies = copies;
    state.spec.density = density;
    rememberDensity(density);
    pushHistory();
    closeModal('print');
    try {
      if (useSheet) {
        const result = await api('/api/print-sheet', 'POST', {
          spec: state.spec,
          rows: state.spec.sheet.rows,
          include_current: true,
          copies,
          density,
          mirror: mirrorRequest(),
        });
        renderJobStatus(result.job || { state: 'running', progress: 0, message: 'Sheet queued…' });
      } else {
        await api('/api/print', 'POST', {
          spec: state.spec,
          copies,
          density,
          mirror: mirrorRequest(),
        });
        renderJobStatus({ state: 'running', progress: 0, message: 'Job accepted…' });
      }
      pollJob();
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  $('[data-print-cancel]', footer).onclick = () => closeModal('print');
  $('[data-print-ok]', footer).onclick = start;
  body.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); start(); }
  });
  refreshIcons(footer);
  setTimeout(() => copiesInput.focus({ preventScroll: true }), 60);
}

function printLabel() {
  openPrintDialog({ fromSheet: false });
}

/* Change the label orientation: turn the whole design 90° with the label, or just swap the canvas. */
function setOrientation(mode) {
  const spec = state.spec;
  if (!spec) return;
  const w = Number(spec.width_mm) || 0;
  const h = Number(spec.height_mm) || 0;
  if (!w || !h) return;
  const rotate = mode === 'cw' || mode === 'ccw';
  if (rotate) {
    for (const element of spec.elements) {
      const x = element.x;
      const y = element.y;
      const ew = element.w;
      const eh = element.h;
      if (mode === 'cw') {
        element.x = round2(h - y - eh);
        element.y = round2(x);
      } else {
        element.x = round2(y);
        element.y = round2(w - x - ew);
      }
      element.w = eh;
      element.h = ew;
      element.rotation = ((Number(element.rotation) || 0) + (mode === 'cw' ? 90 : 270)) % 360;
    }
  }
  spec.width_mm = h;
  spec.height_mm = w;
  clampElements();
  syncSpec();
  layoutStage();
  pushHistory();
  schedulePreview();
  toast(`Label is now ${round2(h)} × ${round2(w)} mm${rotate ? ' — the design turned with it' : ''}`, 'ok');
}

function openOrientationDialog() {
  const spec = state.spec;
  if (!spec) return;
  const body = document.createElement('div');
  body.className = 'print-dialog';
  body.innerHTML = `
    <div class="hint">Now: <b>${round2(spec.width_mm)} × ${round2(spec.height_mm)} mm</b>.
      Turning the label makes it <b>${round2(spec.height_mm)} × ${round2(spec.width_mm)} mm</b>.</div>
    <button type="button" class="btn wide" data-orient="cw"><i data-lucide="rotate-cw"></i> Rotate design 90° right</button>
    <button type="button" class="btn wide" data-orient="ccw"><i data-lucide="rotate-ccw"></i> Rotate design 90° left</button>
    <button type="button" class="btn wide" data-orient="swap"><i data-lucide="rectangle-horizontal"></i> Swap the label only (keep the design)</button>
  `;
  const footer = document.createElement('div');
  footer.className = 'row';
  footer.style.cssText = 'justify-content:flex-end;gap:8px';
  footer.innerHTML = '<button class="btn" data-orient-cancel>Cancel</button>';
  const modal = openModal({
    id: 'orientation',
    title: 'Label orientation',
    icon: 'rotate-cw',
    width: 430,
    height: 300,
    body,
    footer,
  });
  fitModalToContent(modal, { min: 220, max: 420 });
  $('[data-orient-cancel]', footer).onclick = () => closeModal('orientation');
  $$('[data-orient]', body).forEach((button) => {
    button.onclick = () => {
      closeModal('orientation');
      setOrientation(button.dataset.orient);
    };
  });
}

async function testPrint() {
  try {
    await api('/api/test-print', 'POST');
    renderJobStatus({ state: 'running', progress: 0, message: 'Test print…' });
    pollJob();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function detectSize() {
  try {
    const result = await api('/api/detect-size', 'POST');
    applyState(result.state);
    if (result.detected) {
      const geo = result.geometry;
      applySize(geo.width_mm, geo.height_mm, { gap_mm: geo.gap_mm ?? state.spec.gap_mm, label_type: geo.label_type ?? state.spec.label_type });
      toast(`Wykryto format ${geo.width_mm} × ${geo.height_mm} mm`, 'ok');
    } else {
      toast(result.detail || 'The printer did not report a format.', 'error');
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

function exportPng() {
  if (!state.preview) return;
  const link = document.createElement('a');
  // Export exactly what the printer gets (1-bit at print resolution), not the smooth screen render.
  link.href = `data:image/png;base64,${state.preview.png_print || state.preview.png}`;
  link.download = `${(state.spec.name || 'label').replace(/\s+/g, '-')}.png`;
  link.click();
}

async function saveLabel() {
  if (!state.labelId) {
    return saveLabelAs();
  }
  const result = await api('/api/labels', 'POST', { id: state.labelId, spec: state.spec });
  state.labelId = result.id;
  toast('Label saved', 'ok');
  clearDraft();
}

async function saveLabelAs() {
  const name = await askText({
    title: 'Save label as',
    icon: 'file-plus',
    label: 'Label name',
    value: state.spec.name || 'Label',
    okLabel: 'Save as copy',
    hint: 'Creates a new entry in the database — the current label stays untouched.',
  });
  if (!name) return;
  const previous = state.spec.name;
  state.spec.name = name;
  try {
    const result = await api('/api/labels', 'POST', { id: null, spec: state.spec });
    state.labelId = result.id;
    state.options.labels = result.labels;
    clearDraft();
    toast(`Saved as “${name}”`, 'ok');
  } catch (error) {
    state.spec.name = previous;
    toast(error.message, 'error');
  }
}

async function saveAsTemplate() {
  const name = await askText({
    title: 'Save as template',
    icon: 'layout-template',
    label: 'Template name',
    value: state.spec.name || 'Template',
    okLabel: 'Save template',
    hint: 'Templates store the whole layout (elements, sheet columns and mirror settings).',
  });
  if (!name) return;
  try {
    const result = await api('/api/templates', 'POST', { id: null, name, spec: state.spec });
    if (state.options) {
      const stored = await api('/api/templates');
      state.options.templates = buildTemplateList(stored.templates);
    }
    toast(`Template “${name}” saved`, 'ok');
    return result;
  } catch (error) {
    toast(error.message, 'error');
  }
}

function buildTemplateList(userTemplates) {
  const builtins = (state.options?.templates || []).filter((item) => item.source !== 'saved');
  return [
    ...builtins,
    ...(userTemplates || []).map((item) => ({ id: item.id, name: item.name, spec: item.spec, source: 'saved' })),
  ];
}

async function openLabelsModal() {
  const { labels } = await api('/api/labels');
  const body = document.createElement('div');
  body.className = 'modal-content';
  body.innerHTML = labels.length
    ? `<div class="device-list">${labels.map((label) => `
        <div class="device">
          <div><strong>${escapeHtml(label.name)}</strong><small>${label.width_mm} × ${label.height_mm} mm</small></div>
          <button class="btn" data-id="${escapeHtml(label.id)}">Open</button>
        </div>`).join('')}</div>`
    : '<div class="empty">No saved labels yet</div>';

  openModal({ id: 'labels', title: 'Load label', icon: 'folder-open', width: 520, height: 420, body });
  $$('button[data-id]', body).forEach((button) => {
    button.onclick = async () => {
      const data = await api(`/api/labels/${button.dataset.id}`);
      setSpec(data.spec, { labelId: button.dataset.id });
      closeModal('labels');
      toast(`Loaded “${data.spec.name}”`, 'ok');
    };
  });
}

function pollJob() {
  clearInterval(state.jobTimer);
  state.jobTimer = setInterval(async () => {
    try {
      const { job } = await api('/api/job');
      renderJobStatus(job);
      if (job && ['done', 'error', 'cancelled'].includes(job.state)) {
        clearInterval(state.jobTimer);
        if (job.state === 'done') {
          toast('Label printed', 'ok');
          setTimeout(() => refreshPrinter().catch(() => {}), 1200);
        } else if (job.state === 'error') {
          toast(job.error || 'Print failed', 'error');
        }
      }
    } catch (error) {
      clearInterval(state.jobTimer);
      toast(error.message, 'error');
    }
  }, 400);
}

/* ================================================================= connect */
async function connect(address, protocol, label = '') {
  const chosen = protocol && protocol !== 'auto' ? protocol : (state.printerProtocol === 'auto' ? 'auto' : state.printerProtocol);
  const spinner = toast(`Connecting to ${label || address}…`, 'info', 0);
  try {
    const payload = await api('/api/connect', 'POST', { address, protocol: chosen || 'auto' });
    spinner.remove();
    applyState(payload);
    toast(`Connected: ${payload.info.model_name}`, 'ok');
    const paper = payload.paper || {};
    if (!applyKnownFormat(paper) && payload.profile) {
      applySize(payload.profile.width_mm, payload.profile.height_mm, {
        gap_mm: payload.profile.gap_mm ?? state.spec.gap_mm,
        label_type: payload.profile.label_type ?? state.spec.label_type,
      });
    }
    await requestPreview();
    captureSettingFromModel();
    renderSettingsTab();
  } catch (error) {
    spinner.remove();
    toast(`Could not connect to ${label || address}: ${error.message}`, 'error');
  }
}

function captureSettingFromModel() {
  const model = state.printer?.info?.model_name;
  if (model && model !== 'unknown') storeLocal('lastModel', model);
}

function storeLocal(key, value) {
  writePref(key, value);
}

/* --------------------------------------------------- last used calibration */
/* The screen keeps showing the calibration of the last roll that was inserted; the printer
   only reports the roll code while it is connected, so this survives a reload off-line. */
function rememberLastProfile(profile) {
  writePref('lastProfile', profile);
}

function loadLastProfile() {
  const saved = readPref('lastProfile');
  if (saved && saved.key) {
    state.lastProfile = saved;
    return saved;
  }
  return null;
}

/* =================================================================== state */
function applyState(payload) {
  const wasConnected = state.connected;
  const previousScope = state.deviceScope;
  state.printer = payload;
  state.connected = Boolean(payload.connected);
  if (payload.protocol && state.printerProtocol !== 'auto') state.printerProtocol = payload.protocol;
  state.deviceScope = payload.device_scope || previousScope || null;
  if (state.deviceScope && previousScope && state.deviceScope !== previousScope) {
    // A different printer type: labels, templates and calibrations are its own.
    switchToDeviceScope(payload);
  }
  /* A calibration always follows the database for the roll that is inserted. Only when the
     printer is away (no roll code at all) does the last-used one stay on screen, flagged. */
  const syncProfile = payload.profile || null;
  if (state.connected) {
    // Never show another roll's calibration: an uncalibrated roll simply has none.
    state.printer.profile = syncProfile;
    if (syncProfile) {
      state.lastProfile = syncProfile;
      rememberLastProfile(syncProfile);
    }
  } else if (state.lastProfile) {
    state.printer.profile = state.lastProfile;
  }
  /* Off-line the roll code cannot be checked against the database: flag what is on screen. */
  state.calibrationOffline = !state.connected && !!state.printer.profile;
  renderStatusBar();
  renderStageMeta();
  renderPaperRegions();
  renderJobStatus(payload.job || null);
  if (state.connected) startStatusTimer(); else stopStatusTimer();
  updatePaperWait();

  /* The roll changed (or the printer just connected): re-apply the stored calibration. */
  const profileKey = syncProfile?.key || null;
  if (profileKey !== state.appliedProfileKey) {
    state.appliedProfileKey = profileKey;
    if (syncProfile) {
      renderPaperRegions();
      renderStageMeta();
      schedulePreview();
    }
  }
  if (state.spec) layoutStage();
  if (isModalOpen('settings')) renderSettingsTab();

  const paper = payload.paper || {};
  const paperKey = [paper.barcode, paper.detected_width_mm, paper.detected_height_mm, paper.label_type].join('|');
  const freshPaper = state.connected && paperKey !== state.lastPaperKey;
  if (freshPaper) {
    state.lastPaperKey = paperKey;
    // RFID of the roll just arrived (connect or a roll swap) — update the whole screen.
    const rollName = paper.sku || paper.catalog?.name || paper.barcode || 'RFID read';
    if (!wasConnected) toast(`Roll detected: ${rollName}`, 'ok');
    const calibrated = Boolean(payload.profile);
    if (!calibrated && paper.barcode) {
      // A roll with no stored calibration always asks once per insertion, not per poll.
      if (state.promptedRoll !== paper.barcode) {
        state.promptedRoll = paper.barcode;
        toast(`Roll ${paper.barcode}: no calibration in the database for it.`, 'error');
        setTimeout(() => {
          if (state.connected && !isModalOpen('roll-size') && !isModalOpen('calibration-picker')) {
            openCalibrationPicker({ reason: 'unknown' });
          }
        }, 400);
      }
    } else if (calibrated) {
      state.promptedRoll = null;
    }
    applyKnownFormat(paper);
    // The stored calibration also knows the page size, even when the catalogue does not.
    const stored = payload.profile;
    if (stored?.width_mm && stored?.height_mm && state.spec) {
      const sameSize = Math.abs(state.spec.width_mm - stored.width_mm) < 0.05
        && Math.abs(state.spec.height_mm - stored.height_mm) < 0.05;
      if (!sameSize) {
        applySize(stored.width_mm, stored.height_mm, {
          gap_mm: stored.gap_mm ?? gapInfoMm(),
          label_type: stored.label_type ?? state.spec.label_type,
        });
        toast(`Calibration „${stored.name}”: ${round2(stored.width_mm)} × ${round2(stored.height_mm)} mm`, 'ok');
      }
    }
    renderStatusBar();
    renderStageMeta();
    if (state.view === 'sheet') renderSheet();
    schedulePreview();
    if (isModalOpen('settings')) renderSettingsTab();
  }
  if (!state.connected) state.lastPaperKey = null;
}

async function refreshState() {
  const payload = await api('/api/state');
  applyState(payload);
}

/* ------------------------------------------------------- waiting for a roll */
/* After a swap the printer reports no paper for a while. Keep a spinner on screen and poll
   quickly, so the new roll (and its calibration) appears the moment it is inserted. */
let paperWait = { node: null, timer: null };

function paperMissing() {
  const paper = state.printer?.paper || {};
  if (!state.connected) return false;
  return paper.paper_inserted === false || paper.lid_closed === false || !paper.barcode;
}

function startPaperWait() {
  if (paperWait.node) return;
  const node = document.createElement('div');
  node.className = 'toast waiting';
  node.innerHTML = '<span class="spinner"></span> Waiting for the roll — insert the paper, '
    + 'the calibration loads by itself';
  $('#toast').appendChild(node);
  paperWait.node = node;
  paperWait.timer = setInterval(async () => {
    if (!state.connected) {
      stopPaperWait();
      return;
    }
    try {
      const payload = await api('/api/refresh', 'POST');
      applyState(payload);
    } catch (_) { /* the printer is busy feeding — try again in a moment */ }
    if (!paperMissing()) stopPaperWait();
  }, 1500);
}

function stopPaperWait() {
  if (paperWait.timer) clearInterval(paperWait.timer);
  paperWait.timer = null;
  paperWait.node?.remove();
  paperWait.node = null;
}

function updatePaperWait() {
  if (paperMissing()) startPaperWait();
  else if (paperWait.node) stopPaperWait();
}

function startStatusTimer() {
  if (state.statusTimer) return;
  state.statusTimer = setInterval(async () => {
    if (!state.connected) return;
    const job = state.printer?.job;
    if (job && job.state === 'running') return;
    try {
      const payload = await api('/api/refresh', 'POST');
      applyState(payload);
    } catch (_) { /* transient */ }
  }, 12000);
}

/* Picks up a background auto-connect (the server connects to the remembered printer
   on its own), and keeps the paper panel fresh until that happens. */
function startWatchTimer() {
  if (state.watchTimer) return;
  state.watchTimer = setInterval(async () => {
    if (state.connected) return;
    try {
      const payload = await api('/api/state');
      state.printer = payload;
      updateConnectionChip(payload);
      if (payload.connected) {
        applyState(payload);
        toast('Printer connected automatically', 'ok');
      }
    } catch (_) { /* ignore */ }
  }, 4000);
}

function stopStatusTimer() {
  clearInterval(state.statusTimer);
  state.statusTimer = null;
}

/* ==================================================================== init */
function bindControls() {
  $('#menubar').addEventListener('click', (event) => {
    const item = event.target.closest('.menu-item');
    if (item) toggleMenu(item.dataset.menu);
  });
  document.addEventListener('pointerdown', (event) => {
    if (!event.target.closest('#menubar') && !event.target.closest('#menu-dropdown')) {
      const dropdown = $('#menu-dropdown');
      if (dropdown && !dropdown.hidden) {
        dropdown.hidden = true;
        openMenuKey = null;
        $$('.menu-item').forEach((item) => item.classList.remove('active'));
      }
    }
  });

  $$('[data-add]').forEach((button) => { button.onclick = () => addElement(button.dataset.add); });
  $('#btn-undo').onclick = undo;
  $('#btn-redo').onclick = redo;
  $('#btn-save').onclick = () => saveLabel().catch((error) => toast(error.message, 'error'));
  $('#btn-open').onclick = () => openLabelsModal().catch((error) => toast(error.message, 'error'));
  $('#btn-png').onclick = exportPng;
  $('#btn-print').onclick = printLabel;
  $('#btn-settings').onclick = () => openSettings('printer');

  document.addEventListener('click', (event) => {
    const testButton = event.target.closest('#btn-test-settings');
    if (testButton) testPrint();
  });

  $('#floating-toolbar').addEventListener('click', (event) => {
    const button = event.target.closest('[data-ft]');
    if (!button) return;
    const action = button.dataset.ft;
    if (action === 'duplicate') duplicateSelected();
    if (action === 'delete') deleteSelected();
    if (action === 'rotate') rotateSelected();
    if (action === 'up') moveLayer(state.selectedId, 1);
    if (action === 'down') moveLayer(state.selectedId, -1);
    if (action === 'align-h') alignSelected('h');
    if (action === 'align-v') alignSelected('v');
  });

  $('#stage-meta').addEventListener('click', (event) => {
    if (event.target.closest('#meta-mirror')) toggleMirror();
  });

  $('#sb-conn').onclick = () => openSettings(state.connected ? 'paper' : 'printer');
  $('#sb-conn').oncontextmenu = (event) => {
    event.preventDefault();
    openPrinterSwitcher(event);
  };
  $('#sb-paper').onclick = () => openSettings('paper');
  $('#sb-format').onclick = () => openSettings('paper');

  $('#layer-up').onclick = () => { if (state.selectedId) moveLayer(state.selectedId, 1); };
  $('#layer-down').onclick = () => { if (state.selectedId) moveLayer(state.selectedId, -1); };
  $('#layer-duplicate').onclick = () => duplicateSelected();
  $('#layer-delete').onclick = () => deleteSelected();

  $('#btn-grid').onclick = () => toggleGrid();
  $('#btn-snap').onclick = () => toggleSnap();
  $('#btn-icons').onclick = () => openIconPicker();

  bindSheetControls();
  bindSplitters();

  // Right-click on an element: the menu is where an element is deleted, duplicated or reordered
  // (the keyboard only deletes with Delete, never with Backspace).
  document.addEventListener('contextmenu', (event) => {
    if (event.target.closest('#inspector-card') || event.target.closest('.panel')) return;
    const box = event.target.closest('#overlay .element-box');
    if (!box) return;
    const element = state.spec?.elements.find((item) => item.id === box.dataset.id);
    if (!element) return;
    event.preventDefault();
    state.selectedId = element.id;
    renderOverlay();
    buildInspector();
    const locked = isMirrorLocked(element);
    showContextMenu(event, [
      { key: 'duplicate', icon: 'copy', label: 'Duplicate', hint: 'Ctrl+D', run: () => duplicateSelected() },
      { key: 'up', icon: 'chevron-up', label: 'Move up', run: () => moveLayer(element.id, 1) },
      { key: 'down', icon: 'chevron-down', label: 'Move down', run: () => moveLayer(element.id, -1) },
      { key: 'hidden', icon: element.visible === false ? 'eye' : 'eye-off',
        label: element.visible === false ? 'Show' : 'Hide', run: () => toggleElementVisible(element) },
      { sep: true },
      { key: 'delete', icon: 'trash-2', label: locked ? 'Delete (mirror copy)' : 'Delete', hint: 'Del',
        run: () => { state.selectedId = element.id; deleteSelected(); } },
    ]);
  });

  document.addEventListener('pointerdown', onPointerDown);
  document.addEventListener('pointermove', onPointerMove);
  document.addEventListener('pointermove', updateHover);
  document.addEventListener('pointerup', () => setHover(null));
  $('#stage')?.addEventListener('pointerleave', () => setHover(null));
  document.addEventListener('pointerup', onPointerUp);
  document.addEventListener('pointercancel', onPointerUp);

  document.addEventListener('keydown', (event) => {
    const tag = (event.target.tagName || '').toLowerCase();
    const inField = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (event.key === 'Escape' && modalStack.length) {
      closeModal(modalStack[modalStack.length - 1].id);
      return;
    }
    if (inField) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); undo(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') { event.preventDefault(); duplicateSelected(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveLabel().catch((error) => toast(error.message, 'error')); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') { event.preventDefault(); openNewLabelDialog(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'p') { event.preventDefault(); printLabel(); }
    else if ((event.ctrlKey || event.metaKey) && event.key === ',') { event.preventDefault(); openSettings('printer'); }
    // Only Delete removes an element: Backspace belongs to text editing, where deleting the
    // whole element after a typo is the last thing anyone expects.
    else if (event.key === 'Delete') { if (state.selectedId) { event.preventDefault(); deleteSelected(); } }
    else if (event.key === 'ArrowLeft' && state.selectedId) { event.preventDefault(); nudge(-1, 0, event); }
    else if (event.key === 'ArrowRight' && state.selectedId) { event.preventDefault(); nudge(1, 0, event); }
    else if (event.key === 'ArrowUp' && state.selectedId) { event.preventDefault(); nudge(0, -1, event); }
    else if (event.key === 'ArrowDown' && state.selectedId) { event.preventDefault(); nudge(0, 1, event); }
    else if (event.key.toLowerCase() === 'g') { event.preventDefault(); toggleGrid(); }
    else if (event.key.toLowerCase() === 's') { event.preventDefault(); toggleSnap(); }
  });

  document.addEventListener('input', (event) => {
    if (event.target.closest('#inspector-card')) onInspectorInput(event);
  });
  document.addEventListener('focusin', (event) => {
    if (event.target.closest('#inspector-card') && event.target.dataset?.key) pushHistory();
  });

  $('#file-input').onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try { await uploadImage(file); } catch (error) { toast(error.message, 'error'); }
    event.target.value = '';
  };

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { layoutStage(); layoutFloatingToolbar(); }, 120);
  });
}

async function init() {
  await loadUiPrefs();
  state.showAllBle = readPref('showAllBle', false) === true;
  startBackendWatch();
  const savedLanguage = readPref('language');
  if (savedLanguage && window.I18N) {
    window.I18N.setLanguage(savedLanguage, { silent: true, persist: false });
  }
  bindControls();
  bindQueueActions();
  bindQueueSplitter();
  startQueueWatch();
  loadViewPrefs();
  syncViewToggles();
  loadLastProfile();
  try {
    const [options, payload] = await Promise.all([api('/api/options'), api('/api/state')]);
    state.options = options;
    state.catalog = options.catalog || [];
    setSpec(options.default_spec);
    applyState(payload);
    adoptLegacyDraft();
    if (restoreDraft()) {
      toast('Restored the last unsaved draft', 'ok');
    } else if (state.deviceScope && state.connected) {
      // First time on this printer type: start from a label that fits its printhead.
      switchToDeviceScope(state.printer);
    }
    applyKnownFormat(payload.paper);
    renderStatusBar();
    await requestPreview();
    if (state.connected) startStatusTimer();
    startWatchTimer();
    refreshIcons();
  } catch (error) {
    toast(`Could not start the application: ${error.message}`, 'error');
  }
}

window.addEventListener('DOMContentLoaded', init);
