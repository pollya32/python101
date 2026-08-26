# GPU IoT PC + 카메라 실시간 영상분석 시스템

GPU 내장 IoT PC(엣지 디바이스)와 카메라를 이용해 현장에서 실시간으로 객체를 탐지·분석하는
시스템의 구성품 추천과, 실제 동작하는 파이썬 분석 코드를 정리한 예제입니다.

## 1. 시스템 구성품 추천

### 1-1. GPU IoT PC (엣지 컴퓨팅 보드)

| 구분 | 제품 예시 | 참고 가격(USD)ⁿ | 특징 |
|---|---|---|---|
| 소형/저전력 | NVIDIA Jetson Orin Nano Super 개발자 키트 (8GB) | 약 $399 (NVIDIA 공식가, 2026-07 인상 후 / 유통사는 $479~) | 최대 67 TOPS, 소비전력 7~25W. 카메라 1~4대 규모의 실시간 추론에 적합. 가성비 최고 |
| 중형 | NVIDIA Jetson Orin NX 모듈 | 8GB 약 $399 / 16GB 약 $599 (모듈 가격, 캐리어보드 별도) | 다중 카메라(4~8대) 동시 추론 |
| 대형 | NVIDIA Jetson AGX Orin 개발자 키트 (64GB) | 약 $3,499 (2026-07 인상 후, 종전 $1,999) | 최대 275 TOPS. 다중 카메라(8대 이상), 복합 모델(탐지+추적+분류) 동시 처리 |
| 산업용 x86 | 산업용 미니 PC + NVIDIA RTX A2000/A4000 (임베디드 GPU) | 약 $2,500~5,000 (구성·제조사 견적에 따라 상이) | 윈도우/리눅스 모두 지원, USB/PCIe 확장성 우수, 팹/공장 등 기존 x86 인프라와 통합 용이 |

- 선택 기준: 처리할 **카메라 대수**, **모델 크기(YOLOv8n~x)**, **동시 추론 해상도/FPS**, **소비전력·발열 제약**을 먼저 정하고 TOPS(초당 연산량)를 역산해 선택합니다.
- ⁿ NVIDIA가 2026년 7월 Jetson 전 라인업 가격을 최대 101% 인상했습니다. 위 가격은 조사 시점(2026-08) 기준 공식/유통 가격이며 변동될 수 있습니다.

### 1-2. 카메라

| 용도 | 추천 | 참고 가격(USD) | 비고 |
|---|---|---|---|
| Jetson 전용 저지연 | CSI 카메라 (Raspberry Pi HQ/Camera Module 3 호환, IMX219/IMX477) | 약 $25~70 (렌즈 별도) | MIPI-CSI 직결, 지연 최소, 근거리 설치 |
| 고속/정밀 산업 검사 | USB3.0 글로벌 셔터 산업용 카메라 (FLIR Blackfly S 등) | 약 $340~800+ (해상도별 상이, 렌즈 별도) | 빠른 이동체(컨베이어 벨트 등) 왜곡 없이 촬영 |
| 다지점/원거리 설치 | PoE IP 카메라 4MP (RTSP/ONVIF 지원) | 약 $95~250 (대당) | 케이블 1선으로 전원+영상 전송, 확장·유지보수 용이 |
| 야간/저조도 | IR(적외선) 겸용 카메라 + IR 조명 | 위 PoE/USB 카메라 가격에 IR 기능 포함되거나 +$20~50 | 24시간 무인 감시·분석 환경 |

### 1-3. 주변장치 / 인프라

| 항목 | 예시 | 참고 가격(USD) |
|---|---|---|
| 저장장치 | NVMe SSD 1TB (OS·모델용) | 약 $60~90 |
| 저장장치 | 대용량 HDD/NAS 4~8TB (녹화 영상 보관) | 약 $100~300 |
| 네트워크 | PoE 스위치 8포트 (IP카메라 급전용) | 약 $60~150 |
| 네트워크 | 5G/LTE 라우터 모듈 (원격지 전송) | 약 $150~400 |
| 전원 보호 | 소형 UPS | 약 $80~200 |
| 전원 보호 | 서지 보호기 | 약 $20~50 |
| 방열/보호 | IP65 산업용 인클로저 + 방열팬 | 약 $50~300 |
| 알림 | 부저·경광등 모듈 | 약 $15~40 |

- **모델 가속**: TensorRT(NVIDIA GPU 최적화 런타임, 무료) — 동일 모델 대비 2~5배 추론 속도 향상
- 위 표의 가격은 2026년 8월 기준 웹 검색으로 확인한 대략적 시세이며, 브랜드·구성·수량·환율에 따라 실제 구매가는 달라질 수 있습니다. 정확한 견적은 제조사/유통사에 직접 확인하시길 권장합니다.

### 1-4. 최소 구성 예상 총액 (참고용)

| 구성 | 대략적 합계(USD) |
|---|---|
| 소형: Jetson Orin Nano Super + CSI 카메라 1대 + SSD + 소형 UPS | 약 $550~650 |
| 중형: Jetson Orin NX + PoE 카메라 2대 + PoE 스위치 + SSD/HDD | 약 $1,000~1,300 |
| 대형: Jetson AGX Orin + 산업용 USB3 카메라 2대 + 인클로저 + 네트워크 장비 일체 | 약 $5,000~6,500 |

(설치 인건비, VAT/관세, 케이블·렌즈 등 부자재는 별도)

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
