"""공통 기능: 붙여넣기·파일 읽기, 숫자·날짜 정제, 열 분류, 통계 도우미, 엑셀·HTML 리포트 저장."""

import html as html_lib
import io
import re
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
from scipy import stats

# ── 디자인: 부드러운 라운드 ──
# 색은 .streamlit/config.toml 의 [theme] 와 맞춤. 빨강 = 나쁨·증가, 파랑 = 좋음·감소 의미는 유지.
SOFT_COLORS = ["#5B8DEF", "#EF7B6E", "#4CC3A0", "#F5B95F", "#A78BFA", "#4FB8D6", "#F49AC1", "#9AA5B8"]
pio.templates["soft"] = go.layout.Template(layout=dict(
    barcornerradius=8,                       # 둥근 막대
    paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",  # 흰 카드와 같은 배경
    colorway=SOFT_COLORS,
    font=dict(family="'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif", color="#2B2F3A"),
    hoverlabel=dict(bgcolor="white", bordercolor="#E3E7F1", font=dict(color="#2B2F3A")),
    xaxis=dict(gridcolor="#EEF1F7", zeroline=False, linecolor="#E3E7F1"),
    yaxis=dict(gridcolor="#EEF1F7", zeroline=False, linecolor="#E3E7F1"),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
))
# Streamlit 이 import 될 때 기본 템플릿을 "streamlit" 으로 바꾸므로 그 위에 얹음 → 모든 그래프에 적용
pio.templates.default = "streamlit+soft"


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


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _no_formula(v):
    """'=HYPERLINK(…)' 같은 값이 엑셀에서 수식으로 실행되지 않도록 앞에 ' 를 붙여 글자로 저장."""
    return "'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v


def to_excel(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, d in sheets.items():
            d.map(_no_formula).rename(index=_no_formula, columns=_no_formula).to_excel(w, sheet_name=name[:31])
    return buf.getvalue()


def download(sheets: dict, filename: str):
    st.divider()
    st.download_button("📥 분석 결과 엑셀 다운로드", to_excel(sheets), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=f"dl_{filename}")


def guess(cols, keywords, default=None):
    """열 이름에 키워드가 들어 있는 열. 앞쪽 키워드를 우선 (예: '부품명' 이 '부품코드' 보다 먼저)."""
    return next((c for k in keywords for c in cols if k in str(c).lower()), default)


def fmt_number(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def as_label(s: pd.Series) -> pd.Series:
    """키·열 이름용 문자열. 숫자는 1010.0 → '1010', '07' → '7' 로 통일해 두 표의 키가 맞도록 함."""
    t = s.map(lambda v: fmt_number(v) if isinstance(v, (int, float, np.number)) else str(v).strip())
    num = pd.to_numeric(t, errors="coerce")
    return t.where(num.isna(), num.map(lambda v: fmt_number(v) if pd.notna(v) else v))


CHAMBER_KEYS = ("설비", "챔버", "장비", "chamber", "tool", "eqp", "ch")


TOOL_KEYS = ("설비", "장비", "호기", "챔버", "eqp", "equip", "tool", "machine", "chamber")  # 설비 열 이름 키워드 (FDC·부품 비용 공용)


# ── 자동 리포트 ──

REPORT_THEME = "plotly_white+soft"  # Streamlit 테마는 HTML 파일로 내보내면 색이 빠지므로 표준 테마 + 라운드


REPORT_CSS = """body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:1200px;margin:0 auto;
padding:28px 20px;color:#2B2F3A;background:#F7F8FC;line-height:1.55}
h1{margin:0 0 4px;font-size:28px}h2{margin:0 0 12px;font-size:19px;color:#2B2F3A}
.card,section{background:#fff;border:1px solid #E6EAF2;border-radius:20px;padding:20px 24px;margin:16px 0;
box-shadow:0 4px 16px rgba(40,60,120,.06)}
.hero{background:linear-gradient(135deg,#EEF3FF,#FDF1EF);border:none}
.meta,.note{color:#6B7385;font-size:13px}li{margin:5px 0}ul{padding-left:20px}
.judge{display:inline-block;font-size:13px;padding:2px 12px;border-radius:999px;background:#F2F4FA;margin-left:6px;
vertical-align:middle}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:14px}.grid.one{grid-template-columns:1fr}
.fig{min-width:0;overflow:hidden;border:1px solid #EEF1F7;border-radius:16px;padding:6px;background:#fff}
.table-wrap{overflow-x:auto;border:1px solid #E6EAF2;border-radius:14px;margin:12px 0}
table{border-collapse:collapse;font-size:13px;width:100%}td,th{border-bottom:1px solid #EEF1F7;padding:7px 10px;
text-align:right;white-space:nowrap}th{background:#F2F4FA;color:#4A5163;font-weight:600}tr:last-child td{border-bottom:none}
section{page-break-inside:avoid}
@media print{body{background:#fff;padding:0}.card,section{box-shadow:none}.grid{grid-template-columns:1fr 1fr}
.grid.one{grid-template-columns:1fr}}"""


def html_safe(df: pd.DataFrame) -> pd.DataFrame:
    """리포트 HTML 표에 넣기 전에 글자 값의 <, >, & 등을 이스케이프 (데이터 속 태그·스크립트가 실행되지 않도록)."""
    def esc(v):
        return html_lib.escape(v) if isinstance(v, str) else v
    return df.map(esc).rename(index=esc, columns=esc)


def html_page(title, meta, headline, sections, note="") -> str:
    """오프라인으로 열리는 단일 HTML 리포트.
    sections: [{"title", "judge", "lines", "figs": {제목: fig}, "tables": [html], "note"}]"""
    esc = html_lib.escape
    first = [True]

    def fig_html(name, fig):
        # Streamlit 전용 테마는 HTML 파일에서 색이 빠지므로 표준 테마로 고정, 긴 축 이름은 여백 자동 확장
        fig.update_layout(height=max(340, fig.layout.height or 0), margin=dict(l=60, t=50, b=50), title=name,
                          template=REPORT_THEME)
        fig.update_xaxes(automargin=True)
        fig.update_yaxes(automargin=True)
        h = fig.to_html(full_html=False, include_plotlyjs=first[0], default_width="100%",
                        config={"displaylogo": False, "responsive": True})
        first[0] = False
        return f'<div class="fig">{h}</div>'

    parts = [f'<div class="card hero"><h1>{esc(title)}</h1>'
             f'<p class="meta">생성 {datetime.now():%Y-%m-%d %H:%M} · {esc(meta)}</p></div>',
             '<div class="card"><h2>핵심 요약</h2><ul>' + "".join(f"<li>{esc(t)}</li>" for t in headline) + "</ul></div>"]
    for sec in sections:
        judge = f' <span class="judge">{esc(sec["judge"])}</span>' if sec.get("judge") else ""
        lines = "<ul>" + "".join(f"<li>{esc(t)}</li>" for t in sec["lines"]) + "</ul>" if sec.get("lines") else ""
        figs = sec.get("figs") or {}
        cls = "grid" if len(figs) > 1 else "grid one"  # 그래프 하나면 전체 폭
        grid = (f'<div class="{cls}">' + "".join(fig_html(t, f) for t, f in figs.items()) + "</div>") if figs else ""
        tables = "".join(f'<div class="table-wrap">{t}</div>' for t in sec.get("tables") or [])
        sec_note = f"<p class='note'>{esc(sec['note'])}</p>" if sec.get("note") else ""
        parts.append(f"<section><h2>{esc(sec['title'])}{judge}</h2>{lines}{grid}{tables}{sec_note}</section>")
    if note:
        parts.append(f"<p class='note'>{esc(note)}</p>")
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{esc(title)}</title>"
            f"<style>{REPORT_CSS}</style></head><body>{''.join(parts)}</body></html>")


def pattern_share(s: pd.Series, pattern: str) -> float:
    v = s.dropna().astype(str).str.strip()
    return v.str.fullmatch(pattern).mean() if len(v) else 0.0


def looks_tool(s) -> bool:   # ETCH-01, CVD02, EQ_3A
    return pattern_share(s, r"[A-Za-z가-힣]{2,}[-_ ]?\d{1,3}[A-Za-z]?") >= 0.8 and s.nunique() <= 100
