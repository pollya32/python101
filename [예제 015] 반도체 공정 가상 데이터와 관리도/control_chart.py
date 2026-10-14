"""summary.csv로 X-bar 관리도(Shewhart)를 그린다.

- 그릴 항목과 기준 구간은 설정 파일(configs/*.yaml)의 chart 항목에서 읽는다
- 관리 한계: 앞쪽 기준 구간 웨이퍼의 평균 ± 3σ  (Phase I → Phase II 방식)
  웨이퍼당 값이 하나이므로 σ는 이동범위로 추정한다 (σ = 평균 이동범위 / 1.128)
  기준 구간 안의 이탈점은 제외하고 한계를 다시 계산한다 (Phase I 정제)
- 판정 규칙: 관리 한계를 벗어난 점(Rule 1)을 이탈로 표시

출력 (기본):
    output/<equipment>/control_chart.png   관리도 그래프
    output/<equipment>/violations.csv      이탈 웨이퍼 목록
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from config import DEFAULT_CONFIG, data_dir, load_config, output_dir

INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e1e0d9"
SERIES, LIMIT, CRITICAL, SURFACE = "#2a78d6", "#52514e", "#d03b3b", "#fcfcfb"


def control_limits(values, n_base, max_iter=10):
    base = values.iloc[:n_base]
    for _ in range(max_iter):
        center = base.mean()
        sigma = base.diff().abs().mean() / 1.128  # 이동범위 기반 σ
        keep = (base - center).abs() <= 3 * sigma
        if keep.all():
            break
        base = base[keep]  # 기준 구간의 이탈점 제거 후 재계산
    return center - 3 * sigma, center, center + 3 * sigma


def chart_metrics(cfg):
    """설정의 chart.metrics → (summary 컬럼, 제목, 단위) 목록"""
    return [(f"{m['sensor']}_mean_{m['step']}", f"{m['step']} · {m['sensor']}",
             cfg["sensors"][m["sensor"]]["unit"]) for m in cfg["chart"]["metrics"]]


def plot(summary, metrics, n_base, title, out_png):
    x = range(len(summary))
    fig, axes = plt.subplots(len(metrics), 1, figsize=(11, 3.2 * len(metrics)),
                             sharex=True, facecolor=SURFACE, squeeze=False)
    axes = axes[:, 0]
    violations = []

    for ax, (col, label, unit) in zip(axes, metrics):
        y = summary[col]
        lcl, cl, ucl = control_limits(y, n_base)
        out = (y > ucl) | (y < lcl)

        ax.set_facecolor(SURFACE)
        ax.axvspan(-0.5, n_base - 0.5, color=GRID, alpha=0.5, lw=0)
        ax.plot(x, y, color=SERIES, lw=2, marker="o", ms=4, zorder=2)
        ax.scatter([i for i, o in zip(x, out) if o], y[out], s=70, marker="X",
                   color=CRITICAL, edgecolor=SURFACE, linewidth=1.5, zorder=3)

        ax.axhline(cl, color=LIMIT, lw=1, zorder=1)
        # UCL은 선 위, LCL은 선 아래에 붙여서 한계가 좁아도 글자가 겹치지 않게 한다
        for val, name, va, dy in [(ucl, "UCL", "bottom", 2), (lcl, "LCL", "top", -2)]:
            ax.axhline(val, color=LIMIT, lw=1, ls="--", zorder=1)
            ax.annotate(f"{name} {val:.2f}", (1.0, val), xycoords=("axes fraction", "data"),
                        xytext=(-4, dy), textcoords="offset points", ha="right", va=va,
                        fontsize=9, color=INK_2)

        ax.set_title(f"{label} ({unit})  ·  CL {cl:.2f}  ·  out of control: {out.sum()}",
                     loc="left", fontsize=11, color=INK)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.tick_params(colors=INK_2, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)

        for wafer_id, row in summary[out].iterrows():
            violations.append({"wafer_id": wafer_id, "metric": col, "value": round(row[col], 3),
                               "lcl": round(lcl, 3), "ucl": round(ucl, 3), "fault": row["fault"]})

    step = max(1, len(summary) // 10)
    axes[-1].set_xticks(list(x)[::step], summary.index[::step])
    axes[-1].set_xlabel("Wafer", color=INK_2)
    fig.suptitle(f"{title} X-bar control chart   (shaded = baseline for limits, ✕ = beyond ±3σ)",
                 x=0.01, ha="left", fontsize=13, color=INK)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor=SURFACE)
    return pd.DataFrame(violations)


def main():
    parser = argparse.ArgumentParser(description="X-bar 관리도 생성")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="설비 설정 YAML")
    parser.add_argument("--summary", type=Path, default=None,
                        help="summary.csv 경로 (기본: data/<equipment>/summary.csv)")
    parser.add_argument("--base", type=int, default=None,
                        help="관리 한계 계산용 기준 웨이퍼 수 (생략하면 설정 파일 값)")
    parser.add_argument("--out", type=Path, default=None, help="저장 폴더 (기본: output/<equipment>)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    summary = pd.read_csv(args.summary or data_dir(cfg) / "summary.csv", index_col="wafer_id")
    n_base = args.base or cfg["chart"].get("baseline", 25)
    out = args.out or output_dir(cfg)

    out.mkdir(parents=True, exist_ok=True)
    violations = plot(summary, chart_metrics(cfg), n_base, cfg.get("title", cfg["equipment"]),
                      out / "control_chart.png")
    violations.to_csv(out / "violations.csv", index=False)

    print(f"관리도 저장: {out / 'control_chart.png'}")
    print(f"이탈 {len(violations)}건 -> {out / 'violations.csv'}")
    if not violations.empty:
        print(violations.groupby(["metric", "fault"]).size().to_string())


if __name__ == "__main__":
    main()
