"""식각(Etch) 설비 FDC 가상 데이터 생성기.

웨이퍼마다 레시피 스텝별 센서 trace(초 단위)를 만들고,
설비 열화(온도 드리프트)와 고장(RF 저하, 압력 스파이크)을 주입한다.

출력:
    data/trace.csv    웨이퍼별·초 단위 센서값 (시계열)
    data/summary.csv  웨이퍼별·스텝별 평균/표준편차 + 고장 라벨
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

STEPS = {  # step: (초, 온도℃, 압력mTorr, RF W, 가스 sccm)
    "stabilize": (10, 60, 50, 0, 100),
    "main_etch": (40, 65, 30, 500, 80),
    "over_etch": (15, 65, 30, 300, 60),
    "purge":     (5,  60, 80, 0, 200),
}
FAULTS = [None, "rf_drop", "pressure_spike"]
FAULT_PROB = [0.9, 0.05, 0.05]


def run_wafer(rng, wafer_id, drift=0.0, fault=None):
    rows, t = [], 0
    for step, (dur, temp, pres, rf, gas) in STEPS.items():
        for _ in range(dur):
            r = dict(wafer_id=wafer_id, time_s=t, step=step,
                     temp=temp + drift + rng.normal(0, 0.3),
                     pressure=pres + rng.normal(0, 0.5),
                     rf_power=rf + rng.normal(0, 3) if rf else 0.0,
                     gas_flow=gas + rng.normal(0, 1))
            if fault == "rf_drop" and step == "main_etch" and t > 30:
                r["rf_power"] *= 0.85
            if fault == "pressure_spike" and step == "over_etch":
                r["pressure"] += 8
            rows.append(r)
            t += 1
    return rows


def generate(n_wafers=100, seed=42, drift_per_wafer=0.02):
    rng = np.random.default_rng(seed)
    data, labels = [], []
    for i in range(n_wafers):
        wafer_id = f"W{i:03d}"
        fault = rng.choice(FAULTS, p=FAULT_PROB)
        data += run_wafer(rng, wafer_id, drift=i * drift_per_wafer, fault=fault)  # 장비 열화
        labels.append({"wafer_id": wafer_id, "fault": fault or "normal"})

    trace = pd.DataFrame(data)
    summary = (trace.groupby(["wafer_id", "step"])[["temp", "pressure", "rf_power"]]
               .agg(["mean", "std"]).unstack())
    summary.columns = ["_".join(c) for c in summary.columns]
    summary = summary.join(pd.DataFrame(labels).set_index("wafer_id"))
    return trace, summary


def main():
    parser = argparse.ArgumentParser(description="반도체 식각 설비 가상 데이터 생성")
    parser.add_argument("--wafers", type=int, default=100, help="웨이퍼 수")
    parser.add_argument("--seed", type=int, default=42, help="난수 시드")
    parser.add_argument("--drift", type=float, default=0.02, help="웨이퍼당 온도 드리프트(℃)")
    parser.add_argument("--out", default=Path(__file__).parent / "data", type=Path)
    args = parser.parse_args()

    trace, summary = generate(args.wafers, args.seed, args.drift)
    args.out.mkdir(parents=True, exist_ok=True)
    trace.to_csv(args.out / "trace.csv", index=False)
    summary.to_csv(args.out / "summary.csv")
    print(f"trace {trace.shape}, summary {summary.shape} -> {args.out}")
    print(summary["fault"].value_counts().to_string())


if __name__ == "__main__":
    main()
