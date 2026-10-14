"""summary.csv로 X-bar 관리도(Shewhart)를 그린다.

- 관리 한계: 앞쪽 기준 구간(기본 25장)의 평균 ± 3σ  (Phase I → Phase II 방식)
  기준 구간 안의 이탈점은 제외하고 한계를 다시 계산한다 (Phase I 정제)
- 판정 규칙: 관리 한계를 벗어난 점(Rule 1)을 이탈로 표시

출력:
    output/control_chart.png   관리도 그래프
    output/violations.csv      이탈 웨이퍼 목록
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).parent

# 관리할 항목: (summary 컬럼, 그래프 제목, 단위)
METRICS = [
    ("temp_mean_main_etch", "Main etch temperature", "°C"),
    ("rf_power_mean_main_etch", "Main etch RF power", "W"),
    ("pressure_mean_over_etch", "Over etch pressure", "mTorr"),
]

INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e1e0d9"
SERIES, LIMIT, CRITICAL, SURFACE = "#2a78d6", "#52514e", "#d03b3b", "#fcfcfb"


def control_limits(values, n_base, max_iter=10):
    base = values.iloc[:n_base]
    for _ in range(max_iter):
        center, sigma = base.mean(), base.std()
        keep = (base - center).abs() <= 3 * sigma
        if keep.all():
            break
        base = base[keep]  # 기준 구간의 이탈점 제거 후 재계산
    return center - 3 * sigma, center, center + 3 * sigma


def plot(summary, n_base, out_png):
    x = range(len(summary))
    fig, axes = plt.subplots(len(METRICS), 1, figsize=(11, 3.2 * len(METRICS)),
                             sharex=True, facecolor=SURFACE)
    violations = []

    for ax, (col, title, unit) in zip(axes, METRICS):
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

        ax.set_title(f"{title} ({unit})  ·  CL {cl:.2f}  ·  out of control: {out.sum()}",
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
    fig.suptitle("Etch tool X-bar control chart   (shaded = baseline for limits, ✕ = beyond ±3σ)",
                 x=0.01, ha="left", fontsize=13, color=INK)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, facecolor=SURFACE)
    return pd.DataFrame(violations)


def main():
    parser = argparse.ArgumentParser(description="X-bar 관리도 생성")
    parser.add_argument("--summary", type=Path, default=HERE / "data" / "summary.csv")
    parser.add_argument("--base", type=int, default=25, help="관리 한계 계산용 기준 웨이퍼 수")
    parser.add_argument("--out", type=Path, default=HERE / "output")
    args = parser.parse_args()

    summary = pd.read_csv(args.summary, index_col="wafer_id")
    args.out.mkdir(parents=True, exist_ok=True)
    violations = plot(summary, args.base, args.out / "control_chart.png")
    violations.to_csv(args.out / "violations.csv", index=False)

    print(f"관리도 저장: {args.out / 'control_chart.png'}")
    print(f"이탈 {len(violations)}건 -> {args.out / 'violations.csv'}")
    if not violations.empty:
        print(violations.groupby(["metric", "fault"]).size().to_string())


if __name__ == "__main__":
    main()
