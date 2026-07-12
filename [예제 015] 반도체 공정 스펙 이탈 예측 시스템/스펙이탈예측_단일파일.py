"""
반도체 공정 산포(Spread) 이탈 예측 — 단일 파일 버전
=====================================================
Heater Torque, Zscan 등 계측값의 "산포(표준편차)"가 시간이 지나며 점점
벌어지는 추세를 선형회귀로 추정하고, 그 추세가 이어질 경우 관리한계
(mean ± k*sigma)가 언제 스펙(LSL/USL)을 벗어나는지 미래 시점을 예측한다.

설치: pip install -r requirements.txt
실행: python 스펙이탈예측_단일파일.py
      (인자 없이 실행하면 샘플(시뮬레이션) 데이터로 자동 실행/시각화됨)

실데이터 사용법:
    python 스펙이탈예측_단일파일.py my_data.csv
    my_data.csv 컬럼 구성: timestamp, heater_torque, zscan
    (컬럼명이 다르면 아래 main()의 spec_map/컬럼명을 맞게 수정)

참고(리눅스 환경): 그래프에 한글이 깨져 보이면 한글 폰트가 없는 것입니다.
    sudo apt install fonts-nanum  후 다시 실행하세요. (Windows/Mac은 보통 문제 없음)
"""

import sys
import os
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager


def _setup_korean_font():
    """OS에 설치된 한글 폰트를 찾아 matplotlib 기본 폰트로 지정한다."""
    candidates = ['Malgun Gothic', 'AppleGothic', 'NanumGothic', 'Noto Sans CJK KR', 'Noto Sans KR']
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams['font.family'] = name
            break
    plt.rcParams['axes.unicode_minus'] = False


_setup_korean_font()


# ── 스펙 정의 ─────────────────────────────────────────────────────────────

@dataclass
class SpecLimit:
    lsl: float              # Lower Spec Limit
    usl: float               # Upper Spec Limit
    target: float = None     # 목표값(옵션, 없으면 (lsl+usl)/2)

    def __post_init__(self):
        if self.target is None:
            self.target = (self.lsl + self.usl) / 2


# ── 핵심 클래스 ───────────────────────────────────────────────────────────

class SpecDriftPredictor:
    """
    시계열 계측 데이터의 이동평균/이동표준편차 추세를 선형회귀로 추정하고,
    미래 관리한계(UCL/LCL = mean ± k*sigma)가 스펙을 벗어나는 시점을 예측한다.
    """

    def __init__(self, window: int = 20, sigma_k: float = 3.0, forecast_days: int = 365):
        self.window = window          # 이동평균/이동표준편차 계산 구간(포인트 수)
        self.sigma_k = sigma_k        # 관리한계 배수 (보통 3-sigma)
        self.forecast_days = forecast_days  # 최대 예측 범위(일)

    def analyze(self, df: pd.DataFrame, value_col: str, time_col: str, spec: SpecLimit) -> dict:
        """df: time_col, value_col을 포함한 DataFrame. 시간순 정렬 안 되어 있어도 됨."""
        d = df[[time_col, value_col]].dropna().copy()
        d[time_col] = pd.to_datetime(d[time_col])
        d = d.sort_values(time_col).reset_index(drop=True)
        d = d.rename(columns={time_col: 'timestamp'})

        t0 = d['timestamp'].iloc[0]
        d['days'] = (d['timestamp'] - t0).dt.total_seconds() / 86400.0

        d['roll_mean'] = d[value_col].rolling(self.window, min_periods=self.window).mean()
        d['roll_std'] = d[value_col].rolling(self.window, min_periods=self.window).std()
        trend_df = d.dropna(subset=['roll_mean', 'roll_std'])

        if len(trend_df) < 5:
            raise ValueError(
                f"추세 계산에 필요한 데이터가 부족합니다 "
                f"(window={self.window} 기준 최소 {self.window + 5}개 필요, 현재 {len(d)}개)"
            )

        mean_slope, mean_intercept, mean_r2 = self._linfit(trend_df['days'], trend_df['roll_mean'])
        std_slope, std_intercept, std_r2 = self._linfit(trend_df['days'], trend_df['roll_std'])

        last_day = d['days'].iloc[-1]
        last_date = d['timestamp'].iloc[-1]

        breach_day, breach_side, breach_detail = self._find_breach(
            mean_slope, mean_intercept, std_slope, std_intercept, last_day, spec
        )

        return {
            'value_col': value_col,
            'spec': spec,
            'data': d,
            'window': self.window,
            'sigma_k': self.sigma_k,
            'last_date': last_date,
            'last_day': last_day,
            'current_mean': trend_df['roll_mean'].iloc[-1],
            'current_std': trend_df['roll_std'].iloc[-1],
            'mean_slope_per_day': mean_slope,
            'mean_intercept': mean_intercept,
            'mean_r2': mean_r2,
            'std_slope_per_day': std_slope,
            'std_intercept': std_intercept,
            'std_r2': std_r2,
            'breach_day': breach_day,
            'breach_date': (last_date + timedelta(days=breach_day - last_day)) if breach_day else None,
            'days_remaining': (breach_day - last_day) if breach_day else None,
            'breach_side': breach_side,
            'breach_detail': breach_detail,
            'reliable': std_r2 >= 0.3,  # 추세 신뢰도 참고 기준 (R^2)
        }

    @staticmethod
    def _linfit(x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return slope, intercept, r2

    def _find_breach(self, mean_slope, mean_intercept, std_slope, std_intercept, last_day, spec: SpecLimit):
        """미래로 하루씩 전진하며 UCL/LCL이 스펙을 최초로 벗어나는 시점을 탐색한다."""
        for step in range(1, self.forecast_days + 1):
            day = last_day + step
            mu = mean_slope * day + mean_intercept
            sigma = max(std_slope * day + std_intercept, 0.0)  # 표준편차는 음수가 될 수 없음
            ucl = mu + self.sigma_k * sigma
            lcl = mu - self.sigma_k * sigma
            if ucl >= spec.usl:
                return day, 'USL', {'ucl': ucl, 'mu': mu, 'sigma': sigma}
            if lcl <= spec.lsl:
                return day, 'LSL', {'lcl': lcl, 'mu': mu, 'sigma': sigma}
        return None, None, None

    def cpk_forecast(self, result: dict, horizons_days=(0, 30, 60, 90)) -> pd.DataFrame:
        """미래 특정 시점들의 예상 Cpk를 계산해 표로 반환한다."""
        spec = result['spec']
        rows = []
        for h in horizons_days:
            day = result['last_day'] + h
            mu = result['mean_slope_per_day'] * day + result['mean_intercept']
            sigma = max(result['std_slope_per_day'] * day + result['std_intercept'], 1e-9)
            cpk = min(spec.usl - mu, mu - spec.lsl) / (3 * sigma)
            rows.append({'경과일': h, '예상평균': mu, '예상표준편차': sigma, '예상Cpk': cpk})
        return pd.DataFrame(rows)

    # ── 시각화 ───────────────────────────────────────────────────────────

    def plot(self, result: dict, save_path: str = None):
        d = result['data']
        spec = result['spec']
        value_col = result['value_col']
        k = result['sigma_k']

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8))

        # (1) 원본 데이터 + 이동평균 + 관리한계 + 스펙 라인
        ax1.plot(d['timestamp'], d[value_col], '.', color='steelblue', alpha=0.35, label='측정값')
        ax1.plot(d['timestamp'], d['roll_mean'], color='navy', lw=2, label=f'이동평균({self.window}pt)')
        ucl_hist = d['roll_mean'] + k * d['roll_std']
        lcl_hist = d['roll_mean'] - k * d['roll_std']
        ax1.fill_between(d['timestamp'], lcl_hist, ucl_hist, color='navy', alpha=0.1,
                          label=f'±{k:g}σ 관리한계(이력)')
        ax1.axhline(spec.usl, color='red', ls='--', lw=1.5, label='USL')
        ax1.axhline(spec.lsl, color='red', ls='--', lw=1.5, label='LSL')

        if result['breach_date'] is not None:
            ax1.axvline(result['breach_date'], color='orange', ls=':', lw=2,
                        label=f"예측 이탈일: {result['breach_date'].date()} ({result['breach_side']})")

        ax1.set_title(f"{value_col} — 측정값 & 관리한계 추세")
        ax1.legend(loc='upper left', fontsize=8, ncol=2)
        ax1.grid(alpha=0.3)

        # (2) 산포(이동표준편차) 추세 + 미래 예측선
        ax2.plot(d['timestamp'], d['roll_std'], '.', color='seagreen', alpha=0.4, label='이동표준편차')

        extra_days = int(result['days_remaining']) + 15 if result['days_remaining'] else 30
        future_days = np.arange(0, result['last_day'] + extra_days)
        future_std = np.clip(result['std_slope_per_day'] * future_days + result['std_intercept'], 0, None)
        future_dates = d['timestamp'].iloc[0] + pd.to_timedelta(future_days, unit='D')
        is_forecast = future_days > result['last_day']

        ax2.plot(future_dates[~is_forecast], future_std[~is_forecast], color='darkgreen', lw=2, label='σ 추세선(이력)')
        ax2.plot(future_dates[is_forecast], future_std[is_forecast], color='darkorange', lw=2, ls='--', label='σ 추세선(예측)')

        if result['breach_date'] is not None:
            ax2.axvline(result['breach_date'], color='orange', ls=':', lw=2)
            ax2.scatter([result['breach_date']], [result['breach_detail']['sigma']], color='red', zorder=5,
                        label='스펙 이탈 예상 시점')

        ax2.set_title(f"{value_col} — 산포(σ) 추세 및 미래 예측")
        ax2.set_ylabel('표준편차')
        ax2.legend(loc='upper left', fontsize=8)
        ax2.grid(alpha=0.3)

        fig.autofmt_xdate()
        fig.tight_layout()
        if save_path:
            fig.savefig(save_path, dpi=120)
            print(f"  그래프 저장: {save_path}")
        return fig


# ── 리포트 출력 ───────────────────────────────────────────────────────────

def print_report(result: dict, predictor: SpecDriftPredictor):
    spec = result['spec']
    name = result['value_col']
    print(f"\n{'=' * 60}")
    print(f"[{name}]  스펙: LSL={spec.lsl}  USL={spec.usl}  (target={spec.target})")
    print(f"{'=' * 60}")
    print(f"  최근 이동평균(mean)      : {result['current_mean']:.4f}")
    print(f"  최근 이동표준편차(sigma) : {result['current_std']:.4f}")
    print(f"  산포 증가율(σ/day)       : {result['std_slope_per_day']:+.5f}  (R²={result['std_r2']:.2f})")
    print(f"  평균 이동율(mean/day)    : {result['mean_slope_per_day']:+.5f}  (R²={result['mean_r2']:.2f})")

    if not result['reliable']:
        print("  ※ 추세 R²이 낮아(0.3 미만) 예측 신뢰도가 낮습니다. 데이터/구간을 재검토하세요.")

    if result['breach_date'] is not None:
        print(f"  >> 예측: 약 {result['days_remaining']:.0f}일 후 "
              f"({result['breach_date'].date()}) {result['breach_side']} 이탈 예상")
    else:
        print(f"  >> 현재 추세로는 향후 {predictor.forecast_days}일 내 스펙 이탈이 예측되지 않습니다.")

    cpk_table = predictor.cpk_forecast(result)
    print("\n  [Cpk 예측]")
    print(cpk_table.to_string(index=False, formatters={
        '예상평균': '{:.3f}'.format, '예상표준편차': '{:.3f}'.format, '예상Cpk': '{:.3f}'.format
    }))


# ── 샘플(시뮬레이션) 데이터 생성 ────────────────────────────────────────────

def generate_demo_data(n_days: int = 150, points_per_day: int = 3, seed: int = 42) -> pd.DataFrame:
    """
    Heater Torque / Zscan 계측값을 시뮬레이션한다.
    두 파라미터 모두 평균은 대체로 안정적이나, 설비 노후화를 가정해
    표준편차(산포)가 시간이 지날수록 서서히 벌어지도록 만든다.
    """
    rng = np.random.default_rng(seed)
    n = n_days * points_per_day
    timestamps = pd.date_range('2026-01-01', periods=n, freq=f'{24 // points_per_day}h')
    days = np.arange(n) / points_per_day

    # Heater Torque: target 10.0, spec 8.0~12.0, 초기 sigma=0.25 -> 서서히 벌어짐(이탈은 미래 시점)
    torque_std = 0.25 + 0.0015 * days + rng.normal(0, 0.008, n).clip(-0.04, 0.04)
    torque_std = np.clip(torque_std, 0.15, None)
    heater_torque = rng.normal(10.0, torque_std)

    # Zscan: target 0.0, spec -60~60, 초기 sigma=8 -> 서서히 벌어짐(이탈은 미래 시점) + 약간의 평균 이동
    zscan_std = 8.0 + 0.05 * days + rng.normal(0, 0.25, n).clip(-1, 1)
    zscan_std = np.clip(zscan_std, 5.0, None)
    zscan_mean_drift = 0.02 * days
    zscan = rng.normal(zscan_mean_drift, zscan_std)

    return pd.DataFrame({
        'timestamp': timestamps,
        'heater_torque': heater_torque,
        'zscan': zscan,
    })


# ── 실행부 ───────────────────────────────────────────────────────────────

def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else None

    if csv_path:
        df = pd.read_csv(csv_path)
        print(f"실데이터 로드: {csv_path} ({len(df)} rows)")
    else:
        df = generate_demo_data()
        print("실행 인자가 없어 시뮬레이션 샘플 데이터로 실행합니다.")
        print("(실데이터 사용: python 스펙이탈예측_단일파일.py your_data.csv)")

    spec_map = {
        'heater_torque': SpecLimit(lsl=8.0, usl=12.0),
        'zscan': SpecLimit(lsl=-60.0, usl=60.0),
    }

    predictor = SpecDriftPredictor(window=20, sigma_k=3.0, forecast_days=365)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base_dir, 'output')
    os.makedirs(out_dir, exist_ok=True)

    for col, spec in spec_map.items():
        if col not in df.columns:
            continue
        result = predictor.analyze(df, value_col=col, time_col='timestamp', spec=spec)
        print_report(result, predictor)
        predictor.plot(result, save_path=os.path.join(out_dir, f'{col}_forecast.png'))


if __name__ == '__main__':
    main()
