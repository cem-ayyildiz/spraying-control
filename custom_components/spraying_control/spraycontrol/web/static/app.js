'use strict';

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

const state = {
  project: null,      // full project object from the API
  resultId: null,
  pickingBase: false,
  drawing: null,      // vertices while tracing a plot
  fieldRing: null,
  aligning: null,     // {id, corners, layer, handles}
};

const TRACK_EXT = /\.(gpx|csv|tsv|txt|json|geojson|kml)$/i;
const IMAGE_EXT = /\.(png|jpe?g)$/i;

/* ---------------------------------------------------------------- map --- */

const map = L.map('map', { zoomControl: true }).setView([38.3, 32.9], 17);

const satellite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 22, maxNativeZoom: 19, attribution: 'Esri, Maxar' },
);
const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 22, maxNativeZoom: 19, attribution: '&copy; OpenStreetMap',
});
// A blank basemap, for working offline or when the tile server has nothing at
// garden zoom. Your own aerial photo is the backdrop then, which is the point.
const blank = L.tileLayer('', { attribution: 'No basemap' });

satellite.addTo(map);
L.control.layers(
  { Satellite: satellite, Streets: streets, 'None (offline)': blank },
  {}, { position: 'topright' },
).addTo(map);

// Fall back to the blank basemap when tiles cannot be fetched at all, so the
// map does not sit there looking broken.
let tileFailures = 0;
[satellite, streets].forEach((layer) =>
  layer.on('tileerror', () => {
    if (++tileFailures === 8 && map.hasLayer(layer)) {
      map.removeLayer(layer);
      blank.addTo(map);
      hint('No map tiles — working offline. Your aerial photo still shows.', 6000);
    }
  }));

// Aerial photos get their own pane between the basemap and the overlays, so
// they always sit under the coverage and the track rather than hiding them.
map.createPane('photoPane').style.zIndex = 250;

const layers = {
  photos: L.layerGroup().addTo(map),
  coverage: null,
  spraying: L.layerGroup().addTo(map),
  transport: L.layerGroup().addTo(map),
  gaps: L.layerGroup().addTo(map),
  base: L.layerGroup().addTo(map),
  field: L.layerGroup().addTo(map),
  handles: L.layerGroup().addTo(map),
};

function hint(text, ms) {
  const el = $('hint');
  if (!text) { el.hidden = true; return; }
  el.textContent = text;
  el.hidden = false;
  if (ms) setTimeout(() => { if (el.textContent === text) el.hidden = true; }, ms);
}

function showError(message) {
  const el = $('error');
  if (!message) { el.hidden = true; return; }
  el.textContent = message;
  el.hidden = false;
}

/* ------------------------------------------------------------ API ------ */

async function api(path, { method = 'GET', form } = {}) {
  const opts = { method };
  if (form) opts.body = form;
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  if (!res.ok) throw new Error((data && data.detail) || text || `HTTP ${res.status}`);
  return data;
}

function formOf(obj) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null) fd.append(k, v);
  return fd;
}

/* -------------------------------------------------------- projects ----- */

async function loadProjects(selectId) {
  const { projects } = await api('api/projects');
  const sel = $('project-select');
  sel.innerHTML = '';
  projects.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `${p.name} — ${p.n_tracks} session${p.n_tracks === 1 ? '' : 's'}`;
    sel.appendChild(opt);
  });
  const wanted = selectId && projects.some((p) => p.id === selectId) ? selectId : projects[0]?.id;
  if (wanted) { sel.value = wanted; await openProject(wanted); }
}

async function openProject(id) {
  state.project = await api(`api/projects/${id}`);
  state.fieldRing = (state.project.field_rings || [])[0] || null;
  renderProject();
}

function renderProject() {
  const p = state.project;
  if (!p) return;

  // Settings inputs
  const cfg = p.config || {};
  const set = (id, v) => { if (v !== undefined && v !== null) $(id).value = v; };
  set('swath', cfg.swath_width_m);
  set('tank', cfg.tank_capacity_l);
  set('min-speed', cfg.min_speed_kmh);
  set('max-speed', cfg.max_speed_kmh);
  set('max-gap', cfg.max_gap_s);
  set('max-accuracy', cfg.max_accuracy_m);
  set('cell', cfg.cell_size_m);
  set('min-gap-area', cfg.min_gap_area_m2);
  syncSwathPresets();

  $('base-lat').value = p.base?.lat ?? '';
  $('base-lon').value = p.base?.lon ?? '';
  if (p.base?.radius_m) $('base-radius').value = p.base.radius_m;
  if (p.base?.min_dwell_s) $('base-dwell').value = p.base.min_dwell_s;

  renderSessions();
  renderOverlays();
  drawBase();
  drawField();
  drawPhotos();

  // Frame whatever we have.
  const bounds = photoBounds();
  if (bounds) map.fitBounds(bounds, { padding: [40, 40] });
  else if (p.base?.lat) map.setView([p.base.lat, p.base.lon], 18);
}

function renderSessions() {
  const p = state.project;
  const ul = $('sessions');
  ul.innerHTML = '';
  if (!p.tracks.length) {
    ul.innerHTML = '<li class="off"><div class="meta"><b>No sessions yet</b><span>Drop a track file above</span></div></li>';
  }
  p.tracks.forEach((t) => {
    const li = document.createElement('li');
    li.className = t.enabled ? '' : 'off';
    const when = t.start ? new Date(t.start).toLocaleDateString() : '';
    li.innerHTML = `
      <input type="checkbox" ${t.enabled ? 'checked' : ''} data-id="${t.id}" aria-label="Use ${t.name}">
      <div class="meta"><b>${escapeHtml(t.name)}</b><span>${when} · ${t.n_points} fixes</span></div>
      <button type="button" class="row-btn danger" data-del="${t.id}" title="Remove">&times;</button>`;
    ul.appendChild(li);
  });

  ul.querySelectorAll('input[type=checkbox]').forEach((cb) => {
    cb.addEventListener('change', async () => {
      await api(`api/projects/${p.id}/tracks/${cb.dataset.id}`, {
        method: 'PATCH', form: formOf({ enabled: cb.checked }),
      });
      const rec = p.tracks.find((t) => t.id === cb.dataset.id);
      if (rec) rec.enabled = cb.checked;
      cb.closest('li').className = cb.checked ? '' : 'off';
      updateCount();
    });
  });
  ul.querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      state.project = await api(`api/projects/${p.id}/tracks/${btn.dataset.del}`, { method: 'DELETE' });
      renderSessions();
    });
  });
  updateCount();
}

function updateCount() {
  const n = (state.project?.tracks || []).filter((t) => t.enabled).length;
  const total = state.project?.tracks.length || 0;
  $('session-count').textContent = total ? `${n} of ${total} selected` : '';
  $('run').disabled = n === 0;
  $('run').textContent = n > 1 ? `Analyse ${n} sessions together` : 'Analyse selected';
}

function renderOverlays() {
  const p = state.project;
  const ul = $('overlays');
  ul.innerHTML = '';
  $('photo-hint').hidden = p.overlays.length > 0;
  p.overlays.forEach((o) => {
    const li = document.createElement('li');
    li.className = o.enabled ? '' : 'off';
    li.innerHTML = `
      <input type="checkbox" ${o.enabled ? 'checked' : ''} data-oid="${o.id}" aria-label="Show ${o.name}">
      <div class="meta"><b>${escapeHtml(o.name)}</b><span>${o.width_px}×${o.height_px} · ${escapeHtml(o.source || '')}</span></div>
      <button type="button" class="row-btn" data-align="${o.id}" title="Line up">&#9635;</button>
      <button type="button" class="row-btn danger" data-delo="${o.id}" title="Remove">&times;</button>`;
    ul.appendChild(li);
  });

  ul.querySelectorAll('input[type=checkbox]').forEach((cb) => {
    cb.addEventListener('change', async () => {
      await api(`api/projects/${p.id}/overlays/${cb.dataset.oid}`, {
        method: 'PATCH', form: formOf({ enabled: cb.checked }),
      });
      const rec = p.overlays.find((o) => o.id === cb.dataset.oid);
      if (rec) rec.enabled = cb.checked;
      cb.closest('li').className = cb.checked ? '' : 'off';
      drawPhotos();
    });
  });
  ul.querySelectorAll('[data-align]').forEach((b) =>
    b.addEventListener('click', () => startAlign(b.dataset.align)));
  ul.querySelectorAll('[data-delo]').forEach((b) =>
    b.addEventListener('click', async () => {
      state.project = await api(`api/projects/${p.id}/overlays/${b.dataset.delo}`, { method: 'DELETE' });
      renderOverlays(); drawPhotos();
    }));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ------------------------------------------------- aerial photos ------- */

function cornersOf(o) {
  const pl = o.placement;
  return { topLeft: pl.top_left, topRight: pl.top_right, bottomLeft: pl.bottom_left };
}

/* Placing a picture: centre, ground width, and how far it is turned.
 *
 * Three corners are what gets stored - they can express any affine placement -
 * but dragging them one at a time is a poor way to move or rotate something.
 * These two convert between the corners and the handful of numbers a person
 * actually thinks in. Over a garden a flat metres-per-degree conversion is
 * accurate to well under the width of the spray band.
 */
/* Metres per degree on the WGS84 ellipsoid - the same radii the Python side
 * uses, so "180 m wide" means the same thing in both. A flat constant is out by
 * a couple of parts in a thousand, which is a visible third of a metre across a
 * large photo. */
const WGS84_A = 6378137.0;
const WGS84_E2 = 0.00669437999014;

function metresPerDegree(lat) {
  const phi = (lat * Math.PI) / 180;
  const s = Math.sin(phi);
  const w = 1 - WGS84_E2 * s * s;
  const meridional = (WGS84_A * (1 - WGS84_E2)) / Math.pow(w, 1.5);
  const primeVertical = WGS84_A / Math.sqrt(w);
  return {
    lat: (meridional * Math.PI) / 180,
    lon: (primeVertical * Math.cos(phi) * Math.PI) / 180,
  };
}

function cornersFrom(centre, widthM, heightM, rotDeg) {
  const [lat, lon] = centre;
  const per = metresPerDegree(lat);
  const M_PER_DEG_LAT = per.lat;
  const mLon = per.lon || 1e-9;
  const t = (rotDeg * Math.PI) / 180;
  const cos = Math.cos(t);
  const sin = Math.sin(t);
  const hw = widthM / 2;
  const hh = heightM / 2;
  // dx east, dy north before turning; rotate clockwise from north-up.
  const corner = (dx, dy) => [
    lat + (-dx * sin + dy * cos) / M_PER_DEG_LAT,
    lon + (dx * cos + dy * sin) / mLon,
  ];
  return { topLeft: corner(-hw, hh), topRight: corner(hw, hh), bottomLeft: corner(-hw, -hh) };
}

function describeCorners(c) {
  const lat = (c.topLeft[0] + c.topRight[0] + c.bottomLeft[0]) / 3;
  const per = metresPerDegree(lat);
  const mLon = per.lon || 1e-9;
  const toXY = ([la, lo]) => [(lo - c.topLeft[1]) * mLon, (la - c.topLeft[0]) * per.lat];
  const [trx, try_] = toXY(c.topRight);
  const [blx, bly] = toXY(c.bottomLeft);
  const widthM = Math.hypot(trx, try_);
  const heightM = Math.hypot(blx, bly);
  // Bearing of the top edge, less the quarter turn a north-up image already has.
  let rot = (Math.atan2(trx, try_) * 180) / Math.PI - 90;
  rot = ((rot + 540) % 360) - 180;
  const centre = [
    (c.topLeft[0] + c.topRight[0] + c.bottomLeft[0] + (c.topRight[0] + c.bottomLeft[0] - c.topLeft[0])) / 4,
    (c.topLeft[1] + c.topRight[1] + c.bottomLeft[1] + (c.topRight[1] + c.bottomLeft[1] - c.topLeft[1])) / 4,
  ];
  return { centre, widthM, heightM, rotDeg: rot };
}

function drawPhotos() {
  layers.photos.clearLayers();
  (state.project?.overlays || []).forEach((o) => {
    if (!o.enabled) return;
    if (state.aligning && state.aligning.id === o.id) return;  // drawn by the aligner
    L.rotatedOverlay(o.url, cornersOf(o), { opacity: o.opacity ?? 1, pane: 'photoPane' }).addTo(layers.photos);
  });
}

function photoBounds() {
  const pts = [];
  (state.project?.overlays || []).forEach((o) => {
    const c = cornersOf(o);
    pts.push(c.topLeft, c.topRight, c.bottomLeft);
  });
  return pts.length ? L.latLngBounds(pts) : null;
}

function startAlign(overlayId) {
  const o = state.project.overlays.find((x) => x.id === overlayId);
  if (!o) return;
  cancelAlign();

  const corners = cornersOf(o);
  const layer = L.rotatedOverlay(o.url, corners, { opacity: o.opacity ?? 1, pane: 'photoPane' }).addTo(map);
  const handles = {};
  const aspect = o.height_px / o.width_px;

  state.aligning = { id: overlayId, corners: { ...corners }, layer, handles, aspect, original: { ...corners } };

  // Move: one handle in the middle that carries the whole picture.
  const centre = describeCorners(corners).centre;
  const mover = L.marker(centre, {
    draggable: true,
    icon: L.divIcon({ className: 'corner-handle move', iconSize: [22, 22] }),
    zIndexOffset: 1100,
  }).addTo(layers.handles);
  mover.on('drag', () => {
    const a = state.aligning;
    const from = describeCorners(a.corners).centre;
    const to = mover.getLatLng();
    const dLat = to.lat - from[0];
    const dLon = to.lng - from[1];
    for (const key of ['topLeft', 'topRight', 'bottomLeft']) {
      a.corners[key] = [a.corners[key][0] + dLat, a.corners[key][1] + dLon];
    }
    a.layer.setCorners(a.corners);
    moveCornerHandles();
  });
  mover.on('dragend', refreshAlignReadouts);
  handles.move = mover;

  // The corners stay, for pinning the picture onto features exactly.
  [['topLeft', ''], ['topRight', 'tr'], ['bottomLeft', 'bl']].forEach(([key, cls]) => {
    const marker = L.marker(corners[key], {
      draggable: true,
      icon: L.divIcon({ className: `corner-handle ${cls}`, iconSize: [18, 18] }),
      zIndexOffset: 1000,
    }).addTo(layers.handles);
    marker.on('drag', () => {
      const a = state.aligning;
      a.corners[key] = [marker.getLatLng().lat, marker.getLatLng().lng];
      a.layer.setCorners(a.corners);
      if (a.handles.move) a.handles.move.setLatLng(describeCorners(a.corners).centre);
    });
    marker.on('dragend', refreshAlignReadouts);
    handles[key] = marker;
  });

  $('align-box').hidden = false;
  $('opacity').value = o.opacity ?? 1;
  refreshAlignReadouts();
  drawPhotos();
  map.fitBounds(L.latLngBounds([corners.topLeft, corners.topRight, corners.bottomLeft]), { padding: [60, 60] });
  hint('Drag the white handle to move it, the slider to turn it');
}

/* Put the corner markers back where the corners now are. */
function moveCornerHandles() {
  const a = state.aligning;
  if (!a) return;
  ['topLeft', 'topRight', 'bottomLeft'].forEach((key) => {
    if (a.handles[key]) a.handles[key].setLatLng(a.corners[key]);
  });
}

/* Show the current turn and width in the controls, without echoing back. */
function refreshAlignReadouts() {
  const a = state.aligning;
  if (!a) return;
  const d = describeCorners(a.corners);
  $('rotate').value = d.rotDeg.toFixed(1);
  $('rot-read').textContent = `${d.rotDeg.toFixed(1)}°`;
  $('ov-width').value = d.widthM.toFixed(1);
}

/* Rebuild the corners from centre + width + turn, keeping the picture square
   to itself - which is what you want when nudging it into place. */
function applyAlignControls() {
  const a = state.aligning;
  if (!a) return;
  const d = describeCorners(a.corners);
  const widthM = Math.max(1, parseFloat($('ov-width').value) || d.widthM);
  const rotDeg = parseFloat($('rotate').value);
  a.corners = cornersFrom(d.centre, widthM, widthM * a.aspect, rotDeg);
  a.layer.setCorners(a.corners);
  moveCornerHandles();
  if (a.handles.move) a.handles.move.setLatLng(d.centre);
  $('rot-read').textContent = `${rotDeg.toFixed(1)}°`;
}

$('rotate').addEventListener('input', applyAlignControls);
$('ov-width').addEventListener('input', applyAlignControls);

document.querySelectorAll('[data-nudge]').forEach((btn) =>
  btn.addEventListener('click', () => {
    if (!state.aligning) return;
    let next = parseFloat($('rotate').value) + parseFloat(btn.dataset.nudge);
    next = ((next + 540) % 360) - 180;   // keep it inside the slider
    $('rotate').value = next;
    applyAlignControls();
  }));

$('align-reset').addEventListener('click', () => {
  const a = state.aligning;
  if (!a) return;
  a.corners = { ...a.original };
  a.layer.setCorners(a.corners);
  moveCornerHandles();
  if (a.handles.move) a.handles.move.setLatLng(describeCorners(a.corners).centre);
  refreshAlignReadouts();
});

function cancelAlign() {
  if (!state.aligning) return;
  map.removeLayer(state.aligning.layer);
  layers.handles.clearLayers();
  state.aligning = null;
  $('align-box').hidden = true;
  hint(null);
  drawPhotos();
}

$('align-cancel').addEventListener('click', () => { cancelAlign(); });

$('align-save').addEventListener('click', async () => {
  if (!state.aligning) return;
  const c = state.aligning.corners;
  const placement = {
    top_left: c.topLeft, top_right: c.topRight, bottom_left: c.bottomLeft,
  };
  try {
    state.project = await api(`api/projects/${state.project.id}/overlays/${state.aligning.id}`, {
      method: 'PATCH',
      form: formOf({ placement: JSON.stringify(placement), opacity: $('opacity').value }),
    });
    cancelAlign();
    renderOverlays();
    drawPhotos();
    hint('Position saved', 2000);
  } catch (e) {
    showError(e.message);
  }
});

$('opacity').addEventListener('input', () => {
  if (state.aligning) state.aligning.layer.setOpacity(parseFloat($('opacity').value));
});

$('cov-opacity').addEventListener('input', () => {
  if (layers.coverage) layers.coverage.setOpacity(parseFloat($('cov-opacity').value));
});

/* --------------------------------------------------- base + plot ------- */

function drawBase() {
  layers.base.clearLayers();
  const lat = parseFloat($('base-lat').value);
  const lon = parseFloat($('base-lon').value);
  if (!isFinite(lat) || !isFinite(lon)) return;
  const radius = parseFloat($('base-radius').value) || 8;
  L.circle([lat, lon], { radius, color: '#3ddc97', weight: 2, fillOpacity: 0.12, interactive: false }).addTo(layers.base);
  const marker = L.marker([lat, lon], { draggable: true }).bindTooltip('Refill point — drag to move').addTo(layers.base);
  marker.on('dragend', () => {
    const p = marker.getLatLng();
    $('base-lat').value = p.lat.toFixed(6);
    $('base-lon').value = p.lng.toFixed(6);
    drawBase(); saveSettings();
  });
}

function drawField() {
  layers.field.clearLayers();
  $('field-note').textContent = '';
  if (state.drawing?.length) {
    L.polyline(state.drawing.map(([lon, lat]) => [lat, lon]), { color: '#f5c211', weight: 2, dashArray: '5,5', interactive: false }).addTo(layers.field);
    state.drawing.forEach(([lon, lat]) =>
      L.circleMarker([lat, lon], { radius: 4, color: '#f5c211', fillOpacity: 1, interactive: false }).addTo(layers.field));
  }
  if (state.fieldRing) {
    L.polygon(state.fieldRing.map(([lon, lat]) => [lat, lon]), { color: '#f5c211', weight: 2, fillOpacity: 0.05, interactive: false }).addTo(layers.field);
    $('field-note').textContent = `Plot boundary set (${state.fieldRing.length - 1} points).`;
  }
}

map.on('click', (e) => {
  if (state.pickingBase) {
    $('base-lat').value = e.latlng.lat.toFixed(6);
    $('base-lon').value = e.latlng.lng.toFixed(6);
    state.pickingBase = false;
    $('pick-base').classList.remove('active');
    hint(null); drawBase(); saveSettings();
    return;
  }
  if (state.drawing) {
    state.drawing.push([e.latlng.lng, e.latlng.lat]);
    hint(`Plot boundary: ${state.drawing.length} points — click Finish when done`);
    drawField();
  }
});

$('pick-base').addEventListener('click', () => {
  state.pickingBase = !state.pickingBase;
  $('pick-base').classList.toggle('active', state.pickingBase);
  hint(state.pickingBase ? 'Click the map to set the refill point' : null);
});

$('locate').addEventListener('click', () => {
  if (!navigator.geolocation) return hint('This browser cannot share a location.', 3000);
  hint('Finding your location…');
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      $('base-lat').value = pos.coords.latitude.toFixed(6);
      $('base-lon').value = pos.coords.longitude.toFixed(6);
      map.setView([pos.coords.latitude, pos.coords.longitude], 19);
      drawBase(); saveSettings(); hint(null);
    },
    (err) => hint(`Could not get your location: ${err.message}`, 4000),
    { enableHighAccuracy: true, timeout: 10000 },
  );
});

$('clear-base').addEventListener('click', () => {
  $('base-lat').value = ''; $('base-lon').value = '';
  drawBase(); saveSettings();
});

$('base-radius').addEventListener('input', drawBase);

$('draw-field').addEventListener('click', () => {
  const btn = $('draw-field');
  if (state.drawing) {
    state.fieldRing = state.drawing.length >= 3 ? [...state.drawing, state.drawing[0]] : null;
    state.drawing = null;
    btn.textContent = 'Draw plot';
    btn.classList.remove('active');
    hint(null); saveSettings();
  } else {
    state.drawing = []; state.fieldRing = null;
    btn.textContent = 'Finish';
    btn.classList.add('active');
    hint('Click the map to trace the plot boundary');
  }
  drawField();
});

$('clear-field').addEventListener('click', () => {
  state.fieldRing = null; state.drawing = null;
  $('draw-field').textContent = 'Draw plot';
  $('draw-field').classList.remove('active');
  hint(null); drawField(); saveSettings();
});

/* ---------------------------------------------------- settings --------- */

function syncSwathPresets() {
  const v = parseFloat($('swath').value);
  document.querySelectorAll('#swath-presets button').forEach((b) =>
    b.classList.toggle('active', parseFloat(b.dataset.swath) === v));
}
document.querySelectorAll('#swath-presets button').forEach((b) =>
  b.addEventListener('click', () => { $('swath').value = b.dataset.swath; syncSwathPresets(); saveSettings(); }));

let saveTimer = null;
function saveSettings() {
  if (!state.project) return;
  clearTimeout(saveTimer);
  $('save-state').textContent = 'Saving…';
  saveTimer = setTimeout(async () => {
    const config = {
      swath_width_m: +$('swath').value,
      tank_capacity_l: +$('tank').value,
      min_speed_kmh: +$('min-speed').value,
      max_speed_kmh: +$('max-speed').value,
      max_gap_s: +$('max-gap').value,
      max_accuracy_m: +$('max-accuracy').value,
      cell_size_m: +$('cell').value,
      min_gap_area_m2: +$('min-gap-area').value,
    };
    const lat = parseFloat($('base-lat').value);
    const lon = parseFloat($('base-lon').value);
    const base = isFinite(lat) && isFinite(lon)
      ? { lat, lon, radius_m: +$('base-radius').value, min_dwell_s: +$('base-dwell').value }
      : null;
    try {
      state.project = await api(`api/projects/${state.project.id}`, {
        method: 'PATCH',
        form: formOf({
          config: JSON.stringify(config),
          base: JSON.stringify(base),
          field_rings: JSON.stringify(state.fieldRing ? [state.fieldRing] : []),
        }),
      });
      $('save-state').textContent = 'Settings save automatically.';
    } catch (e) {
      $('save-state').textContent = `Could not save: ${e.message}`;
    }
  }, 400);
}

['swath', 'tank', 'min-speed', 'max-speed', 'max-gap', 'max-accuracy', 'cell', 'min-gap-area',
 'base-radius', 'base-dwell'].forEach((id) => $(id).addEventListener('change', saveSettings));
$('swath').addEventListener('input', syncSwathPresets);

$('settings-toggle').addEventListener('click', () => {
  const s = $('settings');
  s.hidden = !s.hidden;
  if (!s.hidden) s.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

/* ------------------------------------------------ project actions ------ */

$('project-select').addEventListener('change', (e) => openProject(e.target.value));

$('new-project').addEventListener('click', async () => {
  const name = prompt('Name for the new garden?', 'My garden');
  if (name === null) return;
  const p = await api('api/projects', { method: 'POST', form: formOf({ name }) });
  await loadProjects(p.id);
});

$('rename-project').addEventListener('click', async () => {
  if (!state.project) return;
  const name = prompt('Rename garden', state.project.name);
  if (name === null) return;
  await api(`api/projects/${state.project.id}`, { method: 'PATCH', form: formOf({ name }) });
  await loadProjects(state.project.id);
});

$('demo').addEventListener('click', async () => {
  const p = await api('api/demo', { method: 'POST' });
  await loadProjects(p.id);
  hint('Demo garden created — press Analyse', 4000);
});

/* --------------------------------------------------- file intake ------- */

async function addFiles(fileList) {
  if (!state.project) return;
  const files = [...fileList];
  const tracks = files.filter((f) => TRACK_EXT.test(f.name));
  const images = files.filter((f) => IMAGE_EXT.test(f.name));
  const worlds = files.filter((f) => /\.(jgw|pgw|wld)$/i.test(f.name));
  const unknown = files.filter((f) => !tracks.includes(f) && !images.includes(f) && !worlds.includes(f));

  showError(null);
  if (unknown.length) showError(`Skipped ${unknown.map((f) => f.name).join(', ')} — unrecognised type.`);

  try {
    if (tracks.length) {
      hint(`Adding ${tracks.length} session${tracks.length === 1 ? '' : 's'}…`);
      const fd = new FormData();
      tracks.forEach((f) => fd.append('files', f));
      const res = await api(`api/projects/${state.project.id}/tracks`, { method: 'POST', form: fd });
      state.project = res.project;
      renderSessions();
      if (res.failed?.length) showError(res.failed.map((f) => `${f.name}: ${f.error}`).join('; '));
    }
    for (const img of images) {
      hint(`Placing ${img.name}…`);
      const fd = new FormData();
      fd.append('file', img);
      const stem = img.name.replace(IMAGE_EXT, '');
      const sidecar = worlds.find((w) => w.name.replace(/\.[^.]+$/, '') === stem);
      if (sidecar) fd.append('world_file', sidecar);
      // Where to drop it if the photo has no GPS and there are no tracks yet:
      // the middle of the current view, sized to about half of what is on screen.
      const c = map.getCenter();
      fd.append('centre_lat', c.lat);
      fd.append('centre_lon', c.lng);
      const b = map.getBounds();
      const across = map.distance(
        [c.lat, b.getWest()], [c.lat, b.getEast()],
      );
      fd.append('width_m', Math.max(10, Math.round(across * 0.5)));
      const res = await api(`api/projects/${state.project.id}/overlays`, { method: 'POST', form: fd });
      state.project = res.project;
      renderOverlays();
      drawPhotos();
      const bounds = photoBounds();
      if (bounds) map.fitBounds(bounds, { padding: [40, 40] });
      startAlign(res.overlay_id);
    }
    hint(null);
  } catch (e) {
    hint(null);
    showError(e.message);
  }
}

$('file').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });

// A photo-only picker, so "Add an aerial photo" cannot pull in a track by mistake.
const photoInput = document.createElement('input');
photoInput.type = 'file';
photoInput.accept = '.png,.jpg,.jpeg,.jgw,.pgw,.wld';
photoInput.multiple = true;
photoInput.style.display = 'none';
document.body.appendChild(photoInput);
photoInput.addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
$('add-photo').addEventListener('click', () => photoInput.click());

// Drop anywhere on the window, not just the little box.
['dragenter', 'dragover'].forEach((ev) =>
  window.addEventListener(ev, (e) => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) =>
  window.addEventListener(ev, (e) => { e.preventDefault(); if (ev === 'drop' || e.target === document.documentElement) $('drop').classList.remove('over'); }));
window.addEventListener('drop', (e) => {
  e.preventDefault();
  if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
});

$('select-all').addEventListener('click', () => setAll(true));
$('select-none').addEventListener('click', () => setAll(false));
async function setAll(on) {
  const p = state.project;
  if (!p) return;
  await Promise.all(p.tracks.map((t) =>
    api(`api/projects/${p.id}/tracks/${t.id}`, { method: 'PATCH', form: formOf({ enabled: on }) })));
  p.tracks.forEach((t) => { t.enabled = on; });
  renderSessions();
}

/* ------------------------------------------------------- HA import ----- */

(async function haSetup() {
  try {
    const data = await api('api/ha/status');
    if (!data.available || !data.trackers.length) return;
    const sel = $('entity');
    data.trackers.filter((t) => t.has_gps).forEach((t) => {
      const opt = document.createElement('option');
      opt.value = t.entity_id;
      opt.textContent = t.name;
      sel.appendChild(opt);
    });
    if (sel.options.length) {
      $('day').valueAsDate = new Date();
      $('ha-import').hidden = false;
    }
  } catch { /* not running next to Home Assistant */ }
})();

$('import-ha').addEventListener('click', async () => {
  if (!state.project) return;
  const btn = $('import-ha');
  btn.disabled = true;
  hint('Importing from Home Assistant…');
  try {
    const res = await api(`api/projects/${state.project.id}/import/ha`, {
      method: 'POST',
      form: formOf({
        entity: $('entity').value,
        day: $('day').value,
        tz_offset: -new Date().getTimezoneOffset() / 60,
      }),
    });
    state.project = res.project;
    renderSessions();
    hint('Session imported', 2500);
  } catch (e) {
    hint(null); showError(e.message);
  } finally {
    btn.disabled = false;
  }
});

/* --------------------------------------------------------- analyse ----- */

$('run').addEventListener('click', async () => {
  const p = state.project;
  if (!p) return;
  const ids = p.tracks.filter((t) => t.enabled).map((t) => t.id);
  if (!ids.length) return showError('Tick at least one session.');

  const btn = $('run');
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Analysing…';
  showError(null);

  try {
    const data = await api(`api/projects/${p.id}/analyze`, {
      method: 'POST', form: formOf({ track_ids: ids.join(',') }),
    });
    render(data);
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
    updateCount();
  }
});

function stat(key, value, unit, cls) {
  return `<div class="stat ${cls || ''}"><div class="k">${key}</div>
    <div class="v">${value}${unit ? ` <small>${unit}</small>` : ''}</div></div>`;
}

function render(data) {
  state.resultId = data.id;
  const s = data.summary;

  $('stats').innerHTML = [
    stat('Sprayed', fmt(s.sprayed_area_ha, 3), 'ha'),
    stat('Product used', fmt(s.total_volume_l, 0), 'L'),
    stat('Rate', fmt(s.overall_rate_l_per_ha, 1), 'L/ha'),
    stat('Refills', s.refills, `of ${s.tank_loads} loads`),
    stat('Missed', fmt(s.gap_area_ha, 3), `ha · ${fmt(s.gap_pct, 1)}%`, s.gap_pct > 1 ? 'bad' : ''),
    stat('Overlap', fmt(s.overlap_pct, 1), `% · ${fmt(s.overlap_area_ha, 3)} ha`, s.overlap_pct > 10 ? 'warn' : ''),
    stat('Coverage', fmt(s.coverage_pct, 1), '%'),
    stat('Worked', fmt(s.spraying_time_s / 3600, 2), 'h'),
  ].join('');

  $('warnings').innerHTML = data.warnings.map((w) => `<div class="warn-item">${escapeHtml(w)}</div>`).join('');

  const multi = data.sessions.length > 1;
  $('sessions-head').hidden = !multi;
  $('session-table').querySelector('tbody').innerHTML = multi
    ? data.sessions.map((x) => `<tr>
        <td>${escapeHtml(x.name)}</td>
        <td class="num">${fmt(x.area_m2, 0)} m²</td>
        <td class="num" title="ground no earlier session had covered">${fmt(x.new_area_m2, 0)} new</td>
        <td class="num partial" title="ground already covered by an earlier session">${fmt(x.repeat_area_m2, 0)} again</td>
      </tr>`).join('')
    : '';

  $('loads').querySelector('tbody').innerHTML = data.loads.length
    ? data.loads.map((l) => `<tr class="${l.complete ? '' : 'partial'}">
        <td>#${l.index}${l.complete ? '' : ' *'}</td>
        <td class="num">${fmt(l.area_ha, 3)} ha</td>
        <td class="num">${fmt(l.volume_l, 0)} L</td>
        <td class="num">${fmt(l.rate_l_per_ha, 0)} L/ha</td></tr>`).join('')
      + (data.loads.some((l) => !l.complete)
        ? '<tr><td colspan="4" class="note">* partial — ended before the next refill</td></tr>' : '')
    : '<tr><td class="note">No tank loads detected.</td></tr>';

  $('gaps').querySelector('tbody').innerHTML = data.gaps.length
    ? data.gaps.slice(0, 12).map((g, i) => `<tr class="clickable" data-lat="${g.lat}" data-lon="${g.lon}">
        <td>${i + 1}</td>
        <td class="num">${g.area_m2.toLocaleString()} m²</td>
        <td class="num">${fmt(g.max_width_m, 1)} m wide</td></tr>`).join('')
    : '<tr><td class="note">No misses above the minimum size.</td></tr>';

  $('gaps').querySelectorAll('tr.clickable').forEach((tr) =>
    tr.addEventListener('click', () => map.flyTo([+tr.dataset.lat, +tr.dataset.lon], 20)));

  $('legend').innerHTML = data.legend
    .map((l) => `<div><i style="background:${l.color}"></i>${l.label}</div>`).join('');

  $('dl-geojson').href = `api/result/${data.id}/geojson`;
  $('dl-json').href = `api/result/${data.id}/summary.json`;
  $('push-result').textContent = '';
  $('results').hidden = false;

  paint(data);
}

function paint(data) {
  if (layers.coverage) { map.removeLayer(layers.coverage); layers.coverage = null; }
  layers.spraying.clearLayers();
  layers.transport.clearLayers();
  layers.gaps.clearLayers();

  data.track.transport.forEach((line) =>
    L.polyline(line, { color: '#6b7c93', weight: 1.5, opacity: 0.7, dashArray: '4,4', interactive: false }).addTo(layers.transport));
  data.track.spraying.forEach((line) =>
    L.polyline(line, { color: '#ffffff', weight: 1, opacity: 0.35, interactive: false }).addTo(layers.spraying));

  if (data.has_overlay) {
    layers.coverage = L.imageOverlay(`api/result/${data.id}/overlay.png`, data.bounds,
      { opacity: parseFloat($('cov-opacity').value), interactive: false });
    layers.coverage.addTo(map);
  }

  data.gaps.slice(0, 30).forEach((g, i) => {
    if (g.polygon?.length >= 4) {
      L.polygon(g.polygon.map(([lon, lat]) => [lat, lon]), { color: '#ff2d55', weight: 2, fill: false, interactive: false }).addTo(layers.gaps);
    }
    L.marker([g.lat, g.lon], {
      interactive: false,
      icon: L.divIcon({ className: '', html: `<div class="gap-label">${i + 1}: ${g.area_m2.toLocaleString()} m²</div>`, iconSize: null }),
    }).addTo(layers.gaps);
  });

  map.fitBounds(data.bounds, { padding: [30, 30] });
}

/* ------------------------------------------------------------ push ----- */

$('push').addEventListener('click', async () => {
  if (!state.resultId) return;
  const btn = $('push');
  btn.disabled = true;
  $('push-result').textContent = 'Pushing…';
  try {
    const data = await api('api/ha/push', {
      method: 'POST', form: formOf({ result_id: state.resultId, prefix: $('prefix').value }),
    });
    $('push-result').innerHTML = `Created ${data.created.length} entities.`;
  } catch (e) {
    $('push-result').textContent = `Failed: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
});

/* ------------------------------------------------------------ boot ----- */

(async function boot() {
  try {
    const defaults = await api('api/defaults');
    if (defaults.prefix) $('prefix').value = defaults.prefix;
  } catch { /* defaults are optional */ }
  try {
    await loadProjects();
  } catch (e) {
    showError(`Could not load your gardens: ${e.message}`);
  }
})();
