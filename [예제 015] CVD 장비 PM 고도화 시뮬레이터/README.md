# CVD 장비 PM 정합성 고도화 시뮬레이터

CVD PM Optimization 실행안(EWC·ESI·PM Life·ROI)을 현장 데이터로 직접 계산해볼 수 있는
단일 HTML 실행형 계산기입니다. 별도 설치나 서버 없이 `index.html`을 브라우저로 열면 바로 동작합니다.

## 사용 방법

`index.html`을 더블클릭하거나 브라우저에 드래그해서 엽니다. (인터넷 연결 불필요, 모든 계산은
브라우저 안에서만 실행되고 외부로 전송되지 않습니다)

## 기능

1. **Baseline 설정** — 대상 장비 수, PM 기준 Wafer, 평균 PM 시간, 월 처리량 등 Pilot 가정값 입력
2. **EWC 환산** — Recipe별 Stress Factor 가중치를 입력하면 Equivalent Wafer(EWC)로 자동 환산
3. **ESI 가중치 설정** — EWC 누적 + FDC 8개 Feature의 가중치를 자유롭게 조정 (합계 100% 검증 포함)
4. **Chamber 분석 · 열화 포인트 탐지** — Chamber별 Wafer/EWC/FDC 점수 입력 시 ESI·판정(Green/Yellow/Orange/Red)·근거 Top3 요인 자동 계산, Wafer 기준만으로는 놓칠 수 있는 이상 Chamber 쌍 자동 탐지
5. **잔여 PM Life** — Chamber별 남은 EWC와 잔여일수 계산 (Cycle 연장률 적용 가능)
6. **ROI · 민감도 분석** — 직접 PM비용 절감 + Capacity Gain 계산, Cycle 연장률별 민감도 표 자동 생성
7. **레포트 자동 생성** — 모든 입력/계산 결과를 텍스트 레포트로 정리, 클립보드 복사 지원
8. **PPT 다운로드** — 현재 계산 결과를 9장짜리 실행 보고용 .pptx 파일로 즉시 생성/다운로드

모든 입력값은 브라우저 로컬 저장소(localStorage)에 자동 저장되어 다음 접속 시에도 유지됩니다.

## 파일 구조

```
index.html                          메인 애플리케이션 (UI + 계산 로직)
vendor/pptxgen.min.js               PPT 생성용 PptxGenJS 라이브러리 (오프라인 동작을 위해 내장)
CVD_PM_시뮬레이터_사용가이드.pptx   사용방법 + 가중치(EWC/ESI/임계값) 산정 근거 정리 슬라이드
```

## 참고

기본값으로 채워진 데이터는 참고용 가정 수치이며, 실제 현장 데이터로 교체하여 사용해야 합니다.
