/**
 * VisionTracker AI – app.js
 * Token-based multi-user dashboard controller
 */

// ─────────────────────────────────────────────
//  Auth Helpers
// ─────────────────────────────────────────────
function getToken()   { return localStorage.getItem('vt_token') || ''; }
function getUsername(){ return localStorage.getItem('vt_username') || 'User'; }
function getDisplay() { return localStorage.getItem('vt_display') || getUsername(); }
function getRole()    { return localStorage.getItem('vt_role') || 'user'; }

function logout() {
    const token = getToken();
    if (token) fetch(`/api/logout?token=${token}`, { method: 'POST' }).catch(() => {});
    ['vt_token','vt_username','vt_display','vt_role'].forEach(k => localStorage.removeItem(k));
    window.location.href = 'login.html';
}

// ─────────────────────────────────────────────
//  Theme
// ─────────────────────────────────────────────
let currentTheme = localStorage.getItem('vt_theme') || 'dark';

function applyTheme(t) {
    currentTheme = t;
    document.documentElement.dataset.theme = t;
    const icon = document.getElementById('theme-icon');
    if (icon) icon.className = t === 'dark' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    localStorage.setItem('vt_theme', t);
    updateChartTheme();
}

// ─────────────────────────────────────────────
//  State
// ─────────────────────────────────────────────
let chart = null;
let flowChart = null;
let isPlaying = false;
let currentSourceType = 'video';

// ─────────────────────────────────────────────
//  DOM Refs (gathered after DOMContentLoaded)
// ─────────────────────────────────────────────
let $;
document.addEventListener('DOMContentLoaded', () => {
    $ = id => document.getElementById(id);

    // Populate user info
    $('logged-user-name').textContent = getDisplay();
    const roleBadge = $('role-badge');
    if (roleBadge) {
        if (getRole() === 'admin') {
            roleBadge.textContent = 'ADMIN';
            roleBadge.style.cssText = 'background:rgba(139,92,246,0.2);color:#a78bfa;border:1px solid rgba(139,92,246,0.3);border-radius:6px;padding:1px 6px;font-size:0.65rem;font-weight:700;margin-left:6px;';
        }
    }

    // Apply theme
    applyTheme(currentTheme);
    $('btn-theme').addEventListener('click', () => applyTheme(currentTheme === 'dark' ? 'light' : 'dark'));
    $('btn-logout').addEventListener('click', logout);

    // Init charts
    initBarChart();
    initFlowChart();

    // Load datasets from server
    loadDatasets();

    // WebSocket
    connectWS();

    // Control buttons
    $('btn-play').addEventListener('click',  startStream);
    $('btn-pause').addEventListener('click', pauseStream);
    $('btn-reset').addEventListener('click', resetTracking);

    // Sliders
    $('conf-slider').addEventListener('input',  () => $('conf-val').textContent = parseFloat($('conf-slider').value).toFixed(2));
    $('conf-slider').addEventListener('change', syncConfig);
    $('line-slider').addEventListener('input',  () => $('line-val').textContent = `${$('line-slider').value}%`);
    $('line-slider').addEventListener('change', syncConfig);

    // Model / Dataset
    $('model-select').addEventListener('change', syncConfig);
    $('dataset-select').addEventListener('change', syncConfig);

    // Class checkboxes
    ['class-person','class-bicycle','class-car','class-motorcycle','class-bus','class-truck']
        .forEach(id => $(id) && $(id).addEventListener('change', syncConfig));

    // Source tabs
    $('src-btn-video').addEventListener('click', () => setSource('video'));
    $('src-btn-cam').addEventListener('click',   () => setSource('webcam'));
    $('src-btn-image').addEventListener('click', () => setSource('image'));

    // File inputs
    $('video-file-input').addEventListener('change', () => {
        if ($('video-file-input').files.length) {
            $('selected-file-name').textContent = $('video-file-input').files[0].name;
            uploadVideo($('video-file-input').files[0]);
        }
    });
    $('image-file-input').addEventListener('change', () => {
        if ($('image-file-input').files.length) {
            $('selected-image-name').textContent = $('image-file-input').files[0].name;
            uploadImage($('image-file-input').files[0]);
        }
    });
});

// ─────────────────────────────────────────────
//  Source Tab switching
// ─────────────────────────────────────────────
function setSource(type) {
    currentSourceType = type;
    ['video','cam','image'].forEach(t => {
        const btn = document.getElementById(`src-btn-${t === 'cam' ? 'cam' : t}`);
        if (btn) btn.classList.toggle('active', t === type || (type === 'webcam' && t === 'cam'));
    });
    // Fix: ensure each tab button properly activates
    document.getElementById('src-btn-video').classList.toggle('active', type === 'video');
    document.getElementById('src-btn-cam').classList.toggle('active',   type === 'webcam');
    document.getElementById('src-btn-image').classList.toggle('active', type === 'image');

    document.getElementById('video-upload-section').style.display = type === 'video'  ? 'block' : 'none';
    document.getElementById('image-upload-section').style.display = type === 'image'  ? 'block' : 'none';

    syncConfig();
}

// ─────────────────────────────────────────────
//  Config Sync
// ─────────────────────────────────────────────
function getCheckedClasses() {
    return ['class-person','class-bicycle','class-car','class-motorcycle','class-bus','class-truck']
        .map(id => document.getElementById(id))
        .filter(el => el && el.checked)
        .map(el => parseInt(el.value));
}

function syncConfig() {
    const token = getToken();
    const config = {
        model:              document.getElementById('model-select').value,
        conf_threshold:     parseFloat(document.getElementById('conf-slider').value),
        line_position_ratio: parseFloat(document.getElementById('line-slider').value) / 100,
        classes:            getCheckedClasses(),
        source_type:        currentSourceType,
        video_file:         document.getElementById('dataset-select').value || ''
    };
    fetch(`/api/config?token=${token}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    })
    .then(r => r.json())
    .then(d => {
        if (d.status !== 'success') console.warn('Config sync failed', d);
        else if (isPlaying) reloadStream();
    })
    .catch(e => console.error('syncConfig error', e));
}

// ─────────────────────────────────────────────
//  Dataset Loader
// ─────────────────────────────────────────────
function loadDatasets() {
    const token = getToken();
    fetch(`/api/datasets?token=${token}`)
    .then(r => r.json())
    .then(d => {
        const sel = document.getElementById('dataset-select');
        sel.innerHTML = '';
        if (d.datasets && d.datasets.length) {
            d.datasets.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f; opt.textContent = f;
                if (f === 'video.mp4') opt.selected = true;
                sel.appendChild(opt);
            });
        } else {
            sel.innerHTML = '<option value="">No video files found</option>';
        }
        syncConfig();
    })
    .catch(e => console.error('loadDatasets error', e));
}

// ─────────────────────────────────────────────
//  Video Upload with XHR progress
// ─────────────────────────────────────────────
function uploadVideo(file) {
    const token = getToken();
    const fd = new FormData();
    fd.append('file', file);

    const progWrap = document.getElementById('upload-progress');
    const progBar  = document.getElementById('upload-bar');
    const progPct  = document.getElementById('upload-pct');
    document.getElementById('selected-file-name').textContent = 'Uploading…';
    progWrap.style.display = 'flex';
    progBar.style.width = '0%';

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload?token=${token}`);

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            progBar.style.width = `${pct}%`;
            progPct.textContent = `${pct}%`;
        }
    };

    xhr.onload = () => {
        progWrap.style.display = 'none';
        try {
            const data = JSON.parse(xhr.responseText);
            if (data.status === 'success') {
                document.getElementById('selected-file-name').textContent = `✓ ${file.name}`;
                loadDatasets();  // refresh dropdown
                resetTracking();
                setTimeout(startStream, 600);
            } else {
                document.getElementById('selected-file-name').textContent = 'Upload failed';
                alert(`Upload error: ${data.message}`);
            }
        } catch { document.getElementById('selected-file-name').textContent = 'Upload error'; }
    };

    xhr.onerror = () => {
        progWrap.style.display = 'none';
        document.getElementById('selected-file-name').textContent = 'Network error';
    };

    xhr.send(fd);
}

// ─────────────────────────────────────────────
//  Image Upload
// ─────────────────────────────────────────────
async function uploadImage(file) {
    const token = getToken();
    const fd = new FormData();
    fd.append('file', file);
    document.getElementById('selected-image-name').textContent = 'Uploading…';
    try {
        const res  = await fetch(`/api/upload_image?token=${token}`, { method: 'POST', body: fd });
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('selected-image-name').textContent = `✓ ${file.name}`;
            resetTracking();
            setTimeout(startStream, 400);
        } else {
            document.getElementById('selected-image-name').textContent = 'Upload failed';
            alert(`Upload error: ${data.message}`);
        }
    } catch (e) {
        document.getElementById('selected-image-name').textContent = 'Network error';
        console.error(e);
    }
}

// ─────────────────────────────────────────────
//  Stream Control
// ─────────────────────────────────────────────
function reloadStream() {
    const token = getToken();
    const ts = Date.now();
    document.getElementById('video-stream').src = `/api/stream?token=${token}&_t=${ts}`;
}

async function startStream() {
    if (isPlaying) return;
    const token = getToken();
    try {
        const res  = await fetch(`/api/control?token=${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'play' })
        });
        const data = await res.json();
        if (data.status === 'success') {
            isPlaying = true;
            document.getElementById('btn-play').disabled  = true;
            document.getElementById('btn-pause').disabled = false;
            const vid = document.getElementById('video-stream');
            vid.style.display = 'block';
            document.getElementById('stream-placeholder').style.display = 'none';
            reloadStream();
        }
    } catch (e) { console.error('startStream error', e); }
}

async function pauseStream() {
    if (!isPlaying) return;
    const token = getToken();
    try {
        const res = await fetch(`/api/control?token=${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'pause' })
        });
        const data = await res.json();
        if (data.status === 'success') {
            isPlaying = false;
            document.getElementById('btn-play').disabled  = false;
            document.getElementById('btn-pause').disabled = true;
            const vid = document.getElementById('video-stream');
            vid.src = '';
            vid.style.display = 'none';
            document.getElementById('stream-placeholder').style.display = 'flex';
        }
    } catch (e) { console.error('pauseStream error', e); }
}

async function resetTracking() {
    const token = getToken();
    try {
        await fetch(`/api/control?token=${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'reset' })
        });
    } catch (e) { console.error('resetTracking error', e); }
    document.getElementById('stat-active').textContent    = '0';
    document.getElementById('stat-crossings').textContent = '0';
    document.getElementById('event-log').innerHTML = '<li class="empty-log">No events yet.</li>';
    if (chart)     { chart.data.datasets[0].data = [0,0,0,0,0,0]; chart.update(); }
    if (flowChart) {
        flowChart.data.labels = [];
        flowChart.data.datasets.forEach(ds => ds.data = []);
        flowChart.update();
    }
}

// ─────────────────────────────────────────────
//  WebSocket
// ─────────────────────────────────────────────
let ws = null;

function connectWS() {
    const token = getToken();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${token}`);

    ws.onopen = () => {
        const el = document.getElementById('connection-status');
        if (el) { el.className = 'status-badge connected'; el.innerHTML = '<span class="status-dot"></span> Online'; }
    };
    ws.onclose = () => {
        const el = document.getElementById('connection-status');
        if (el) { el.className = 'status-badge disconnected'; el.innerHTML = '<span class="status-dot"></span> Offline'; }
        setTimeout(connectWS, 3000);
    };
    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'stats')  updateStats(msg.data);
            if (msg.type === 'event') addEventLog(msg.data);
        } catch { }
    };
}

// ─────────────────────────────────────────────
//  Stats Update
// ─────────────────────────────────────────────
function updateStats(data) {
    document.getElementById('stat-active').textContent    = data.active_count;
    document.getElementById('stat-crossings').textContent = data.total_crossings;
    document.getElementById('stat-fps').textContent       = (data.fps || 0).toFixed(1);

    const viewers = document.getElementById('active-viewers');
    if (viewers && data.active_viewers) viewers.textContent = data.active_viewers;

    // Resource bars
    setBar('cpu', data.cpu_usage);
    setBar('ram', data.ram_usage);

    // Bar chart (crossings by class)
    if (chart) {
        const classMap = { person:0, bicycle:1, car:2, motorcycle:3, bus:4, truck:5 };
        const counts = [0,0,0,0,0,0];
        for (const [cls, cnt] of Object.entries(data.crossings_by_class || {})) {
            if (classMap[cls] !== undefined) counts[classMap[cls]] = cnt;
        }
        chart.data.datasets[0].data = counts;
        chart.update('none');
    }

    // Flow chart
    if (flowChart) {
        const t = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
        flowChart.data.labels.push(t);
        flowChart.data.datasets[0].data.push(data.active_count);
        flowChart.data.datasets[1].data.push(data.total_crossings);
        if (flowChart.data.labels.length > 20) {
            flowChart.data.labels.shift();
            flowChart.data.datasets.forEach(ds => ds.shift());
        }
        flowChart.update('none');
    }
}

function setBar(id, pct) {
    const bar = document.getElementById(`${id}-bar`);
    const val = document.getElementById(`${id}-val`);
    if (!bar || !val || pct === undefined) return;
    bar.style.width = `${pct}%`;
    val.textContent = `${Math.round(pct)}%`;
    bar.className   = 'progress-bar' + (pct > 85 ? ' danger' : pct > 60 ? ' warning' : '');
}

// ─────────────────────────────────────────────
//  Event Log
// ─────────────────────────────────────────────
function addEventLog(ev) {
    const log = document.getElementById('event-log');
    const empty = log.querySelector('.empty-log');
    if (empty) empty.remove();

    const li = document.createElement('li');
    li.className = ev.direction === 'down' ? 'cross-in' : 'cross-out';
    li.innerHTML = `<span class="timestamp">[${ev.time}]</span>
        <span><strong>${(ev.class||'').toUpperCase()} #${ev.id}</strong> crossed <em>${ev.direction}</em></span>`;
    log.insertBefore(li, log.firstChild);
    while (log.children.length > 40) log.removeChild(log.lastChild);
}

// ─────────────────────────────────────────────
//  Charts
// ─────────────────────────────────────────────
function chartColors() {
    const isDark = document.documentElement.dataset.theme !== 'light';
    return { grid: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)', tick: isDark ? '#94a3b8' : '#64748b' };
}

function initBarChart() {
    const ctx = document.getElementById('analytics-chart');
    if (!ctx) return;
    const { grid, tick } = chartColors();
    chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Person','Bicycle','Car','Motorcycle','Bus','Truck'],
            datasets: [{
                label: 'Crossings',
                data: [0,0,0,0,0,0],
                backgroundColor: ['rgba(6,182,212,0.55)','rgba(139,92,246,0.55)','rgba(16,185,129,0.55)','rgba(245,158,11,0.55)','rgba(59,130,246,0.55)','rgba(239,68,68,0.55)'],
                borderColor:     ['#06b6d4','#8b5cf6','#10b981','#f59e0b','#3b82f6','#ef4444'],
                borderWidth: 1.5, borderRadius: 5
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { grid: { color: grid }, ticks: { color: tick, font: { family: 'Outfit', size: 9 } } },
                x: { grid: { display: false }, ticks: { color: tick, font: { family: 'Outfit', size: 9 } } }
            }
        }
    });
}

function initFlowChart() {
    const ctx = document.getElementById('flow-chart');
    if (!ctx) return;
    const { grid, tick } = chartColors();
    flowChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'Active', data: [], borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.06)', fill: true, tension: 0.35, borderWidth: 2, pointRadius: 0 },
                { label: 'Crossings', data: [], borderColor: '#06b6d4', backgroundColor: 'rgba(6,182,212,0.06)', fill: true, tension: 0.35, borderWidth: 2, pointRadius: 0 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: { legend: { labels: { color: tick, font: { family: 'Outfit', size: 10 }, boxWidth: 12 } } },
            scales: {
                y: { grid: { color: grid }, ticks: { color: tick, font: { family: 'Outfit', size: 9 } } },
                x: { grid: { display: false }, ticks: { color: tick, font: { family: 'Outfit', size: 8 }, maxTicksLimit: 6 } }
            }
        }
    });
}

function updateChartTheme() {
    if (!chart && !flowChart) return;
    const { grid, tick } = chartColors();
    [chart, flowChart].forEach(c => {
        if (!c) return;
        c.options.scales.x.ticks.color = tick;
        c.options.scales.y.ticks.color = tick;
        c.options.scales.y.grid.color  = grid;
        if (c.options.plugins?.legend?.labels) c.options.plugins.legend.labels.color = tick;
        c.update();
    });
}
