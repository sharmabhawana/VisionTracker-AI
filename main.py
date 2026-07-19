import os
import cv2
import time
import asyncio
from datetime import datetime
from collections import deque, defaultdict
from typing import List, Dict, Set
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from ultralytics import YOLO
except ImportError:
    import subprocess
    import sys
    print("Ultralytics not found. Installing dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ultralytics", "lap"])
    from ultralytics import YOLO

# CPU and RAM Monitoring Fallback
try:
    import psutil
except ImportError:
    psutil = None

app = FastAPI(title="VisionTracker AI Backend")

# State variables
class TrackerConfig(BaseModel):
    model: str = "yolov8n.pt"
    conf_threshold: float = 0.25
    line_position_ratio: float = 0.5
    classes: List[int] = [0, 1, 2, 3, 5, 7]
    source_type: str = "video"  # "video", "webcam", "image"
    video_file: str = "video.mp4"

# User Session State class
class UserSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.config = TrackerConfig()
        self.playback_state = "idle"  # "idle", "playing", "paused"
        self.active_tracks_count = 0
        self.total_crossings = 0
        self.crossings_by_class = defaultdict(int)
        self.crossed_ids: Set[int] = set()
        self.track_history = defaultdict(lambda: deque(maxlen=30))
        self.fps_val = 0.0
        self.cap = None
        self.last_source = None
        self.last_source_type = None

# Active Sessions Registry
active_sessions: Dict[str, UserSession] = {}

def get_session(session_id: str) -> UserSession:
    if not session_id:
        session_id = "default_session"
    if session_id not in active_sessions:
        active_sessions[session_id] = UserSession(session_id)
    return active_sessions[session_id]

loop = None

@app.on_event("startup")
async def startup_event():
    global loop
    loop = asyncio.get_running_loop()

# WebSocket Connection Manager with Session isolation
class ConnectionManager:
    def __init__(self):
        # Maps WebSocket connection to its session_id string
        self.active_connections: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[websocket] = session_id

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]

    async def broadcast_to_session(self, session_id: str, message: dict):
        for connection, sess_id in list(self.active_connections.items()):
            if sess_id == session_id:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

    def get_active_viewers_count(self) -> int:
        unique_sessions = set(self.active_connections.values())
        return max(1, len(unique_sessions))

manager = ConnectionManager()

# Model Cache
models_cache = {}

def get_yolo_model(model_name: str) -> YOLO:
    if model_name not in models_cache:
        models_cache[model_name] = YOLO(model_name)
    return models_cache[model_name]

# Helper to format timestamps
def get_current_time_str():
    return datetime.now().strftime("%H:%M:%S")

# Retrieve CPU / RAM load
def get_system_stats():
    if psutil is not None:
        try:
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            return cpu, ram
        except Exception:
            pass
    return 15.0, 35.0

# Frame Generator for isolated multipart JPEG stream
def frame_generator(session_id: str):
    session = get_session(session_id)
    frame_time_history = deque(maxlen=10)
    
    last_sig = None
    cached_jpeg = None
    
    while True:
        # Check if playback is active
        if session.playback_state != "playing":
            time.sleep(0.1)
            continue
            
        # Get settings signature to detect config updates
        current_sig = (
            session.config.model,
            session.config.conf_threshold,
            session.config.line_position_ratio,
            tuple(session.config.classes) if session.config.classes else (),
            session.config.video_file,
            session.config.source_type
        )
        
        # 1. STATIC IMAGE ANALYSIS MODE
        if session.config.source_type == "image":
            if current_sig != last_sig or cached_jpeg is None:
                image_path = f"uploaded_{session_id}.jpg"
                if not os.path.exists(image_path):
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, "No image uploaded yet. Choose a file below.", (70, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1, cv2.LINE_AA)
                else:
                    frame = cv2.imread(image_path)
                    if frame is None:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, "Failed to read image file.", (150, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1, cv2.LINE_AA)
                
                if frame is not None and os.path.exists(image_path):
                    # Resize if too large
                    height, width = frame.shape[:2]
                    max_dim = 960
                    if max(height, width) > max_dim:
                        scale = max_dim / max(height, width)
                        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
                        height, width = frame.shape[:2]
                        
                    # Process image
                    try:
                        model = get_yolo_model(session.config.model)
                        classes_filter = session.config.classes if session.config.classes else None
                        
                        results = model.predict(
                            frame, 
                            conf=session.config.conf_threshold,
                            classes=classes_filter, 
                            verbose=False
                        )
                        
                        session.active_tracks_count = 0
                        if results and results[0].boxes is not None:
                            boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
                            clss = results[0].boxes.cls.cpu().numpy().astype(int)
                            session.active_tracks_count = len(boxes)
                            
                            session.total_crossings = len(boxes)
                            session.crossings_by_class.clear()
                            
                            for box, cls in zip(boxes, clss):
                                class_name = model.names[cls]
                                session.crossings_by_class[class_name] += 1
                                
                                # Draw box & simulated ID Label
                                cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (10, 230, 10), 2)
                                label = f"{class_name}"
                                t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0]
                                cv2.rectangle(frame, (box[0], box[1] - t_size[1] - 5), (box[0] + t_size[0], box[1]), (10, 230, 10), -1)
                                cv2.putText(frame, label, (box[0], box[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
                    except Exception as e:
                        pass
                
                # Draw status overlay
                overlay_text = f"Image Mode | Objects Detected: {session.active_tracks_count}"
                cv2.putText(frame, overlay_text, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 230, 10), 2, cv2.LINE_AA)
                
                # Telemetry broadcast
                cpu_usage, ram_usage = get_system_stats()
                active_viewers = manager.get_active_viewers_count()
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast_to_session(session_id, {
                            "type": "stats",
                            "data": {
                                "active_count": session.active_tracks_count,
                                "total_crossings": session.total_crossings,
                                "crossings_by_class": dict(session.crossings_by_class),
                                "fps": 1.0,
                                "cpu_usage": cpu_usage,
                                "ram_usage": ram_usage,
                                "active_viewers": active_viewers
                            }
                        }),
                        loop
                    )
                
                # Encode and Cache
                ret_enc, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret_enc:
                    cached_jpeg = jpeg.tobytes()
                
                last_sig = current_sig
            
            # Yield cached frame
            if cached_jpeg is not None:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + cached_jpeg + b'\r\n'
            
            time.sleep(0.5)
            continue
            
        # 2. VIDEO OR WEBCAM MODE
        else:
            current_source = session.config.video_file if session.config.source_type == "video" else 0
            current_source_type = session.config.source_type
            
            if session.cap is None or current_source != session.last_source or current_source_type != session.last_source_type:
                if session.cap is not None:
                    session.cap.release()
                
                if current_source_type == "video":
                    if not os.path.exists(str(current_source)):
                        current_source = "video.mp4"
                    session.cap = cv2.VideoCapture(current_source)
                else:
                    session.cap = cv2.VideoCapture(0)
                
                # Secure check to verify camera opened successfully
                if not session.cap.isOpened():
                    session.cap.release()
                    session.cap = None
                    
                    cpu_usage, ram_usage = get_system_stats()
                    active_viewers = manager.get_active_viewers_count()
                    if loop is not None:
                        asyncio.run_coroutine_threadsafe(
                            manager.broadcast_to_session(session_id, {
                                "type": "stats",
                                "data": {
                                    "active_count": 0,
                                    "total_crossings": session.total_crossings,
                                    "crossings_by_class": dict(session.crossings_by_class),
                                    "fps": 0.0,
                                    "cpu_usage": cpu_usage,
                                    "ram_usage": ram_usage,
                                    "active_viewers": active_viewers
                                }
                            }),
                            loop
                        )
                    
                    err_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(err_frame, "Webcam / Video source could not be opened.", (80, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1, cv2.LINE_AA)
                    ret_enc, jpeg = cv2.imencode('.jpg', err_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ret_enc:
                        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
                    time.sleep(1.0)
                    continue
                    
                session.last_source = current_source
                session.last_source_type = current_source_type
                
            start_time = time.time()
            ret, frame = session.cap.read()
            
            if not ret:
                if current_source_type == "video":
                    session.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    time.sleep(0.05)
                    continue
                else:
                    session.cap.release()
                    session.cap = None
                    time.sleep(0.2)
                    continue
                    
            height, width = frame.shape[:2]
            max_dim = 960
            if max(height, width) > max_dim:
                scale = max_dim / max(height, width)
                frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
                height, width = frame.shape[:2]
                
            line_y = int(height * session.config.line_position_ratio)
            cv2.line(frame, (0, line_y), (width, line_y), (0, 0, 255), 2)
            cv2.putText(frame, "Crossing Line", (15, line_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
            
            try:
                model = get_yolo_model(session.config.model)
                classes_filter = session.config.classes if session.config.classes else None
                
                results = model.track(
                    frame, 
                    persist=True, 
                    conf=session.config.conf_threshold,
                    classes=classes_filter, 
                    tracker="botsort.yaml",
                    verbose=False
                )
                
                session.active_tracks_count = 0
                if results and results[0].boxes is not None and results[0].boxes.id is not None:
                    boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
                    ids = results[0].boxes.id.cpu().numpy().astype(int)
                    clss = results[0].boxes.cls.cpu().numpy().astype(int)
                    
                    session.active_tracks_count = len(ids)
                    
                    for box, track_id, cls in zip(boxes, ids, clss):
                        class_name = model.names[cls]
                        
                        cx = int((box[0] + box[2]) / 2)
                        cy = int((box[1] + box[3]) / 2)
                        
                        session.track_history[track_id].append((cx, cy))
                        
                        if len(session.track_history[track_id]) >= 2:
                            prev_cy = session.track_history[track_id][-2][1]
                            
                            if track_id not in session.crossed_ids:
                                if prev_cy < line_y <= cy:
                                    session.crossed_ids.add(track_id)
                                    session.total_crossings += 1
                                    session.crossings_by_class[class_name] += 1
                                    if loop is not None:
                                        asyncio.run_coroutine_threadsafe(
                                            manager.broadcast_to_session(session_id, {
                                                "type": "event",
                                                "data": {
                                                    "time": get_current_time_str(),
                                                    "id": int(track_id),
                                                    "class": class_name,
                                                    "direction": "down"
                                                }
                                            }),
                                            loop
                                        )
                                elif prev_cy > line_y >= cy:
                                    session.crossed_ids.add(track_id)
                                    session.total_crossings += 1
                                    session.crossings_by_class[class_name] += 1
                                    if loop is not None:
                                        asyncio.run_coroutine_threadsafe(
                                            manager.broadcast_to_session(session_id, {
                                                "type": "event",
                                                "data": {
                                                    "time": get_current_time_str(),
                                                    "id": int(track_id),
                                                    "class": class_name,
                                                    "direction": "up"
                                                }
                                            }),
                                            loop
                                        )
                                        
                        for i in range(1, len(session.track_history[track_id])):
                            cv2.line(frame, session.track_history[track_id][i-1], session.track_history[track_id][i], (0, 255, 255), 2)
                            
                        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (10, 230, 10), 2)
                        label = f"ID:{track_id} {class_name}"
                        t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0]
                        cv2.rectangle(frame, (box[0], box[1] - t_size[1] - 5), (box[0] + t_size[0], box[1]), (10, 230, 10), -1)
                        cv2.putText(frame, label, (box[0], box[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
            except Exception as e:
                pass
                
            overlay_text = f"FPS: {session.fps_val:.1f} | Active: {session.active_tracks_count} | Crossed: {session.total_crossings}"
            cv2.putText(frame, overlay_text, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 230, 10), 2, cv2.LINE_AA)
            
            end_time = time.time()
            elapsed = end_time - start_time
            frame_time_history.append(elapsed)
            avg_frame_time = sum(frame_time_history) / len(frame_time_history)
            session.fps_val = 1.0 / avg_frame_time if avg_frame_time > 0 else 0.0
            
            cpu_usage, ram_usage = get_system_stats()
            active_viewers = manager.get_active_viewers_count()
            
            if loop is not None:
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast_to_session(session_id, {
                        "type": "stats",
                        "data": {
                            "active_count": session.active_tracks_count,
                            "total_crossings": session.total_crossings,
                            "crossings_by_class": dict(session.crossings_by_class),
                            "fps": session.fps_val,
                            "cpu_usage": cpu_usage,
                            "ram_usage": ram_usage,
                            "active_viewers": active_viewers
                        }
                    }),
                    loop
                )
                
            ret_enc, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ret_enc:
                continue
                
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
            
            delay = max(0.001, 0.033 - elapsed)
            time.sleep(delay)
            
    if session.cap is not None:
        session.cap.release()

# Discover available datasets (video files) in root folder
@app.get("/api/datasets")
async def list_datasets(session_id: str = None):
    video_extensions = (".mp4", ".avi", ".mkv", ".mov")
    datasets = [f for f in os.listdir(".") if f.lower().endswith(video_extensions) and os.path.isfile(f)]
    if "video.mp4" not in datasets and os.path.exists("video.mp4"):
        datasets.append("video.mp4")
    return {"status": "success", "datasets": datasets}

# FastAPI HTTP endpoints
@app.post("/api/config")
async def update_config(config: TrackerConfig, session_id: str = None):
    session = get_session(session_id)
    session.config = config
    return {"status": "success", "config": session.config}

class ControlRequest(BaseModel):
    action: str

@app.post("/api/control")
async def control_playback(req: ControlRequest, session_id: str = None):
    session = get_session(session_id)
    if req.action == "play":
        session.playback_state = "playing"
    elif req.action == "pause":
        session.playback_state = "paused"
    elif req.action == "reset":
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
    return {"status": "success", "playback_state": session.playback_state}

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...), session_id: str = None):
    session = get_session(session_id)
    try:
        file_location = f"uploaded_{file.filename}"
        with open(file_location, "wb+") as file_object:
            file_object.write(file.file.read())
            
        session.config.video_file = file_location
        session.config.source_type = "video"
        
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
        
        return {"status": "success", "filename": file_location}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/upload_image")
async def upload_image(file: UploadFile = File(...), session_id: str = None):
    session = get_session(session_id)
    try:
        file_location = f"uploaded_{session_id}.jpg"
        with open(file_location, "wb+") as file_object:
            file_object.write(file.file.read())
            
        session.config.source_type = "image"
        
        session.active_tracks_count = 0
        session.total_crossings = 0
        session.crossings_by_class.clear()
        session.crossed_ids.clear()
        session.track_history.clear()
        
        return {"status": "success", "filename": file_location}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/stream")
def video_stream_endpoint(session_id: str = None):
    return StreamingResponse(frame_generator(session_id), media_type="multipart/x-mixed-replace; boundary=frame")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str = None):
    if not session_id:
        session_id = "default_session"
    await manager.connect(websocket, session_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Mount static files site at root "/"
app.mount("/", StaticFiles(directory="static", html=True), name="static")
