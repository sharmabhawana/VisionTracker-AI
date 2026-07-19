import os
import cv2
import time
import asyncio
from datetime import datetime
from collections import deque, defaultdict
from typing import List, Dict, Set
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


app = FastAPI(title="VisionTracker AI Backend")

# State variables
class TrackerConfig(BaseModel):
    model: str = "yolov8n.pt"
    conf_threshold: float = 0.25
    line_position_ratio: float = 0.5
    classes: List[int] = [0, 1, 2, 3, 5, 7]
    source_type: str = "video"  # "video" or "webcam"
    video_file: str = "video.mp4"

current_config = TrackerConfig()
playback_state = "idle"  # "idle", "playing", "paused"
active_tracks_count = 0
total_crossings = 0
crossings_by_class = defaultdict(int)
crossed_ids: Set[int] = set()
track_history = defaultdict(lambda: deque(maxlen=30))
fps_val = 0.0

loop = None

@app.on_event("startup")
async def startup_event():
    global loop
    loop = asyncio.get_running_loop()

# WebSocket Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Handle broken connections silently
                pass

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

# Reset counters
def reset_counters():
    global total_crossings, crossings_by_class, crossed_ids, track_history
    total_crossings = 0
    crossings_by_class.clear()
    crossed_ids.clear()
    track_history.clear()

# Frame Generator for multipart JPEG stream
def frame_generator():
    global playback_state, active_tracks_count, total_crossings, fps_val, crossed_ids, crossings_by_class
    
    cap = None
    last_source = None
    last_source_type = None
    frame_time_history = deque(maxlen=10)
    
    while True:
        # Check source update
        current_source = current_config.video_file if current_config.source_type == "video" else 0
        current_source_type = current_config.source_type
        
        if cap is None or current_source != last_source or current_source_type != last_source_type:
            if cap is not None:
                cap.release()
            
            if current_source_type == "video":
                if not os.path.exists(str(current_source)):
                    # Fallback to local default file
                    current_source = "video.mp4"
                cap = cv2.VideoCapture(current_source)
            else:
                cap = cv2.VideoCapture(0)
                
            last_source = current_source
            last_source_type = current_source_type
            
        if playback_state != "playing":
            time.sleep(0.1)
            continue
            
        start_time = time.time()
        ret, frame = cap.read()
        
        if not ret:
            if current_source_type == "video":
                # Loop video
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.05)
                continue
            else:
                time.sleep(0.2)
                continue
                
        # Resize frame if too large for performance
        height, width = frame.shape[:2]
        max_dim = 960
        if max(height, width) > max_dim:
            scale = max_dim / max(height, width)
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
            height, width = frame.shape[:2]
            
        # Draw Counting Line
        line_y = int(height * current_config.line_position_ratio)
        cv2.line(frame, (0, line_y), (width, line_y), (0, 0, 255), 2)
        cv2.putText(frame, "Crossing Line", (15, line_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        
        try:
            model = get_yolo_model(current_config.model)
            classes_filter = current_config.classes if current_config.classes else None
            
            results = model.track(
                frame, 
                persist=True, 
                conf=current_config.conf_threshold,
                classes=classes_filter, 
                verbose=False
            )
            
            active_tracks_count = 0
            if results and results[0].boxes is not None and results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
                ids = results[0].boxes.id.cpu().numpy().astype(int)
                clss = results[0].boxes.cls.cpu().numpy().astype(int)
                
                active_tracks_count = len(ids)
                
                for box, track_id, cls in zip(boxes, ids, clss):
                    class_name = model.names[cls]
                    
                    cx = int((box[0] + box[2]) / 2)
                    cy = int((box[1] + box[3]) / 2)
                    
                    track_history[track_id].append((cx, cy))
                    
                    # Line Crossing Check
                    if len(track_history[track_id]) >= 2:
                        prev_cy = track_history[track_id][-2][1]
                        
                        if track_id not in crossed_ids:
                            # Moving down (in)
                            if prev_cy < line_y <= cy:
                                crossed_ids.add(track_id)
                                total_crossings += 1
                                crossings_by_class[class_name] += 1
                                if loop is not None:
                                    asyncio.run_coroutine_threadsafe(
                                        manager.broadcast({
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
                            # Moving up (out)
                            elif prev_cy > line_y >= cy:
                                crossed_ids.add(track_id)
                                total_crossings += 1
                                crossings_by_class[class_name] += 1
                                if loop is not None:
                                    asyncio.run_coroutine_threadsafe(
                                        manager.broadcast({
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
                                    
                    # Draw visual trails
                    for i in range(1, len(track_history[track_id])):
                        cv2.line(frame, track_history[track_id][i-1], track_history[track_id][i], (0, 255, 255), 2)
                        
                    # Draw bounding box and label
                    cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (10, 230, 10), 2)
                    label = f"ID:{track_id} {class_name}"
                    t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0]
                    cv2.rectangle(frame, (box[0], box[1] - t_size[1] - 5), (box[0] + t_size[0], box[1]), (10, 230, 10), -1)
                    cv2.putText(frame, label, (box[0], box[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
        except Exception as e:
            # Handle tracking glitches gracefully
            pass
            
        # Draw stats directly onto frame
        overlay_text = f"FPS: {fps_val:.1f} | Active: {active_tracks_count} | Crossed: {total_crossings}"
        cv2.putText(frame, overlay_text, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 230, 10), 2, cv2.LINE_AA)
        
        # Calculate processing FPS
        end_time = time.time()
        elapsed = end_time - start_time
        frame_time_history.append(elapsed)
        avg_frame_time = sum(frame_time_history) / len(frame_time_history)
        fps_val = 1.0 / avg_frame_time if avg_frame_time > 0 else 0.0
        
        # Broadcast live stats
        if loop is not None:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({
                    "type": "stats",
                    "data": {
                        "active_count": active_tracks_count,
                        "total_crossings": total_crossings,
                        "crossings_by_class": dict(crossings_by_class),
                        "fps": fps_val
                    }
                }),
                loop
            )
            
        ret_enc, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ret_enc:
            continue
            
        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
        
        # Throttling
        delay = max(0.001, 0.033 - elapsed)
        time.sleep(delay)
        
    if cap is not None:
        cap.release()

# FastAPI HTTP endpoints
@app.post("/api/config")
async def update_config(config: TrackerConfig):
    global current_config
    current_config = config
    return {"status": "success", "config": current_config}

class ControlRequest(BaseModel):
    action: str

@app.post("/api/control")
async def control_playback(req: ControlRequest):
    global playback_state
    if req.action == "play":
        playback_state = "playing"
    elif req.action == "pause":
        playback_state = "paused"
    elif req.action == "reset":
        reset_counters()
    return {"status": "success", "playback_state": playback_state}

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    global current_config
    try:
        file_location = f"uploaded_{file.filename}"
        with open(file_location, "wb+") as file_object:
            file_object.write(file.file.read())
            
        current_config.video_file = file_location
        current_config.source_type = "video"
        reset_counters()
        return {"status": "success", "filename": file_location}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/stream")
def video_stream_endpoint():
    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Mount the static site at "/"
app.mount("/", StaticFiles(directory="static", html=True), name="static")
