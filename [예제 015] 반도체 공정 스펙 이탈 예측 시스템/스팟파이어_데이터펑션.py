"""
TIBCO Spotfire Data Function 등록용 스크립트
=====================================================
스펙이탈예측_단일파일.py의 로직을 Spotfire Data Function 규격에 맞게 옮긴 버전.
matplotlib 시각화 대신, Spotfire 자체 차트로 그릴 수 있도록 "표(Table)"만 반환한다.

※ 이 파일은 그냥 실행하는 스크립트가 아니라, Spotfire의
   [Insert > Data Function > New] 편집창에 "그대로 붙여넣는" 코드입니다.
   Spotfire가 실행 시 input_table/usl/lsl/... 변수를 자동으로 주입해준다.

[Spotfire에 등록할 Input 파라미터]
  input_table    Table    필수 컬럼: timestamp, value
  usl            Value (Real)     Upper Spec Limit
  lsl            Value (Real)     Lower Spec Limit
  window         Value (Integer)  이동평균/표준편차 구간, 기본 20
  sigma_k        Value (Real)     관리한계 배수, 기본 3.0
  forecast_days  Value (Integer)  예측 범위(일), 기본 365

[Spotfire에 등록할 Output 파라미터]
  forecast_table Table   일자별 예측(Mean/Sigma/UCL/LCL/Cpk) — Line Chart용
  summary_table  Table   현재 상태 + 예측 이탈일 요약(1행) — KPI/Text Area용
"""

import numpy as np
import pandas as pd

# ── 아래 5개 변수는 Spotfire가 Input 파라미터로 자동 주입한다 ──────────────
# input_table, usl, lsl, window, sigma_k, forecast_days
# (Spotfire 밖에서 직접 실행해 테스트하려면 이 값들을 먼저 정의해야 한다)

df = input_table.copy()
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)

t0 = df['timestamp'].iloc[0]
df['days'] = (df['timestamp'] - t0).dt.total_seconds() / 86400.0

w = int(window)
df['roll_mean'] = df['value'].rolling(w, min_periods=w).mean()
df['roll_std'] = df['value'].rolling(w, min_periods=w).std()
trend = df.dropna(subset=['roll_mean', 'roll_std'])

if len(trend) < 5:
    raise ValueError(f"추세 계산에 필요한 데이터가 부족합니다 (window={w} 기준 최소 {w + 5}개 필요)")


def _linfit(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2


mean_slope, mean_intercept, mean_r2 = _linfit(trend['days'], trend['roll_mean'])
std_slope, std_intercept, std_r2 = _linfit(trend['days'], trend['roll_std'])

last_day = df['days'].iloc[-1]
last_date = df['timestamp'].iloc[-1]

# ── 일자별 미래 예측 테이블 생성 ────────────────────────────────────────
rows = []
breach_day, breach_side = None, None
for step in range(0, int(forecast_days) + 1):
    day = last_day + step
    mu = mean_slope * day + mean_intercept
    sigma = max(std_slope * day + std_intercept, 0.0)
    ucl = mu + sigma_k * sigma
    lcl = mu - sigma_k * sigma
    cpk = min(usl - mu, mu - lsl) / (3 * sigma) if sigma > 0 else np.nan
    date = last_date + pd.Timedelta(days=step)
    rows.append([date, day, mu, sigma, ucl, lcl, cpk])
    if breach_day is None:
        if ucl >= usl:
            breach_day, breach_side = day, 'USL'
        elif lcl <= lsl:
            breach_day, breach_side = day, 'LSL'

forecast_table = pd.DataFrame(rows, columns=['Date', 'Day', 'Mean', 'Sigma', 'UCL', 'LCL', 'Cpk'])

breach_date = (last_date + pd.Timedelta(days=breach_day - last_day)) if breach_day is not None else pd.NaT
days_remaining = (breach_day - last_day) if breach_day is not None else np.nan

summary_table = pd.DataFrame([{
    'LastDataDate': last_date,
    'CurrentMean': trend['roll_mean'].iloc[-1],
    'CurrentSigma': trend['roll_std'].iloc[-1],
    'SigmaSlopePerDay': std_slope,
    'SigmaTrendR2': std_r2,
    'MeanTrendR2': mean_r2,
    'BreachDate': breach_date,
    'DaysRemaining': days_remaining,
    'BreachSide': breach_side if breach_side else 'N/A',
    'Reliable': bool(std_r2 >= 0.3),
    'USL': usl,
    'LSL': lsl,
}])

# ── Output 파라미터로 등록된 forecast_table, summary_table을 Spotfire가 회수해간다 ──
