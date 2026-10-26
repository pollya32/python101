"""
엑셀 붙여넣기 상관분석 시스템
==============================
설치: pip install -r requirements.txt
실행: streamlit run 상관분석.py
사용: 엑셀에서 표(머리글 포함)를 복사(Ctrl+C) → 입력창에 붙여넣기(Ctrl+V) → 끝!
"""

import io
import re
from itertools import combinations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

st.set_page_config(page_title="상관분석 시스템", page_icon="📊", layout="wide")


# ── 샘플 데이터 ──────────────────────────────────────────────────────────────

SAMPLE = """일자\t온도(℃)\t습도(%)\t광고비\t방문자수\t매출\t불량률
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


def to_number(s: pd.Series) -> pd.Series:
    """'1,200,000' · '45%' · '₩3,000' · '(500)' 같은 엑셀 표기를 숫자로 변환."""
    t = s.astype(str).str.strip()
    t = t.str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # 회계식 음수 (500)
    t = t.str.replace(_NUM_JUNK, "", regex=True)
    t = t.replace({"": np.nan, "-": np.nan, "nan": np.nan, "None": np.nan,
                   "#N/A": np.nan, "#DIV/0!": np.nan, "#VALUE!": np.nan})
    return pd.to_numeric(t, errors="coerce")


def classify_columns(df: pd.DataFrame, threshold: float = 0.8):
    """값의 80% 이상이 숫자로 바뀌면 수치형, 아니면 범주형."""
    numeric, categorical, out = [], [], pd.DataFrame(index=df.index)
    for c in df.columns:
        nonblank = df[c].notna() & (df[c].astype(str).str.strip() != "")
        conv = to_number(df[c])
        ratio = conv[nonblank].notna().mean() if nonblank.any() else 0
        if ratio >= threshold and conv.nunique() > 1:
            out[c] = conv
            numeric.append(c)
        else:
            out[c] = df[c]
            n_unique = df[c].nunique()
            # 값이 모두 다른 열(ID·날짜)은 범주로 보지 않음
            if 1 < n_unique < nonblank.sum() and n_unique <= max(20, len(df) // 2):
                categorical.append(c)
    return out, numeric, categorical


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
    # 여러 쌍을 동시에 검정하므로 Benjamini-Hochberg 보정 p값도 함께 제공
    res["보정 p-value"] = stats.false_discovery_control(res["p-value"].fillna(1))
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


def to_excel(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, d in sheets.items():
            d.to_excel(w, sheet_name=name[:31])
    return buf.getvalue()


# ── 화면: 입력 ───────────────────────────────────────────────────────────────

st.title("📊 엑셀 붙여넣기 상관분석")
st.caption("엑셀에서 머리글 포함 범위를 복사(Ctrl+C) → 아래에 붙여넣기(Ctrl+V). 첫 행은 항목명으로 사용합니다.")

with st.sidebar:
    st.header("⚙️ 설정")
    method_label = st.radio("상관계수 방법", list(METHODS), help=(
        "피어슨: 직선 관계 / 스피어만: 한쪽이 늘면 다른 쪽도 늘어나는 단조 관계, 이상치에 강함 / "
        "켄달: 표본이 적을 때 안정적"))
    method = METHODS[method_label]
    orient = st.radio("항목 방향", ["열 = 항목 (기본)", "행 = 항목 (전치)"],
                      help="항목이 행 방향(첫 열에 항목명)으로 나열된 표라면 '행 = 항목'을 선택하세요.")
    min_abs = st.slider("표시할 최소 |상관계수|", 0.0, 1.0, 0.0, 0.05)
    only_sig = st.checkbox("유의한 쌍만 보기 (보정 p < 0.05)")

tab_paste, tab_file = st.tabs(["📋 붙여넣기", "📁 파일 업로드"])
with tab_paste:
    if st.button("샘플 데이터 넣기"):
        st.session_state["raw"] = SAMPLE
    raw = st.text_area("데이터 붙여넣기", key="raw", height=200,
                       placeholder="여기에 엑셀 표를 붙여넣으세요…")
with tab_file:
    up = st.file_uploader("엑셀/CSV 파일", type=["xlsx", "xlsm", "xls", "csv", "txt", "tsv"])

try:
    if up is not None:
        df_raw = read_uploaded(up)
    elif raw and raw.strip():
        df_raw = read_pasted(raw)
    else:
        st.info("👆 데이터를 붙여넣거나 '샘플 데이터 넣기'를 눌러 시작하세요.")
        st.stop()
except Exception as e:  # noqa: BLE001 — 사용자 입력 오류는 화면에 안내
    st.error(f"데이터를 읽지 못했습니다: {e}")
    st.stop()

df_raw = df_raw.dropna(how="all").dropna(axis=1, how="all")
df_raw.columns = [str(c).strip() for c in df_raw.columns]
if orient.startswith("행"):
    df_raw = df_raw.set_index(df_raw.columns[0]).T.reset_index(names="구분")

df, num_cols, cat_cols = classify_columns(df_raw)

# ── 화면: 데이터 요약 ────────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
c1.metric("행(관측치)", len(df))
c2.metric("전체 항목", len(df.columns))
c3.metric("수치형 항목", len(num_cols))
c4.metric("범주형 항목", len(cat_cols))

with st.expander("🔍 데이터 미리보기 · 항목 인식 결과", expanded=False):
    st.dataframe(df, width="stretch", height=250)
    st.write("**수치형:**", ", ".join(num_cols) or "없음")
    st.write("**범주형:**", ", ".join(cat_cols) or "없음")
    skipped = [c for c in df.columns if c not in num_cols + cat_cols]
    if skipped:
        st.write("**분석 제외 (ID·날짜·고유값 등):**", ", ".join(skipped))

selected = st.multiselect("분석할 수치형 항목", num_cols, default=num_cols)
if len(selected) < 2:
    st.warning("상관분석에는 수치형 항목이 2개 이상 필요합니다.")
    st.stop()

num = df[selected]
corr = num.corr(method=method)
pairs = pair_table(num, method)

tab_heat, tab_rank, tab_target, tab_scatter, tab_cat = st.tabs(
    ["🟥 상관 히트맵", "🏆 상관 순위", "🎯 타깃 분석", "📈 산점도", "🏷️ 범주형"])

# ── 히트맵 ──
with tab_heat:
    fig = px.imshow(corr, text_auto=".2f", zmin=-1, zmax=1, aspect="auto",
                    color_continuous_scale="RdBu_r")
    fig.update_layout(height=max(400, 45 * len(selected)), margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig, width="stretch")
    st.caption("빨강 = 양(+)의 상관 · 파랑 = 음(-)의 상관 · 흰색 = 상관 없음")

# ── 순위 ──
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

# ── 타깃 ──
with tab_target:
    target = st.selectbox("결과(타깃) 항목", selected, index=len(selected) - 1)
    t = corr[target].drop(target).sort_values(key=abs, ascending=True)
    fig = go.Figure(go.Bar(x=t.values, y=t.index, orientation="h",
                           marker_color=np.where(t.values >= 0, "#d6604d", "#4393c3"),
                           text=[f"{v:+.2f}" for v in t.values], textposition="outside"))
    fig.update_layout(xaxis=dict(range=[-1.1, 1.1], title="상관계수"),
                      height=max(300, 40 * len(t)), margin=dict(l=0, r=0, t=30, b=0),
                      title=f"'{target}'에 영향을 주는(함께 움직이는) 항목")
    st.plotly_chart(fig, width="stretch")

# ── 산점도 ──
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

# ── 범주형 ──
with tab_cat:
    if not cat_cols:
        st.info("범주형 항목(예: 라인, 지역, 제품군)이 있으면 그룹별 차이를 분석합니다.")
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

# ── 다운로드 ──
st.divider()
st.download_button("📥 분석 결과 엑셀 다운로드",
                   to_excel({"상관행렬": corr, "상관순위": pairs.set_index(["항목 A", "항목 B"]),
                             "원본(정제)": df}),
                   file_name="상관분석_결과.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
