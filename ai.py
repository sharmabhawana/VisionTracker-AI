# Local Object Tracking Script
# To run this script: 
# 1. Install dependencies: pip install ultralytics opencv-python
# 2. Run: python ai.py (Press 'q' to quit the display window)

import cv2
from ultralytics import YOLO

# Load model
model = YOLO('yolov8n.pt')

video_path = "video.mp4"
cap = cv2.VideoCapture(video_path)

frame_count = 0
while cap.isOpened():
    ret, frame = cap.read()
    if not ret or frame_count > 200: # Increased frame limit for better local viewing
        break

    if frame_count % 5 == 0:
        # Run tracking
        results = model.track(frame, persist=True, classes=[0,1,2,3,5,7], verbose=False)

        if results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
            ids = results[0].boxes.id.cpu().numpy().astype(int)
            clss = results[0].boxes.cls.cpu().numpy().astype(int)

            for box, id, cls in zip(boxes, ids, clss):
                # Draw bounding box
                cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)

                # CLEAN TEXT PLACEMENT: Shift text slightly above the box
                label = f"ID:{id} {model.names[cls]}"
                t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]

                # Create a filled background for the text so it doesn't mix with image colors
                cv2.rectangle(frame, (box[0], box[1] - t_size[1] - 5), (box[0] + t_size[0], box[1]), (0, 255, 0), -1)
                cv2.putText(frame, label, (box[0], box[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Use standard OpenCV window rendering instead of Colab patches
        cv2.imshow("VisionTracker - Local Window (Press 'q' to Quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    frame_count += 1

cap.release()
cv2.destroyAllWindows()