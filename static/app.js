// Global State
let wsConnection = null;
let chart = null;
let flowChart = null;
let isPlaying = false;

// Generate or retrieve independent User Session ID
let sessionId = sessionStorage.getItem('tracker_session_id');
if (!sessionId) {
    sessionId = 'sess_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now().toString(36);
    sessionStorage.setItem('tracker_session_id', sessionId);
}

// DOM Elements
const connectionStatus = document.getElementById('connection-status');
const videoStream = document.getElementById('video-stream');
const streamPlaceholder = document.getElementById('stream-placeholder');
const btnPlay = document.getElementById('btn-play');
const btnPause = document.getElementById('btn-pause');
const btnReset = document.getElementById('btn-reset');
const btnLogout = document.getElementById('btn-logout');

const modelSelect = document.getElementById('model-select');
const datasetSelect = document.getElementById('dataset-select');
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
const tabImage = document.getElementById('src-btn-image');

const videoUploadSection = document.getElementById('video-upload-section');
const videoFileInput = document.getElementById('video-file-input');
const selectedFileName = document.getElementById('selected-file-name');

const imageUploadSection = document.getElementById('image-upload-section');
const imageFileInput = document.getElementById('image-file-input');
const selectedImageName = document.getElementById('selected-image-name');

const statActive = document.getElementById('stat-active');
const statCrossings = document.getElementById('stat-crossings');
const statFps = document.getElementById('stat-fps');
const eventLog = document.getElementById('event-log');

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    // Populate username display
    const loggedUser = document.getElementById('logged-user-name');
    if (loggedUser) {
        loggedUser.innerText = localStorage.getItem('logged_user') || 'Admin';
    }

    initChart();
    initFlowChart();
    loadDatasets();
    connectWebSocket();
    setupEventListeners();
    syncConfig();
});

// Setup Chart 1: Bar Chart (Class crossings count)
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
                    'rgba(59, 130, 246, 0.6)',  // Blue
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
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af', font: { family: 'Outfit', size: 9 } }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#9ca3af', font: { family: 'Outfit', size: 9 } }
                }
            }
        }
    });
}

// Setup Chart 2: Line Chart (Flow over time)
function initFlowChart() {
    const ctx = document.getElementById('flow-chart').getContext('2d');
    flowChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Active Detections',
                    data: [],
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.05)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 1
                },
                {
                    label: 'Line Crossings',
                    data: [],
                    borderColor: '#06b6d4',
                    backgroundColor: 'rgba(6, 182, 212, 0.05)',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#9ca3af', font: { family: 'Outfit', size: 10 } }
                }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af', font: { family: 'Outfit', size: 9 } }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#9ca3af', font: { family: 'Outfit', size: 8 } }
                }
            }
        }
    });
}

// Fetch list of available datasets from server
function loadDatasets() {
    fetch(`/api/datasets?session_id=${sessionId}`)
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success' && data.datasets) {
            datasetSelect.innerHTML = '';
            data.datasets.forEach(dataset => {
                const opt = document.createElement('option');
                opt.value = dataset;
                opt.innerText = dataset;
                // Set default video selection
                if (dataset === 'video.mp4') {
                    opt.selected = true;
                }
                datasetSelect.appendChild(opt);
            });
            syncConfig();
        }
    })
    .catch(err => console.error('Error fetching datasets:', err));
}

// WebSocket Connection with Session ID
function connectWebSocket() {
    const loc = window.location;
    let wsUri = loc.protocol === "https:" ? "wss:" : "ws:";
    wsUri += `//${loc.host}/ws?session_id=${sessionId}`;

    wsConnection = new WebSocket(wsUri);

    wsConnection.onopen = () => {
        connectionStatus.className = 'status-badge connected';
        connectionStatus.innerHTML = '<span class="status-dot"></span> Online';
    };

    wsConnection.onclose = () => {
        connectionStatus.className = 'status-badge disconnected';
        connectionStatus.innerHTML = '<span class="status-dot"></span> Offline';
        setTimeout(connectWebSocket, 3000); // Reconnect loop
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

// Update Stats & Charts with Broadcast Telemetry
function updateStats(data) {
    statActive.innerText = data.active_count;
    statCrossings.innerText = data.total_crossings;
    statFps.innerText = data.fps.toFixed(1);

    // Concurrent viewer count
    if (data.active_viewers !== undefined) {
        document.getElementById('active-viewers').innerText = data.active_viewers;
    }

    // CPU/RAM usage meters
    if (data.cpu_usage !== undefined) {
        document.getElementById('cpu-val').innerText = `${data.cpu_usage.toFixed(0)}%`;
        const cpuBar = document.getElementById('cpu-bar');
        cpuBar.style.width = `${data.cpu_usage}%`;
        cpuBar.className = 'progress-bar' + (data.cpu_usage > 85 ? ' danger' : data.cpu_usage > 60 ? ' warning' : '');
    }
    if (data.ram_usage !== undefined) {
        document.getElementById('ram-val').innerText = `${data.ram_usage.toFixed(0)}%`;
        const ramBar = document.getElementById('ram-bar');
        ramBar.style.width = `${data.ram_usage}%`;
        ramBar.className = 'progress-bar' + (data.ram_usage > 85 ? ' danger' : data.ram_usage > 60 ? ' warning' : '');
    }

    // Update crossings bar chart
    const classMapping = {
        'person': 0, 'bicycle': 1, 'car': 2, 'motorcycle': 3, 'bus': 4, 'truck': 5
    };
    const counts = [0, 0, 0, 0, 0, 0];
    for (const [cls, count] of Object.entries(data.crossings_by_class)) {
        if (classMapping[cls] !== undefined) {
            counts[classMapping[cls]] = count;
        }
    }
    chart.data.datasets[0].data = counts;
    chart.update();

    // Update flow line chart over time
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    flowChart.data.labels.push(timeStr);
    flowChart.data.datasets[0].data.push(data.active_count);
    flowChart.data.datasets[1].data.push(data.total_crossings);

    // Retain only the last 15 points
    if (flowChart.data.labels.length > 15) {
        flowChart.data.labels.shift();
        flowChart.data.datasets[0].data.shift();
        flowChart.data.datasets[1].data.shift();
    }
    flowChart.update();
}

// Add Crossing Log
function addEventLog(eventData) {
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

    eventLog.insertBefore(li, eventLog.firstChild);

    while (eventLog.children.length > 30) {
        eventLog.removeChild(eventLog.lastChild);
    }
}

// Sync session configuration with server
function syncConfig() {
    let source = 'video';
    if (tabCam.classList.contains('active')) {
        source = 'webcam';
    } else if (tabImage.classList.contains('active')) {
        source = 'image';
    }

    const config = {
        model: modelSelect.value,
        conf_threshold: parseFloat(confSlider.value),
        line_position_ratio: parseFloat(lineSlider.value) / 100,
        classes: classCheckboxes.filter(cb => cb.checked).map(cb => parseInt(cb.value)),
        source_type: source,
        video_file: datasetSelect.value || 'video.mp4'
    };

    fetch(`/api/config?session_id=${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status !== 'success') {
            console.error('Config synchronization failed', data);
        } else if (isPlaying) {
            // Force stream reload to sync settings instantly
            const timestamp = new Date().getTime();
            videoStream.src = `/api/stream?session_id=${sessionId}&t=${timestamp}`;
        }
    })
    .catch(err => console.error('Error syncing config:', err));
}

// Setup Event Listeners
function setupEventListeners() {
    confSlider.addEventListener('input', () => {
        confVal.innerText = parseFloat(confSlider.value).toFixed(2);
    });
    confSlider.addEventListener('change', syncConfig);

    lineSlider.addEventListener('input', () => {
        lineVal.innerText = `${lineSlider.value}%`;
    });
    lineSlider.addEventListener('change', syncConfig);

    modelSelect.addEventListener('change', syncConfig);
    datasetSelect.addEventListener('change', syncConfig);

    classCheckboxes.forEach(cb => {
        cb.addEventListener('change', syncConfig);
    });

    // Logout Command
    if (btnLogout) {
        btnLogout.addEventListener('click', () => {
            localStorage.removeItem('is_logged_in');
            localStorage.removeItem('logged_user');
            window.location.href = 'login.html';
        });
    }

    // Input Tabs Configuration
    tabVideo.addEventListener('click', () => {
        tabVideo.classList.add('active');
        tabCam.classList.remove('active');
        tabImage.classList.remove('active');
        videoUploadSection.style.display = 'block';
        imageUploadSection.style.display = 'none';
        syncConfig();
    });

    tabCam.addEventListener('click', () => {
        tabCam.classList.add('active');
        tabVideo.classList.remove('active');
        tabImage.classList.remove('active');
        videoUploadSection.style.display = 'none';
        imageUploadSection.style.display = 'none';
        syncConfig();
    });

    tabImage.addEventListener('click', () => {
        tabImage.classList.add('active');
        tabVideo.classList.remove('active');
        tabCam.classList.remove('active');
        videoUploadSection.style.display = 'none';
        imageUploadSection.style.display = 'block';
        syncConfig();
    });

    // Custom Video File selection
    videoFileInput.addEventListener('change', () => {
        if (videoFileInput.files.length > 0) {
            const file = videoFileInput.files[0];
            selectedFileName.innerText = file.name;
            uploadVideoFile(file);
        }
    });

    // Custom Image File selection
    imageFileInput.addEventListener('change', () => {
        if (imageFileInput.files.length > 0) {
            const file = imageFileInput.files[0];
            selectedImageName.innerText = file.name;
            uploadImageFile(file);
        }
    });

    btnPlay.addEventListener('click', startStream);
    btnPause.addEventListener('click', pauseStream);
    btnReset.addEventListener('click', resetTracking);
}

// Upload custom video for this user session
function uploadVideoFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    selectedFileName.innerText = "Uploading...";
    fetch(`/api/upload?session_id=${sessionId}`, {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            selectedFileName.innerText = `Uploaded: ${file.name}`;
            
            // Re-fetch dataset list to populate dropdown with the newly uploaded dataset
            fetch(`/api/datasets?session_id=${sessionId}`)
            .then(res => res.json())
            .then(datasetData => {
                if (datasetData.status === 'success') {
                    datasetSelect.innerHTML = '';
                    datasetData.datasets.forEach(dataset => {
                        const opt = document.createElement('option');
                        opt.value = dataset;
                        opt.innerText = dataset;
                        if (dataset === data.filename) {
                            opt.selected = true;
                        }
                        datasetSelect.appendChild(opt);
                    });
                    resetTracking();
                    
                    // Automatically trigger stream play
                    setTimeout(startStream, 500);
                }
            });
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

// Upload custom image for static analysis
function uploadImageFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    selectedImageName.innerText = "Uploading...";
    fetch(`/api/upload_image?session_id=${sessionId}`, {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            selectedImageName.innerText = `Uploaded: ${file.name}`;
            resetTracking();
            
            // Automatically play static image stream
            setTimeout(startStream, 500);
        } else {
            selectedImageName.innerText = "Upload failed";
            alert(`Error: ${data.message}`);
        }
    })
    .catch(err => {
        selectedImageName.innerText = "Upload error";
        console.error('Error uploading image:', err);
    });
}

// Play Stream actions
function startStream() {
    if (isPlaying) return;

    fetch(`/api/control?session_id=${sessionId}`, {
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
            
            const timestamp = new Date().getTime();
            videoStream.src = `/api/stream?session_id=${sessionId}&t=${timestamp}`;
            videoStream.style.display = 'block';
            streamPlaceholder.style.display = 'none';
        }
    })
    .catch(err => console.error('Error starting stream:', err));
}

// Pause Stream actions
function pauseStream() {
    if (!isPlaying) return;

    fetch(`/api/control?session_id=${sessionId}`, {
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
            
            videoStream.src = '';
            videoStream.style.display = 'none';
            streamPlaceholder.style.display = 'flex';
        }
    })
    .catch(err => console.error('Error pausing stream:', err));
}

// Reset tracking stats
function resetTracking() {
    fetch(`/api/control?session_id=${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'reset' })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            statActive.innerText = '0';
            statCrossings.innerText = '0';
            eventLog.innerHTML = '<li class="empty-log">No events recorded yet.</li>';
            
            chart.data.datasets[0].data = [0, 0, 0, 0, 0, 0];
            chart.update();

            flowChart.data.labels = [];
            flowChart.data.datasets[0].data = [];
            flowChart.data.datasets[1].data = [];
            flowChart.update();
        }
    })
    .catch(err => console.error('Error resetting stream:', err));
}
