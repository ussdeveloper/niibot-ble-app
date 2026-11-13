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
const GRID_MM = 1;
const GUIDE_TOLERANCE_PX = 6;

function snapToStep(value, free) {
  if (free || !state.snapToGrid) return round2(value);
  return Math.round(value / GRID_MM) * GRID_MM;
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

function toast(message, kind = 'info') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  $('#toast').appendChild(node);
  setTimeout(() => node.remove(), kind === 'error' ? 6000 : 3000);
}

async function api(path, method = 'GET', body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
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

/* ==================================================================== state */
const state = {
  spec: null,
  options: null,
  printer: null,
  connected: false,
  selectedId: null,
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
  catalog: [],
  lastDevices: null,
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

function saveDraft() {
  try {
    localStorage.setItem('niibot.draft', JSON.stringify({
      at: Date.now(),
      labelId: state.labelId,
      view: state.view,
      spec: state.spec,
    }));
  } catch (_) { /* quota / private mode */ }
}

function clearDraft() {
  clearTimeout(autosaveTimer);
  try { localStorage.removeItem('niibot.draft'); } catch (_) { /* ignore */ }
}

function restoreDraft() {
  try {
    const raw = localStorage.getItem('niibot.draft');
    if (!raw) return false;
    const draft = JSON.parse(raw);
    if (!draft || !draft.spec || !Array.isArray(draft.spec.elements)) return false;
    setSpec(draft.spec, { labelId: draft.labelId || null });
    if (draft.view === 'sheet') switchView('sheet');
    return true;
  } catch (_) {
    return false;
  }
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
  pushHistory();
  syncSpec();
  buildInspector();
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
  const step = Math.max(4, GRID_MM * state.pxPerMm);
  layer.style.setProperty('--grid-x', `${step}px`);
  layer.style.setProperty('--grid-y', `${step}px`);
}

function renderStageMeta() {
  const meta = $('#stage-meta');
  if (!meta || !state.spec) return;
  const spec = state.spec;
  const geo = paperGeometry();
  const paper = state.printer?.paper || {};
  const rollName = paper.sku || paper.catalog?.name || paper.barcode;
  const known = !!geo.catalog || Number(paper.detected_width_mm) > 0 || !!state.printer?.profile;
  const chips = [`<span class="meta-chip"><b>${round2(spec.width_mm)} × ${round2(spec.height_mm)} mm</b></span>`];
  if (geo.typeName) chips.push(`<span class="meta-chip type">paper: ${escapeHtml(geo.typeName)}</span>`);
  if (rollName) chips.push(`<span class="meta-chip mono">${escapeHtml(rollName)}</span>`);
  if (state.connected) {
    chips.push(`<button type="button" class="meta-chip${known ? '' : ' warn'}" id="meta-calibrate"
      title="Size and printable cells of this roll — reopen the calibrator any time">
      <i data-lucide="ruler"></i> ${known ? 'Correct calibration' : 'Calibrate this roll'}
    </button>`);
  }
  const rollProfile = state.printer?.profile;
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

/* A roll the printer/catalogue cannot describe: size it, draw the printable cells once,
   remember everything per roll code. */
function openRollSizeDialog(options = {}) {
  const paper = state.printer?.paper || {};
  const rollName = paper.sku || paper.catalog?.name || paper.barcode || 'this roll';
  const profile = state.printer?.profile || null;
  /* A friendly name for this roll (the barcode is a poor label everywhere in the UI). */
  const codeOnly = (value) => !value || /^\d+$/.test(String(value).trim());
  let rollLabel = !codeOnly(profile?.name) ? String(profile.name)
    : !codeOnly(paper.catalog?.name) ? String(paper.catalog.name)
      : '';
  const presets = (state.options?.paper_presets || []).map((preset) => ({
    label: preset.name || `${round2(preset.width_mm)} × ${round2(preset.height_mm)} mm`,
    width: preset.width_mm,
    height: preset.height_mm,
    rows: preset.rows || 1,
    type: preset.label_type,
  }));

  let width = Number(profile?.width_mm) || Number(state.spec?.width_mm) || 40;
  let height = Number(profile?.height_mm) || Number(state.spec?.height_mm) || 30;
  let gap = Number(profile?.gap_mm ?? gapInfoMm()) || 0;
  let rows = Math.max(1, Number(profile?.rows || paper.catalog?.rows) || 1);
  let cols = Math.max(1, Number(profile?.cols || paper.catalog?.cols) || 1);
  let rowSpace = 0;
  let colSpace = 0;
  let cells = (profile?.regions || []).map((cell) => ({ ...cell }));
  let selected = cells.length ? 0 : -1;
  let selection = new Set(selected >= 0 ? [selected] : []);

  const body = document.createElement('div');
  body.className = 'calibrator';
  body.innerHTML = `
    <div class="cal-col cal-col-left">
      <div class="hint">The B1 cannot report the label size over Bluetooth and
        <code>${escapeHtml(rollName)}</code> is not in the catalogue on this machine.
        Set the size, lay out the cells, then fine-tune them — everything is remembered for this roll code.</div>

      <div class="cal-section">
        <div class="cal-section-head">Grid layout</div>
        <div class="grid-2">
          ${field('Rows', numberInput('cal-rows', rows, 1, 1, 12))}
          ${field('Columns', numberInput('cal-cols', cols, 1, 1, 12))}
        </div>
        <div class="grid-2">
          ${field('Row spacing (mm)', numberInput('cal-rowspace', rowSpace, 0.5, 0, 50))}
          ${field('Column spacing (mm)', numberInput('cal-colspace', colSpace, 0.5, 0, 50))}
        </div>
        <button type="button" class="btn wide" data-cal="grid"><i data-lucide="grid-2x2"></i> Spread the grid over the label</button>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Roll size</div>
        <div class="field">
          <label for="cal-name">Roll name</label>
          <input id="cal-name" data-key="cal-name" value="${escapeHtml(rollLabel)}"
            placeholder="e.g. Round 21 mm white" autocomplete="off">
        </div>
        <div class="grid-2">
          ${field('Width (mm)', numberInput('cal-width', round2(width), 0.5, 5, 108))}
          ${field('Height (mm)', numberInput('cal-height', round2(height), 0.5, 5, 300))}
        </div>
        <div class="grid-2">
          ${field('Label gap (mm)', numberInput('cal-gap', round2(gap), 0.5, 0, 20))}
          ${presets.length ? `<div class="field"><label for="cal-preset">Common size</label>
            <select id="cal-preset"><option value="">— pick one —</option>
              ${presets.map((preset, index) => `<option value="${index}">${escapeHtml(preset.label)}${preset.rows > 1 ? ` · ${preset.rows} rows` : ''}</option>`).join('')}
            </select></div>` : '<div></div>'}
        </div>
      </div>

      <div class="cal-section">
        <div class="cal-section-head">Draw</div>
        <div class="cal-tools" role="group" aria-label="Drawing tool">
          <button type="button" class="cal-tool on" data-tool="select"><i data-lucide="mouse-pointer-2"></i> Select</button>
          <button type="button" class="cal-tool" data-tool="rect"><i data-lucide="square"></i> Rectangle</button>
          <button type="button" class="cal-tool" data-tool="circle"><i data-lucide="circle"></i> Circle</button>
          <button type="button" class="cal-tool" data-tool="line"><i data-lucide="minus"></i> Line</button>
        </div>
        <div class="cal-toolbar">
          <button type="button" class="btn" data-cal="delete"><i data-lucide="trash-2"></i> Delete</button>
          <button type="button" class="btn" data-cal="undo" title="Undo (Ctrl+Z)"><i data-lucide="undo-2"></i></button>
          <button type="button" class="btn" data-cal="redo" title="Redo (Ctrl+Y)"><i data-lucide="redo-2"></i></button>
        </div>
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
        <div class="hint">Shift+click cells to select several.</div>
      </div>

      <div class="cal-cellchips" id="cal-cellchips"></div>
    </div>

    <div class="cal-col cal-col-right">
      <div class="cal-stage-wrap">
        <div class="cal-viewport" id="cal-viewport">
          <div class="cal-stage" id="cal-stage"><div class="cal-cells" id="cal-cells"></div></div>
        </div>
        <div class="cal-zoombar">
          <button type="button" class="btn" data-zoom="out">−</button>
          <span class="cal-zoom-label" id="cal-zoom-label">100%</span>
          <button type="button" class="btn" data-zoom="in">+</button>
          <button type="button" class="btn" data-zoom="fit">Fit</button>
          <span class="hint">wheel = zoom · drag = pan / draw · Del = delete</span>
        </div>
      </div>
      <div class="cal-editor" id="cal-editor"></div>
      <div class="cal-summary" id="cal-summary"></div>
    </div>
  `;

  const footer = document.createElement('div');
  footer.className = 'row';
  footer.style.cssText = 'justify-content:flex-end;gap:8px';
  footer.innerHTML = `
    <button class="btn" data-cal-cancel>Cancel</button>
    <button class="btn primary" data-cal-save><i data-lucide="ruler"></i> Save for this roll</button>`;

  const modal = openModal({
    id: 'roll-size',
    title: 'Calibrate this roll',
    icon: 'ruler',
    width: 760,
    height: 840,
    body,
    footer,
  });
  fitModalToContent(modal, { min: 480, max: 940 });

  const stage = $('#cal-stage', body);
  const viewport = $('#cal-viewport', body);
  const cellsBox = $('#cal-cells', body);
  const editor = $('#cal-editor', body);
  const summary = $('#cal-summary', body);
  const zoomLabel = $('#cal-zoom-label', body);
  const chipsBox = $('#cal-cellchips', body);
  let tool = 'select';
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  /* True while the cells are just the rows × columns grid, so the fields can keep rebuilding it. */
  let gridMode = !cells.length;

  const num = (name) => Math.max(0, Number($(`[data-key="cal-${name}"]`, body)?.value) || 0);
  let lastFit = -1;

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const snap = (value) => Math.round(value * 2) / 2;

  const readSize = () => {
    width = Math.max(5, num('width'));
    height = Math.max(5, num('height'));
    rows = Math.max(1, Math.min(12, Math.round(num('rows')) || 1));
    cols = Math.max(1, Math.min(12, Math.round(num('cols')) || 1));
    gap = Math.max(0, num('gap'));
    rowSpace = Math.max(0, num('rowspace'));
    colSpace = Math.max(0, num('colspace'));
  };

  const applyView = () => {
    stage.style.transform = `translate(${Math.round(panX)}px, ${Math.round(panY)}px) scale(${zoom})`;
    // Keep the handles a constant size on screen: they live inside the scaled stage.
    stage.style.setProperty('--inv-zoom', String(1 / zoom));
    if (zoomLabel) zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
  };

  const selectedIndices = () => [...selection].filter((index) => cells[index]).sort((a, b) => a - b);
  const selectedCells = () => selectedIndices().map((index) => cells[index]);

  const selectOnly = (index) => {
    selection = new Set(index >= 0 && cells[index] ? [index] : []);
    selected = index;
    draw();
  };

  const toggleSelection = (index) => {
    if (index < 0 || !cells[index]) return;
    if (selection.has(index)) {
      selection.delete(index);
      if (selected === index) selected = selectedIndices()[0] ?? -1;
    } else {
      selection.add(index);
      selected = index;
    }
    draw();
  };

  /* Align / distribute the selected cells inside their common bounding box. */
  const alignSelection = (mode) => {
    const indices = selectedIndices();
    if (indices.length < 2) return;
    const list = indices.map((index) => cells[index]);
    const left = Math.min(...list.map((cell) => cell.x));
    const right = Math.max(...list.map((cell) => cell.x + cell.w));
    const top = Math.min(...list.map((cell) => cell.y));
    const bottom = Math.max(...list.map((cell) => cell.y + cell.h));
    const centerX = (left + right) / 2;
    const centerY = (top + bottom) / 2;
    const place = (cell, x, y) => {
      cell.x = snap(clamp(x, 0, Math.max(0, width - cell.w)));
      cell.y = snap(clamp(y, 0, Math.max(0, height - cell.h)));
    };
    if (mode === 'left') list.forEach((cell) => place(cell, left, cell.y));
    if (mode === 'right') list.forEach((cell) => place(cell, right - cell.w, cell.y));
    if (mode === 'hcenter') list.forEach((cell) => place(cell, centerX - cell.w / 2, cell.y));
    if (mode === 'top') list.forEach((cell) => place(cell, cell.x, top));
    if (mode === 'bottom') list.forEach((cell) => place(cell, cell.x, bottom - cell.h));
    if (mode === 'vmiddle') list.forEach((cell) => place(cell, cell.x, centerY - cell.h / 2));
    if (mode === 'dist-h' || mode === 'dist-v') {
      const horizontal = mode === 'dist-h';
      const sorted = [...list].sort((a, b) => (horizontal ? a.x - b.x : a.y - b.y));
      const size = sorted.reduce((sum, cell) => sum + (horizontal ? cell.w : cell.h), 0);
      const span = horizontal ? right - left : bottom - top;
      const gap = (span - size) / Math.max(1, sorted.length - 1);
      let cursor = horizontal ? left : top;
      sorted.forEach((cell) => {
        if (horizontal) {
          place(cell, cursor, cell.y);
          cursor += cell.w + gap;
        } else {
          place(cell, cell.x, cursor);
          cursor += cell.h + gap;
        }
      });
    }
    gridMode = false;
    commit();
  };

  /* Rows/columns that the drawn cells actually form (distinct start rows and columns). */
  const derivedGrid = () => {
    if (!cells.length) return { rows, cols };
    const rowsSeen = new Set(cells.map((cell) => Math.round(Number(cell.y) || 0)));
    const colsSeen = new Set(cells.map((cell) => Math.round(Number(cell.x) || 0)));
    return { rows: Math.max(1, rowsSeen.size), cols: Math.max(1, colsSeen.size) };
  };

  /* Undo history lives inside the dialog: every committed change is one step. */
  let history = [JSON.stringify(cells)];
  let historyIndex = 0;

  const commit = () => {
    const snapshot = JSON.stringify(cells);
    if (history[historyIndex] !== snapshot) {
      history = history.slice(0, historyIndex + 1);
      history.push(snapshot);
      if (history.length > 60) history.shift();
      historyIndex = history.length - 1;
    }
    draw();
  };

  const stepHistory = (delta) => {
    const next = historyIndex + delta;
    if (next < 0 || next >= history.length) return;
    historyIndex = next;
    cells = JSON.parse(history[historyIndex]);
    selected = cells.length ? Math.min(Math.max(selected, 0), cells.length - 1) : -1;
    selection = new Set(selected >= 0 ? [selected] : []);
    draw();
  };

  const deleteCell = (index) => {
    const targets = index === undefined || index === null
      ? selectedIndices()
      : [index];
    const valid = targets.filter((item) => item >= 0 && item < cells.length).sort((a, b) => b - a);
    if (!valid.length) return;
    valid.forEach((item) => cells.splice(item, 1));
    gridMode = false;
    const next = Math.max(-1, Math.min(valid[valid.length - 1], cells.length - 1));
    selection = new Set(next >= 0 ? [next] : []);
    selected = next;
    commit();
  };

  const applyGrid = () => {
    readSize();
    cells = [];
    // Spread every row and column evenly across the whole label, honouring the spacings.
    const cellW = Math.max(0.5, (width - (cols - 1) * colSpace) / cols);
    const cellH = Math.max(0.5, (height - (rows - 1) * rowSpace) / rows);
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        cells.push({
          shape: 'rect',
          x: round2(col * (cellW + colSpace)),
          y: round2(row * (cellH + rowSpace)),
          w: round2(cellW),
          h: round2(cellH),
          angle: 0,
          thickness_mm: 0.8,
          label: rows * cols > 1 ? `R${row + 1}C${col + 1}` : '',
        });
      }
    }
    gridMode = true;
    selected = cells.length ? 0 : -1;
    selection = new Set(selected >= 0 ? [selected] : []);
    commit();
  };

  const centreView = () => {
    const view = viewport.getBoundingClientRect();
    const stageW = parseFloat(stage.style.width) || 280;
    const stageH = parseFloat(stage.style.height) || 190;
    panX = Math.max(0, (view.width - stageW * zoom) / 2);
    panY = Math.max(0, (view.height - stageH * zoom) / 2);
    applyView();
  };

  const handleMarkup = (cell) => {
    if (cell.shape === 'line') {
      return '<i class="cal-handle line-end" data-handle="end"></i>'
        + '<i class="cal-handle line-start" data-handle="start"></i>'
        + '<i class="cal-handle thickness" data-handle="thickness"></i>';
    }
    return ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']
      .map((spot) => `<i class="cal-handle ${spot}" data-handle="${spot}"></i>`)
      .join('');
  };

  const draw = () => {
    // Size the preview from the label proportions — a max-height alone would squash it.
    const maxW = 300;
    const maxH = 200;
    const ratio = width / Math.max(0.1, height);
    let stageW = maxW;
    let stageH = Math.round(maxW / ratio);
    if (stageH > maxH) {
      stageH = maxH;
      stageW = Math.round(maxH * ratio);
    }
    stage.style.width = `${Math.max(80, stageW)}px`;
    stage.style.height = `${Math.max(40, stageH)}px`;
    applyView();
    const pxPerMm = (parseFloat(stage.style.width) || 300) / Math.max(1e-6, width);
    cellsBox.innerHTML = cells.map((cell, index) => {
      const on = selection.has(index);
      const tone = on ? ' selected' : '';
      const shape = cell.shape === 'circle' ? ' circle' : cell.shape === 'line' ? ' line' : '';
      const angle = Number(cell.angle || 0);
      const rotate = cell.shape === 'line' && angle
        ? `;transform:rotate(${angle}deg);transform-origin:0 50%`
        : '';
      const weight = cell.shape === 'line'
        ? ''
        : `;border-width:${Math.max(1, Number(cell.thickness_mm || 0.8) * pxPerMm)}px`;
      const handles = on && selection.size === 1 ? handleMarkup(cell) : '';
      return `<div class="cal-cell${tone}${shape}" data-cell="${index}"
        style="left:${(cell.x / width) * 100}%;top:${(cell.y / height) * 100}%;
               width:${(cell.w / width) * 100}%;height:${(cell.h / height) * 100}%${rotate}${weight}">
        <span>${index + 1}</span>${handles}</div>`;
    }).join('');

    if (selection.size > 1) {
      editor.innerHTML = `
        <div class="cal-editor-head">${selection.size} cells selected</div>
        <div class="hint">Align them with the buttons on the left, or drag any selected cell to move
          the whole group. Shift+click adds or removes a cell from the selection.</div>`;
    } else if (selected >= 0 && cells[selected]) {
      const cell = cells[selected];
      const isLine = cell.shape === 'line';
      const shapeName = cell.shape === 'circle' ? 'circle' : isLine ? 'line' : 'rectangle';
      const thickness = Number(cell.thickness_mm ?? 0.8);
      editor.innerHTML = `
        <div class="cal-editor-head">Cell ${selected + 1} · ${shapeName}</div>
        <div class="cal-shapes" role="group" aria-label="Cell shape">
          <button type="button" class="cal-shape${cell.shape === 'rect' ? ' on' : ''}" data-shape="rect">Rectangle</button>
          <button type="button" class="cal-shape${cell.shape === 'circle' ? ' on' : ''}" data-shape="circle">Circle</button>
          <button type="button" class="cal-shape${cell.shape === 'line' ? ' on' : ''}" data-shape="line">Line</button>
        </div>
        <div class="grid-4">
          ${field('X (mm)', numberInput('cal-x', round2(cell.x), 0.5, 0, Math.max(0, width - 0.5)))}
          ${field('Y (mm)', numberInput('cal-y', round2(cell.y), 0.5, 0, Math.max(0, height - 0.5)))}
          ${field(isLine ? 'Length (mm)' : 'Width (mm)', numberInput('cal-w', round2(cell.w), 0.5, 0.5, width))}
          ${isLine
            ? field('Angle (°)', numberInput('cal-angle', round2(cell.angle || 0), 1, -180, 180))
            : field('Height (mm)', numberInput('cal-h', round2(cell.h), 0.5, 0.5, height))}
        </div>
        <div class="grid-2">
          ${field(isLine ? 'Thickness (mm)' : 'Outline (mm)', numberInput('cal-thickness', thickness, 0.1, 0.2, 10))}
          ${isLine ? '' : `<div class="hint" style="align-self:end">Outline only changes the guide weight.</div>`}
        </div>`;
    } else {
      editor.innerHTML = '<div class="hint">No cells yet — press <strong>Grid from rows × columns</strong> for a regular roll, '
        + 'or pick <strong>Draw rectangle / circle / line</strong> and drag on the label.</div>';
    }

    const perCell = cells.length
      ? `${round2(cells[0].w)} × ${round2(cells[0].h)} mm each`
      : 'whole label usable';
    const headLimit = printheadWidthMm();
    const clipNote = headLimit && width > headLimit + 0.05
      ? ` · prints up to ${round2(headLimit)} mm (printhead)`
      : '';
    const layout = derivedGrid();
    const layoutNote = cells.length > 1
      ? ` · ${layout.rows} row${layout.rows === 1 ? '' : 's'} × ${layout.cols} column${layout.cols === 1 ? '' : 's'}`
      : '';
    summary.textContent = `${cells.length || 1} ${cells.length === 1 ? 'cell' : 'cells'}${layoutNote} · ${perCell}${clipNote}`;

    refreshIcons(body);
    const selCount = $('#cal-selcount', body);
    if (selCount) selCount.textContent = selection.size > 1 ? `${selection.size} selected` : '';
    $$('.cal-a', body).forEach((button) => {
      button.disabled = selection.size < 2;
    });
    if (chipsBox) {
      chipsBox.innerHTML = cells.length
        ? cells.map((cell, index) => `
          <button type="button" class="cal-chip${selection.has(index) ? ' on' : ''}" data-chip="${index}">
            <b>${index + 1}</b><span>${cell.shape === 'circle' ? 'circle' : cell.shape === 'line' ? 'line' : 'rect'}
            ${round2(cell.w)}×${round2(cell.h)}</span>
            <i class="cal-chip-x" data-chip-del="${index}" title="Delete this cell">✕</i>
          </button>`).join('')
        : '<span class="hint">No cells yet — press the grid button or draw on the label.</span>';
    }
    if (lastFit !== `${cells.length}:${selected}:${cells[selected]?.shape || ''}:${selection.size}`) {
      lastFit = `${cells.length}:${selected}:${cells[selected]?.shape || ''}:${selection.size}`;
      fitModalToContent(modal, { min: 420, max: 940 });
    }
  };

  const selectCell = (index) => selectOnly(index);

  const setTool = (next) => {
    tool = next;
    $$('.cal-tool', body).forEach((button) => {
      const on = button.dataset.tool === tool;
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    stage.classList.toggle('drawing', tool !== 'select');
    stage.dataset.tool = tool;
  };

  $$('.cal-tool', body).forEach((button) => {
    button.onclick = () => setTool(button.dataset.tool);
  });

  /* Drag on empty label space to draw the active shape; a plain click places it at its default size.
     With the Select tool the same drag pans the zoomed view. */
  stage.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.cal-handle') || event.target.closest('.cal-cell')) return;
    const box = stage.getBoundingClientRect();
    const toMm = (clientX, clientY) => ({
      x: Math.min(width, Math.max(0, ((clientX - box.left) / box.width) * width)),
      y: Math.min(height, Math.max(0, ((clientY - box.top) / box.height) * height)),
    });

    if (tool === 'select') {
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = panX;
      const originY = panY;
      let moved = false;
      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
        panX = originX + dx;
        panY = originY + dy;
        applyView();
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        if (!moved) selectCell(-1);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      event.preventDefault();
      return;
    }

    const start = toMm(event.clientX, event.clientY);
    gridMode = false;
    const draft = document.createElement('div');
    draft.className = `cal-cell drafting${tool === 'circle' ? ' circle' : ''}${tool === 'line' ? ' line' : ''}`;
    cellsBox.appendChild(draft);

    const paint = (from, to) => {
      const x = Math.min(from.x, to.x);
      const y = Math.min(from.y, to.y);
      const w = Math.abs(to.x - from.x);
      const h = Math.abs(to.y - from.y);
      draft.style.left = `${(x / width) * 100}%`;
      draft.style.top = `${(y / height) * 100}%`;
      draft.style.width = `${(w / width) * 100}%`;
      draft.style.height = `${(h / height) * 100}%`;
    };
    paint(start, start);

    const onMove = (moveEvent) => paint(start, toMm(moveEvent.clientX, moveEvent.clientY));
    const onUp = (upEvent) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      draft.remove();
      const end = toMm(upEvent.clientX, upEvent.clientY);
      let x = Math.min(start.x, end.x);
      let y = Math.min(start.y, end.y);
      let w = Math.abs(end.x - start.x);
      let h = Math.abs(end.y - start.y);

      if (w < 1.5 && h < 1.5) {
        // A click, not a drag: drop the shape at its default size where the user clicked.
        w = Math.max(2, width / cols);
        h = Math.max(2, height / rows);
        x = Math.min(x, Math.max(0, width - w));
        y = Math.min(y, Math.max(0, height - h));
      }
      if (tool === 'circle') {
        const side = Math.max(1, Math.min(w, h));
        x += (w - side) / 2;
        y += (h - side) / 2;
        w = side;
        h = side;
      }
      if (tool === 'line') {
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const length = Math.hypot(dx, dy);
        const angle = Math.round((Math.atan2(dy, dx) * 180) / Math.PI * 10) / 10;
        cells.push({
          shape: 'line',
          x: round2(Math.min(Math.max(0, start.x), width)),
          y: round2(Math.min(Math.max(0, start.y), height)),
          w: round2(Math.max(1, Math.min(length, width))),
          h: 0.8,
          angle,
          thickness_mm: 0.8,
          label: '',
        });
        selected = cells.length - 1;
        selection = new Set([selected]);
        commit();
        setTool(tool);
        return;
      }
      cells.push({
        shape: tool,
        x: round2(Math.min(Math.max(0, x), Math.max(0, width - w))),
        y: round2(Math.min(Math.max(0, y), Math.max(0, height - h))),
        w: round2(Math.min(w, width)),
        h: round2(Math.min(h, height)),
        angle: 0,
        thickness_mm: 0.8,
        label: '',
      });
      selected = cells.length - 1;
      selection = new Set([selected]);
      commit();
      if (tool !== 'select') setTool(tool);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    event.preventDefault();
  });

  cellsBox.addEventListener('pointerdown', (event) => {
    const node = event.target.closest('.cal-cell');
    if (!node) return;
    const index = Number(node.dataset.cell);
    const handle = event.target.dataset?.handle || null;

    if (event.shiftKey) {
      toggleSelection(index);
      event.preventDefault();
      return;
    }
    if (!selection.has(index) || selection.size === 1) selectOnly(index);
    else selected = index; // keep the group, just make this one primary

    const startCell = { ...cells[index] };
    const groupStart = selectedIndices().map((item) => ({ index: item, x: cells[item].x, y: cells[item].y, w: cells[item].w, h: cells[item].h }));
    const box = stage.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const perPxX = width / box.width;
    const perPxY = height / box.height;
    let moved = false;

    const onMove = (moveEvent) => {
      const dx = (moveEvent.clientX - startX) * perPxX;
      const dy = (moveEvent.clientY - startY) * perPxY;
      if (Math.abs(dx) > 0.2 || Math.abs(dy) > 0.2) moved = true;
      const cell = cells[index];

      if (handle) {
        if (cell.shape === 'line') {
          const rad = ((startCell.angle || 0) * Math.PI) / 180;
          const cos = Math.cos(rad);
          const sin = Math.sin(rad);
          const along = dx * cos + dy * sin;
          const across = -dx * sin + dy * cos;
          if (handle === 'end') {
            cell.w = snap(clamp(startCell.w + along, 1, width * 2));
          } else if (handle === 'start') {
            const length = snap(clamp(startCell.w - along, 1, width * 2));
            cell.w = length;
            cell.x = snap(startCell.x + (startCell.w - length) * cos);
            cell.y = snap(startCell.y + (startCell.w - length) * sin);
          } else {
            cell.thickness_mm = snap(clamp(startCell.thickness_mm + 2 * across, 0.2, 10));
            cell.h = cell.thickness_mm;
          }
        } else {
          let left = startCell.x;
          let top = startCell.y;
          let right = startCell.x + startCell.w;
          let bottom = startCell.y + startCell.h;
          if (handle.includes('w')) left = clamp(startCell.x + dx, 0, right - 0.5);
          if (handle.includes('n')) top = clamp(startCell.y + dy, 0, bottom - 0.5);
          if (handle.includes('e')) right = clamp(right + dx, left + 0.5, width);
          if (handle.includes('s')) bottom = clamp(bottom + dy, top + 0.5, height);
          if (cell.shape === 'circle') {
            const side = Math.max(right - left, bottom - top);
            if (handle.includes('w')) left = Math.max(0, right - side); else right = Math.min(width, left + side);
            if (handle.includes('n')) top = Math.max(0, bottom - side); else bottom = Math.min(height, top + side);
          }
          cell.x = snap(left);
          cell.y = snap(top);
          cell.w = snap(Math.max(0.5, right - left));
          cell.h = snap(Math.max(0.5, bottom - top));
          if (cell.shape === 'circle') cell.h = cell.w;
        }
        draw();
        return;
      }

      if (groupStart.length > 1) {
        // Move the whole selection together, keeping it inside the label.
        const minX = Math.min(...groupStart.map((item) => item.x));
        const maxX = Math.max(...groupStart.map((item) => item.x + item.w));
        const minY = Math.min(...groupStart.map((item) => item.y));
        const maxY = Math.max(...groupStart.map((item) => item.y + item.h));
        const shiftX = clamp(dx, -minX, Math.max(0, width - maxX));
        const shiftY = clamp(dy, -minY, Math.max(0, height - maxY));
        groupStart.forEach((item) => {
          cells[item.index].x = snap(item.x + shiftX);
          cells[item.index].y = snap(item.y + shiftY);
        });
        draw();
        return;
      }

      cell.x = clamp(snap(startCell.x + dx), 0, Math.max(0, width - cell.w));
      cell.y = clamp(snap(startCell.y + dy), 0, Math.max(0, height - cell.h));
      const node2 = $(`.cal-cell[data-cell="${index}"]`, cellsBox);
      if (node2) {
        node2.style.left = `${(cell.x / width) * 100}%`;
        node2.style.top = `${(cell.y / height) * 100}%`;
      }
      const xInput = $('[data-key="cal-x"]', body);
      const yInput = $('[data-key="cal-y"]', body);
      if (xInput) xInput.value = round2(cell.x);
      if (yInput) yInput.value = round2(cell.y);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (moved) commit();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    event.preventDefault();
  });

  /* Wheel = zoom around the cursor, buttons for the obvious steps. */
  viewport.addEventListener('wheel', (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    const next = clamp(zoom * factor, 1, 8);
    if (Math.abs(next - zoom) < 0.001) return;
    const rect = stage.getBoundingClientRect();
    const localX = event.clientX - rect.left;
    const localY = event.clientY - rect.top;
    const ratio = next / zoom;
    panX -= localX * (ratio - 1);
    panY -= localY * (ratio - 1);
    zoom = next;
    applyView();
  }, { passive: false });

  $$('[data-zoom]', body).forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.zoom;
      if (action === 'fit') {
        zoom = 1;
        centreView();
        return;
      }
      const factor = action === 'in' ? 1.25 : 1 / 1.25;
      zoom = clamp(zoom * factor, 1, 8);
      centreView();
    };
  });

  body.addEventListener('change', (event) => {
    const key = event.target.dataset?.key;
    if (!key) return;
    if (key.startsWith('cal-')) commit();
  });

  const onCalKeydown = (event) => {
    if (!document.body.contains(stage)) {
      document.removeEventListener('keydown', onCalKeydown, true);
      return;
    }
    const mod = event.ctrlKey || event.metaKey;
    const tag = (event.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
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
    if (typing) return;
    if (event.key !== 'Delete' && event.key !== 'Backspace') return;
    if (selected < 0) return;
    event.preventDefault();
    event.stopPropagation();
    deleteCell(selected);
  };
  document.addEventListener('keydown', onCalKeydown, true);

  const addCell = (shape) => {
    if (shape === 'circle') {
      // A circle is a region with equal width and height in millimetres.
      const side = Math.max(2, Math.round(Math.min(width, height) * 0.45 * 2) / 2);
      cells.push({
        shape,
        x: round2((width - side) / 2),
        y: round2((height - side) / 2),
        w: Math.min(side, width),
        h: Math.min(side, height),
        label: '',
      });
      selectCell(cells.length - 1);
      return;
    }
    const cellW = Math.max(2, Math.round((width / cols) * 2) / 2);
    const cellH = Math.max(2, Math.round((height / rows) * 2) / 2);
    cells.push({ shape, x: 0, y: 0, w: Math.min(cellW, width), h: Math.min(cellH, height), label: '' });
    selectCell(cells.length - 1);
  };

  $$('[data-cal]', body).forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.cal;
      if (action === 'grid') {
        applyGrid();
      } else if (action === 'delete') {
        deleteCell();
      } else if (action === 'undo') {
        stepHistory(-1);
      } else if (action === 'redo') {
        stepHistory(1);
      }
    };
  });

  /* Turn the selected cell(s) into another shape without redrawing them. */
  const setCellShape = (shape) => {
    const indices = selectedIndices();
    if (!indices.length) return;
    indices.forEach((index) => {
      const cell = cells[index];
      if (!cell || cell.shape === shape) return;
      const centerX = cell.x + cell.w / 2;
      const centerY = cell.y + cell.h / 2;
      cell.shape = shape;
      if (shape === 'circle') {
        const side = Math.min(Math.max(1, Math.min(cell.w, cell.h)), width, height);
        cell.w = snap(side);
        cell.h = snap(side);
        cell.x = snap(clamp(centerX - cell.w / 2, 0, Math.max(0, width - cell.w)));
        cell.y = snap(clamp(centerY - cell.h / 2, 0, Math.max(0, height - cell.h)));
        cell.angle = 0;
      } else if (shape === 'line') {
        const thickness = Math.max(0.2, Number(cell.thickness_mm) || 0.8);
        cell.w = snap(Math.max(1, cell.w));
        cell.h = snap(thickness);
        cell.y = snap(clamp(centerY - cell.h / 2, 0, Math.max(0, height - cell.h)));
        cell.angle = Number(cell.angle) || 0;
      } else {
        if (cell.h < 1) cell.h = snap(Math.max(2, height / Math.max(1, rows)));
        cell.angle = 0;
      }
    });
    gridMode = false;
    commit();
  };

  editor.addEventListener('click', (event) => {
    const button = event.target.closest('[data-shape]');
    if (button) setCellShape(button.dataset.shape);
  });

  $$('[data-align]', body).forEach((button) => {
    button.onclick = () => alignSelection(button.dataset.align);
  });

  if (chipsBox) {
    chipsBox.addEventListener('click', (event) => {
      const remove = event.target.closest('[data-chip-del]');
      if (remove) {
        deleteCell(Number(remove.dataset.chipDel));
        return;
      }
      const chip = event.target.closest('[data-chip]');
      if (!chip) return;
      const index = Number(chip.dataset.chip);
      if (event.shiftKey) toggleSelection(index);
      else selectOnly(index);
    });
  }

  body.addEventListener('input', (event) => {
    const key = event.target.dataset?.key;
    if (!key) return;
    if (['cal-width', 'cal-height', 'cal-rows', 'cal-cols', 'cal-gap', 'cal-rowspace', 'cal-colspace'].includes(key)) {
      readSize();
      const gridKeys = ['cal-rows', 'cal-cols', 'cal-rowspace', 'cal-colspace'];
      if (gridKeys.includes(key)) {
        // Rows/columns/spacings always describe the grid, so rebuild it right away.
        applyGrid();
        return;
      }
      if (gridMode && cells.length) {
        applyGrid();
        return;
      }
      draw();
      return;
    }
    const cell = cells[selected];
    if (!cell) return;
    gridMode = false;
    if (key === 'cal-x') cell.x = Math.min(Math.max(0, Number(event.target.value) || 0), Math.max(0, width - cell.w));
    if (key === 'cal-y') cell.y = Math.min(Math.max(0, Number(event.target.value) || 0), Math.max(0, height - cell.h));
    if (key === 'cal-w') {
      cell.w = Math.min(Math.max(0.5, Number(event.target.value) || 0.5), width - cell.x);
      if (cell.shape === 'circle') cell.h = Math.min(cell.w, height - cell.y);
    }
    if (key === 'cal-h') {
      cell.h = Math.min(Math.max(0.5, Number(event.target.value) || 0.5), height - cell.y);
      if (cell.shape === 'circle') cell.w = Math.min(cell.h, width - cell.x);
    }
    if (key === 'cal-angle') {
      cell.angle = Math.max(-180, Math.min(180, Number(event.target.value) || 0));
      draw();
      return;
    }
    if (key === 'cal-thickness') {
      cell.thickness_mm = Math.max(0.2, Math.min(10, Number(event.target.value) || 0.8));
      if (cell.shape === 'line') cell.h = cell.thickness_mm;
      draw();
      return;
    }
    draw();
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
      cells = [];
      selected = -1;
      draw();
    };
  }

  draw();
  centreView();

  $('[data-cal-cancel]', footer).onclick = () => closeModal('roll-size');
  $('[data-cal-save]', footer).onclick = async () => {
    readSize();
    const layout = derivedGrid();
    const typedName = ($('[data-key="cal-name"]', body)?.value || '').trim();
    const savedName = typedName || (codeOnly(rollName) ? rollName : String(rollName));
    closeModal('roll-size');
    try {
      const payload = await api('/api/roll-format', 'POST', {
        barcode: paper.barcode || 'unknown',
        name: savedName,
        width_mm: width,
        height_mm: height,
        rows: layout.rows,
        cols: layout.cols,
        gap_mm: gap,
        label_type: paper.label_type ?? state.spec.label_type,
        capacity: paper.capacity ?? null,
        regions: cells.map((cell, index) => ({
          shape: cell.shape,
          x: cell.x,
          y: cell.y,
          w: cell.w,
          h: cell.h,
          angle: Number(cell.angle) || 0,
          thickness_mm: Number(cell.thickness_mm) || 0.8,
          label: cell.label || `Cell ${index + 1}`,
        })),
      });
      applyState(payload);
      applySize(width, height, { gap_mm: gap, label_type: paper.label_type ?? state.spec.label_type });
      try {
        const catalog = await api('/api/catalog');
        state.catalog = catalog.catalog || [];
      } catch (_) { /* the list refreshes with the settings tab */ }
      toast(`Roll ${savedName}: ${round2(width)} × ${round2(height)} mm, ${cells.length || 1} cell${cells.length === 1 ? '' : 's'} remembered`, 'ok');
      renderPaperRegions();
      schedulePreview();
      if (isModalOpen('settings')) renderSettingsTab();
    } catch (error) {
      toast(error.message, 'error');
    }
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

  // Cells drawn in the calibrator for this roll: show where content can go.
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
    box.innerHTML = `<b>${index + 1}</b>`;
    box.title = `${cell.label || `Cell ${index + 1}`} — ${round2(cell.w)} × ${round2(cell.h)} mm`;
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
    box.className = `element-box${element.id === state.selectedId ? ' selected' : ''}${hidden ? ' hidden-el' : ''}${locked ? ' locked' : ''}${crossing ? ' crosses' : ''}`;
    box.dataset.id = element.id;
    box.style.left = `${(element.x / labelWidth) * 100}%`;
    box.style.top = `${(element.y / labelHeight) * 100}%`;
    box.style.width = `${(element.w / labelWidth) * 100}%`;
    box.style.height = `${(element.h / labelHeight) * 100}%`;

    if (element.visible === false) box.title = 'Hidden layer — it will not be printed';
    if (locked) box.title = 'Region generated by Mirror — edit the first region';
    if (crossing) box.title = 'Mirror: this element crosses the cut — the part below it is clipped when printing';

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
    entry = { img: null, src: null };
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
          renderLayers();
        };
        image.src = `data:image/png;base64,${result.png}`;
        entry.src = image.src;
      })
      .catch(() => { elementImages.delete(key); });
  }
  return entry.img;
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
  const own = elementImage(element);
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
      element.visible = element.visible === false;
      pushHistory();
      renderOverlay();
      schedulePreview();
      buildInspector();
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

/* Tells the user what the crop does to print quality — the label is only 203 dpi. */
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
  const fit = element.image_fit === 'contain' ? 'contain' : element.image_fit === 'stretch' ? 'stretch' : 'cover';
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
  if (type === 'image') Object.assign(base, { w: Math.min(20, width - 2), h: Math.min(20, height - 2) });
  state.spec.elements.push(base);
  state.selectedId = base.id;
  pushHistory();
  renderOverlay();
  buildInspector();
  schedulePreview();
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
function makeDragGhost(element) {
  const preview = $('#preview');
  const stage = $('#stage');
  if (!stage) return null;
  const ghost = document.createElement('div');
  ghost.className = 'drag-ghost';

  // Prefer the element's own render: the lifted artwork must not contain neighbours.
  const own = elementImage(element);
  if (own && own.naturalWidth) {
    ghost.dataset.mode = 'element';
    ghost.style.backgroundImage = `url(${own.src})`;
    ghost.style.backgroundRepeat = 'no-repeat';
    ghost.style.backgroundSize = '100% 100%';
    ghost.style.backgroundPosition = 'center';
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

  if (drag && drag.mode === 'resize' && drag.ghostOrigin) {
    // stretch the crop together with the frame
    const stage = $('#stage');
    if (ghost.dataset.mode === 'element') {
      ghost.style.backgroundSize = '100% 100%';
      return;
    }
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
  const element = state.spec.elements.find((item) => item.id === box.dataset.id);
  if (!element) return;
  if (isMirrorLocked(element)) {
    toast('Mirror: this region is generated automatically — edit the first region.');
    return;
  }
  state.selectedId = element.id;
  renderOverlay();
  buildInspector();
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
    element.w = round2(Math.max(2, Math.min(labelWidth - element.x, w)));
    element.h = round2(Math.max(2, Math.min(band.height - element.y, h)));
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

/* =============================================================== status bar */
function renderStatusBar() {
  const payload = state.printer || {};
  const paper = payload.paper || {};
  const info = payload.info || {};

  const connBox = $('#sb-conn');
  const connText = $('#sb-conn-text');
  if (state.connected) {
    connBox.classList.add('ok');
    connBox.classList.remove('err');
    connText.textContent = `${info.model_name || 'printer'} · ${payload.link?.name || payload.link?.address || ''}`.trim();
  } else {
    connBox.classList.remove('ok');
    connBox.classList.add('err');
    connText.textContent = 'not connected';
  }

  const dot = $('#conn-dot');
  dot.className = `dot ${state.connected ? 'ok' : 'err'}`;
  $('#conn-text').textContent = state.connected
    ? `${info.model_name || 'printer'} · ${payload.link?.name || ''}`.trim()
    : 'not connected';

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

async function rasterizeIcon(icon, sizePx = 384) {
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
  const panelWidth = Number(localStorage.getItem('niibot.panelWidth'));
  if (panelWidth >= 240 && panelWidth <= 640) {
    document.documentElement.style.setProperty('--panel-w', `${panelWidth}px`);
  }
  const sheetHeight = Number(localStorage.getItem('niibot.sheetHeight'));
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
      try { localStorage.setItem('niibot.panelWidth', String(parseInt(width, 10))); } catch (_) { /* ignore */ }
    };
    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', up);
  });

  const sheetSplitter = $('#splitter-sheet');
  const setSheetHeight = (height) => {
    state.sheetHeight = height;
    try { localStorage.setItem('niibot.sheetHeight', String(height)); } catch (_) { /* ignore */ }
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
      try { localStorage.setItem('niibot.sheetHeight', String(state.sheetHeight)); } catch (_) { /* ignore */ }
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
  if (settingsTab === 'paper') bindPaperTabInputs(content);
  if (settingsTab === 'windows') loadFitInto(content);
  if (settingsTab === 'stats') loadStats(content);
}

function bindSettingsActions(content) {
  content.onclick = async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    try {
      if (action === 'scan') await scanDevices(event.target.closest('[data-action]'));
      if (action === 'connect') await connect(event.target.closest('[data-address]').dataset.address);
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

function renderPrinterTab() {
  const payload = state.printer || {};
  const devices = state.lastDevices;
  let list = '';
  if (state.connected) {
    list = `<div class="device">
      <div><strong>${escapeHtml(payload.info?.model_name || 'printer')}</strong><small>${escapeHtml(payload.link?.address || '')}</small></div>
      <div class="row" style="gap:6px">
        <button class="btn" data-action="test-print"><i data-lucide="printer"></i> Test print</button>
        <span class="dot ok"></span>
      </div>
    </div>`;
  } else if (devices && devices.length) {
    list = devices.map((device) => `
      <div class="device">
        <div><strong>${escapeHtml(device.name)}</strong><small>${escapeHtml(device.address)}${device.rssi ? ` · ${device.rssi} dBm` : ''}</small></div>
        <button class="btn" data-action="connect" data-address="${escapeHtml(device.address)}">Connect</button>
      </div>`).join('');
  } else {
    list = '<div class="empty">Turn the printer on and press “Scan”.</div>';
  }

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
      <div class="device-list" style="margin-top:10px">${list}</div>
      <div class="hint" style="margin-top:6px">The test print sends a small sample using the detected paper format — the quickest way to verify the connection and the print quality.</div>
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
  try {
    localStorage.setItem('niibot.view', JSON.stringify({
      showGrid: state.showGrid,
      snapToGrid: state.snapToGrid,
      showRegions: state.showRegions,
    }));
  } catch (_) { /* ignore */ }
}

function loadViewPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem('niibot.view') || 'null');
    if (!saved) return;
    if (typeof saved.showGrid === 'boolean') state.showGrid = saved.showGrid;
    if (typeof saved.snapToGrid === 'boolean') state.snapToGrid = saved.snapToGrid;
    if (typeof saved.showRegions === 'boolean') state.showRegions = saved.showRegions;
  } catch (_) { /* ignore */ }
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
    const { devices } = await api('/api/scan', 'POST', { timeout: 6, show_all: false });
    state.lastDevices = devices;
    renderSettingsTab();
    if (!devices.length) toast('No printers found — check that they are switched on.', 'error');
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.innerHTML = previous;
      refreshIcons(button);
    }
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
const DENSITY_STORAGE_KEY = 'niibot.density';

/* Density is the printer's own heat setting (the vendor calls it "print density", range 1-5).
   The last value the user picked wins; otherwise follow what the printer reports. */
function storedDensity() {
  const clamp = (value) => (Number.isFinite(value) ? Math.max(1, Math.min(5, Math.round(value))) : null);
  try {
    const raw = localStorage.getItem(DENSITY_STORAGE_KEY);
    if (raw !== null && String(raw).trim() !== '') {
      const saved = clamp(Number(raw));
      if (saved) return saved;
    }
  } catch (_) { /* private mode */ }
  const device = clamp(Number(state.printer?.info?.density));
  if (device) return device;
  return clamp(Number(state.spec?.density)) || 3;
}

function rememberDensity(value) {
  try {
    localStorage.setItem(DENSITY_STORAGE_KEY, String(value));
  } catch (_) { /* private mode */ }
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
async function connect(address) {
  const payload = await api('/api/connect', 'POST', { address });
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
}

function captureSettingFromModel() {
  const model = state.printer?.info?.model_name;
  if (model && model !== 'unknown') storeLocal('lastModel', model);
}

function storeLocal(key, value) {
  try { localStorage.setItem(`niibot.${key}`, value); } catch (_) { /* ignore */ }
}

/* =================================================================== state */
function applyState(payload) {
  const wasConnected = state.connected;
  state.printer = payload;
  state.connected = Boolean(payload.connected);
  renderStatusBar();
  renderStageMeta();
  renderPaperRegions();
  renderJobStatus(payload.job || null);
  if (state.connected) startStatusTimer(); else stopStatusTimer();
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
    const knownSize = Boolean(paper.catalog || Number(paper.detected_width_mm) > 0 || payload.profile);
    if (!knownSize && paper.barcode) {
      // An unrecognised roll always starts the calibrator — once per insertion, not per poll.
      if (state.promptedRoll !== paper.barcode) {
        state.promptedRoll = paper.barcode;
        toast(`Roll ${paper.barcode}: unknown format — calibrate it once.`, 'error');
        setTimeout(() => {
          if (state.connected && !isModalOpen('roll-size')) openRollSizeDialog();
        }, 400);
      }
    } else if (knownSize) {
      state.promptedRoll = null;
    }
    applyKnownFormat(paper);
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
  }, 25000);
}

/* Picks up a background auto-connect (the server connects to the remembered printer
   on its own), and keeps the paper panel fresh until that happens. */
function startWatchTimer() {
  if (state.watchTimer) return;
  state.watchTimer = setInterval(async () => {
    if (state.connected) return;
    try {
      const payload = await api('/api/state');
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

  document.addEventListener('pointerdown', onPointerDown);
  document.addEventListener('pointermove', onPointerMove);
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
    else if (event.key === 'Delete' || event.key === 'Backspace') { if (state.selectedId) { event.preventDefault(); deleteSelected(); } }
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
  bindControls();
  loadViewPrefs();
  syncViewToggles();
  try {
    const [options, payload] = await Promise.all([api('/api/options'), api('/api/state')]);
    state.options = options;
    state.catalog = options.catalog || [];
    setSpec(options.default_spec);
    applyState(payload);
    applyKnownFormat(payload.paper);
    renderStatusBar();
    if (restoreDraft()) toast('Restored the last unsaved draft', 'ok');
    await requestPreview();
    if (state.connected) startStatusTimer();
    startWatchTimer();
    refreshIcons();
  } catch (error) {
    toast(`Could not start the application: ${error.message}`, 'error');
  }
}

window.addEventListener('DOMContentLoaded', init);
