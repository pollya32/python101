"""
FDC (Fault Detection and Classification) 이상 감지 시스템
반도체 설비 센서 데이터 기반 이상치 탐지 및 AI 원인 분석

구성:
  FDCDataLoader     - CSV 로드 및 전처리
  AnomalyDetector   - 통계 기반 이상치 탐지 (Z-score, 허용 오차, 추세)
  FDCAIAnalyst      - Claude AI 기반 원인 분석 및 조치 방안 생성
  FDCReportGenerator - 콘솔 대시보드 및 CSV 리포트 출력
  FDCSystem         - 메인 오케스트레이터

사용법:
  # 1단계: 샘플 데이터 생성
  python fdc_데이터생성기.py --anomaly pressure_drift

  # 2단계: 이상 감지 및 AI 분석 실행
  python fdc_이상감지시스템.py

  # AI 분석 없이 실행
  python fdc_이상감지시스템.py --no-ai

  # 다른 시나리오 데이터 분석
  python fdc_데이터생성기.py --anomaly multi_sensor --output multi.csv
  python fdc_이상감지시스템.py --csv multi.csv --equipment CVD-EQ-002
"""

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# 센서 설정: 정상 범위 기준값 및 허용 한계
# ──────────────────────────────────────────────
SENSOR_CONFIG: dict[str, dict] = {
    "Chamber_Pressure": {
        "unit": "Torr",
        "nominal": 2.000,
        "tolerance": 0.01,          # ±0.01 Torr
    },
    "RF_Forward_Power": {
        "unit": "W",
        "nominal": 1000.0,
        "tolerance_pct": 0.5,       # ±0.5%
    },
    "RF_Reflected_Power": {
        "unit": "W",
        "nominal": 8.0,
        "max_limit": 50.0,          # 낮을수록 좋음, 50W 초과 시 이상
    },
    "ESC_Temperature": {
        "unit": "degC",
        "nominal": 20.0,
        "tolerance": 2.0,           # ±2°C
    },
    "Gas_Flow_MFC": {
        "unit": "sccm",
        "nominal": 100.0,
        "tolerance_pct": 1.0,       # ±1%
    },
    "APC_Valve": {
        "unit": "%",
        "nominal": 65.0,
        "tolerance": 5.0,           # ±5%
    },
    "Pump_Speed": {
        "unit": "RPM",
        "nominal": 3000.0,
        "tolerance": 100.0,         # ±100 RPM
    },
}

# 심각도별 콘솔 색상 (ANSI)
COLOR = {
    "NORMAL":  "\033[92m",   # 녹색
    "WARNING": "\033[93m",   # 황색
    "ALARM":   "\033[91m",   # 적색
    "BOLD":    "\033[1m",
    "RESET":   "\033[0m",
}

SEVERITY_LABEL = {
    "NORMAL":  "[ OK   ]",
    "WARNING": "[WARN  ]",
    "ALARM":   "[ALARM ]",
}


# ──────────────────────────────────────────────
# 1. FDC 데이터 로더
# ──────────────────────────────────────────────
class FDCDataLoader:
    def load(self, filepath: str) -> pd.DataFrame:
        df = pd.read_csv(filepath, parse_dates=["Timestamp"])
        df = df.sort_values("Timestamp").reset_index(drop=True)
        return df


# ──────────────────────────────────────────────
# 2. 이상치 탐지 엔진
# ──────────────────────────────────────────────
class AnomalyDetector:
    def __init__(
        self,
        z_threshold: float = 3.0,
        trend_window: int = 3,
    ):
        self.z_threshold = z_threshold
        self.trend_window = trend_window

    # ── 2-1. Z-score 이상치 ──────────────────
    def _detect_zscore(self, series: pd.Series) -> pd.Series:
        std = series.std()
        if std == 0:
            return pd.Series(False, index=series.index)
        z = (series - series.mean()) / std
        return z.abs() > self.z_threshold

    # ── 2-2. 허용 범위 이탈 ──────────────────
    def _detect_out_of_range(
        self,
        series: pd.Series,
        lo: Optional[float],
        hi: Optional[float],
    ) -> pd.Series:
        mask = pd.Series(False, index=series.index)
        if lo is not None:
            mask |= series < lo
        if hi is not None:
            mask |= series > hi
        return mask

    # ── 2-3. 추세 분석 ──────────────────────
    def _detect_trend(self, series: pd.Series) -> dict:
        diffs = series.diff().dropna()

        max_up = max_dn = cur_up = cur_dn = 0
        for d in diffs:
            if d > 0:
                cur_up += 1
                cur_dn = 0
                max_up = max(max_up, cur_up)
            elif d < 0:
                cur_dn += 1
                cur_up = 0
                max_dn = max(max_dn, cur_dn)
            else:
                cur_up = cur_dn = 0

        direction = "stable"
        if max_up >= self.trend_window:
            direction = "increasing"
        elif max_dn >= self.trend_window:
            direction = "decreasing"

        x = np.arange(len(series))
        slope = float(np.polyfit(x, series.values, 1)[0]) if len(x) > 1 else 0.0

        return {
            "direction": direction,
            "slope": slope,
            "max_consecutive_up": max_up,
            "max_consecutive_down": max_dn,
        }

    # ── 2-4. 센서별 종합 분석 ────────────────
    def analyze_sensor(self, df: pd.DataFrame, sensor: str, config: dict) -> dict:
        series = df[sensor].dropna()

        result = {
            "sensor": sensor,
            "unit": config.get("unit", ""),
            "current_value": float(series.iloc[-1]) if len(series) > 0 else None,
            "mean": float(series.mean()),
            "std": float(series.std()),
            "min": float(series.min()),
            "max": float(series.max()),
            "anomalies": [],
            "trend": self._detect_trend(series),
            "severity": "NORMAL",
        }

        # Z-score 검사
        z_flags = self._detect_zscore(series)
        z_count = int(z_flags.sum())
        if z_count > 0:
            result["anomalies"].append({
                "type": "Z-Score 초과",
                "detail": f"{z_count}개 포인트가 {self.z_threshold}σ 초과",
                "count": z_count,
            })

        # 절대 허용 오차 검사
        if "tolerance" in config:
            nom, tol = config["nominal"], config["tolerance"]
            flags = self._detect_out_of_range(series, nom - tol, nom + tol)
            cnt = int(flags.sum())
            if cnt > 0:
                pct = cnt / len(series) * 100
                result["anomalies"].append({
                    "type": "허용 범위 이탈",
                    "detail": f"{cnt}개({pct:.1f}%)가 설정값 {nom} ± {tol} {config['unit']} 초과",
                    "count": cnt,
                })

        # 비율 허용 오차 검사
        if "tolerance_pct" in config:
            nom, tol_pct = config["nominal"], config["tolerance_pct"] / 100
            flags = self._detect_out_of_range(series, nom * (1 - tol_pct), nom * (1 + tol_pct))
            cnt = int(flags.sum())
            if cnt > 0:
                pct = cnt / len(series) * 100
                result["anomalies"].append({
                    "type": "허용 범위 이탈",
                    "detail": f"{cnt}개({pct:.1f}%)가 설정값 ± {config['tolerance_pct']}% 초과",
                    "count": cnt,
                })

        # 최대값 한계 검사
        if "max_limit" in config:
            flags = self._detect_out_of_range(series, None, config["max_limit"])
            cnt = int(flags.sum())
            if cnt > 0:
                result["anomalies"].append({
                    "type": "최대 한계 초과",
                    "detail": f"{cnt}개가 최대 허용값 {config['max_limit']} {config['unit']} 초과",
                    "count": cnt,
                })

        # 추세 이상 검사
        trend = result["trend"]
        if trend["direction"] == "increasing":
            consec = trend["max_consecutive_up"]
            result["anomalies"].append({
                "type": "지속적 증가 추세",
                "detail": f"{consec}회 연속 증가 (기울기: {trend['slope']:+.5f}/포인트)",
                "count": consec,
            })
        elif trend["direction"] == "decreasing":
            consec = trend["max_consecutive_down"]
            result["anomalies"].append({
                "type": "지속적 감소 추세",
                "detail": f"{consec}회 연속 감소 (기울기: {trend['slope']:+.5f}/포인트)",
                "count": consec,
            })

        # 심각도 결정
        total_anomaly_count = sum(a["count"] for a in result["anomalies"])
        if total_anomaly_count == 0:
            result["severity"] = "NORMAL"
        elif total_anomaly_count <= 3:
            result["severity"] = "WARNING"
        else:
            result["severity"] = "ALARM"

        return result


# ──────────────────────────────────────────────
# 3. Claude AI 분석 모듈
# ──────────────────────────────────────────────
class FDCAIAnalyst:
    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.client = anthropic.Anthropic()
        self.model = model

    def analyze(self, sensor_results: list[dict], equipment_id: str) -> str:
        anomalous = [r for r in sensor_results if r["severity"] != "NORMAL"]

        if not anomalous:
            return "모든 센서가 정상 범위에서 동작 중입니다. 특이사항 없음."

        lines = []
        for r in anomalous:
            lines.append(f"[{r['sensor']}] 현재값={r['current_value']:.4f} {r['unit']}, "
                         f"평균={r['mean']:.4f}, 표준편차={r['std']:.4f}")
            lines.append(f"  추세: {r['trend']['direction']} (기울기: {r['trend']['slope']:+.5f})")
            for a in r["anomalies"]:
                lines.append(f"  이상: {a['type']} - {a['detail']}")
        sensor_summary = "\n".join(lines)

        prompt = f"""당신은 반도체 CVD/Etch 설비 전문 엔지니어입니다.
설비 {equipment_id}의 FDC 이상 감지 결과를 분석해주세요.

=== 이상 감지 센서 데이터 ===
{sensor_summary}

다음 형식으로 한국어 분석 보고서를 작성하세요:

1. 이상 징후 요약
   - 발견된 주요 이상 패턴 요약

2. 가능한 원인 (우선순위 순)
   - 원인 1: (설명)
   - 원인 2: (설명)
   - 원인 3: (설명)

3. 공정/품질 영향도
   - 현재 이상이 공정 및 제품 품질에 미치는 영향

4. 권장 조치사항
   - [즉시] 지금 바로 해야 할 조치
   - [단기] 24~48시간 내 점검 항목
   - [장기] 예방 보전 계획

5. 집중 모니터링 항목
   - 다음 주기에 특별히 확인할 센서 및 파라미터

간결하고 실용적으로 작성해주세요."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text


# ──────────────────────────────────────────────
# 4. 리포트 생성기
# ──────────────────────────────────────────────
class FDCReportGenerator:
    def _colored(self, text: str, severity: str) -> str:
        return f"{COLOR[severity]}{text}{COLOR['RESET']}"

    def print_console_report(
        self,
        sensor_results: list[dict],
        ai_analysis: str,
        equipment_id: str,
        timestamp: datetime,
    ) -> None:
        W = 70
        bold = COLOR["BOLD"]
        rst = COLOR["RESET"]

        print()
        print(f"{bold}{'=' * W}{rst}")
        print(f"{bold}  FDC 이상 감지 시스템 분석 보고서{rst}")
        print(f"  설비 ID : {equipment_id}")
        print(f"  분석 시각: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{bold}{'=' * W}{rst}")

        # 센서 상태 테이블
        print(f"\n{bold}[ 센서 상태 요약 ]{rst}")
        print(f"{'센서명':<28} {'현재값':>14} {'평균':>10} {'상태':>10}")
        print("-" * W)
        for r in sensor_results:
            current = (
                f"{r['current_value']:.4f} {r['unit']}"
                if r["current_value"] is not None
                else "N/A"
            )
            mean_str = f"{r['mean']:.4f}"
            label = SEVERITY_LABEL[r["severity"]]
            colored_label = self._colored(label, r["severity"])
            print(f"{r['sensor']:<28} {current:>14} {mean_str:>10}  {colored_label}")

        # 이상 상세
        abnormal = [r for r in sensor_results if r["severity"] != "NORMAL"]
        if abnormal:
            print(f"\n{bold}[ 이상 상세 내역 ]{rst}")
            for r in abnormal:
                severity_label = self._colored(f"[{r['severity']}]", r["severity"])
                print(f"\n  {bold}{r['sensor']}{rst} {severity_label}")
                for a in r["anomalies"]:
                    print(f"    * {a['type']}: {a['detail']}")

        # AI 분석
        print(f"\n{bold}[ AI 분석 결과 (Claude) ]{rst}")
        print("-" * W)
        print(ai_analysis)
        print(f"\n{bold}{'=' * W}{rst}")

        # 종합 요약
        normal_cnt = sum(1 for r in sensor_results if r["severity"] == "NORMAL")
        warn_cnt = sum(1 for r in sensor_results if r["severity"] == "WARNING")
        alarm_cnt = sum(1 for r in sensor_results if r["severity"] == "ALARM")

        print(f"\n{bold}[ 종합 요약 ]{rst}")
        print(
            f"  전체: {len(sensor_results)}개  |  "
            f"정상: {normal_cnt}  |  "
            f"경고: {warn_cnt}  |  "
            f"알람: {alarm_cnt}"
        )

        if alarm_cnt > 0:
            print(f"\n  {self._colored(f'즉각 조치 필요 — {alarm_cnt}개 알람 발생', 'ALARM')}")
        elif warn_cnt > 0:
            print(f"\n  {self._colored(f'주의 모니터링 — {warn_cnt}개 경고 발생', 'WARNING')}")
        else:
            print(f"\n  {self._colored('설비 정상 가동 중', 'NORMAL')}")
        print()

    def save_csv_report(
        self,
        sensor_results: list[dict],
        ai_analysis: str,
        output_path: str,
    ) -> tuple[str, str]:
        rows = []
        for r in sensor_results:
            anomaly_str = " | ".join(
                f"{a['type']}: {a['detail']}" for a in r["anomalies"]
            )
            rows.append(
                {
                    "센서명": r["sensor"],
                    "단위": r["unit"],
                    "현재값": r["current_value"],
                    "평균": round(r["mean"], 5),
                    "표준편차": round(r["std"], 5),
                    "최솟값": round(r["min"], 5),
                    "최댓값": round(r["max"], 5),
                    "추세": r["trend"]["direction"],
                    "추세_기울기": round(r["trend"]["slope"], 6),
                    "이상_내용": anomaly_str,
                    "심각도": r["severity"],
                }
            )

        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")

        ai_path = output_path.replace(".csv", "_AI분석.txt")
        with open(ai_path, "w", encoding="utf-8") as f:
            f.write(ai_analysis)

        return output_path, ai_path


# ──────────────────────────────────────────────
# 5. 메인 오케스트레이터
# ──────────────────────────────────────────────
class FDCSystem:
    def __init__(self, equipment_id: str = "CVD-EQ-001"):
        self.equipment_id = equipment_id
        self.loader = FDCDataLoader()
        self.detector = AnomalyDetector(z_threshold=3.0, trend_window=3)
        self.reporter = FDCReportGenerator()

    def run(
        self,
        csv_path: str,
        use_ai: bool = True,
        save_report: bool = True,
    ) -> tuple[list[dict], str]:
        timestamp = datetime.now()

        print(f"\nFDC 데이터 로딩: {csv_path}")
        df = self.loader.load(csv_path)
        print(f"  {len(df)}행 x {len(df.columns)}열 로드 완료")
        print(f"  기간: {df['Timestamp'].min()} ~ {df['Timestamp'].max()}")

        print("\n이상치 탐지 실행 중...")
        sensor_results = []
        for sensor, config in SENSOR_CONFIG.items():
            if sensor not in df.columns:
                continue
            result = self.detector.analyze_sensor(df, sensor, config)
            sensor_results.append(result)
            label = SEVERITY_LABEL[result["severity"]]
            print(f"  {label} {sensor}")

        ai_analysis = "AI 분석 건너뜀 (--no-ai 옵션)"
        if use_ai:
            print("\nClaude AI 분석 중...")
            try:
                analyst = FDCAIAnalyst()
                ai_analysis = analyst.analyze(sensor_results, self.equipment_id)
            except anthropic.AuthenticationError:
                ai_analysis = (
                    "AI 분석 오류: 인증 실패\n"
                    "환경변수 ANTHROPIC_API_KEY를 설정해주세요.\n"
                    "  export ANTHROPIC_API_KEY=sk-ant-..."
                )
            except Exception as e:
                ai_analysis = f"AI 분석 오류: {e}"

        self.reporter.print_console_report(
            sensor_results, ai_analysis, self.equipment_id, timestamp
        )

        if save_report:
            report_dir = Path(csv_path).parent / "reports"
            report_dir.mkdir(exist_ok=True)
            ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
            report_path = str(report_dir / f"fdc_report_{ts_str}.csv")
            csv_out, ai_out = self.reporter.save_csv_report(
                sensor_results, ai_analysis, report_path
            )
            print(f"리포트 저장: {csv_out}")
            print(f"AI 분석 저장: {ai_out}\n")

        return sensor_results, ai_analysis


# ──────────────────────────────────────────────
# 실행 진입점
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="FDC 이상 감지 시스템",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "사용 예시:\n"
            "  # 1단계: 샘플 데이터 생성\n"
            "  python fdc_데이터생성기.py --anomaly pressure_drift\n\n"
            "  # 2단계: 분석 실행\n"
            "  python fdc_이상감지시스템.py\n"
            "  python fdc_이상감지시스템.py --csv multi.csv --equipment CVD-EQ-002 --no-ai\n"
        ),
    )
    parser.add_argument(
        "--csv", default="fdc_sample_data.csv", help="FDC 데이터 CSV 경로"
    )
    parser.add_argument(
        "--equipment", default="CVD-EQ-001", help="설비 ID"
    )
    parser.add_argument(
        "--no-ai", action="store_true", help="AI 분석 건너뜀 (API 키 없을 때)"
    )
    parser.add_argument(
        "--no-save", action="store_true", help="리포트 파일 저장 안 함"
    )
    args = parser.parse_args()

    system = FDCSystem(equipment_id=args.equipment)
    system.run(
        csv_path=args.csv,
        use_ai=not args.no_ai,
        save_report=not args.no_save,
    )


if __name__ == "__main__":
    main()
