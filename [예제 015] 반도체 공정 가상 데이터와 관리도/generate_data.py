"""설정 파일(YAML) 기반 반도체 설비 FDC 가상 데이터 생성기.

웨이퍼마다 레시피 스텝별 센서 trace(초 단위)를 만들고,
설비 열화(드리프트)와 고장을 주입한다. 설비 종류는 configs/*.yaml로 정의한다.

출력 (기본):
    data/<equipment>/trace.csv    웨이퍼별·초 단위 센서값 (시계열)
    data/<equipment>/summary.csv  웨이퍼별·스텝별 평균/표준편차 + 고장 라벨
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from config import DEFAULT_CONFIG, data_dir, load_config


def apply_fault(value, fault, k, duration):
    """스텝 안에서 k번째 초의 센서값에 고장을 적용한다."""
    if k < fault.get("start", 0):
        return value
    if fault["type"] == "scale":
        return value * fault["value"]
    if fault["type"] == "offset":
        return value + fault["value"]
    return value + fault["value"] * (k + 1) / duration  # ramp


def run_wafer(rng, cfg, wafer_id, index, fault=None):
    sensors = cfg["sensors"]
    rows, t = [], 0
    for step in cfg["steps"]:
        for k in range(step["duration"]):
            r = dict(wafer_id=wafer_id, time_s=t, step=step["name"])
            for name, spec in sensors.items():
                setpoint = step[name]
                if setpoint == 0:  # 꺼진 센서
                    r[name] = 0.0
                    continue
                r[name] = setpoint + spec.get("drift", 0) * index + rng.normal(0, spec["noise"])
            if fault and fault["step"] == step["name"]:
                s = fault["sensor"]
                r[s] = apply_fault(r[s], fault, k, step["duration"])
            rows.append(r)
            t += 1
    return rows


def generate(cfg, n_wafers=None, seed=None):
    rng = np.random.default_rng(seed)
    n_wafers = n_wafers or cfg.get("wafers", 100)
    faults = cfg.get("faults", [])
    choices = [None] + faults
    probs = [1 - sum(f["prob"] for f in faults)] + [f["prob"] for f in faults]

    data, labels = [], []
    for i in range(n_wafers):
        wafer_id = f"W{i:03d}"
        fault = choices[rng.choice(len(choices), p=probs)]
        data += run_wafer(rng, cfg, wafer_id, i, fault)
        labels.append({"wafer_id": wafer_id, "fault": fault["name"] if fault else "normal"})

    trace = pd.DataFrame(data)
    summary = (trace.groupby(["wafer_id", "step"])[list(cfg["sensors"])]
               .agg(["mean", "std"]).unstack())
    summary.columns = ["_".join(c) for c in summary.columns]
    summary = summary.join(pd.DataFrame(labels).set_index("wafer_id"))
    return trace, summary


def main():
    parser = argparse.ArgumentParser(description="반도체 설비 가상 데이터 생성")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="설비 설정 YAML")
    parser.add_argument("--wafers", type=int, default=None, help="웨이퍼 수 (생략하면 설정 파일 값)")
    parser.add_argument("--seed", type=int, default=None,
                        help="난수 시드 (생략하면 매번 랜덤, 같은 값을 주면 같은 데이터 재현)")
    parser.add_argument("--out", type=Path, default=None, help="저장 폴더 (기본: data/<equipment>)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.seed is None:
        args.seed = int(np.random.SeedSequence().entropy % 1_000_000)  # 매번 새로운 시드
    out = args.out or data_dir(cfg)

    trace, summary = generate(cfg, args.wafers, args.seed)
    out.mkdir(parents=True, exist_ok=True)
    trace.to_csv(out / "trace.csv", index=False)
    summary.to_csv(out / "summary.csv")
    print(f"[{cfg['equipment']}] seed {args.seed}  (같은 데이터를 다시 만들려면: --seed {args.seed})")
    print(f"trace {trace.shape}, summary {summary.shape} -> {out}")
    print(summary["fault"].value_counts().to_string())


if __name__ == "__main__":
    main()
