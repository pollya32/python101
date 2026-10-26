"""
엑셀 붙여넣기 분석 시스템 (상관 · FDC LOT · 월별 비용 추이 · 그룹 평균 비교)
==========================================================================
설치: pip install -r requirements.txt
실행: streamlit run 상관분석.py
사용: 엑셀에서 표(머리글 포함)를 복사(Ctrl+C) → 입력창에 붙여넣기(Ctrl+V) → 왼쪽에서 분석 모드 선택
"""

import io
import re
import warnings
from itertools import combinations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

st.set_page_config(page_title="엑셀 분석 시스템", page_icon="📊", layout="wide")

MODES = ["🔗 상관분석", "🧪 FDC LOT/Wafer 분석", "📅 월별 비용 추이", "⚖️ 그룹 평균 비교"]


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
    Wafer 단위 원인: 1~2번 슬롯 Dep_Rate 저하 (first wafer effect)"""
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
                  "DEP": {"Heater_Temp": rng.normal(400, 2, n_wf), "Dep_Rate": dep}}
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


def sample_cost() -> str:
    """부품 사용/수리 내역 (월별 상승 추세, ETCH-03 수리비 고가)."""
    rng = np.random.default_rng(11)
    n = 160
    price = {"O-Ring": 80_000, "Heater": 1_800_000, "ESC": 12_000_000,
             "Showerhead": 4_500_000, "Valve": 650_000}
    day = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 366, n), "D")
    part = rng.choice(list(price), n, p=[0.4, 0.15, 0.05, 0.15, 0.25])
    eq = rng.choice(["ETCH-01", "ETCH-02", "ETCH-03", "CVD-01", "CVD-02"], n)
    kind = rng.choice(["교체", "수리"], n, p=[0.7, 0.3])
    qty = np.where(part == "ESC", 1, rng.integers(1, 5, n))
    amt = np.array([price[p] for p in part]) * qty * rng.normal(1, 0.08, n)
    amt *= np.where(kind == "수리", 0.35, 1) * (1 + 0.03 * day.month.to_numpy())
    amt *= np.where((kind == "수리") & (eq == "ETCH-03"), 1.6, 1)
    df = pd.DataFrame({"사용일": day.strftime("%Y-%m-%d"), "설비": eq, "부품명": part,
                       "구분": kind, "수량": qty, "금액(원)": [f"{a:,.0f}" for a in amt]})
    return df.sort_values("사용일").to_csv(sep="\t", index=False)


def load_sample(text: str, mode: str, result: str = ""):
    st.session_state["raw"] = text
    st.session_state["result_raw"] = result
    st.session_state["mode"] = mode


# ── 데이터 파싱 ──────────────────────────────────────────────────────────────

def read_pasted(text: str) -> pd.DataFrame:
    """엑셀 복사본은 탭 구분. 탭이 없으면 쉼표/공백 구분으로 추정."""
    text = text.strip("\n")
    first = text.splitlines()[0]
    if "\t" in first:
        sep = "\t"
    elif ";" in first:
        sep = ";"
    elif "," in first:
        sep = ","
    else:
        sep = r"\s+"
    return pd.read_csv(io.StringIO(text), sep=sep, engine="python", dtype=str,
                       skip_blank_lines=True)


def read_uploaded(file) -> pd.DataFrame:
    if file.name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(file, dtype=str)
    raw = file.getvalue()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return read_pasted(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    raise ValueError("파일 인코딩을 알 수 없습니다 (UTF-8 / CP949 지원).")


_NUM_JUNK = re.compile(r"[,\s₩$€¥원%]")
# 연도 19xx/20xx, 월 1~12 만 날짜로 인정 (1512.3 같은 수치가 날짜로 오인되지 않도록)
_YM = r"((?:19|20)\d{2})"
_M = r"(0?[1-9]|1[0-2])"
_DATE_KO = re.compile(rf"^{_YM}\s*년\s*{_M}\s*월(?:\s*(\d{{1,2}})\s*일)?")
_DATE_NUM = re.compile(rf"^{_YM}[-./]{_M}(?:[-./](\d{{1,2}}))?\.?(?=\s|$)")


def to_number(s: pd.Series) -> pd.Series:
    """'1,200,000' · '45%' · '₩3,000' · '(500)' 같은 엑셀 표기를 숫자로 변환."""
    t = s.astype(str).str.strip()
    t = t.str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # 회계식 음수 (500)
    t = t.str.replace(_NUM_JUNK, "", regex=True)
    t = t.replace({"": np.nan, "-": np.nan, "nan": np.nan, "None": np.nan,
                   "#N/A": np.nan, "#DIV/0!": np.nan, "#VALUE!": np.nan})
    return pd.to_numeric(t, errors="coerce")


def to_date(s: pd.Series) -> pd.Series:
    """'2024-01-05' · '2024.01.05' · '2024/1/5 13:20' · '2024-01' · '2024년 1월' 을 날짜로 변환."""
    def norm(m):
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3] or 1):02d}"
    t = s.astype(str).str.strip()
    t = t.str.replace(_DATE_KO, norm, regex=True).str.replace(_DATE_NUM, norm, regex=True)
    t = t.where(t.str.match(r"^\d{4}-\d{2}-\d{2}"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(t, errors="coerce", format="mixed")


def unique_names(cols) -> list:
    """같은 이름의 항목이 여러 개면 '이름_2', '이름_3' 으로 구분."""
    seen, out = {}, []
    for c in map(str, cols):
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return out


def classify_columns(df: pd.DataFrame, threshold: float = 0.8):
    """값의 80% 이상이 날짜/숫자로 바뀌면 날짜형/수치형, 나머지 중 반복값이 있으면 범주형."""
    numeric, categorical, dates, out = [], [], [], pd.DataFrame(index=df.index)
    for c in df.columns:
        nonblank = df[c].notna() & (df[c].astype(str).str.strip() != "")
        if not nonblank.any():
            continue
        as_date = to_date(df[c])
        if as_date[nonblank].notna().mean() >= threshold:
            out[c] = as_date
            dates.append(c)
            continue
        conv = to_number(df[c])
        if conv[nonblank].notna().mean() >= threshold and conv.nunique() > 1:
            out[c] = conv
            numeric.append(c)
            continue
        out[c] = df[c].where(nonblank).astype(str).str.strip().where(nonblank)
        n_unique = out[c].nunique()
        # 값이 모두 다른 열(LOT ID 등)은 범주로 보지 않음
        if 1 < n_unique < nonblank.sum() and n_unique <= max(30, len(df) // 2):
            categorical.append(c)
    return out, numeric, categorical, dates


# ── 통계 ─────────────────────────────────────────────────────────────────────

METHODS = {"피어슨 (선형)": "pearson", "스피어만 (순위)": "spearman", "켄달 (순위·소표본)": "kendall"}
TESTS = {"pearson": stats.pearsonr, "spearman": stats.spearmanr, "kendall": stats.kendalltau}


def strength(r: float) -> str:
    a = abs(r)
    level = ("매우 강한" if a >= 0.8 else "강한" if a >= 0.6 else
             "중간" if a >= 0.4 else "약한" if a >= 0.2 else "거의 없는")
    if a < 0.2:
        return "상관 거의 없음"
    return f"{level} {'양(+)' if r > 0 else '음(-)'}의 상관"


def bh(p: pd.Series) -> np.ndarray:
    """여러 항목을 동시에 검정할 때의 Benjamini-Hochberg 보정 p값."""
    return stats.false_discovery_control(p.fillna(1))


def pair_table(num: pd.DataFrame, method: str) -> pd.DataFrame:
    rows = []
    for a, b in combinations(num.columns, 2):
        pair = num[[a, b]].dropna()
        if len(pair) < 3:
            continue
        r, p = TESTS[method](pair[a], pair[b])
        rows.append({"항목 A": a, "항목 B": b, "상관계수": r, "p-value": p,
                     "표본수": len(pair), "해석": strength(r)})
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    res["보정 p-value"] = bh(res["p-value"])
    res["유의(5%)"] = np.where(res["보정 p-value"] < 0.05, "✅", "")
    return res.reindex(res["상관계수"].abs().sort_values(ascending=False).index)


def correlation_ratio(cat: pd.Series, val: pd.Series) -> float:
    """범주형 ↔ 수치형 연관도 η (0~1)."""
    d = pd.DataFrame({"c": cat, "v": val}).dropna()
    if d["c"].nunique() < 2:
        return np.nan
    grand = d["v"].mean()
    ss_between = d.groupby("c")["v"].apply(lambda g: len(g) * (g.mean() - grand) ** 2).sum()
    ss_total = ((d["v"] - grand) ** 2).sum()
    return float(np.sqrt(ss_between / ss_total)) if ss_total else np.nan


def parse_float(text: str):
    v = to_number(pd.Series([text])).iloc[0]
    return None if pd.isna(v) else float(v)


def to_excel(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, d in sheets.items():
            d.to_excel(w, sheet_name=name[:31])
    return buf.getvalue()


def download(sheets: dict, filename: str):
    st.divider()
    st.download_button("📥 분석 결과 엑셀 다운로드", to_excel(sheets), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=f"dl_{filename}")


# ── 모드 1: 상관분석 ─────────────────────────────────────────────────────────

def page_corr(df, num_cols, cat_cols):
    with st.sidebar:
        st.subheader("상관분석 옵션")
        method_label = st.radio("상관계수 방법", list(METHODS), help=(
            "피어슨: 직선 관계 / 스피어만: 한쪽이 늘면 다른 쪽도 늘어나는 단조 관계, 이상치에 강함 / "
            "켄달: 표본이 적을 때 안정적"))
        method = METHODS[method_label]
        min_abs = st.slider("표시할 최소 |상관계수|", 0.0, 1.0, 0.0, 0.05)
        only_sig = st.checkbox("유의한 쌍만 보기 (보정 p < 0.05)")

    selected = st.multiselect("분석할 수치형 항목", num_cols, default=num_cols)
    if len(selected) < 2:
        st.warning("상관분석에는 수치형 항목이 2개 이상 필요합니다.")
        return

    num = df[selected]
    corr = num.corr(method=method)
    pairs = pair_table(num, method)

    tab_heat, tab_rank, tab_target, tab_scatter, tab_cat = st.tabs(
        ["🟥 상관 히트맵", "🏆 상관 순위", "🎯 타깃 분석", "📈 산점도", "🏷️ 범주형"])

    with tab_heat:
        fig = px.imshow(corr, text_auto=".2f", zmin=-1, zmax=1, aspect="auto",
                        color_continuous_scale="RdBu_r")
        fig.update_layout(height=max(400, 45 * len(selected)), margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, width="stretch")
        st.caption("빨강 = 양(+)의 상관 · 파랑 = 음(-)의 상관 · 흰색 = 상관 없음")

    with tab_rank:
        view = pairs[pairs["상관계수"].abs() >= min_abs] if not pairs.empty else pairs
        if only_sig and not view.empty:
            view = view[view["유의(5%)"] == "✅"]
        if view.empty:
            st.info("조건에 맞는 항목 쌍이 없습니다.")
        else:
            st.dataframe(view.style.format({"상관계수": "{:+.3f}", "p-value": "{:.4f}",
                                            "보정 p-value": "{:.4f}"})
                         .background_gradient(subset=["상관계수"], cmap="RdBu_r", vmin=-1, vmax=1),
                         width="stretch", hide_index=True)
            top = view.iloc[0]
            st.success(f"가장 강한 관계: **{top['항목 A']} ↔ {top['항목 B']}** "
                       f"(r = {top['상관계수']:+.2f}, {top['해석']})")
        st.caption("※ 상관관계는 인과관계가 아닙니다. 보정 p-value는 여러 쌍을 동시에 검정한 것을 감안한 값(BH-FDR)입니다.")

    with tab_target:
        target = st.selectbox("결과(타깃) 항목", selected, index=len(selected) - 1)
        t = corr[target].drop(target).sort_values(key=abs, ascending=True)
        fig = go.Figure(go.Bar(x=t.values, y=t.index, orientation="h",
                               marker_color=np.where(t.values >= 0, "#d6604d", "#4393c3"),
                               text=[f"{v:+.2f}" for v in t.values], textposition="outside"))
        fig.update_layout(xaxis=dict(range=[-1.1, 1.1], title="상관계수"),
                          height=max(300, 40 * len(t)), margin=dict(l=0, r=0, t=30, b=0),
                          title=f"'{target}'와(과) 함께 움직이는 항목")
        st.plotly_chart(fig, width="stretch")

    with tab_scatter:
        a_col, b_col = st.columns(2)
        x = a_col.selectbox("X축", selected, index=0)
        y = b_col.selectbox("Y축", selected, index=1)
        color = st.selectbox("색상 구분 (범주형, 선택)", ["(없음)"] + cat_cols)
        d = df[[x, y] + ([color] if color != "(없음)" else [])].dropna(subset=[x, y])
        fig = px.scatter(d, x=x, y=y, color=None if color == "(없음)" else color)
        if len(d) >= 2 and x != y:
            slope, intercept = np.polyfit(d[x], d[y], 1)
            xs = np.linspace(d[x].min(), d[x].max(), 50)
            fig.add_trace(go.Scatter(x=xs, y=slope * xs + intercept, mode="lines",
                                     name="추세선", line=dict(dash="dash", color="gray")))
            r = d[x].corr(d[y], method=method)
            st.markdown(f"**r = {r:+.3f}** ({strength(r)}) · 추세선: `{y} = {slope:.4g} × {x} + {intercept:.4g}`")
        st.plotly_chart(fig, width="stretch")

    with tab_cat:
        if not cat_cols:
            st.info("범주형 항목(예: 설비, 라인, 부품명)이 있으면 그룹별 차이를 분석합니다.")
        else:
            eta = pd.DataFrame({c: [correlation_ratio(df[c], df[v]) for v in selected] for c in cat_cols},
                               index=selected)
            st.write("**범주형 ↔ 수치형 연관도 η (0 = 무관, 1 = 범주가 값을 완전히 결정)**")
            st.dataframe(eta.style.format("{:.2f}").background_gradient(cmap="Reds", vmin=0, vmax=1),
                         width="stretch")
            g1, g2 = st.columns(2)
            cat = g1.selectbox("범주 항목", cat_cols)
            val = g2.selectbox("값 항목", selected)
            st.plotly_chart(px.box(df, x=cat, y=val, points="all"), width="stretch")

    download({"상관행렬": corr, "상관순위": pairs.set_index(["항목 A", "항목 B"]), "원본(정제)": df},
             "상관분석_결과.xlsx")


# ── 모드 2: FDC LOT 분석 ─────────────────────────────────────────────────────

SEQ = "진행 순서"


def control_chart(df, param, xcol, group, usl, lsl):
    """I-관리도: σ = 평균 이동범위 / 1.128. 관리한계 이탈 + 중심선 한쪽 연속 7점(드리프트) 표시."""
    d = df.dropna(subset=[param]).copy()
    if xcol != SEQ:
        d = d.dropna(subset=[xcol]).sort_values(xcol)
        d["_x"] = d[xcol]
    else:
        d["_x"] = np.arange(1, len(d) + 1)
    v = d[param]
    center = v.mean()
    sigma = v.diff().abs().mean() / 1.128
    ucl, lcl = center + 3 * sigma, center - 3 * sigma

    side = np.sign(v - center)
    run = side.groupby((side != side.shift()).cumsum()).cumcount() + 1
    d["판정"] = ""
    d.loc[run >= 7, "판정"] = "드리프트(연속 7점)"
    d.loc[(v > ucl) | (v < lcl), "판정"] = "관리한계 이탈"
    if usl is not None:
        d.loc[v > usl, "판정"] = "SPEC 이탈"
    if lsl is not None:
        d.loc[v < lsl, "판정"] = "SPEC 이탈"

    fig = go.Figure(go.Scatter(x=d["_x"], y=v, mode="lines", line=dict(color="lightgray"),
                               showlegend=False, hoverinfo="skip"))
    if group != "(없음)":
        for g, gd in d.groupby(group):
            fig.add_trace(go.Scatter(x=gd["_x"], y=gd[param], mode="markers", name=str(g)))
    else:
        fig.add_trace(go.Scatter(x=d["_x"], y=v, mode="markers", name=param,
                                 marker=dict(color="#4393c3")))
    for label, color, symbol in [("드리프트(연속 7점)", "orange", "diamond"),
                                 ("관리한계 이탈", "red", "x"), ("SPEC 이탈", "darkred", "x")]:
        hit = d[d["판정"] == label]
        if len(hit):
            fig.add_trace(go.Scatter(x=hit["_x"], y=hit[param], mode="markers", name=label,
                                     marker=dict(color=color, symbol=symbol, size=13)))
    lines = [("CL", center, "green", "solid"), ("UCL", ucl, "red", "dash"), ("LCL", lcl, "red", "dash")]
    lines += [("USL", usl, "darkred", "dot")] if usl is not None else []
    lines += [("LSL", lsl, "darkred", "dot")] if lsl is not None else []
    for name, val, color, dash in lines:
        fig.add_hline(y=val, line=dict(color=color, dash=dash, width=1),
                      annotation_text=f"{name} {val:.4g}", annotation_position="right")
    fig.update_layout(height=450, xaxis_title=xcol, yaxis_title=param, margin=dict(l=0, r=80, t=30, b=0))
    return fig, d[d["판정"] != ""].drop(columns="_x"), (center, sigma, ucl, lcl)


FDC_STATS = {"평균": "mean", "최대": "max", "최소": "min", "표준편차": "std",
             "범위": lambda v: v.max() - v.min(), "중앙값": "median", "마지막": "last"}
LOT_KEYS = ("lot",)
WAFER_KEYS = ("wafer", "웨이퍼", "slot", "슬롯", "wf")
STEP_KEYS = ("step", "스텝", "공정", "oper", "process")
PARAM_KEYS = ("param", "파라미터", "항목", "item", "sensor", "센서", "svid")
VALUE_KEYS = ("value", "값", "val", "data")
SLOT = "Slot번호"


def guess(cols, keywords, default=None):
    """열 이름에 키워드가 들어 있는 첫 열."""
    return next((c for c in cols if any(k in str(c).lower() for k in keywords)), default)


def _fmt(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def as_label(s: pd.Series) -> pd.Series:
    """키·열 이름용 문자열. 숫자는 1010.0 → '1010', '07' → '7' 로 통일해 두 표의 키가 맞도록 함."""
    t = s.map(lambda v: _fmt(v) if isinstance(v, (int, float, np.number)) else str(v).strip())
    num = pd.to_numeric(t, errors="coerce")
    return t.where(num.isna(), num.map(lambda v: _fmt(v) if pd.notna(v) else v))


def pivot_long(df, keys, step, param, val, stat_names):
    """세로형(LOT|Wafer|Step|파라미터|값) → 한 행 = 한 LOT(또는 Wafer), 열 = 'Step:파라미터'."""
    d = df.dropna(subset=keys + [param]).copy()
    d[val] = to_number(d[val]) if not pd.api.types.is_numeric_dtype(d[val]) else d[val]
    d = d.dropna(subset=[val])
    for k in keys:
        d[k] = as_label(d[k])
    d["_열"] = as_label(d[param])
    if step:
        d[step] = as_label(d[step])
        d["_열"] = d[step] + ":" + d["_열"]

    parts = []
    for name in stat_names:
        p = d.pivot_table(index=keys, columns="_열", values=val, aggfunc=FDC_STATS[name])
        if len(stat_names) > 1:
            p.columns = [f"{c}[{name}]" for c in p.columns]
        parts.append(p)
    wide = pd.concat(parts, axis=1)
    wide = wide[sorted(wide.columns)]

    # 설비·진행일시 같은 나머지 열: Step마다 다르면 'Step:열'로, 같으면 하나로
    by = keys + ([step] if step else [])
    carry = []
    for c in [c for c in df.columns if c not in keys + [step, param, val]]:
        per = d.groupby(by)[c].first()
        if step and per.groupby(level=keys).nunique().max() > 1:
            u = per.unstack(step)
            u.columns = [f"{x}:{c}" for x in u.columns]
            carry.append(u)
        else:
            carry.append(per.groupby(level=keys).first().rename(c))
    out = pd.concat(carry + [wide], axis=1)
    out.index.names = keys
    return out.reset_index()


def merge_result(wide, keys, text):
    """별도로 붙여넣은 결과표(LOT | Wafer | 수율 …)를 키 기준으로 합침."""
    res = read_pasted(text)
    res.columns = unique_names(str(c).strip() for c in res.columns)
    for pos, (k, words) in enumerate(zip(keys, (LOT_KEYS, WAFER_KEYS))):
        rk = k if k in res.columns else guess(res.columns, words, res.columns[pos])
        res = res.rename(columns={rk: k})
        res[k] = as_label(res[k])
    res = res.drop_duplicates(keys)
    merged = wide.merge(res, on=keys, how="left", suffixes=("", "_결과"))
    matched = int(wide.set_index(keys).index.isin(res.set_index(keys).index).sum())
    return merged, matched


def page_fdc(df, num_cols, cat_cols, date_cols):
    st.subheader("🧪 FDC LOT / Wafer 분석")
    cols = list(df.columns)
    param_guess = guess(cols, PARAM_KEYS)
    is_long = param_guess is not None and param_guess not in num_cols
    shape = st.radio("데이터 형태", ["세로형 (LOT | Wafer | Step | 파라미터 | 값)", "가로형 (한 행 = 한 LOT/Wafer)"],
                     index=0 if is_long else 1, horizontal=True)
    if shape.startswith("가로"):
        st.caption("한 행 = 한 LOT(또는 Wafer). FDC 요약값과 결과값(수율·CD·두께·불량 등)을 함께 붙여넣으세요.")
        fdc_analysis(df, num_cols, cat_cols, date_cols, {})
        return

    st.caption("세로형 FDC를 Wafer(또는 LOT)별 한 행으로 펼칩니다. "
               "수율·계측값은 위 '🎯 결과 데이터' 탭에 따로 붙여넣으면 LOT·Wafer 기준으로 합쳐집니다.")

    def pick(box, label, keys, fallback, optional=False):
        options = (["(없음)"] if optional else []) + cols
        default = guess(cols, keys, fallback)
        return box.selectbox(label, options, index=options.index(default) if default in options else 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    lot = pick(c1, "LOT 열", LOT_KEYS, cols[0])
    wafer = pick(c2, "Wafer 열", WAFER_KEYS, "(없음)", optional=True)
    step = pick(c3, "Step 열", STEP_KEYS, "(없음)", optional=True)
    param = pick(c4, "파라미터 열", PARAM_KEYS, cols[min(2, len(cols) - 1)])
    val = pick(c5, "값 열", VALUE_KEYS, num_cols[-1] if num_cols else cols[-1])
    wafer = None if wafer == "(없음)" else wafer
    step = None if step == "(없음)" else step
    chosen = [c for c in (lot, wafer, step, param, val) if c]
    if len(set(chosen)) < len(chosen):
        st.warning("LOT · Wafer · Step · 파라미터 · 값 열은 서로 달라야 합니다.")
        return
    keys = [lot] + ([wafer] if wafer else [])

    c1, c2, c3, c4 = st.columns(4)
    stat_names = c1.multiselect("요약 방법 (같은 키·Step·파라미터에 값이 여러 개일 때)",
                                list(FDC_STATS), default=["평균"])
    steps = sorted(as_label(df[step].dropna()).unique()) if step else []
    keep_steps = c2.multiselect("Step 선택", steps, default=steps) if step else []
    all_params = sorted(as_label(df[param].dropna()).unique())
    keep_params = c3.multiselect("파라미터 선택", all_params, default=all_params)
    level = c4.radio("분석 단위", ["Wafer", "LOT 평균"], horizontal=True,
                     help="Wafer: 웨이퍼별 FDC ↔ 웨이퍼별 결과 (권장) / LOT 평균: LOT 단위로 평균 낸 뒤 분석"
                     ) if wafer else "LOT"
    if not stat_names or not keep_params or (step and not keep_steps):
        st.warning("요약 방법 · Step · 파라미터를 1개 이상 선택하세요.")
        return

    d = df[as_label(df[param]).isin(keep_params)]
    if step:
        d = d[as_label(d[step]).isin(keep_steps)]
    wide = pivot_long(d, keys, step, param, val, stat_names)
    if wafer:
        # W07 · 7 · LOT001-07 → 슬롯 번호 7 (슬롯 위치 효과 분석용)
        slot = pd.to_numeric(wide[wafer].str.extract(r"(\d+)\D*$")[0], errors="coerce")
        if 1 < slot.nunique() <= 50:
            wide.insert(len(keys), SLOT, slot)

    unit_word = "Wafer" if wafer else "LOT"
    result_text = st.session_state.get("result_raw", "")
    if result_text.strip():
        try:
            wide, matched = merge_result(wide, keys, result_text)
            (st.caption if matched == len(wide) else st.warning)(
                f"결과 데이터 매칭: **{matched} / {len(wide)} {unit_word}**")
        except Exception as e:  # noqa: BLE001
            st.error(f"결과 데이터를 읽지 못했습니다: {e}")
    else:
        st.info("결과값(수율 등)이 없으면 FDC 파라미터끼리만 분석됩니다. '🎯 결과 데이터' 탭에 붙여넣어 보세요.")

    wdf, wnum, wcat, wdate = classify_columns(wide)
    if level == "LOT 평균":
        agg = {c: ("mean" if c in wnum else "first") for c in wdf.columns if c not in (lot, wafer, SLOT)}
        wdf, wnum, wcat, wdate = classify_columns(wdf.groupby(lot, sort=False).agg(agg).reset_index())
    unit = "Wafer" if level == "Wafer" else "LOT"
    wnum = [c for c in wnum if c not in keys]  # 숫자로 된 LOT·Wafer 번호는 파라미터가 아님
    with st.expander(f"🔄 변환 결과: {len(wdf)} {unit} × {len(wnum)} 수치 항목", expanded=False):
        st.dataframe(wdf, width="stretch", height=250)
    fdc_analysis(wdf, wnum, wcat, wdate, {f"{unit}별 변환표": wdf.set_index(lot)},
                 unit=unit, lot_col=lot if unit == "Wafer" else None)


def fdc_analysis(df, num_cols, cat_cols, date_cols, extra_sheets, unit="LOT", lot_col=None):
    if len(num_cols) < 2:
        st.warning("FDC 파라미터와 결과값, 수치형 항목이 2개 이상 필요합니다.")
        return

    c1, c2, c3 = st.columns(3)
    target = c1.selectbox("결과 항목 (수율·계측값)", num_cols, index=len(num_cols) - 1)
    good_high = c2.radio("양호 방향", ["높을수록 양호", "낮을수록 양호"], horizontal=True) == "높을수록 양호"
    group = c3.selectbox("설비/챔버 구분", ["(없음)"] + cat_cols)
    params = st.multiselect("FDC 파라미터", [c for c in num_cols if c != target],
                            default=[c for c in num_cols if c != target])
    if not params:
        st.warning("FDC 파라미터를 1개 이상 선택하세요.")
        return

    y = df[target]
    thr = st.number_input("Good / Bad 기준값 (기본: 중앙값)", value=float(y.median()), format="%.4g",
                          help=f"이 값 이상(또는 이하)인 {unit}를 Good으로 분류합니다.")
    good = (y >= thr) if good_high else (y <= thr)
    bad = ~good & y.notna()
    st.caption(f"Good **{int(good.sum())}** {unit} · Bad **{int(bad.sum())}** {unit}")

    names = ["🔎 Good vs Bad 원인 파라미터", "🏭 설비별 상관", "📉 트렌드·관리도"]
    if lot_col:
        names.append("🧩 LOT 간 · LOT 내 변동")
    tab_gb, tab_eq, tab_cc, *tab_var = st.tabs(names)

    with tab_gb:
        rows = []
        for p in params:
            g, b = df.loc[good, p].dropna(), df.loc[bad, p].dropna()
            if len(g) < 2 or len(b) < 2:
                continue
            _, pv = stats.ttest_ind(g, b, equal_var=False)
            sd = np.sqrt((g.var() + b.var()) / 2)
            pair = df[[p, target]].dropna()
            rows.append({"파라미터": p, "Good 평균": g.mean(), "Bad 평균": b.mean(),
                         "차이(%)": (b.mean() / g.mean() - 1) * 100 if g.mean() else np.nan,
                         "효과크기 d": (b.mean() - g.mean()) / sd if sd else 0.0,
                         "p-value": pv, "결과와 상관 r": pair[p].corr(pair[target], method="spearman")})
        res = pd.DataFrame(rows)
        if res.empty:
            st.info(f"Good/Bad 그룹에 각각 2 {unit} 이상이 있어야 비교할 수 있습니다.")
        else:
            res["보정 p-value"] = bh(res["p-value"])
            ad = res["효과크기 d"].abs()
            res["판정"] = np.select([(ad >= 0.8) & (res["보정 p-value"] < 0.05), ad >= 0.5],
                                  ["🔴 주요 의심", "🟡 관찰"], "")
            res = res.reindex(ad.sort_values(ascending=False).index)
            st.dataframe(res.style.format({"Good 평균": "{:.4g}", "Bad 평균": "{:.4g}", "차이(%)": "{:+.2f}",
                                           "효과크기 d": "{:+.2f}", "p-value": "{:.4f}",
                                           "보정 p-value": "{:.4f}", "결과와 상관 r": "{:+.2f}"})
                         .background_gradient(subset=["효과크기 d"], cmap="RdBu_r", vmin=-2, vmax=2),
                         width="stretch", hide_index=True)
            st.caption("효과크기 d: Bad가 Good보다 표준편차 몇 배만큼 다른지 (|d| ≥ 0.8 큼, 0.5 중간). "
                       "상관 r은 이상치에 강한 스피어만 순위상관.")
            top = st.selectbox("분포 비교할 파라미터", res["파라미터"])
            d = df.loc[y.notna(), [top]].assign(판정=np.where(good[y.notna()], "Good", "Bad"))
            st.plotly_chart(px.box(d, x="판정", y=top, color="판정", points="all",
                                   color_discrete_map={"Good": "#4393c3", "Bad": "#d6604d"}),
                            width="stretch")

    with tab_eq:
        if group == "(없음)":
            st.info("위에서 '설비/챔버 구분' 열을 선택하면 설비별로 따로 상관을 계산합니다.")
        else:
            groups = [g for g, n in df[group].value_counts().items() if n >= 5]
            mat = pd.DataFrame(index=params)
            mat["전체"] = [df[[p, target]].dropna().corr(method="spearman").iloc[0, 1] for p in params]
            mat = mat.reindex(mat["전체"].abs().sort_values(ascending=False).index[:30])
            for g in groups:
                sub = df[df[group] == g]
                mat[str(g)] = [sub[[p, target]].dropna().corr(method="spearman").iloc[0, 1] for p in mat.index]
            fig = px.imshow(mat, text_auto=".2f", zmin=-1, zmax=1, aspect="auto",
                            color_continuous_scale="RdBu_r", title=f"파라미터 ↔ {target} 상관 (설비별)")
            fig.update_layout(height=max(350, 45 * len(mat)))
            st.plotly_chart(fig, width="stretch")
            st.caption("전체로 보면 상관이 있는데 설비별로는 없다면 → 설비 간 오프셋(설비 차이) 때문일 수 있습니다. "
                       f"설비마다 부호가 다르면 설비별로 따로 관리해야 합니다. (5 {unit} 이상인 설비, 전체 상관 상위 30개 파라미터)")
            summary = df.groupby(group)[[target] + params].mean().T
            st.write("**설비별 평균**")
            st.dataframe(summary.style.format("{:.4g}").background_gradient(axis=1, cmap="RdBu_r"),
                         width="stretch")

    with tab_cc:
        c1, c2, c3, c4, c5 = st.columns(5)
        param = c1.selectbox("항목", [p for p in params if p != SLOT] + [target])
        xcol = c2.selectbox("X축", [SEQ] + date_cols, index=1 if date_cols else 0)
        usl = parse_float(c3.text_input("USL (선택)"))
        lsl = parse_float(c4.text_input("LSL (선택)"))
        cd, cc_unit = df, unit
        if lot_col and c5.radio("관리도 점", ["LOT 평균", "Wafer"], help=(
                "같은 LOT 안의 Wafer끼리는 값이 비슷해서, Wafer 점으로 그리면 관리한계가 좁게 잡혀 "
                "LOT이 바뀔 때마다 이탈로 보이기 쉽습니다. 기본은 LOT 평균.")) == "LOT 평균":
            agg = {param: "mean"}
            agg.update({c: "first" for c in {xcol, group} - {SEQ, "(없음)", lot_col}})
            cd, cc_unit = df.groupby(lot_col, sort=False).agg(agg).reset_index(), "LOT"
        fig, flagged, (center, sigma, ucl, lcl) = control_chart(cd, param, xcol, group, usl, lsl)
        st.plotly_chart(fig, width="stretch")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("평균(CL)", f"{center:.4g}")
        m2.metric("σ (이동범위)", f"{sigma:.4g}")
        m3.metric("관리한계", f"{lcl:.4g} ~ {ucl:.4g}")
        m4.metric(f"이상 {cc_unit}", len(flagged))
        if len(flagged):
            st.dataframe(flagged, width="stretch", hide_index=True)
        if usl is not None and lsl is not None and sigma:
            cpk = min(usl - center, center - lsl) / (3 * sigma)
            st.write(f"**Cpk = {cpk:.2f}** ({'양호' if cpk >= 1.33 else '주의' if cpk >= 1 else '부족'})")

    if tab_var:
        with tab_var[0]:
            items = [target] + [p for p in params if p != SLOT]
            share = pd.Series({c: correlation_ratio(df[lot_col], df[c]) ** 2 * 100 for c in items}
                              ).dropna().sort_values()
            fig = go.Figure(go.Bar(x=share.values, y=share.index, orientation="h",
                                   text=[f"{v:.0f}%" for v in share.values], textposition="outside"))
            fig.update_layout(xaxis=dict(range=[0, 110], title="전체 변동 중 LOT 간 차이로 설명되는 비율 (%)"),
                              height=max(300, 35 * len(share)), margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, width="stretch")
            st.caption("높을수록(→100%) LOT 단위 원인: 설비·챔버 상태, PM 시점, 레시피 / "
                       "낮을수록 같은 LOT 안 Wafer끼리 차이: 슬롯 위치, First wafer effect, Wafer 순서")
            if SLOT in df.columns:
                sel = st.selectbox("슬롯별 프로파일", items)
                st.plotly_chart(px.box(df, x=SLOT, y=sel, points="all", title=f"슬롯 번호별 {sel}"),
                                width="stretch")
                r = df[[SLOT, sel]].dropna().corr(method="spearman").iloc[0, 1]
                eta = correlation_ratio(df[SLOT], df[sel])
                st.write(f"슬롯 효과: 슬롯별 차이로 설명되는 비율 **{eta ** 2 * 100:.0f}%** · "
                         f"슬롯 번호와 순위상관 **{r:+.2f}** (특정 슬롯만 튀면 상관은 작고 비율은 큼)")

    download({**extra_sheets, "원본(정제)": df}, "FDC_분석.xlsx")


# ── 모드 3: 월별 비용 추이 ───────────────────────────────────────────────────

PERIODS = {"월": "M", "분기": "Q", "주": "W"}
AGGS = {"합계": "sum", "평균": "mean", "건수": "count"}


def page_trend(df, num_cols, cat_cols, date_cols):
    st.subheader("📅 기간별 비용 추이")
    if not date_cols or not num_cols:
        st.warning("날짜 열(예: 사용일 2024-01-05)과 금액 같은 수치 열이 필요합니다.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    dcol = c1.selectbox("날짜 열", date_cols)
    vcol = c2.selectbox("값 열", num_cols, index=len(num_cols) - 1)
    agg = c3.selectbox("집계", list(AGGS))
    period = c4.selectbox("기간 단위", list(PERIODS))
    group = c5.selectbox("구분 (선택)", ["(없음)"] + cat_cols)

    d = df.dropna(subset=[dcol, vcol]).copy()
    d["기간"] = d[dcol].dt.to_period(PERIODS[period]).astype(str)
    if group == "(없음)":
        pivot = d.groupby("기간")[vcol].agg(AGGS[agg]).to_frame(vcol)
    else:
        pivot = d.pivot_table(index="기간", columns=group, values=vcol, aggfunc=AGGS[agg],
                              fill_value=None if agg == "평균" else 0)
    if pivot.empty:
        st.info("집계할 데이터가 없습니다.")
        return

    total = d.groupby("기간")[vcol].agg(AGGS[agg])
    m1, m2, m3 = st.columns(3)
    m1.metric(f"전체 {agg}", f"{d[vcol].agg(AGGS[agg]):,.0f}")
    m2.metric(f"최근 {period} ({total.index[-1]})", f"{total.iloc[-1]:,.0f}",
              f"{(total.iloc[-1] / total.iloc[-2] - 1) * 100:+.1f}% (전{period} 대비)"
              if len(total) > 1 and total.iloc[-2] else None, delta_color="inverse")
    m3.metric(f"{period}평균", f"{total.mean():,.0f}")

    chart = st.radio("그래프", ["선", "누적 막대"], horizontal=True)
    long = pivot.reset_index().melt(id_vars="기간", var_name="구분", value_name="_값")
    fig = (px.line(long, x="기간", y="_값", color="구분", markers=True) if chart == "선"
           else px.bar(long, x="기간", y="_값", color="구분"))
    fig.update_yaxes(title=f"{vcol} {agg}")
    if group == "(없음)" and len(pivot) >= 3:
        xs = np.arange(len(pivot))
        slope, intercept = np.polyfit(xs, pivot[vcol].fillna(0), 1)
        fig.add_trace(go.Scatter(x=pivot.index, y=slope * xs + intercept, mode="lines", name="추세선",
                                 line=dict(dash="dash", color="gray")))
        st.caption(f"추세: {period}당 평균 **{slope:+,.0f}** 변화")
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, width="stretch")

    tab_tbl, tab_chg, tab_pareto = st.tabs(["📋 집계표", "📈 전기 대비 증감", "📊 파레토"])
    with tab_tbl:
        shown = pivot.copy()
        if group != "(없음)":
            shown["합계"] = shown.sum(axis=1)
        st.dataframe(shown.style.format("{:,.0f}"), width="stretch")
    with tab_chg:
        chg = pivot.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan) * 100
        st.dataframe(chg.style.format("{:+.1f}%", na_rep="-")
                     .background_gradient(cmap="RdBu_r", vmin=-50, vmax=50), width="stretch")
        if group != "(없음)" and len(pivot) > 1:
            delta = (pivot.iloc[-1] - pivot.iloc[-2]).sort_values(ascending=False)
            st.write(f"**{pivot.index[-2]} → {pivot.index[-1]} 증가 상위**")
            st.dataframe(delta.head(5).to_frame("증감").style.format("{:+,.0f}"), width="stretch")
        st.caption("※ 마지막 기간이 아직 진행 중이면 증감이 작게 보일 수 있습니다.")
    with tab_pareto:
        pcol = st.selectbox("파레토 기준", cat_cols) if cat_cols else None
        if pcol is None:
            st.info("범주형 열(부품명·설비 등)이 있으면 비용 파레토를 그립니다.")
        else:
            tot = d.groupby(pcol)[vcol].sum().sort_values(ascending=False)
            cum = tot.cumsum() / tot.sum() * 100
            fig = go.Figure(go.Bar(x=tot.index, y=tot.values, name=f"{vcol} 합계"))
            fig.add_trace(go.Scatter(x=cum.index, y=cum.values, name="누적 %", yaxis="y2",
                                     mode="lines+markers", line=dict(color="#d6604d")))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", range=[0, 105], ticksuffix="%"),
                              height=420, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, width="stretch")
            n80 = int((cum < 80).sum()) + 1
            st.write(f"상위 **{min(n80, len(tot))}개** {pcol}이(가) 전체 {vcol}의 80%를 차지합니다: "
                     + ", ".join(map(str, tot.index[:n80])))

    download({"기간별 집계": pivot}, "기간별_추이.xlsx")


# ── 모드 4: 그룹 평균 비교 ───────────────────────────────────────────────────

def page_group(df, num_cols, cat_cols):
    st.subheader("⚖️ 그룹 평균 비교 (예: 설비별 수리비)")
    if not cat_cols or not num_cols:
        st.warning("그룹 열(설비·부품명 등)과 수치 열(수리비 등)이 필요합니다.")
        return

    c1, c2, c3 = st.columns(3)
    gcol = c1.selectbox("그룹 열", cat_cols)
    vcol = c2.selectbox("값 열", num_cols, index=len(num_cols) - 1)
    sort_by = c3.selectbox("정렬", ["평균", "중앙값", "합계", "건수"])

    d = df[[gcol, vcol]].dropna()
    tbl = d.groupby(gcol)[vcol].agg(["count", "mean", "median", "std", "sum", "min", "max"])
    tbl.columns = ["건수", "평균", "중앙값", "표준편차", "합계", "최소", "최대"]
    tbl["95% 신뢰구간 ±"] = stats.t.ppf(0.975, (tbl["건수"] - 1).clip(lower=1)) * tbl["표준편차"] / np.sqrt(tbl["건수"])
    overall = d[vcol].mean()
    tbl["전체평균 대비(%)"] = (tbl["평균"] / overall - 1) * 100
    tbl = tbl.sort_values(sort_by, ascending=False)

    fig = px.bar(tbl.reset_index(), x=gcol, y="평균", error_y="95% 신뢰구간 ±", text_auto=",.0f")
    fig.add_hline(y=overall, line=dict(dash="dash", color="gray"),
                  annotation_text=f"전체 평균 {overall:,.0f}")
    fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(tbl.style.format("{:,.0f}").format({"전체평균 대비(%)": "{:+.1f}%", "건수": "{:,.0f}"})
                 .background_gradient(subset=["전체평균 대비(%)"], cmap="RdBu_r", vmin=-100, vmax=100),
                 width="stretch")

    samples = [g[vcol].to_numpy() for _, g in d.groupby(gcol) if len(g) >= 2]
    if len(samples) >= 2:
        _, p_anova = stats.f_oneway(*samples)
        _, p_kw = stats.kruskal(*samples)
        verdict = "있습니다 ✅" if min(p_anova, p_kw) < 0.05 else "있다고 보기 어렵습니다"
        st.info(f"그룹 간 차이가 통계적으로 **{verdict}**  ·  ANOVA p = {p_anova:.4f}  ·  "
                f"Kruskal-Wallis p = {p_kw:.4f} (이상치·고액 건에 강한 검정)")

        rows = []
        valid = [g for g, n in tbl["건수"].items() if n >= 2]
        for a, b in combinations(valid, 2):
            _, pv = stats.ttest_ind(d.loc[d[gcol] == a, vcol], d.loc[d[gcol] == b, vcol], equal_var=False)
            rows.append({"그룹 A": a, "그룹 B": b, "평균 차이": tbl.at[a, "평균"] - tbl.at[b, "평균"],
                         "p-value": pv})
        pw = pd.DataFrame(rows)
        if not pw.empty:
            pw["보정 p-value"] = bh(pw["p-value"])
            sig = pw[pw["보정 p-value"] < 0.05].sort_values("보정 p-value")
            with st.expander(f"쌍별 비교: 유의한 차이 {len(sig)}쌍 / 전체 {len(pw)}쌍"):
                st.dataframe(sig.style.format({"평균 차이": "{:+,.0f}", "p-value": "{:.4f}",
                                               "보정 p-value": "{:.4f}"}), width="stretch", hide_index=True)
    st.caption("※ 수리비처럼 고액 건이 섞인 데이터는 평균이 왜곡되기 쉬우니 중앙값과 Kruskal-Wallis 결과도 함께 보세요.")

    tab_box, tab_cross = st.tabs(["📦 분포", "🧩 교차 비교"])
    with tab_box:
        st.plotly_chart(px.box(d, x=gcol, y=vcol, points="all",
                               category_orders={gcol: list(tbl.index)}), width="stretch")
    with tab_cross:
        others = [c for c in cat_cols if c != gcol]
        if not others:
            st.info("범주 열이 2개 이상이면 교차 평균표(예: 설비 × 부품명)를 볼 수 있습니다.")
        else:
            ccol = st.selectbox("교차할 열", others)
            cross = df.pivot_table(index=gcol, columns=ccol, values=vcol, aggfunc="mean")
            fig = px.imshow(cross, text_auto=",.0f", aspect="auto", color_continuous_scale="Reds")
            st.plotly_chart(fig, width="stretch")
            st.caption("빈 칸 = 해당 조합의 데이터 없음")

    download({"그룹별 통계": tbl}, "그룹_평균비교.xlsx")


# ── 화면: 입력 ───────────────────────────────────────────────────────────────

st.title("📊 엑셀 붙여넣기 분석 시스템")
st.caption("엑셀에서 머리글 포함 범위를 복사(Ctrl+C) → 아래에 붙여넣기(Ctrl+V). 첫 행은 항목명으로 사용합니다.")

with st.sidebar:
    st.header("⚙️ 설정")
    mode = st.radio("분석 모드", MODES, key="mode")
    orient = st.radio("항목 방향", ["열 = 항목 (기본)", "행 = 항목 (전치)"],
                      help="항목이 행 방향(첫 열에 항목명)으로 나열된 표라면 '행 = 항목'을 선택하세요.")

tab_paste, tab_file, tab_result = st.tabs(["📋 붙여넣기", "📁 파일 업로드", "🎯 결과 데이터 (FDC 세로형용, 선택)"])
with tab_paste:
    b1, b2, b3 = st.columns(3)
    b1.button("샘플: 일반 데이터", on_click=load_sample, args=(SAMPLE_BASIC, MODES[0]), width="stretch")
    fdc_long, fdc_result = sample_fdc()
    b2.button("샘플: Wafer FDC + 수율", on_click=load_sample, args=(fdc_long, MODES[1], fdc_result),
              width="stretch")
    b3.button("샘플: 부품 비용/수리비", on_click=load_sample, args=(sample_cost(), MODES[2]), width="stretch")
    raw = st.text_area("데이터 붙여넣기", key="raw", height=200,
                       placeholder="여기에 엑셀 표를 붙여넣으세요…")
with tab_file:
    up = st.file_uploader("엑셀/CSV 파일", type=["xlsx", "xlsm", "xls", "csv", "txt", "tsv"])
with tab_result:
    st.text_area("결과값 붙여넣기 (예: LOT_ID | WAFER | 수율 | CD …)", key="result_raw", height=150,
                 placeholder="FDC가 세로형일 때 수율·계측값 표를 여기에 붙여넣으면 LOT·Wafer 기준으로 합쳐집니다.")

try:
    if up is not None:
        df_raw = read_uploaded(up)
    elif raw and raw.strip():
        df_raw = read_pasted(raw)
    else:
        st.info("👆 데이터를 붙여넣거나 샘플 버튼을 눌러 시작하세요.")
        st.stop()
except Exception as e:  # noqa: BLE001 — 사용자 입력 오류는 화면에 안내
    st.error(f"데이터를 읽지 못했습니다: {e}")
    st.stop()

df_raw = df_raw.dropna(how="all").dropna(axis=1, how="all")
df_raw.columns = [str(c).strip() for c in df_raw.columns]
if orient.startswith("행"):
    df_raw = df_raw.set_index(df_raw.columns[0]).T.reset_index(names="구분")
df_raw.columns = unique_names(df_raw.columns)

df, num_cols, cat_cols, date_cols = classify_columns(df_raw.reset_index(drop=True))

# ── 필터 (모든 모드 공통) ──
with st.sidebar:
    st.subheader("🔽 필터")
    fcol = st.selectbox("필터 열 (선택)", ["(없음)"] + cat_cols,
                        help="예: 구분 = '수리'만, 설비 = ETCH 계열만")
    if fcol != "(없음)":
        opts = sorted(df[fcol].dropna().unique(), key=str)
        keep = st.multiselect("포함할 값", opts, default=opts)
        df = df[df[fcol].isin(keep)]
    if date_cols:
        dcol = date_cols[0]
        lo, hi = df[dcol].min(), df[dcol].max()
        if pd.notna(lo) and pd.notna(hi) and lo < hi:
            rng = st.date_input(f"{dcol} 범위", (lo.date(), hi.date()), min_value=lo.date(), max_value=hi.date())
            if isinstance(rng, tuple) and len(rng) == 2:
                df = df[df[dcol].isna() | df[dcol].between(pd.Timestamp(rng[0]),
                                                          pd.Timestamp(rng[1]) + pd.Timedelta(days=1),
                                                          inclusive="left")]

# ── 데이터 요약 ──
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("행", len(df))
c2.metric("전체 항목", len(df.columns))
c3.metric("수치형", len(num_cols))
c4.metric("범주형", len(cat_cols))
c5.metric("날짜형", len(date_cols))

with st.expander("🔍 데이터 미리보기 · 항목 인식 결과", expanded=False):
    st.dataframe(df, width="stretch", height=250)
    st.write("**수치형:**", ", ".join(num_cols) or "없음")
    st.write("**범주형:**", ", ".join(cat_cols) or "없음")
    st.write("**날짜형:**", ", ".join(date_cols) or "없음")
    skipped = [c for c in df.columns if c not in num_cols + cat_cols + date_cols]
    if skipped:
        st.write("**분석 제외 (LOT ID 등 고유값):**", ", ".join(skipped))

if df.empty:
    st.warning("필터 조건에 맞는 데이터가 없습니다.")
elif mode == MODES[0]:
    page_corr(df, num_cols, cat_cols)
elif mode == MODES[1]:
    page_fdc(df, num_cols, cat_cols, date_cols)
elif mode == MODES[2]:
    page_trend(df, num_cols, cat_cols, date_cols)
else:
    page_group(df, num_cols, cat_cols)
