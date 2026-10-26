"""샘플 데이터 (모든 분석에 '심어 둔 사실'이 들어 있어 결과가 맞는지 확인용으로도 씀)."""

import numpy as np
import pandas as pd


# ── 샘플 데이터 ──────────────────────────────────────────────────────────────

SAMPLE_BASIC = """일자\t온도(℃)\t습도(%)\t광고비\t방문자수\t매출\t불량률
2024-01-01\t-2.1\t45%\t1,200,000\t320\t4,150,000\t3.2%
2024-01-02\t-0.5\t50%\t1,500,000\t410\t5,020,000\t2.9%
2024-01-03\t1.2\t48%\t900,000\t280\t3,610,000\t3.5%
2024-01-04\t3.4\t55%\t2,000,000\t530\t6,480,000\t2.7%
2024-01-05\t5.0\t60%\t1,800,000\t500\t6,120,000\t2.5%
2024-01-06\t6.8\t62%\t2,500,000\t650\t7,900,000\t2.6%
2024-01-07\t4.1\t58%\t700,000\t240\t3,100,000\t3.0%
2024-01-08\t2.2\t52%\t1,100,000\t330\t4,300,000\t3.4%
2024-01-09\t8.5\t70%\t2,800,000\t720\t8,800,000\t2.1%
2024-01-10\t9.9\t72%\t3,000,000\t760\t9,150,000\t1.9%
2024-01-11\t7.3\t66%\t1,600,000\t470\t5,700,000\t2.4%
2024-01-12\t0.4\t47%\t1,000,000\t300\t3,900,000\t3.6%
2024-01-13\t-1.8\t44%\t1,300,000\t350\t4,400,000\t3.3%
2024-01-14\t10.5\t75%\t2,200,000\t610\t7,300,000\t1.8%
2024-01-15\t11.2\t78%\t2,600,000\t690\t8,350,000\t1.7%"""


def sample_fdc() -> tuple[str, str]:
    """세로형 Wafer FDC (LOT|WAFER|Step|설비|진행일시|파라미터|값) + 별도 Wafer 수율표.
    LOT 단위 원인: ETCH-03 압력 오프셋, LOT 23~25 ESC 온도 드리프트
    Wafer 단위 원인: 1~2번 슬롯 Dep_Rate 저하 (first wafer effect)
    챔버 불일치(수율 무관): CVD-02 Heater_Temp 산포 큼 → 3/6 Heater 교체 후 정상화 (부품 비용 샘플과 연계)"""
    rng = np.random.default_rng(7)
    n_lot, n_wf = 30, 10
    lots = [f"LOT{2403001 + i}" for i in range(n_lot)]
    t0 = pd.date_range("2024-03-01", periods=n_lot, freq="8h")
    etch_eq = rng.choice(["ETCH-01", "ETCH-02", "ETCH-03"], n_lot)
    dep_eq = rng.choice(["CVD-01", "CVD-02"], n_lot)
    lot_temp = rng.normal(60, 1.0, n_lot)
    lot_temp[22:25] += 5
    lot_press = rng.normal(30, 0.8, n_lot) + np.where(etch_eq == "ETCH-03", 1.2, 0)
    lot_rf = rng.normal(1500, 15, n_lot)
    slot = np.arange(1, n_wf + 1)
    rows, res = [], []
    for i, lot in enumerate(lots):
        rf = lot_rf[i] + rng.normal(0, 3, n_wf)
        press = lot_press[i] + rng.normal(0, 0.2, n_wf)
        temp = lot_temp[i] + rng.normal(0, 0.3, n_wf)
        dep = rng.normal(50, 0.5, n_wf) - np.where(slot <= 2, 2.5, 0)
        values = {"ETCH": {"RF_Power": rf, "Chamber_Press": press, "ESC_Temp": temp,
                           "Gas_Flow": rng.normal(200, 3, n_wf),
                           "EPD_Time": 45 + 0.02 * (rf - 1500) + rng.normal(0, 1, n_wf)},
                  "DEP": {"Heater_Temp": 400 + rng.normal(0, 1, n_wf) * (5 if dep_eq[i] == "CVD-02" and i < 15 else 2),
                          "Dep_Rate": dep}}
        tools = {"ETCH": etch_eq[i], "DEP": dep_eq[i]}
        yld = 97 - 0.6 * (temp - 60) - 1.1 * (press - 30) + 0.5 * (dep - 50) + rng.normal(0, 0.5, n_wf)
        for w in range(n_wf):
            wid = f"W{w + 1:02d}"
            for k, (step, plist) in enumerate(values.items()):
                when = (t0[i] + pd.Timedelta(hours=3 * k, minutes=6 * w)).strftime("%Y-%m-%d %H:%M")
                for name, v in plist.items():
                    rows.append((lot, wid, step, tools[step], when, name, round(float(v[w]), 3)))
            res.append((lot, wid, round(float(yld[w]), 2)))
    long = pd.DataFrame(rows, columns=["LOT_ID", "WAFER", "Step", "설비", "진행일시", "파라미터", "값"])
    result = pd.DataFrame(res, columns=["LOT_ID", "WAFER", "수율(%)"]).iloc[:-3]  # 마지막 3장은 결과 미입력
    return long.to_csv(sep="\t", index=False), result.to_csv(sep="\t", index=False)


COST_PARTS = {  # 코드: (부품명, 단가, 기본 교체 주기(일), 사용 설비)
    "P-1001": ("O-Ring", 80_000, 14, "ALL"),
    "P-2001": ("Valve", 650_000, 45, "ALL"),
    "P-3001": ("Heater", 1_800_000, 90, "CVD"),
    "P-4001": ("Showerhead", 4_500_000, 120, "ALL"),
    "P-5001": ("ESC", 12_000_000, 240, "ETCH"),
}


def sample_cost() -> tuple[str, str]:
    """2024년 부품 교체·수리 내역 + 월별 생산량.
    심어 둔 사실: ETCH-03 Showerhead 조기 교체, 12월 Valve 단가 +20%, 10월~ ETCH-01 O-Ring 사용 2배,
    CVD-02 Heater 3/6 교체(FDC 샘플과 연계), ETCH-03 수리비 고가, 단가 오타·금액 불일치·중복 입력 각 1건"""
    rng = np.random.default_rng(11)
    tools = ["ETCH-01", "ETCH-02", "ETCH-03", "CVD-01", "CVD-02"]
    start = pd.Timestamp("2024-01-01")
    rows = []
    for eq in tools:
        for code, (name, price, cycle, use) in COST_PARTS.items():
            if use != "ALL" and not eq.startswith(use):
                continue
            t = 65.0 if (eq, name) == ("CVD-02", "Heater") else rng.uniform(0, cycle)
            while t < 366:
                day = start + pd.Timedelta(days=int(t))
                unit = price * rng.uniform(0.97, 1.03) * (1.2 if name == "Valve" and day.month == 12 else 1)
                qty = int(rng.integers(2, 7)) if name == "O-Ring" else 1
                rows.append([day, eq, code, name, "교체", qty, round(unit, -2)])
                step = cycle * rng.uniform(0.85, 1.15)
                if (eq, name) == ("ETCH-03", "Showerhead"):
                    step *= 0.45
                if (eq, name) == ("ETCH-01", "O-Ring") and day.month >= 10:
                    step *= 0.5
                t += step
    for _ in range(30):
        code = rng.choice(["P-2001", "P-3001", "P-4001"])
        name, price, _, use = COST_PARTS[code]
        eq = rng.choice([t for t in tools if use == "ALL" or t.startswith(use)])
        unit = price * 0.3 * rng.uniform(0.9, 1.1) * (1.6 if eq == "ETCH-03" else 1)
        rows.append([start + pd.Timedelta(days=int(rng.integers(0, 366))), eq, code, name, "수리", 1, round(unit, -2)])
    df = pd.DataFrame(rows, columns=["사용일", "설비", "부품코드", "부품명", "구분", "수량", "단가"])
    df["금액"] = df["수량"] * df["단가"]
    df = df.sort_values("사용일").reset_index(drop=True)
    oring = df.index[df["부품명"] == "O-Ring"]
    df.loc[oring[40], "단가"] *= 10                       # 단가 오타 (0 하나 더)
    df.loc[oring[40], "금액"] = df.loc[oring[40], "수량"] * df.loc[oring[40], "단가"]
    df.loc[oring[60], "금액"] += 500_000                  # 금액 ≠ 수량 × 단가
    df = pd.concat([df, df.loc[[oring[80]]]]).sort_values("사용일")  # 중복 입력
    out = df.assign(사용일=df["사용일"].dt.strftime("%Y-%m-%d"),
                    단가=df["단가"].map("{:,.0f}".format), 금액=df["금액"].map("{:,.0f}".format))

    months = pd.period_range("2024-01", "2024-12", freq="M").astype(str)
    prod = pd.DataFrame([(m, eq, int(rng.normal(2200 if eq == "ETCH-03" else 3000, 150)))
                         for m in months for eq in tools], columns=["월", "설비", "생산량(Wafer)"])
    return out.to_csv(sep="\t", index=False), prod.to_csv(sep="\t", index=False)
