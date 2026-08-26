"""
GPU IoT PC + 카메라 실시간 영상분석

웹캠 / USB카메라 / RTSP(IP카메라) / 영상파일을 입력받아 YOLOv8로 실시간 객체 탐지를
수행한다. 카메라 캡처와 모델 추론을 별도 스레드로 분리해, 추론이 프레임 처리 속도를
따라가지 못해도(엣지 GPU의 흔한 상황) 항상 "가장 최근" 프레임만 분석하도록 해
화면 지연이 누적되지 않게 했다.

사용 예:
    python realtime_video_analysis.py --source 0
    python realtime_video_analysis.py --source rtsp://192.168.0.10:554/stream1
"""

import argparse
import csv
import os
import threading
import time
from datetime import datetime

import cv2
import torch
from ultralytics import YOLO


class FrameGrabber:
    """카메라/스트림에서 프레임을 계속 읽어 항상 최신 프레임만 보관하는 스레드.

    RTSP 등 네트워크 카메라는 읽기 속도가 추론 속도보다 빠를 수 있는데,
    큐에 쌓아두면 화면이 점점 과거로 밀리는 지연(latency)이 발생한다.
    최신 프레임만 덮어쓰는 방식으로 이를 방지한다.
    """

    def __init__(self, source):
        self.capture = cv2.VideoCapture(source)
        if not self.capture.isOpened():
            raise RuntimeError(f"영상 소스를 열 수 없습니다: {source}")

        self.lock = threading.Lock()
        self.frame = None
        self.grabbed = False
        self.stopped = False

        ok, frame = self.capture.read()
        if ok:
            self.frame = frame
            self.grabbed = True

        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while not self.stopped:
            ok, frame = self.capture.read()
            if not ok:
                self.stopped = True
                break
            with self.lock:
                self.frame = frame
                self.grabbed = True

    def read(self):
        with self.lock:
            if not self.grabbed:
                return False, None
            return True, self.frame.copy()

    def release(self):
        self.stopped = True
        self.thread.join(timeout=1.0)
        self.capture.release()


def create_detection_logger(log_path):
    is_new_file = not os.path.exists(log_path)
    log_file = open(log_path, mode="a", newline="", encoding="utf-8")
    writer = csv.writer(log_file)
    if is_new_file:
        writer.writerow(["timestamp", "class_name", "confidence", "x1", "y1", "x2", "y2"])

    def log(detections):
        timestamp = datetime.now().isoformat(timespec="seconds")
        for class_name, confidence, box in detections:
            x1, y1, x2, y2 = box
            writer.writerow([timestamp, class_name, f"{confidence:.3f}", x1, y1, x2, y2])
        log_file.flush()

    return log, log_file


def parse_args():
    parser = argparse.ArgumentParser(description="GPU IoT PC 실시간 영상분석")
    parser.add_argument("--source", default="0", help="카메라 인덱스(0), 영상 파일 경로, 또는 RTSP URL")
    parser.add_argument("--model", default="yolov8n.pt", help="사용할 YOLOv8 가중치 파일")
    parser.add_argument("--conf", type=float, default=0.5, help="탐지 신뢰도 임계값")
    parser.add_argument("--classes", type=int, nargs="*", default=None, help="탐지할 클래스 ID 목록 (예: 0 2)")
    parser.add_argument("--log-path", default="detections.csv", help="탐지 이벤트 CSV 로그 경로")
    parser.add_argument("--device", default=None, help="추론 디바이스 지정 (예: 0, cpu). 미지정 시 자동 선택")
    parser.add_argument("--no-display", action="store_true", help="화면 출력 없이 실행(헤드리스 엣지 장비용)")
    return parser.parse_args()


def resolve_source(source):
    # 숫자 문자열이면 로컬 카메라 인덱스로 취급
    if source.isdigit():
        return int(source)
    return source


def main():
    args = parse_args()

    device = args.device or (0 if torch.cuda.is_available() else "cpu")
    print(f"[INFO] 추론 디바이스: {device} (CUDA 사용 가능: {torch.cuda.is_available()})")

    model = YOLO(args.model)

    grabber = FrameGrabber(resolve_source(args.source))
    log_detection, log_file = create_detection_logger(args.log_path)

    frame_count = 0
    fps = 0.0
    fps_timer = time.time()

    try:
        while True:
            ok, frame = grabber.read()
            if not ok:
                print("[INFO] 더 이상 프레임이 없습니다. 종료합니다.")
                break

            results = model.predict(
                frame,
                conf=args.conf,
                classes=args.classes,
                device=device,
                verbose=False,
            )[0]

            detections = []
            for box in results.boxes:
                class_id = int(box.cls[0])
                class_name = model.names[class_id]
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detections.append((class_name, confidence, (x1, y1, x2, y2)))

            if detections:
                log_detection(detections)

            annotated = results.plot()

            frame_count += 1
            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                fps_timer = time.time()

            cv2.putText(
                annotated,
                f"FPS: {fps:.1f}  Objects: {len(detections)}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
            )

            if not args.no_display:
                cv2.imshow("Realtime Video Analysis", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        print("[INFO] 사용자 중단(Ctrl+C)으로 종료합니다.")
    finally:
        grabber.release()
        log_file.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
