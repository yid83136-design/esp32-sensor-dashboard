/* Board 1 local dashboard. Measurements come only from /api/data. */
const $ = id => document.getElementById(id);
const DAY_MS = 86400000;
const DB_NAME = 'esp32-sensors-history-v1';
let samples = [];
let seen = new Set();
let database = null;
let boot = '';
let latestSeq = 0;
let paused = false;
let lastPayload = null;
let lastPrune = 0;
let lastChartDraw = 0;
let chartViews = {};

const fmt = (value, digits = 1) => value == null ? '--' : Number(value).toFixed(digits);
const setText = (id, value) => { $(id).textContent = value; };
const localTime = ms => new Date(ms).toLocaleString('zh-CN', {hour12: false});

function setConnection(message, live = false) {
  setText('connection-text', message);
  $('connection').classList.toggle('live', live);
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) { reject(Error('浏览器不支持本地记录')); return; }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore('samples', {keyPath: 'id'});
      store.createIndex('wall', 'wall');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || Error('无法打开本地记录'));
  });
}

function readSaved() {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction('samples', 'readonly');
    const query = transaction.objectStore('samples').index('wall')
      .getAll(IDBKeyRange.lowerBound(Date.now() - DAY_MS));
    query.onsuccess = () => resolve(query.result);
    query.onerror = () => reject(query.error || Error('无法读取本地记录'));
  });
}

function saveSaved(entries) {
  if (!database || !entries.length) return;
  try {
    const transaction = database.transaction('samples', 'readwrite');
    const store = transaction.objectStore('samples');
    for (const entry of entries) store.put(entry);
    transaction.onerror = () => { setText('storage-info', '浏览器保存失败；请及时导出 CSV。'); };
  } catch (error) {
    setText('storage-info', '浏览器保存失败；请及时导出 CSV。');
  }
}

function pruneSaved() {
  const cutoff = Date.now() - DAY_MS;
  samples = samples.filter(entry => entry.wall >= cutoff);
  seen = new Set(samples.map(entry => entry.id));
  if (!database) return;
  try {
    const transaction = database.transaction('samples', 'readwrite');
    const query = transaction.objectStore('samples').index('wall')
      .openCursor(IDBKeyRange.upperBound(cutoff, true));
    query.onsuccess = () => {
      const cursor = query.result;
      if (cursor) { cursor.delete(); cursor.continue(); }
    };
  } catch (error) {
    setText('storage-info', '浏览器清理旧记录失败；当前记录仍可导出。');
  }
}

async function startStorage() {
  try {
    database = await openDatabase();
    samples = await readSaved();
    samples.sort((a, b) => a.wall - b.wall);
    seen = new Set(samples.map(entry => entry.id));
    pruneSaved();
    setText('storage-info', `本浏览器已保存 ${samples.length} 组记录，保留最近 24 小时。`);
  } catch (error) {
    database = null;
    setText('storage-info', '浏览器持久保存不可用；此页面仍可显示和导出本次打开期间的数据。');
  }
  drawCharts(true);
  poll();
}

function merge(payload) {
  if (payload.boot !== boot) { boot = payload.boot; latestSeq = 0; }
  const received = Date.now();
  const last = payload.latest;
  const anchor = received - (payload.sample_age_ms || 0);
  const fresh = [];
  for (const row of payload.rows || []) {
    latestSeq = Math.max(latestSeq, row[0]);
    const id = `${payload.boot}:${row[0]}`;
    if (seen.has(id)) continue;
    const wall = last ? anchor - (last[1] - row[1]) * 1000 : received;
    const entry = {id, wall, boot: payload.boot, row};
    fresh.push(entry);
    seen.add(id);
    samples.push(entry);
  }
  if (fresh.length) {
    if (samples.length > 1 && samples[samples.length - 2].wall > samples[samples.length - 1].wall)
      samples.sort((a, b) => a.wall - b.wall);
    saveSaved(fresh);
  }
  if (received - lastPrune > 600000) { pruneSaved(); lastPrune = received; }
  lastPayload = payload;
  render(payload);
}

function delta(a, b) {
  return a == null || b == null ? '--' : `${a - b >= 0 ? '+' : ''}${(a - b).toFixed(2)} °C`;
}

function showSwitch(net, remaining) {
  const switching = net && net.sta_state === 'connected' && net.sta_ip &&
    location.hostname === net.ap_ip;
  $('switch-banner').hidden = !switching;
  if (!switching) return;
  $('switch-link').href = `http://${net.sta_ip}/`;
  $('switch-link').textContent = `http://${net.sta_ip}/`;
  setText('switch-detail', remaining > 0
    ? `设备约 ${remaining} 秒后关闭自身热点。请让手机回到原来的 Wi‑Fi，再打开：`
    : '设备已连上新网络。请让手机回到原来的 Wi‑Fi，再打开：');
}

function render(payload) {
  $('demo').hidden = !payload.demo;
  const row = payload.latest || [0, 0, null, null, null];
  for (const [id, column, digits] of [['st', 2, 1], ['sh', 3, 1], ['kt', 4, 2]])
    setText(id, fmt(row[column], digits));
  const errors = payload.errors || {};
  for (const key of ['s', 'k']) {
    const note = $(`note-${key}`);
    note.classList.toggle('error', !!errors[key]);
    note.textContent = errors[key] ? `读取失败：${errors[key]}` :
      key === 'k' ? 'K 型探头 · 不测湿度' : '当前读数正常';
  }
  setText('delta-k', delta(row[4], row[2]));
  const age = payload.sample_age_ms;
  const taskError = payload.task_errors && payload.task_errors.sensor;
  const networkError = payload.task_errors && payload.task_errors.network;
  const stale = age != null && age > 8000;
  $('health-banner').hidden = !(taskError || networkError || stale);
  if (taskError || networkError || stale) setText('health-banner', taskError
    ? `采样任务正在恢复：${taskError}；当前读数可能已过期。`
    : networkError ? `联网任务正在恢复：${networkError}；请核对 OLED 上的地址。`
    : `采样已超过 ${(age / 1000).toFixed(0)} 秒未更新；当前读数可能已过期。`);
  setConnection(paused ? '已暂停' : taskError || stale ? '数据未更新' :
    age == null ? '等待采样' : '实时连接', age != null && !stale && !taskError && !paused);
  const net = payload.net || {};
  showSwitch(net, payload.handoff_remaining_s || 0);
  const states = {hotspot_only: '仅设备热点', connecting: '正在连接路由器',
    connected: '已连接路由器', retry_wait: '等待下次连接'};
  const names = payload.configured_ssids || [];
  setText('network-status', `${states[net.sta_state] || net.sta_state || ''}` +
    `${net.sta_ssid ? `：${net.sta_ssid}` : ''}` +
    `${net.sta_ip ? `；局域网地址 http://${net.sta_ip}/` : ''}` +
    `${names.length ? `；已保存 ${names.join('、')}` : ''}。`);
  setText('sample-info', `设备已运行 ${Math.floor(payload.uptime / 60)} 分 ${payload.uptime % 60} 秒；` +
    `${age == null ? '尚未采样' : `最近采样距现在约 ${(age / 1000).toFixed(1)} 秒`}；` +
    `会话 ${payload.boot}，已采 ${payload.latest ? payload.latest[0] : 0} 次` +
    `${payload.free == null ? '' : `，剩余内存 ${Math.round(payload.free / 1024)} KB`}` +
    `${payload.clients == null ? '' : `，在线连接 ${payload.clients}`}。`);
  setText('storage-info', `${database ? '本浏览器已保存' : '本页面已记录'} ${samples.length} 组` +
    `（最近 24 小时）；手机时间仅为估算，设备断电期间没有记录。`);
  drawCharts();
}

function visibleSamples() {
  const end = Date.now();
  const range = Number($('window').value) * 1000;
  return {start: end - range, end, range,
    visible: samples.filter(entry => entry.wall >= end - range && entry.wall <= end)};
}

function plot(canvas, columns, minimumSpan, view) {
  chartViews[canvas.id] = view;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width, height = rect.height;
  if (!width || !height) return;
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.font = '11px system-ui';
  const left = 44, right = 10, top = 20, bottom = 29;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const values = [];
  for (const series of columns)
    for (const entry of view.visible)
      if (entry.row[series.col] != null) values.push(entry.row[series.col]);
  if (!values.length) {
    ctx.fillStyle = '#a9bdd1'; ctx.textAlign = 'center';
    ctx.fillText('等待数据，或选择要看的曲线', width / 2, height / 2);
    return;
  }
  let lowValue = Infinity, highValue = -Infinity;
  for (const value of values) {
    if (value < lowValue) lowValue = value;
    if (value > highValue) highValue = value;
  }
  const span = Math.max(minimumSpan, (highValue - lowValue) * 1.25);
  const mid = (highValue + lowValue) / 2, low = mid - span / 2, high = mid + span / 2;
  for (let i = 0; i <= 4; i++) {
    const y = top + plotHeight * i / 4;
    ctx.strokeStyle = '#2b3d51'; ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillStyle = '#a9bdd1'; ctx.textAlign = 'right';
    ctx.fillText((high - span * i / 4).toFixed(span > 100 ? 0 : 1), left - 7, y + 4);
  }
  ctx.fillStyle = '#a9bdd1'; ctx.textAlign = 'center';
  ctx.fillText(new Date(view.start).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}), left + 27, height - 7);
  ctx.fillText('现在', width - right - 14, height - 7);
  if (view.visible.length < 2) ctx.fillText('正在积累曲线', width / 2, top + 15);
  for (const series of columns) {
    ctx.strokeStyle = series.color; ctx.fillStyle = series.color; ctx.lineWidth = 1.8;
    ctx.beginPath();
    let previous = null, last = null;
    for (const entry of view.visible) {
      const value = entry.row[series.col];
      if (value == null) { previous = null; continue; }
      const x = left + (entry.wall - view.start) / view.range * plotWidth;
      const y = top + (high - value) / span * plotHeight;
      if (previous && entry.boot === previous.boot && entry.wall - previous.wall <= 5500)
        ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
      previous = entry; last = [x, y];
    }
    ctx.stroke();
    if (last) { ctx.beginPath(); ctx.arc(last[0], last[1], 2.5, 0, Math.PI * 2); ctx.fill(); }
  }
}

function drawCharts(force = false) {
  const range = Number($('window').value);
  if (!force && range > 3600 && Date.now() - lastChartDraw < 10000) return;
  lastChartDraw = Date.now();
  const view = visibleSamples();
  const mode = $('temp-scale').value;
  const columns = [];
  if ($('show-s').checked && mode !== 'probe') columns.push({col: 2, color: '#62dfbc'});
  if ($('show-k').checked && mode !== 'air') columns.push({col: 4, color: '#bbadff'});
  setText('temp-axis-label', mode === 'air' ? '环境 °C · 独立刻度' :
    mode === 'probe' ? '探头 °C · 独立刻度' : '°C · 共用自动刻度');
  plot($('temp-chart'), columns, 1, view);
  plot($('humidity-chart'), $('show-s').checked ? [{col: 3, color: '#62dfbc'}] : [], 5, view);
}

function chartTouch(canvasId, hintId) {
  const canvas = $(canvasId);
  canvas.addEventListener('pointerdown', event => {
    const view = chartViews[canvasId];
    if (!view || !view.visible.length) return;
    const rect = canvas.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - 44) / (rect.width - 54)));
    const wanted = view.start + fraction * view.range;
    let nearest = view.visible[0];
    for (const entry of view.visible)
      if (Math.abs(entry.wall - wanted) < Math.abs(nearest.wall - wanted)) nearest = entry;
    setText(hintId, `${localTime(nearest.wall)}（估算） · 环境 ${fmt(nearest.row[2])} °C / ` +
      `${fmt(nearest.row[3])}%RH · 探头 ${fmt(nearest.row[4], 2)} °C`);
  });
}

async function poll() {
  if (!paused) {
    const control = new AbortController();
    const timeout = setTimeout(() => control.abort(), 4500);
    try {
      const response = await fetch(`/api/data?after=${latestSeq}&boot=${boot}`,
        {cache: 'no-store', signal: control.signal});
      if (!response.ok) throw Error(`HTTP ${response.status}`);
      merge(await response.json());
    } catch (error) {
      const changing = lastPayload && lastPayload.net && lastPayload.net.sta_ip &&
        location.hostname === lastPayload.net.ap_ip;
      setConnection(changing ? '请打开新地址' : '连接中断，正在重试');
      if (changing) showSwitch(lastPayload.net, 0);
      for (const key of ['s', 'k']) {
        $(`note-${key}`).textContent = '连接已中断；屏幕保留的是上次读数';
        $(`note-${key}`).classList.add('error');
      }
      $('health-banner').hidden = false;
      setText('health-banner', changing ? '设备已切换网络，请用上方新地址访问。' :
        '暂时无法取得新数据；请核对手机 Wi‑Fi 和 OLED 显示的地址。');
    } finally { clearTimeout(timeout); }
  }
  setTimeout(poll, 2000);
}

function csvEscape(value) {
  const string = value == null ? '' : String(value);
  return /[",\r\n]/.test(string) ? `"${string.replace(/"/g, '""')}"` : string;
}

const exportHeaders = [
  'browser_estimated_time_local', 'browser_estimated_time_utc', 'device_boot',
  'sequence', 'device_uptime_s', 'SHT3x_temperature_C',
  'SHT3x_humidity_RH_pct', 'MAX6675_temperature_C'
];

function exportRow(entry) {
  return [localTime(entry.wall), new Date(entry.wall).toISOString(), entry.boot,
    ...entry.row];
}

function downloadBlob(blob, extension) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `ESP32-sensors-${new Date().toISOString().slice(0, 10)}.${extension}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportCsv() {
  if (!samples.length) { alert('还没有可导出的数据'); return; }
  const lines = [exportHeaders.join(',')];
  for (const entry of samples) lines.push(exportRow(entry).map(csvEscape).join(','));
  downloadBlob(new Blob(['\ufeff', lines.join('\r\n')],
    {type: 'text/csv;charset=utf-8'}), 'csv');
}

function xmlEscape(value) {
  return String(value == null ? '' : value)
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function xlsxSheet() {
  const rows = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    `<dimension ref="A1:H${samples.length + 1}"/><sheetData>`];
  const columns = 'ABCDEFGH';
  const appendRow = (values, number) => {
    let cells = '';
    for (let index = 0; index < values.length; index++) {
      const value = values[index];
      const ref = columns[index] + number;
      if (index >= 3 && value != null && value !== '' && Number.isFinite(Number(value)))
        cells += `<c r="${ref}"><v>${Number(value)}</v></c>`;
      else
        cells += `<c r="${ref}" t="inlineStr"><is><t>${xmlEscape(value)}</t></is></c>`;
    }
    rows.push(`<row r="${number}">${cells}</row>`);
  };
  appendRow(exportHeaders, 1);
  for (let index = 0; index < samples.length; index++)
    appendRow(exportRow(samples[index]), index + 2);
  rows.push('</sheetData></worksheet>');
  return rows.join('');
}

function crc32(bytes) {
  let crc = -1;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ -1) >>> 0;
}

function xlsxZip(files) {
  const encoder = new TextEncoder();
  const chunks = [];
  const directory = [];
  let offset = 0;
  for (const [name, content] of files) {
    const filename = encoder.encode(name);
    const bytes = encoder.encode(content);
    const checksum = crc32(bytes);
    const local = new Uint8Array(30 + filename.length);
    const view = new DataView(local.buffer);
    view.setUint32(0, 0x04034b50, true);
    view.setUint16(4, 20, true);
    view.setUint32(14, checksum, true);
    view.setUint32(18, bytes.length, true);
    view.setUint32(22, bytes.length, true);
    view.setUint16(26, filename.length, true);
    local.set(filename, 30);
    chunks.push(local, bytes);

    const central = new Uint8Array(46 + filename.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint32(16, checksum, true);
    cv.setUint32(20, bytes.length, true);
    cv.setUint32(24, bytes.length, true);
    cv.setUint16(28, filename.length, true);
    cv.setUint32(42, offset, true);
    central.set(filename, 46);
    directory.push(central);
    offset += local.length + bytes.length;
  }
  const directorySize = directory.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, directorySize, true);
  ev.setUint32(16, offset, true);
  return new Blob([...chunks, ...directory, end],
    {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}

function exportXlsx() {
  if (!samples.length) { alert('还没有可导出的数据'); return; }
  const files = [
    ['[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?>' +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
      '</Types>'],
    ['_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
      '</Relationships>'],
    ['xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?>' +
      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' +
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
      '<sheets><sheet name="Sensors" sheetId="1" r:id="rId1"/></sheets></workbook>'],
    ['xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
      '</Relationships>'],
    ['xl/worksheets/sheet1.xml', xlsxSheet()]
  ];
  downloadBlob(xlsxZip(files), 'xlsx');
}

async function saveWifi(data) {
  const change = data.hotspot_only ? '改为仅使用设备热点' : `连接 ${data.ssid}`;
  if (!confirm(`${change}？其他人正在看的页面可能暂时断开，访问地址也可能改变。`)) return;
  setText('wifi-message', '正在保存…');
  $('wifi-save').disabled = true;
  try {
    const response = await fetch('/api/wifi', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.message || '保存未成功');
    setText('wifi-message', data.hotspot_only ? '已切回设备热点，请连接 ESP32-Sensors-1。' :
      '已保存。设备连上后会关闭自身热点；请按上方提示或 OLED 的完整 IP 打开新地址。');
  } catch (error) {
    setText('wifi-message', `未完成：${error.message}`);
  } finally { $('wifi-save').disabled = false; }
}

$('pause').onclick = () => {
  paused = !paused;
  $('pause').textContent = paused ? '继续刷新' : '暂停刷新';
  setConnection(paused ? '已暂停' : '正在恢复');
  if (paused) for (const key of ['s', 'k'])
    $(`note-${key}`).textContent = '页面已暂停；超过设备 5 分钟缓存可能漏数据';
};
for (const id of ['window', 'temp-scale', 'show-s', 'show-k'])
  $(id).onchange = () => drawCharts(true);
window.addEventListener('resize', () => drawCharts(true));
$('export').onclick = exportCsv;
$('export-xlsx').onclick = exportXlsx;
$('wifi-form').onsubmit = event => {
  event.preventDefault();
  saveWifi({ssid: $('wifi-ssid').value.trim(), password: $('wifi-password').value});
};
$('hotspot-only').onclick = () => saveWifi({hotspot_only: true});
chartTouch('temp-chart', 'temp-hint');
chartTouch('humidity-chart', 'humidity-hint');
startStorage();
