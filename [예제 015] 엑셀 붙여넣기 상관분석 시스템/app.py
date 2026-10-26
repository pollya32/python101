"""
엑셀 붙여넣기 분석 시스템 — 실행 시작점
=======================================
실행: 실행.bat 더블클릭 (Windows) 또는  streamlit run app.py
사용: 엑셀에서 표(머리글 포함)를 복사(Ctrl+C) → 입력창에 붙여넣기(Ctrl+V) → 왼쪽에서 분석 모드 선택

파일 구성
  app.py      화면 (입력 탭, 필터, 모드 선택) ← 이 파일만 실행
  common.py   붙여넣기·정제·열 분류·통계 도우미·엑셀/HTML 리포트
  samples.py  샘플 데이터
  basic.py    상관분석 · 월별 비용 추이 · 그룹 평균 비교
  fdc.py      FDC LOT/Wafer 분석 · 챔버 매칭
  parts.py    부품 비용 분석
"""

import pandas as pd
import streamlit as st

from basic import page_corr, page_group, page_trend
from common import classify_columns, read_pasted, read_uploaded, unique_names
from fdc import page_fdc
from parts import page_parts
from samples import SAMPLE_BASIC, sample_cost, sample_fdc

st.set_page_config(page_title="엑셀 분석 시스템", page_icon="📊", layout="wide")

MODES = ["🔗 상관분석", "🧪 FDC LOT/Wafer 분석", "🔧 부품 비용 분석", "📅 월별 비용 추이", "⚖️ 그룹 평균 비교"]


def load_sample(text: str, mode: str, result: str = "", prod: str = "", fdc: str = ""):
    st.session_state["raw"] = text
    st.session_state["result_raw"] = result
    st.session_state["prod_raw"] = prod
    st.session_state["fdc_aux_raw"] = fdc
    st.session_state["mode"] = mode


# ── 화면: 입력 ───────────────────────────────────────────────────────────────

st.title("📊 엑셀 붙여넣기 분석 시스템")
st.caption("엑셀에서 머리글 포함 범위를 복사(Ctrl+C) → 아래에 붙여넣기(Ctrl+V). 첫 행은 항목명으로 사용합니다.")

with st.sidebar:
    st.header("⚙️ 설정")
    mode = st.radio("분석 모드", MODES, key="mode")
    orient = st.radio("항목 방향", ["열 = 항목 (기본)", "행 = 항목 (전치)"],
                      help="항목이 행 방향(첫 열에 항목명)으로 나열된 표라면 '행 = 항목'을 선택하세요.")

tab_paste, tab_file, tab_result, tab_aux = st.tabs(
    ["📋 붙여넣기", "📁 파일 업로드", "🎯 결과 데이터 (FDC 세로형용, 선택)", "🏭 생산량 / FDC (부품 비용용, 선택)"])
with tab_paste:
    b1, b2, b3 = st.columns(3)
    b1.button("샘플: 일반 데이터", on_click=load_sample, args=(SAMPLE_BASIC, MODES[0]), width="stretch")
    fdc_long, fdc_result = sample_fdc()
    b2.button("샘플: Wafer FDC + 수율", on_click=load_sample, args=(fdc_long, MODES[1], fdc_result),
              width="stretch")
    cost_text, prod_text = sample_cost()
    b3.button("샘플: 부품 비용 (+생산량·FDC)", on_click=load_sample,
              args=(cost_text, MODES[2], "", prod_text, fdc_long), width="stretch")
    raw = st.text_area("데이터 붙여넣기", key="raw", height=200,
                       placeholder="여기에 엑셀 표를 붙여넣으세요…")
with tab_file:
    up = st.file_uploader("엑셀/CSV 파일", type=["xlsx", "xlsm", "xls", "csv", "txt", "tsv"])
with tab_aux:
    a1, a2 = st.columns(2)
    a1.text_area("월별 생산량 (예: 월 | 설비 | 생산량)", key="prod_raw", height=150,
                 placeholder="부품 비용 분석에서 Wafer당 부품비를 계산합니다.")
    a2.text_area("FDC 데이터 (시각 · 설비 · 파라미터 · 값)", key="fdc_aux_raw", height=150,
                 placeholder="부품 교체 전후 FDC 변화를 비교합니다. FDC 세로형 원본 그대로 붙여넣으면 됩니다.")
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
    page_parts(df, num_cols, cat_cols, date_cols)
elif mode == MODES[3]:
    page_trend(df, num_cols, cat_cols, date_cols)
else:
    page_group(df, num_cols, cat_cols)
