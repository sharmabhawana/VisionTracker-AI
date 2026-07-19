// Global State
let wsConnection = null;
let chart = null;
let isPlaying = false;

// DOM Elements
const connectionStatus = document.getElementById('connection-status');
const videoStream = document.getElementById('video-stream');
const streamPlaceholder = document.getElementById('stream-placeholder');
const btnPlay = document.getElementById('btn-play');
const btnPause = document.getElementById('btn-pause');
const btnReset = document.getElementById('btn-reset');

const modelSelect = document.getElementById('model-select');
const confSlider = document.getElementById('conf-slider');
const confVal = document.getElementById('conf-val');
const lineSlider = document.getElementById('line-slider');
const lineVal = document.getElementById('line-val');

const classCheckboxes = [
    document.getElementById('class-person'),
    document.getElementById('class-bicycle'),
    document.getElementById('class-car'),
    document.getElementById('class-motorcycle'),
    document.getElementById('class-bus'),
    document.getElementById('class-truck')
];

const tabVideo = document.getElementById('src-btn-video');
const tabCam = document.getElementById('src-btn-cam');
const videoUploadSection = document.getElementById('video-upload-section');
const videoFileInput = document.getElementById('video-file-input');
const selectedFileName = document.getElementById('selected-file-name');

const statActive = document.getElementById('stat-active');
const statCrossings = document.getElementById('stat-crossings');
const statFps = document.getElementById('stat-fps');
const eventLog = document.getElementById('event-log');

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    initChart();
    connectWebSocket();
    setupEventListeners();
    syncConfig();
});

// Setup Chart.js
function initChart() {
    const ctx = document.getElementById('analytics-chart').getContext('2d');
    chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Person', 'Bicycle', 'Car', 'Motorcycle', 'Bus', 'Truck'],
            datasets: [{
                label: 'Total Crossings',
                data: [0, 0, 0, 0, 0, 0],
                backgroundColor: [
                    'rgba(6, 182, 212, 0.6)',   // Cyan
                    'rgba(139, 92, 246, 0.6)',  // Purple
                    'rgba(16, 185, 129, 0.6)',  // Green
                    'rgba(245, 158, 11, 0.6)',  // Orange
                    'rgba(59, 130, 246, 0.6)',   // Blue
                    'rgba(239, 68, 68, 0.6)'    // Red
                ],
                borderColor: [
                    '#06b6d4',
                    '#8b5cf6',
                    '#10b981',
                    '#f59e0b',
                    '#3b82f6',
                    '#ef4444'
                ],
                borderWidth: 1.5,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)'
                    },
                    ticks: {
                        color: '#9ca3af',
                        font: { family: 'Outfit' }
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: '#9ca3af',
                        font: { family: 'Outfit' }
                    }
                }
            }
        }
    });
}

// WebSocket Connection
function connectWebSocket() {
    const loc = window.location;
    let wsUri = loc.protocol === "https:" ? "wss:" : "ws:";
    wsUri += `//${loc.host}/ws`;

    wsConnection = new WebSocket(wsUri);

    wsConnection.onopen = () => {
        connectionStatus.className = 'status-badge connected';
        connectionStatus.innerHTML = '<span class="status-dot"></span> Online';
    };

    wsConnection.onclose = () => {
        connectionStatus.className = 'status-badge disconnected';
        connectionStatus.innerHTML = '<span class="status-dot"></span> Offline';
        // Auto-reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
    };

    wsConnection.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'stats') {
            updateStats(msg.data);
        } else if (msg.type === 'event') {
            addEventLog(msg.data);
        }
    };
}

// Update UI Stats & Chart
function updateStats(data) {
    statActive.innerText = data.active_count;
    statCrossings.innerText = data.total_crossings;
    statFps.innerText = data.fps.toFixed(1);

    // Update Chart data
    // Map class names to Chart index
    const classMapping = {
        'person': 0,
        'bicycle': 1,
        'car': 2,
        'motorcycle': 3,
        'bus': 4,
        'truck': 5
    };

    const counts = [0, 0, 0, 0, 0, 0];
    for (const [cls, count] of Object.entries(data.crossings_by_class)) {
        if (classMapping[cls] !== undefined) {
            counts[classMapping[cls]] = count;
        }
    }
    chart.data.datasets[0].data = counts;
    chart.update();
}

// Add Crossing Event Log
function addEventLog(eventData) {
    // Remove empty log placeholder if there
    const emptyLog = eventLog.querySelector('.empty-log');
    if (emptyLog) {
        eventLog.removeChild(emptyLog);
    }

    const li = document.createElement('li');
    const directionClass = eventData.direction === 'in' || eventData.direction === 'down' ? 'cross-in' : 'cross-out';
    li.className = directionClass;

    const timestamp = document.createElement('span');
    timestamp.className = 'timestamp';
    timestamp.innerText = `[${eventData.time}]`;

    const details = document.createElement('span');
    details.innerHTML = `<span class="highlight">${eventData.class.toUpperCase()} (ID:${eventData.id})</span> crossed line moving <span class="highlight">${eventData.direction}</span>`;

    li.appendChild(timestamp);
    li.appendChild(details);

    // Prepend to show newest events at the top
    eventLog.insertBefore(li, eventLog.firstChild);

    // Keep only last 30 logs
    while (eventLog.children.length > 30) {
        eventLog.removeChild(eventLog.lastChild);
    }
}

// Sync Form configurations with Backend
function syncConfig() {
    const config = {
        model: modelSelect.value,
        conf_threshold: parseFloat(confSlider.value),
        line_position_ratio: parseFloat(lineSlider.value) / 100,
        classes: classCheckboxes.filter(cb => cb.checked).map(cb => parseInt(cb.value)),
        source_type: tabVideo.classList.contains('active') ? 'video' : 'webcam'
    };

    fetch('/api/config', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(config)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status !== 'success') {
            console.error('Config synchronization failed', data);
        }
    })
    .catch(err => console.error('Error syncing config:', err));
}

// Event Listeners setup
function setupEventListeners() {
    // Sliders input visual feedback
    confSlider.addEventListener('input', () => {
        confVal.innerText = parseFloat(confSlider.value).toFixed(2);
    });
    confSlider.addEventListener('change', syncConfig);

    lineSlider.addEventListener('input', () => {
        lineVal.innerText = `${lineSlider.value}%`;
    });
    lineSlider.addEventListener('change', syncConfig);

    modelSelect.addEventListener('change', syncConfig);

    classCheckboxes.forEach(cb => {
        cb.addEventListener('change', syncConfig);
    });

    // Control tabs
    tabVideo.addEventListener('click', () => {
        tabVideo.classList.add('active');
        tabCam.classList.remove('active');
        videoUploadSection.style.display = 'block';
        syncConfig();
    });

    tabCam.addEventListener('click', () => {
        tabCam.classList.add('active');
        tabVideo.classList.remove('active');
        videoUploadSection.style.display = 'none';
        syncConfig();
    });

    // Custom File selection
    videoFileInput.addEventListener('change', () => {
        if (videoFileInput.files.length > 0) {
            const file = videoFileInput.files[0];
            selectedFileName.innerText = file.name;
            uploadVideoFile(file);
        }
    });

    // Playback Controls
    btnPlay.addEventListener('click', startStream);
    btnPause.addEventListener('click', pauseStream);
    btnReset.addEventListener('click', resetTracking);
}

// Upload Custom Video
function uploadVideoFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    selectedFileName.innerText = "Uploading...";
    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            selectedFileName.innerText = `Uploaded: ${file.name}`;
            resetTracking();
        } else {
            selectedFileName.innerText = "Upload failed";
            alert(`Error: ${data.message}`);
        }
    })
    .catch(err => {
        selectedFileName.innerText = "Upload error";
        console.error('Error uploading video:', err);
    });
}

// Stream Actions
function startStream() {
    if (isPlaying) return;

    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'play' })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            isPlaying = true;
            btnPlay.disabled = true;
            btnPause.disabled = false;
            
            // Set image source to the stream endpoint
            const timestamp = new Date().getTime();
            videoStream.src = `/api/stream?t=${timestamp}`;
            videoStream.style.display = 'block';
            streamPlaceholder.style.display = 'none';
        }
    })
    .catch(err => console.error('Error playing stream:', err));
}

// Stream Actions (Pause)
function pauseStream() {
    if (!isPlaying) return;

    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'pause' })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            isPlaying = false;
            btnPlay.disabled = false;
            btnPause.disabled = true;
            
            // Stop image download
            videoStream.src = '';
            videoStream.style.display = 'none';
            streamPlaceholder.style.display = 'flex';
        }
    })
    .catch(err => console.error('Error pausing stream:', err));
}

function resetTracking() {
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'reset' })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            // Reset stats
            statActive.innerText = '0';
            statCrossings.innerText = '0';
            
            // Clear event logs
            eventLog.innerHTML = '<li class="empty-log">No events recorded yet.</li>';
            
            // Reset Chart
            chart.data.datasets[0].data = [0, 0, 0, 0, 0, 0];
            chart.update();
        }
    })
    .catch(err => console.error('Error resetting stream:', err));
}
