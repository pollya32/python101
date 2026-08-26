# GPU IoT PC + 카메라 실시간 영상분석 시스템

GPU 내장 IoT PC(엣지 디바이스)와 카메라를 이용해 현장에서 실시간으로 객체를 탐지·분석하는
시스템의 구성품 추천과, 실제 동작하는 파이썬 분석 코드를 정리한 예제입니다.

## 1. 시스템 구성품 추천

### 1-1. GPU IoT PC (엣지 컴퓨팅 보드)

| 구분 | 제품 예시 | 특징 |
|---|---|---|
| 소형/저전력 | NVIDIA Jetson Orin Nano Super (8GB) | 최대 67 TOPS, 소비전력 7~25W. 카메라 1~4대 규모의 실시간 추론에 적합. 가성비 최고 |
| 중대형 | NVIDIA Jetson Orin NX / AGX Orin | 최대 275 TOPS. 다중 카메라(8대 이상) 동시 추론, 복합 모델(탐지+추적+분류) 처리 |
| 산업용 x86 | 산업용 미니 PC + NVIDIA RTX A2000/A4000 (임베디드 GPU) | 윈도우/리눅스 모두 지원, USB/PCIe 확장성 우수, 팹/공장 등 기존 x86 인프라와 통합 용이 |

- 선택 기준: 처리할 **카메라 대수**, **모델 크기(YOLOv8n~x)**, **동시 추론 해상도/FPS**, **소비전력·발열 제약**을 먼저 정하고 TOPS(초당 연산량)를 역산해 선택합니다.

### 1-2. 카메라

| 용도 | 추천 | 비고 |
|---|---|---|
| Jetson 전용 저지연 | CSI 카메라 (Raspberry Pi HQ 카메라 호환, IMX219/IMX477) | MIPI-CSI 직결, 지연 최소, 근거리 설치 |
| 고속/정밀 산업 검사 | USB3.0 글로벌 셔터 산업용 카메라 (FLIR Blackfly S, e-con Systems 등) | 빠른 이동체(컨베이어 벨트 등) 왜곡 없이 촬영 |
| 다지점/원거리 설치 | PoE IP 카메라 (RTSP/ONVIF 지원) | 케이블 1선으로 전원+영상 전송, 확장·유지보수 용이 |
| 야간/저조도 | IR(적외선) 겸용 카메라 + IR 조명 | 24시간 무인 감시·분석 환경 |

### 1-3. 주변장치 / 인프라

- **저장장치**: NVMe SSD (OS·모델) + 대용량 HDD 또는 NAS (녹화 영상 보관)
- **네트워크**: PoE 스위치(IP카메라 급전용), 유선 기가비트 또는 5G/LTE 모듈(원격지 전송)
- **전원/보호**: UPS(순단 대비), 서지 보호기, 산업 현장은 IP65 방열 인클로저
- **디스플레이/알림**: 로컬 모니터(선택), 이상 감지 시 알림용 부저·경광등 또는 메신저/이메일 연동
- **모델 가속**: TensorRT(NVIDIA GPU 최적화 런타임) — 동일 모델 대비 2~5배 추론 속도 향상

### 1-4. 소프트웨어 스택

- OS: Jetson Linux(L4T) 또는 Ubuntu 22.04 + NVIDIA 드라이버/CUDA
- 추론 프레임워크: PyTorch + Ultralytics YOLOv8(탐지) → TensorRT로 변환해 배포 시 가속
- 영상 입출력: OpenCV(cuda 빌드 권장), GStreamer(RTSP/CSI 파이프라인)
- 배포/운영: Docker 컨테이너화, 다중 스트림 대규모 처리 시 NVIDIA DeepStream 고려
- 모니터링: 탐지 이벤트를 CSV/DB로 적재 후 Grafana 등으로 시각화

## 2. 실시간 영상분석 코드

`realtime_video_analysis.py` 는 웹캠/USB카메라/RTSP(IP카메라)/영상파일을 입력으로 받아
YOLOv8로 실시간 객체 탐지를 수행하고, FPS를 화면에 표시하며, 탐지 이벤트를 CSV로 로깅합니다.
캡처와 추론을 분리된 스레드로 처리해 카메라 프레임 드랍을 최소화했습니다.

### 설치

```bash
pip install -r requirements.txt
```

GPU(NVIDIA) 가속을 쓰려면 CUDA 빌드의 PyTorch가 필요합니다. 설치 방법은 아래를 참고하세요.
https://pytorch.org/get-started/locally/

### 실행 예시

```bash
# 기본 웹캠(0번)으로 실행
python realtime_video_analysis.py --source 0

# IP 카메라(RTSP) 스트림
python realtime_video_analysis.py --source rtsp://user:pass@192.168.0.10:554/stream1

# 영상 파일 분석 + 탐지 로그 저장 경로 지정
python realtime_video_analysis.py --source sample.mp4 --log-path detections.csv

# 특정 클래스만 탐지 (예: 사람=0, 자동차=2), 신뢰도 임계값 조정
python realtime_video_analysis.py --source 0 --classes 0 2 --conf 0.4
```

`q` 키를 누르면 종료됩니다.
