import os
import cv2
import time
import json
import asyncio
import hashlib
import secrets
from datetime import datetime
from collections import deque, defaultdict
from typing import List, Dict, Set, Optional
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from ultralytics import YOLO
except ImportError:
    import subprocess, sys
    print("Ultralytics not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ultralytics", "lap"])
    from ultralytics import YOLO

try:
    import psutil
except ImportError:
    psutil = None

# ─────────────────────────────────────────────
#  App & Storage Paths
# ─────────────────────────────────────────────
app = FastAPI(title="VisionTracker AI")
USERS_FILE = "users.json"
UPLOADS_DIR = "uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ─────────────────────────────────────────────
#  Auth helpers
# ─────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    # Bootstrap admin
    admin = {
        "bhawanasharma": {
            "password_hash": hash_password("sharma#1S"),
            "role": "admin",
            "display_name": "Bhawana Sharma"
        }
    }
    save_users(admin)
    return admin

def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# In-memory token store: token -> username
active_tokens: Dict[str, str] = {}
users_db: dict = {}

@app.on_event("startup")
async def startup_event():
    global loop, users_db
    loop = asyncio.get_running_loop()
    users_db = load_users()

# ─────────────────────────────────────────────
#  Auth Endpoints
# ─────────────────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None

@app.post("/api/signup")
async def signup(req: SignupRequest):
    uname = req.username.strip().lower()
    if not uname or len(uname) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if uname in users_db:
        raise HTTPException(409, "Username already exists. Please log in.")
    users_db[uname] = {
        "password_hash": hash_password(req.password),
        "role": "user",
        "display_name": req.display_name or req.username
    }
    save_users(users_db)
    token = secrets.token_hex(32)
    active_tokens[token] = uname
    return {"status": "success", "token": token, "username": uname,
            "display_name": users_db[uname]["display_name"], "role": "user"}

@app.post("/api/login")
async def login(req: AuthRequest):
    uname = req.username.strip().lower()
    user = users_db.get(uname)
    if not user or user["password_hash"] != hash_password(req.password):
        raise HTTPException(401, "Invalid username or password.")
    token = secrets.token_hex(32)
    active_tokens[token] = uname
    return {"status": "success", "token": token, "username": uname,
            "display_name": user.get("display_name", uname), "role": user.get("role", "user")}

@app.post("/api/logout")
async def logout(token: str = None):
    if token and token in active_tokens:
        del active_tokens[token]
    return {"status": "success"}

def get_current_user(token: str = None) -> Optional[str]:
    if not token or token not in active_tokens:
        return None
    return active_tokens[token]

def require_user(token: str = None) -> str:
    username = get_current_user(token)
    if not username:
        raise HTTPException(401, "Authentication required.")
    return username

# ─────────────────────────────────────────────
#  Tracker Config & Session
# ─────────────────────────────────────────────
class TrackerConfig(BaseModel):
    model: str = "yolov8n.pt"
    conf_threshold: float = 0.25
    line_position_ratio: float = 0.5
    classes: List[int] = [0, 1, 2, 3, 5, 7]
    source_type: str = "video"
    video_file: str = ""

class UserSession:
    def __init__(self, username: str):
        self.username = username
        self.config = TrackerConfig()
        self.playback_state = "idle"
        self.active_tracks_count = 0
        self.total_crossings = 0
        self.crossings_by_class = defaultdict(int)
        self.crossed_ids: Set[int] = set()
        self.track_history = defaultdict(lambda: deque(maxlen=30))
        self.fps_val = 0.0
        self.cap = None
        self.last_source = None
        self.last_source_type = None
        # Per-user upload folder
        self.upload_dir = os.path.join(UPLOADS_DIR, username)
        os.makedirs(self.upload_dir, exist_ok=True)

active_sessions: Dict[str, UserSession] = {}

def get_session(username: str) -> UserSession:
    if username not in active_sessions:
        active_sessions[username] = UserSession(username)
    return active_sessions[username]

loop = None

# ─────────────────────────────────────────────
#  WebSocket Manager (per-user)
# ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, username: str):
        await ws.accept()
        self.active_connections[ws] = username

    def disconnect(self, ws: WebSocket):
        self.active_connections.pop(ws, None)

    async def send_to_user(self, username: str, message: dict):
        for ws, uname in list(self.active_connections.items()):
            if uname == username:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    def viewer_count(self) -> int:
        return max(1, len(set(self.active_connections.values())))

manager = ConnectionManager()

# ─────────────────────────────────────────────
#  YOLO Model Cache
# ─────────────────────────────────────────────
models_cache: Dict[str, YOLO] = {}

def get_model(name: str) -> YOLO:
    if name not in models_cache:
        models_cache[name] = YOLO(name)
    return models_cache[name]

def get_system_stats():
    if psutil:
        try:
            return psutil.cpu_percent(), psutil.virtual_memory().percent
        except Exception:
            pass
    return 15.0, 35.0

def ts():
    return datetime.now().strftime("%H:%M:%S")

# ─────────────────────────────────────────────
#  Frame Generator
# ─────────────────────────────────────────────
def frame_generator(username: str):
    session = get_session(username)
    frame_times = deque(maxlen=10)
    last_sig = None
    cached_jpeg = None

    while True:
        if session.playback_state != "playing":
            time.sleep(0.1)
            continue

        cfg = session.config
        sig = (cfg.model, cfg.conf_threshold, cfg.line_position_ratio,
               tuple(cfg.classes or []), cfg.video_file, cfg.source_type)

        # ── IMAGE MODE ──────────────────────────────────────
        if cfg.source_type == "image":
            if sig != last_sig or cached_jpeg is None:
                img_path = os.path.join(session.upload_dir, "current_image.jpg")
                if not os.path.exists(img_path):
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, "No image uploaded. Use the panel to upload an image.",
                                (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 160), 1)
                else:
                    frame = cv2.imread(img_path)
                    if frame is None:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, "Cannot read image file.", (160, 240),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)
                    else:
                        h, w = frame.shape[:2]
                        if max(h, w) > 960:
                            s = 960 / max(h, w)
                            frame = cv2.resize(frame, (int(w * s), int(h * s)))
                        try:
                            mdl = get_model(cfg.model)
                            res = mdl.predict(frame, conf=cfg.conf_threshold,
                                              classes=cfg.classes or None, verbose=False)
                            session.active_tracks_count = 0
                            session.crossings_by_class.clear()
                            if res and res[0].boxes is not None:
                                boxes = res[0].boxes.xyxy.cpu().numpy().astype(int)
                                clss  = res[0].boxes.cls.cpu().numpy().astype(int)
                                session.active_tracks_count = len(boxes)
                                session.total_crossings = len(boxes)
                                for box, c in zip(boxes, clss):
                                    cname = mdl.names[c]
                                    session.crossings_by_class[cname] += 1
                                    cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (10, 230, 10), 2)
                                    lbl = f"{cname}"
                                    ts_size = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
                                    cv2.rectangle(frame, (box[0], box[1]-ts_size[1]-5),
                                                  (box[0]+ts_size[0], box[1]), (10, 230, 10), -1)
                                    cv2.putText(frame, lbl, (box[0], box[1]-4),
                                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0,0,0), 1)
                        except Exception:
                            pass
                cv2.putText(frame, f"Image Mode | Detected: {session.active_tracks_count}",
                            (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 230, 10), 2)
                cpu, ram = get_system_stats()
                if loop:
                    asyncio.run_coroutine_threadsafe(
                        manager.send_to_user(username, {"type": "stats", "data": {
                            "active_count": session.active_tracks_count,
                            "total_crossings": session.total_crossings,
                            "crossings_by_class": dict(session.crossings_by_class),
                            "fps": 1.0, "cpu_usage": cpu, "ram_usage": ram,
                            "active_viewers": manager.viewer_count()
                        }}), loop)
                ok, enc = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    cached_jpeg = enc.tobytes()
                last_sig = sig

            if cached_jpeg:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + cached_jpeg + b'\r\n'
            time.sleep(0.4)
            continue

        # ── VIDEO / WEBCAM MODE ─────────────────────────────
        current_source = cfg.video_file if cfg.source_type == "video" else 0
        if (session.cap is None
                or current_source != session.last_source
                or cfg.source_type != session.last_source_type):
            if session.cap:
                session.cap.release()
            if cfg.source_type == "video":
                vpath = os.path.join(session.upload_dir, cfg.video_file) if cfg.video_file else ""
                if not os.path.exists(vpath):
                    vpath = cfg.video_file  # fallback: root-level file
                session.cap = cv2.VideoCapture(vpath)
            else:
                session.cap = cv2.VideoCapture(0)

            if not session.cap.isOpened():
                session.cap.release()
                session.cap = None
                err = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(err, "Source could not be opened. Check video/webcam.",
                            (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 80, 255), 2)
                ok, enc = cv2.imencode('.jpg', err, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + enc.tobytes() + b'\r\n'
                time.sleep(1.5)
                continue
            session.last_source = current_source
            session.last_source_type = cfg.source_type

        t0 = time.time()
        ret, frame = session.cap.read()
        if not ret:
            if cfg.source_type == "video":
                session.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.05)
                continue
            else:
                session.cap.release()
                session.cap = None
                time.sleep(0.3)
                continue

        h, w = frame.shape[:2]
        if max(h, w) > 960:
            s = 960 / max(h, w)
            frame = cv2.resize(frame, (int(w * s), int(h * s)))
            h, w = frame.shape[:2]

        line_y = int(h * cfg.line_position_ratio)
        cv2.line(frame, (0, line_y), (w, line_y), (0, 0, 255), 2)
        cv2.putText(frame, "Counting Line", (12, line_y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        try:
            mdl = get_model(cfg.model)
            res = mdl.track(frame, persist=True, conf=cfg.conf_threshold,
                            classes=cfg.classes or None, tracker="botsort.yaml", verbose=False)
            session.active_tracks_count = 0
            if res and res[0].boxes is not None and res[0].boxes.id is not None:
                boxes = res[0].boxes.xyxy.cpu().numpy().astype(int)
                ids   = res[0].boxes.id.cpu().numpy().astype(int)
                clss  = res[0].boxes.cls.cpu().numpy().astype(int)
                session.active_tracks_count = len(ids)
                for box, tid, c in zip(boxes, ids, clss):
                    cname = mdl.names[c]
                    cx = (box[0] + box[2]) // 2
                    cy = (box[1] + box[3]) // 2
                    session.track_history[tid].append((cx, cy))
                    if len(session.track_history[tid]) >= 2:
                        prev_cy = session.track_history[tid][-2][1]
                        if tid not in session.crossed_ids:
                            direction = None
                            if prev_cy < line_y <= cy:
                                direction = "down"
                            elif prev_cy > line_y >= cy:
                                direction = "up"
                            if direction:
                                session.crossed_ids.add(tid)
                                session.total_crossings += 1
                                session.crossings_by_class[cname] += 1
                                if loop:
                                    asyncio.run_coroutine_threadsafe(
                                        manager.send_to_user(username, {"type": "event", "data": {
                                            "time": ts(), "id": int(tid),
                                            "class": cname, "direction": direction
                                        }}), loop)
                    for i in range(1, len(session.track_history[tid])):
                        cv2.line(frame, session.track_history[tid][i-1],
                                 session.track_history[tid][i], (0, 255, 255), 2)
                    cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (10, 230, 10), 2)
                    lbl = f"ID:{tid} {cname}"
                    ts_size = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0]
                    cv2.rectangle(frame, (box[0], box[1]-ts_size[1]-5),
                                  (box[0]+ts_size[0], box[1]), (10, 230, 10), -1)
                    cv2.putText(frame, lbl, (box[0], box[1]-4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,0,0), 1)
        except Exception:
            pass

        elapsed = time.time() - t0
        frame_times.append(elapsed)
        avg = sum(frame_times) / len(frame_times)
        session.fps_val = 1.0 / avg if avg > 0 else 0.0

        cv2.putText(frame, f"FPS:{session.fps_val:.1f} Active:{session.active_tracks_count} Cross:{session.total_crossings}",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 230, 10), 2)

        cpu, ram = get_system_stats()
        if loop:
            asyncio.run_coroutine_threadsafe(
                manager.send_to_user(username, {"type": "stats", "data": {
                    "active_count": session.active_tracks_count,
                    "total_crossings": session.total_crossings,
                    "crossings_by_class": dict(session.crossings_by_class),
                    "fps": session.fps_val, "cpu_usage": cpu, "ram_usage": ram,
                    "active_viewers": manager.viewer_count()
                }}), loop)

        ok, enc = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + enc.tobytes() + b'\r\n'

        time.sleep(max(0.001, 0.033 - elapsed))

    if session.cap:
        session.cap.release()

# ─────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────
@app.get("/api/datasets")
async def list_datasets(token: str = None):
    username = require_user(token)
    session = get_session(username)
    exts = (".mp4", ".avi", ".mkv", ".mov")
    user_dir = session.upload_dir
    files = [f for f in os.listdir(user_dir) if f.lower().endswith(exts)]
    # Also include root-level video.mp4 as a demo dataset
    if os.path.exists("video.mp4"):
        files = list(set(["video.mp4"] + files))
    return {"status": "success", "datasets": sorted(files)}

@app.post("/api/config")
async def update_config(config: TrackerConfig, token: str = None):
    username = require_user(token)
    session = get_session(username)
    session.config = config
    return {"status": "success"}

class ControlRequest(BaseModel):
    action: str

@app.post("/api/control")
async def control_playback(req: ControlRequest, token: str = None):
    username = require_user(token)
    session = get_session(username)
    if req.action == "play":
        session.playback_state = "playing"
    elif req.action == "pause":
        session.playback_state = "paused"
    elif req.action == "reset":
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
        session.active_tracks_count = 0
    return {"status": "success", "state": session.playback_state}

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...), token: str = None):
    username = require_user(token)
    session = get_session(username)
    try:
        safe_name = os.path.basename(file.filename).replace(" ", "_")
        dest = os.path.join(session.upload_dir, safe_name)
        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)
        session.config.video_file = safe_name
        session.config.source_type = "video"
        # Reset counters
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
        session.last_source = None  # Force re-open
        return {"status": "success", "filename": safe_name}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/upload_image")
async def upload_image(file: UploadFile = File(...), token: str = None):
    username = require_user(token)
    session = get_session(username)
    try:
        dest = os.path.join(session.upload_dir, "current_image.jpg")
        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)
        session.config.source_type = "image"
        session.active_tracks_count = 0
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
        return {"status": "success", "filename": "current_image.jpg"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/stream")
def video_stream(token: str = None):
    username = get_current_user(token)
    if not username:
        from fastapi.responses import Response
        return Response("Unauthorized", status_code=401)
    return StreamingResponse(frame_generator(username),
                             media_type="multipart/x-mixed-replace; boundary=frame")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = None):
    username = get_current_user(token)
    if not username:
        await ws.close(code=1008)
        return
    await manager.connect(ws, username)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
