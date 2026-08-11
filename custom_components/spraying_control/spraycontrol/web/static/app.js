'use strict';

const $ = (id) => document.getElementById(id);

const state = {
  source: 'file',
  file: null,
  fieldRing: null,      // [[lon, lat], ...]
  drawing: null,        // vertices while drawing
  pickingBase: false,
  resultId: null,
};

/* ---------------------------------------------------------------- map --- */

const map = L.map('map', { zoomControl: true }).setView([40.0, 32.5], 15);

const satellite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 21, maxNativeZoom: 19, attribution: 'Esri, Maxar, Earthstar Geographics' }
);
const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap',
});
satellite.addTo(map);
L.control.layers({ Satellite: satellite, Streets: streets }, {}, { position: 'topright' }).addTo(map);

const layers = {
  overlay: null, spraying: L.layerGroup().addTo(map), transport: L.layerGroup().addTo(map),
  gaps: L.layerGroup().addTo(map), base: L.layerGroup().addTo(map), field: L.layerGroup().addTo(map),
};

function hint(text) {
  const el = $('hint');
  if (!text) { el.hidden = true; return; }
  el.textContent = text;
  el.hidden = false;
}

/* ------------------------------------------------------- base + field --- */

function drawBase() {
  layers.base.clearLayers();
  const lat = parseFloat($('base-lat').value);
  const lon = parseFloat($('base-lon').value);
  if (!isFinite(lat) || !isFinite(lon)) return;
  const radius = parseFloat($('base-radius').value) || 8;
  // Decorative, so it never intercepts a map click while re-picking.
  L.circle([lat, lon], { radius, color: '#3ddc97', weight: 2, fillOpacity: 0.12, interactive: false })
    .addTo(layers.base);
  // A draggable pin so the point can be nudged without re-clicking.
  const marker = L.marker([lat, lon], { draggable: true, autoPan: true })
    .bindTooltip('Refill point — drag to move').addTo(layers.base);
  marker.on('dragend', () => {
    const p = marker.getLatLng();
    $('base-lat').value = p.lat.toFixed(6);
    $('base-lon').value = p.lng.toFixed(6);
    drawBase();
  });
}

function drawField() {
  layers.field.clearLayers();
  $('field-note').textContent = '';
  if (state.drawing && state.drawing.length) {
    L.polyline(state.drawing.map(([lon, lat]) => [lat, lon]), {
      color: '#f5c211', weight: 2, dashArray: '5,5',
    }).addTo(layers.field);
    state.drawing.forEach(([lon, lat]) =>
      L.circleMarker([lat, lon], { radius: 4, color: '#f5c211', fillOpacity: 1 }).addTo(layers.field));
  }
  if (state.fieldRing) {
    L.polygon(state.fieldRing.map(([lon, lat]) => [lat, lon]), {
      color: '#f5c211', weight: 2, fillOpacity: 0.05,
    }).addTo(layers.field);
    $('field-note').textContent = `Plot boundary set (${state.fieldRing.length - 1} points).`;
  }
}

map.on('click', (e) => {
  if (state.pickingBase) {
    $('base-lat').value = e.latlng.lat.toFixed(6);
    $('base-lon').value = e.latlng.lng.toFixed(6);
    state.pickingBase = false;
    $('pick-base').classList.remove('active');
    hint(null);
    drawBase();
    return;
  }
  if (state.drawing) {
    state.drawing.push([e.latlng.lng, e.latlng.lat]);
    hint(`Plot boundary: ${state.drawing.length} points — click "Finish" when done`);
    drawField();
  }
});

$('pick-base').addEventListener('click', () => {
  state.pickingBase = !state.pickingBase;
  $('pick-base').classList.toggle('active', state.pickingBase);
  hint(state.pickingBase ? 'Click the map to set the refill point' : null);
});

$('locate').addEventListener('click', () => {
  if (!navigator.geolocation) {
    hint('This browser cannot share a location.');
    return;
  }
  const btn = $('locate');
  btn.disabled = true;
  hint('Finding your location…');
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude, longitude } = pos.coords;
      $('base-lat').value = latitude.toFixed(6);
      $('base-lon').value = longitude.toFixed(6);
      map.setView([latitude, longitude], 19);
      drawBase();
      hint(null);
      btn.disabled = false;
    },
    (err) => {
      hint(`Could not get your location: ${err.message}`);
      btn.disabled = false;
    },
    { enableHighAccuracy: true, timeout: 10000 },
  );
});

$('clear-base').addEventListener('click', () => {
  $('base-lat').value = '';
  $('base-lon').value = '';
  drawBase();
});

$('base-radius').addEventListener('input', drawBase);

$('draw-field').addEventListener('click', () => {
  const btn = $('draw-field');
  if (state.drawing) {
    if (state.drawing.length >= 3) {
      state.fieldRing = [...state.drawing, state.drawing[0]];
    }
    state.drawing = null;
    btn.textContent = 'Draw plot boundary';
    btn.classList.remove('active');
    hint(null);
  } else {
    state.drawing = [];
    state.fieldRing = null;
    btn.textContent = 'Finish';
    btn.classList.add('active');
    hint('Click the map to trace the plot boundary');
  }
  drawField();
});

$('clear-field').addEventListener('click', () => {
  state.fieldRing = null;
  state.drawing = null;
  $('draw-field').textContent = 'Draw plot boundary';
  $('draw-field').classList.remove('active');
  hint(null);
  drawField();
});

/* --------------------------------------------------------- source tabs --- */

document.querySelectorAll('#source-tabs .tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('#source-tabs .tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.pane').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    state.source = tab.dataset.source;
    document.querySelector(`.pane[data-pane="${state.source}"]`).classList.add('active');
    if (state.source === 'ha') loadTrackers();
  });
});

$('file').addEventListener('change', (e) => {
  state.file = e.target.files[0] || null;
  $('file-label').textContent = state.file ? state.file.name : 'Drop a track file here, or click to choose';
});

const drop = $('drop');
['dragenter', 'dragover'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => {
  state.file = e.dataTransfer.files[0] || null;
  if (state.file) $('file-label').textContent = state.file.name;
});

let trackersLoaded = false;
async function loadTrackers() {
  if (trackersLoaded) return;
  trackersLoaded = true;
  try {
    const res = await fetch('api/ha/status');
    const data = await res.json();
    if (!data.available) {
      $('ha-status').textContent = data.error;
      return;
    }
    $('ha-status').textContent = `Connected to Home Assistant ${data.version}.`;
    const sel = $('entity');
    sel.innerHTML = '';
    data.trackers.forEach((t) => {
      const opt = document.createElement('option');
      opt.value = t.entity_id;
      opt.textContent = t.has_gps ? `${t.name} (GPS)` : `${t.name} — no position`;
      sel.appendChild(opt);
    });
    if (!data.trackers.length) $('ha-status').textContent = 'No device trackers found.';
  } catch (err) {
    trackersLoaded = false;
    $('ha-status').textContent = `Could not reach the add-on API: ${err.message}`;
  }
}

$('day').valueAsDate = new Date();
$('tz-offset').value = -new Date().getTimezoneOffset() / 60;

/* Spray-width quick presets. Clicking one fills the number box; typing a custom
   value clears the highlight. */
function syncSwathPresets() {
  const v = parseFloat($('swath').value);
  document.querySelectorAll('#swath-presets button').forEach((b) =>
    b.classList.toggle('active', parseFloat(b.dataset.swath) === v));
}
document.querySelectorAll('#swath-presets button').forEach((b) => {
  b.addEventListener('click', () => { $('swath').value = b.dataset.swath; syncSwathPresets(); });
});
$('swath').addEventListener('input', syncSwathPresets);

/* Seed the form from the add-on options, so the sprayer only gets set up once. */
(async function applyDefaults() {
  try {
    const d = await (await fetch('api/defaults')).json();
    const fields = {
      swath: 'swath', tank: 'tank', 'base-radius': 'base_radius', 'base-dwell': 'base_dwell',
      'min-speed': 'min_speed', 'max-speed': 'max_speed', prefix: 'prefix',
    };
    for (const [id, key] of Object.entries(fields)) {
      if (d[key] !== null && d[key] !== undefined) $(id).value = d[key];
    }
    if (d.base_lat !== null && d.base_lon !== null) {
      $('base-lat').value = d.base_lat;
      $('base-lon').value = d.base_lon;
      map.setView([d.base_lat, d.base_lon], 15);
      drawBase();
    }
    syncSwathPresets();
    if (d.addon) {
      // Inside Home Assistant the tracker source is the point of the add-on.
      document.querySelector('.tab[data-source="ha"]').click();
    }
  } catch (e) {
    /* Defaults are a convenience; the form works without them. */
  }
})();

/* ------------------------------------------------------------- analyse --- */

$('run').addEventListener('click', async () => {
  const btn = $('run');
  const err = $('error');
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Analysing…';

  const fd = new FormData();
  fd.append('source', state.source);
  if (state.source === 'file') {
    if (!state.file) {
      err.textContent = 'Choose a track file first.';
      err.hidden = false;
      btn.disabled = false;
      btn.textContent = 'Analyse';
      return;
    }
    fd.append('file', state.file);
  } else if (state.source === 'ha') {
    fd.append('entity', $('entity').value);
    fd.append('day', $('day').value);
    fd.append('tz_offset', $('tz-offset').value);
  }

  ['swath', 'tank', 'base-lat', 'base-lon', 'base-radius', 'base-dwell',
   'min-speed', 'max-speed', 'max-gap', 'max-accuracy', 'cell', 'min-gap-area',
  ].forEach((id) => fd.append(id.replace(/-/g, '_'), $(id).value));

  if (state.fieldRing) fd.append('field', JSON.stringify([state.fieldRing]));

  try {
    const res = await fetch('api/analyze', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    render(data);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Analyse';
  }
});

/* -------------------------------------------------------------- render --- */

const fmt = (v, d = 2) => Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

function stat(key, value, unit, cls) {
  return `<div class="stat ${cls || ''}"><div class="k">${key}</div>
    <div class="v">${value}${unit ? ` <small>${unit}</small>` : ''}</div></div>`;
}

function render(data) {
  state.resultId = data.id;
  const s = data.summary;

  $('stats').innerHTML = [
    stat('Sprayed', fmt(s.sprayed_area_ha, 2), 'ha'),
    stat('Product used', fmt(s.total_volume_l, 0), 'L'),
    stat('Rate', fmt(s.overall_rate_l_per_ha, 1), 'L/ha'),
    stat('Refills', s.refills, `of ${s.tank_loads} loads`),
    stat('Missed', fmt(s.gap_area_ha, 3), `ha &middot; ${fmt(s.gap_pct, 1)}%`, s.gap_pct > 1 ? 'bad' : ''),
    stat('Overlap', fmt(s.overlap_pct, 1), `% &middot; ${fmt(s.overlap_area_ha, 2)} ha`, s.overlap_pct > 10 ? 'warn' : ''),
    stat('Coverage', fmt(s.coverage_pct, 1), '%'),
    stat('Worked', fmt(s.spraying_time_s / 3600, 2), 'h'),
  ].join('');

  $('warnings').innerHTML = data.warnings.map((w) => `<div class="warn-item">${w}</div>`).join('');

  $('loads').querySelector('tbody').innerHTML = data.loads.length
    ? data.loads.map((l) => `<tr class="${l.complete ? '' : 'partial'}">
        <td>#${l.index}${l.complete ? '' : ' *'}</td>
        <td class="num">${fmt(l.area_ha, 2)} ha</td>
        <td class="num">${fmt(l.volume_l, 0)} L</td>
        <td class="num">${fmt(l.rate_l_per_ha, 0)} L/ha</td></tr>`).join('')
      + (data.loads.some((l) => !l.complete)
        ? '<tr><td colspan="4" class="note">* partial — run ended before the next refill</td></tr>' : '')
    : '<tr><td class="note">No tank loads detected.</td></tr>';

  $('gaps').querySelector('tbody').innerHTML = data.gaps.length
    ? data.gaps.slice(0, 12).map((g, i) => `<tr class="clickable" data-lat="${g.lat}" data-lon="${g.lon}">
        <td>${i + 1}</td>
        <td class="num">${g.area_m2.toLocaleString()} m&sup2;</td>
        <td class="num">${fmt(g.max_width_m, 1)} m wide</td></tr>`).join('')
    : '<tr><td class="note">No misses above the minimum size.</td></tr>';

  $('gaps').querySelectorAll('tr.clickable').forEach((tr) => {
    tr.addEventListener('click', () => map.flyTo([+tr.dataset.lat, +tr.dataset.lon], 19));
  });

  $('legend').innerHTML = data.legend
    .map((l) => `<div><i style="background:${l.color}"></i>${l.label}</div>`).join('');

  $('dl-geojson').href = `api/result/${data.id}/geojson`;
  $('dl-json').href = `api/result/${data.id}/summary.json`;
  $('push-result').textContent = '';
  $('results').hidden = false;

  paint(data);
}

function paint(data) {
  if (layers.overlay) { map.removeLayer(layers.overlay); layers.overlay = null; }
  layers.spraying.clearLayers();
  layers.transport.clearLayers();
  layers.gaps.clearLayers();

  // Every analysis layer is decorative and non-interactive, so that picking or
  // drawing on the map afterwards is never swallowed by a drawn feature.
  data.track.transport.forEach((line) =>
    L.polyline(line, { color: '#6b7c93', weight: 1.5, opacity: 0.7, dashArray: '4,4', interactive: false }).addTo(layers.transport));
  data.track.spraying.forEach((line) =>
    L.polyline(line, { color: '#ffffff', weight: 1, opacity: 0.35, interactive: false }).addTo(layers.spraying));

  if (data.has_overlay) {
    layers.overlay = L.imageOverlay(`api/result/${data.id}/overlay.png`, data.bounds, { opacity: 0.85, interactive: false });
    layers.overlay.addTo(map);
  }

  data.gaps.slice(0, 30).forEach((g, i) => {
    if (g.polygon && g.polygon.length >= 4) {
      L.polygon(g.polygon.map(([lon, lat]) => [lat, lon]), {
        color: '#ff2d55', weight: 2, fill: false, interactive: false,
      }).addTo(layers.gaps);
    }
    L.marker([g.lat, g.lon], {
      interactive: false,
      icon: L.divIcon({
        className: '', html: `<div class="gap-label">${i + 1}: ${g.area_m2.toLocaleString()} m&sup2;</div>`,
        iconSize: null,
      }),
    }).addTo(layers.gaps);
  });

  if (data.base) {
    $('base-lat').value = data.base.lat.toFixed(6);
    $('base-lon').value = data.base.lon.toFixed(6);
    drawBase();
  }

  map.fitBounds(data.bounds, { padding: [30, 30] });
}

/* ------------------------------------------------------------ push HA --- */

$('push').addEventListener('click', async () => {
  if (!state.resultId) return;
  const btn = $('push');
  btn.disabled = true;
  const out = $('push-result');
  out.textContent = 'Pushing…';

  const fd = new FormData();
  fd.append('result_id', state.resultId);
  fd.append('prefix', $('prefix').value);

  try {
    const res = await fetch('api/ha/push', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    out.innerHTML = `Created ${data.created.length} entities:<br>${data.created.join('<br>')}`;
  } catch (e) {
    out.textContent = `Failed: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
});
