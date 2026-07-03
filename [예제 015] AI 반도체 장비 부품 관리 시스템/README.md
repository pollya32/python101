# Smart Semiconductor Parts Management

AI 기반 반도체 장비 부품 관리 시스템 — 기획서(부품 데이터 흐름 및 지능형 대시보드 아키텍처)를 웹 애플리케이션으로 구현한 프로젝트입니다.

**내 PC를 DB 서버로 사용**(SQLite 파일 DB)하며, 같은 네트워크의 **팀원 최대 15명이 브라우저로 동시 접속**해 사용할 수 있습니다.

## 주요 기능 (기획서 데이터 흐름 5단계)

| 단계 | 기능 | 구현 내용 |
|---|---|---|
| 01. 설비 센싱 | Data Ingestion | 센서 데이터(압력·온도·RF) 수집 — REST API + 시뮬레이션 |
| 02. FDC 감지 | Fault Detection | 3σ SPC 기반 이상치 탐지, 고장 모드·원인 부품 자동 분류 |
| 03. AI 분석 | RUL 예측 | Health Index(0~100) 산출, 열화 추세 회귀 분석으로 잔여 수명 예측 |
| 04. 교체 추천 | 지능형 추천 | TBM vs AI 추천 비교, 최적 교체 시점 자동 알림, 잔여 수명 활용 비용 절감 산출 |
| 05. 이력 관리 | 전주기 추적 | 부품 ID·장비·교체일자·사용일수·고장원인·상태 이력, CSV 내보내기 |

추가로 기획서의 나머지 요구사항도 모두 포함합니다.

- **핵심 관리 지표**: MTBF(평균 고장 간격) / MTTR(평균 수리 시간) 자동 산출, 장비별 비교
- **부품 수명 예측 및 열화 트렌드**: Health Index 열화 곡선 + Replacement Limit 시각화
- **QR 스마트 부품 트래킹**: 부품별 고유 ID + QR 코드 발급(모바일 스캔용)
- **Smart Alarm**: 수명 임계 도달 전 자동 알림, FDC 이상 즉시 통지
- **Safety Stock**: 안전 재고 미달 감지 + 30일 내 AI 예측 교체 수요 기반 발주 제안
- **Visual Analytics**: 고장 패턴 분석, 카테고리별 비교, 월별 교체 추이, RUL 예측 정확도

## 설치 및 실행

```bash
pip install -r requirements.txt
python app.py           # 기본 포트 8000 (다른 포트: python app.py 9000)
```

실행하면 콘솔에 접속 주소가 표시됩니다.

```
  이 PC에서 접속   : http://127.0.0.1:8000
  팀원 공유 주소   : http://192.168.x.x:8000   (같은 네트워크, ~15명)
  DB 파일          : .../smart_parts.db
```

### 팀원 15명과 공유하는 방법

1. 이 프로그램을 **한 대의 PC(호스트)** 에서 실행합니다. DB는 그 PC의 `smart_parts.db` 파일에 저장됩니다.
2. 콘솔에 표시된 **팀원 공유 주소**(예: `http://192.168.0.10:8000`)를 팀원에게 알려줍니다.
3. 팀원은 같은 사내 네트워크에서 브라우저로 접속해 로그인합니다. (별도 설치 불필요)
4. Windows 방화벽 사용 시 최초 1회 포트 허용이 필요할 수 있습니다:
   `제어판 → Windows Defender 방화벽 → 고급 설정 → 인바운드 규칙 → 새 규칙 → 포트 8000(TCP) 허용`
- 활성 계정은 **최대 15명**으로 제한되며, 관리자 화면에서 계정을 추가/비활성화할 수 있습니다.
- SQLite WAL 모드 + 다중 스레드 서버(waitress)로 15명 수준의 동시 사용을 지원합니다.

## 기본 계정

| 역할 | 아이디 | 비밀번호 | 권한 |
|---|---|---|---|
| 관리자 | `admin` | `admin1234` | 전체 (사용자 관리 포함) |
| 엔지니어 | `engineer01` ~ `engineer09` | `semi1234` | 등록/수정/교체/발주 |
| 조회 전용 | `viewer01` ~ `viewer05` | `semi1234` | 읽기 전용 |

> 최초 로그인 후 관리자 화면에서 비밀번호를 변경하세요.

## 설비 연동 API (Data Ingestion)

설비 PC/PLC에서 실시간 파라미터를 전송하면 FDC 판정 → Health 갱신 → Smart Alarm까지 자동 처리됩니다.

```bash
curl -X POST http://<호스트IP>:8000/api/sensor \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: sensor-token-2026" \
  -d '{"part_code": "CH-VP-001", "pressure": 0.051, "temperature": 45.2, "rf_power": 1500}'
```

API 키는 환경변수 `SENSOR_API_TOKEN`으로 변경할 수 있습니다.

## 데이터 백업

DB는 단일 파일이므로 서버 중지 후 `smart_parts.db` 파일만 복사하면 백업이 완료됩니다.
데모 데이터를 초기화하려면 `smart_parts.db`(및 `-wal`, `-shm`) 파일을 삭제하고 재실행하세요.

## 기술 스택

- **Backend**: Python 3 + Flask, SQLite(WAL) — 별도 DB 서버 불필요
- **Frontend**: Jinja2 + Chart.js(로컬 번들, 오프라인 동작), 다크 네이비/시안 테마
- **Server**: waitress(멀티스레드) — 미설치 시 Flask 내장 서버로 자동 대체
