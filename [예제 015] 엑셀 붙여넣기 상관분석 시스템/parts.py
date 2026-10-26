"""부품 비용 분석 모드: 증감 원인 분해, 교체 주기, 이상 비용 건, 연간 예측, Wafer당 비용, FDC 연계, 자동 리포트."""

from datetime import datetime
from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

from common import (
    CHAMBER_KEYS,
    REPORT_THEME,
    TOOL_KEYS,
    as_label,
    classify_columns,
    download,
    guess,
    html_page,
    html_safe,
    looks_tool,
    parse_float,
    pattern_share,
    read_pasted,
    to_excel,
    to_number,
    unique_names,
)
from fdc import auto_map_fdc


# ── 모드: 부품 비용 분석 ─────────────────────────────────────────────────────

COST_ROLES = {  # 역할: (이름 키워드 — 앞쪽 우선, 필수 여부)
    "날짜": (("사용일", "교체일", "출고일", "일자", "날짜", "일시", "date", "day"), True),
    "설비": (TOOL_KEYS, False),
    "부품": (("부품명", "품명", "자재명", "품목", "part name", "description", "desc", "부품", "자재", "part", "item"), True),
    "부품코드": (("부품코드", "자재코드", "품번", "코드", "code", "p/n", "part no", "pn"), False),
    "구분": (("구분", "유형", "작업", "type", "kind", "category"), False),
    "수량": (("수량", "q'ty", "qty", "quantity", "개수", "count"), False),
    "단가": (("단가", "unit price", "u/p", "unit", "price"), False),
    "금액": (("금액", "총액", "amount", "total", "비용", "cost", "합계", "sum"), False),
}


WON = "{:,.0f}"


KIND_WORDS = ("교체", "교환", "수리", "점검", "보수", "정비", "replace", "repair", "pm", "rework")


def _looks_code(s) -> bool:   # P-1001, 12345-678, AB1234
    return pattern_share(s, r"[A-Za-z]{0,4}[-_]?\d[\w\-./]{2,}") >= 0.8


def _looks_kind(s) -> bool:   # 교체 / 수리 / 점검
    vals = s.dropna().astype(str).str.lower().unique()
    return len(vals) <= 6 and any(w in v for v in vals for w in KIND_WORDS)


def auto_map_cost(df, num_cols, date_cols):
    """열 이름 키워드로 먼저 찾고, 못 찾거나 이름과 값이 안 맞으면 값 패턴으로 추정.
    반환: (역할 → 열, 역할 → 찾은 방법)"""
    m, how, used = {r: None for r in COST_ROLES}, {}, set()

    def take(role, col, why):
        m[role], how[role] = col, why
        used.add(col)

    def free(pool):
        return [c for c in pool if c not in used]

    if date_cols:
        take("날짜", guess(date_cols, COST_ROLES["날짜"][0], date_cols[0]), "이름" if guess(date_cols, COST_ROLES["날짜"][0]) else "값 패턴")

    # 숫자 열: 금액 = 수량 × 단가 관계가 있으면 가장 확실
    nums = [c for c in num_cols if df[c].notna().sum() >= 3]
    for a, b in combinations(nums, 2):
        for t in nums:
            if t in (a, b) or m["금액"]:
                continue
            prod = df[a] * df[b]
            ok = ((df[t] - prod).abs() <= df[t].abs() * 0.01 + 1).mean()
            if ok >= 0.8:
                qa = (df[a].dropna() % 1 == 0).all() and df[a].median() <= df[b].median()
                q, u = (a, b) if qa else (b, a)
                for role, col in (("금액", t), ("수량", q), ("단가", u)):
                    # 이름도 같은 결론이면 '이름'으로 표시
                    take(role, col, "이름" if guess([col], COST_ROLES[role][0]) else "금액 = 수량 × 단가 관계")
    for role in ("금액", "수량", "단가"):
        if not m[role]:
            col = guess(free(nums), COST_ROLES[role][0])
            if col:
                take(role, col, "이름")
    if not m["수량"]:
        small_int = [c for c in free(nums) if (df[c].dropna() % 1 == 0).all() and df[c].median() <= 50]
        if small_int:
            take("수량", small_int[0], "값 패턴 (작은 정수)")
    if not m["금액"] and free(nums):
        take("금액", max(free(nums), key=lambda c: df[c].median()), "값 패턴 (가장 큰 금액)")

    # 글자 열
    texts = [c for c in df.columns if c not in num_cols + date_cols]

    def pick(role, check):
        named = guess(free(texts), COST_ROLES[role][0])
        by_value = [c for c in free(texts) if check(df[c])]
        if named and (check(df[named]) or not by_value):
            take(role, named, "이름")
        elif by_value:
            take(role, by_value[0], "값 패턴")

    pick("구분", _looks_kind)
    pick("설비", looks_tool)
    pick("부품코드", _looks_code)
    named = guess(free(texts), COST_ROLES["부품"][0])
    plain = [c for c in free(texts) if not _looks_code(df[c]) and df[c].nunique() > 1]
    if named and not _looks_code(df[named]):
        take("부품", named, "이름")
    elif plain:  # 이름이 없거나 코드처럼 생겼으면 값 종류가 가장 많은 글자 열
        take("부품", max(plain, key=lambda c: df[c].nunique()), "값 패턴")
    elif m["부품코드"]:
        m["부품"], how["부품"] = m["부품코드"], "부품코드로 대체"
    return m, how


def _num(s: pd.Series) -> pd.Series:
    return s if pd.api.types.is_numeric_dtype(s) else to_number(s)


def won(v) -> str:
    """1,234,000원 → '123.4만원' / '1.23억원' 처럼 읽기 쉬운 금액."""
    a = abs(v)
    if a < 0.5:
        return "0원"
    if a >= 1e8:
        return f"{v / 1e8:,.2f}억원"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def cost_frame(df, m) -> pd.DataFrame:
    """매핑된 열로 표준 비용표(날짜·설비·부품·구분·수량·단가·금액)를 만듦. 금액/단가는 빠지면 서로 계산."""
    c = pd.DataFrame(index=df.index)
    c["날짜"] = df[m["날짜"]]
    c["설비"] = df[m["설비"]].astype(str).replace("nan", "(미지정)") if m["설비"] else "(전체)"
    c["부품"] = df[m["부품"]].astype(str)
    if m["부품코드"] and m["부품코드"] != m["부품"]:
        c["부품코드"] = df[m["부품코드"]].astype(str)
    c["구분"] = df[m["구분"]].astype(str) if m["구분"] else "(구분 없음)"
    qty = _num(df[m["수량"]]) if m["수량"] else pd.Series(1.0, index=df.index)
    price = _num(df[m["단가"]]) if m["단가"] else None
    amount = _num(df[m["금액"]]) if m["금액"] else None
    c["수량"] = qty
    c["금액"] = amount if amount is not None else qty * price
    c["단가"] = price if price is not None else c["금액"] / qty.replace(0, np.nan)
    if price is not None and amount is not None:
        c["_계산금액"] = qty * price
    return c.dropna(subset=["날짜", "금액"])


# 1) 증감 원인 분해

def decompose(c, pa, pb):
    """기간 A → B 비용 증감을 단가·수량·구성(비싼 부품 비중)·신규·중단 효과로 분해.
    교체와 수리는 단가가 크게 달라 섞이면 비율 변화가 단가 효과로 잘못 잡히므로 '부품 · 구분' 단위로 계산."""
    agg = dict(수량=("수량", "sum"), 금액=("금액", "sum"))
    item = c["부품"] + (" · " + c["구분"] if c["구분"].nunique() > 1 else "")
    c = c.assign(항목=item)
    t = (c[c["기간"] == pa].groupby("항목").agg(**agg).add_suffix("_A")
         .join(c[c["기간"] == pb].groupby("항목").agg(**agg).add_suffix("_B"), how="outer").fillna(0))
    t["단가_A"] = t["금액_A"] / t["수량_A"].replace(0, np.nan)
    t["단가_B"] = t["금액_B"] / t["수량_B"].replace(0, np.nan)
    both = (t["수량_A"] > 0) & (t["수량_B"] > 0)
    tb = t[both]
    t["단가 효과"] = ((tb["단가_B"] - tb["단가_A"]) * tb["수량_B"]).reindex(t.index).fillna(0)
    t["수량 효과"] = (tb["단가_A"] * (tb["수량_B"] - tb["수량_A"])).reindex(t.index).fillna(0)
    t["증감"] = t["금액_B"] - t["금액_A"]
    t["주요 요인"] = np.select(
        [t["수량_A"] == 0, t["수량_B"] == 0, t["단가 효과"].abs() >= t["수량 효과"].abs()],
        ["B에만 사용", "A에만 사용", "단가"], "수량")
    p0 = (tb["단가_A"] * tb["수량_A"]).sum() / tb["수량_A"].sum() if tb["수량_A"].sum() else 0
    total = {
        f"{pa} 비용": t["금액_A"].sum(),
        "단가 효과": t["단가 효과"].sum(),
        "수량 효과": (tb["수량_B"].sum() - tb["수량_A"].sum()) * p0,
        "구성 효과": (tb["단가_A"] * tb["수량_B"]).sum() - tb["수량_B"].sum() * p0,
        "B에만 사용": t.loc[t["수량_A"] == 0, "금액_B"].sum(),
        "A에만 사용": -t.loc[t["수량_B"] == 0, "금액_A"].sum(),
        f"{pb} 비용": t["금액_B"].sum(),
    }
    return total, t.reindex(t["증감"].abs().sort_values(ascending=False).index)


def waterfall(total):
    keys = list(total)
    fig = go.Figure(go.Waterfall(
        x=keys, y=list(total.values()), measure=["absolute"] + ["relative"] * (len(keys) - 2) + ["total"],
        text=[won(v) for v in total.values()], textposition="outside",
        increasing=dict(marker_color="#EF7B6E"), decreasing=dict(marker_color="#5B8DEF"),
        totals=dict(marker_color="#AEB5C2")))
    fig.update_layout(height=400, showlegend=False, margin=dict(l=0, r=0, t=40, b=0))
    return fig


# 2) 교체 주기

def replacement_cycles(c, kinds, early_pct):
    """교체 이벤트(설비·부품·날짜)별 간격과 부품 기준 주기(중앙값) 대비 비율. 기준의 early_pct% 미만이면 조기 교체."""
    r = c[c["구분"].isin(kinds)]
    ev = (r.assign(교체일=r["날짜"].dt.normalize()).groupby(["설비", "부품", "교체일"])
          .agg(수량=("수량", "sum"), 금액=("금액", "sum")).reset_index())
    ev["간격(일)"] = ev.groupby(["설비", "부품"])["교체일"].diff().dt.days
    ev["부품 기준 주기(일)"] = ev["부품"].map(ev.groupby("부품")["간격(일)"].median())
    ev["기준 대비(%)"] = ev["간격(일)"] / ev["부품 기준 주기(일)"] * 100
    ev["조기 교체"] = ev["기준 대비(%)"] < early_pct

    g = ev.groupby(["설비", "부품"])
    summ = g.agg(교체횟수=("교체일", "count"), 평균주기=("간격(일)", "mean"), 기준주기=("부품 기준 주기(일)", "first"),
                 조기교체=("조기 교체", "sum"), 조기교체비용=("금액", lambda s: s[ev.loc[s.index, "조기 교체"]].sum()),
                 마지막교체=("교체일", "max")).reset_index()
    summ["주기 비율(%)"] = summ["평균주기"] / summ["기준주기"] * 100
    cycle = summ["평균주기"].fillna(summ["기준주기"])
    summ["다음 예상일"] = summ["마지막교체"] + pd.to_timedelta(cycle, unit="D")
    summ.columns = ["설비", "부품", "교체 횟수", "평균 주기(일)", "부품 기준 주기(일)", "조기 교체", "조기 교체 비용",
                    "마지막 교체", "주기 비율(%)", "다음 예상일"]
    return ev, summ


# 3) 이상 비용 건

def cost_anomalies(c) -> pd.DataFrame:
    """입력 오류 후보: 같은 부품·구분 중앙 단가의 2배 이상·절반 이하(+ 강건 z ≥ 3.5), 금액 ≠ 수량×단가, 중복 입력.
    단가 인상이나 설비별 수리비 차이(1.2~1.6배)는 정상 변동으로 보고 제외."""
    g = c.groupby(["부품", "구분"])["단가"]
    med, n = g.transform("median"), g.transform("count")
    mad = g.transform(lambda s: (s - s.median()).abs().median())
    rel = c["단가"] / med - 1
    z = (c["단가"] - med) / (1.4826 * mad.replace(0, np.nan))
    reasons = pd.Series("", index=c.index)
    price_bad = (n >= 4) & ((rel >= 1) | (rel <= -0.5)) & ((z.abs() >= 3.5) | (mad == 0))
    reasons[price_bad] += [f"단가 이상 (같은 부품 중앙값 대비 {v:+.0%}) " for v in rel[price_bad]]
    if "_계산금액" in c:
        diff = c["금액"] - c["_계산금액"]
        bad = diff.abs() > np.maximum(1, c["금액"].abs() * 0.01)
        reasons[bad] += [f"금액 ≠ 수량×단가 (차이 {v:+,.0f}) " for v in diff[bad]]
    dup = c.duplicated(subset=["날짜", "설비", "부품", "구분", "수량", "금액"], keep=False)
    reasons[dup] += "중복 입력 의심 "
    out = c[reasons != ""].assign(사유=reasons[reasons != ""].str.strip(), **{"부품 중앙 단가": med[reasons != ""]})
    return out.drop(columns=[x for x in ("_계산금액", "기간") if x in out]).sort_values("날짜")


# 4) 연간 예측·예산

def year_forecast(c, year, budget):
    y = c[c["날짜"].dt.year == year]
    monthly = y.groupby(y["날짜"].dt.month)["금액"].sum().reindex(range(1, 13))
    last = int(monthly.last_valid_index())
    actual = monthly.loc[:last].fillna(0)
    n = len(actual)
    if n >= 3:
        slope, inter = np.polyfit(np.arange(n), actual.to_numpy(), 1)
        fc = [max(0.0, inter + slope * i) for i in range(n, 12)]
    else:
        fc = [actual.mean()] * (12 - n)
    plan = pd.DataFrame({"월": range(1, 13), "실적": list(actual) + [np.nan] * (12 - n),
                         "예측": [np.nan] * n + fc})
    plan["누적"] = plan["실적"].fillna(plan["예측"]).cumsum()
    info = {"실적 누계": actual.sum(), "마지막 실적 월": last, "단순 연환산": actual.sum() / n * 12,
            "추세 예측 연간": actual.sum() + sum(fc)}
    if budget:
        over = plan.loc[plan["누적"] > budget, "월"]
        info["예산"] = budget
        info["예산 대비(%)"] = info["추세 예측 연간"] / budget * 100
        info["초과 예상 월"] = int(over.iloc[0]) if len(over) else None
    fig = go.Figure()
    fig.add_bar(x=plan["월"], y=plan["실적"], name="월 실적", marker_color="#5B8DEF")
    fig.add_bar(x=plan["월"], y=plan["예측"], name="월 예측", marker_color="#D5DAE3")
    fig.add_scatter(x=plan["월"], y=plan["누적"], name="누적 (실적+예측)", yaxis="y2", mode="lines+markers",
                    line=dict(color="#EF7B6E"))
    if budget:
        fig.add_scatter(x=[1, 12], y=[budget, budget], name="연간 예산", yaxis="y2", mode="lines",
                        line=dict(color="black", dash="dash"))
    fig.update_layout(barmode="stack", height=420, xaxis=dict(title=f"{year}년 월", dtick=1),
                      yaxis=dict(title="월 비용"), yaxis2=dict(title="누적", overlaying="y", side="right"),
                      margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=1.12))
    by_eq = y.groupby("설비")["금액"].sum().to_frame("실적 누계")
    by_eq["비중(%)"] = by_eq["실적 누계"] / by_eq["실적 누계"].sum() * 100
    by_eq["연환산"] = by_eq["실적 누계"] / n * 12
    return plan, info, fig, by_eq.sort_values("실적 누계", ascending=False)


# 5) 생산량 대비 비용

def parse_production(text):
    """월 | (설비) | 생산량 표 → 월·설비별 생산량."""
    raw = read_pasted(text)
    raw.columns = unique_names(str(x).strip() for x in raw.columns)
    p, num, cat, dates = classify_columns(raw)
    if not dates or not num:
        raise ValueError("생산량 표에 날짜(월) 열과 숫자 열이 필요합니다.")
    eq = guess(cat, CHAMBER_KEYS)
    q = guess(num, ("생산", "wafer", "웨이퍼", "투입", "out", "수량"), num[-1])
    out = pd.DataFrame({"월": p[dates[0]].dt.to_period("M").astype(str),
                        "설비": p[eq].astype(str) if eq else "(전체)", "생산량": p[q]})
    return out.groupby(["월", "설비"], as_index=False)["생산량"].sum(), eq is not None


def cost_per_wafer(c, prod, by_eq):
    cost = c.assign(월=c["날짜"].dt.to_period("M").astype(str))
    keys = ["월", "설비"] if by_eq else ["월"]
    cm = cost.groupby(keys, as_index=False)["금액"].sum()
    pm = prod.groupby(keys, as_index=False)["생산량"].sum()
    mm = cm.merge(pm, on=keys, how="inner")
    if not by_eq:
        mm["설비"] = "(전체)"
    mm["원/Wafer"] = mm["금액"] / mm["생산량"].replace(0, np.nan)
    tot = mm.groupby("설비")[["금액", "생산량"]].sum()
    tot["원/Wafer"] = tot["금액"] / tot["생산량"].replace(0, np.nan)
    tot["전체 대비(%)"] = (tot["원/Wafer"] / (tot["금액"].sum() / tot["생산량"].sum()) - 1) * 100
    return mm, tot.sort_values("원/Wafer", ascending=False)


# 6) FDC 연계

def parse_fdc_aux(text) -> pd.DataFrame:
    """FDC 표(세로형 또는 가로형) → 시각 | 설비 | 파라미터 | 값."""
    raw = read_pasted(text)
    raw.columns = unique_names(str(x).strip() for x in raw.columns)
    f, num, cat, dates = classify_columns(raw)
    texts = [x for x in f.columns if x not in num + dates]
    eq = guess(texts, TOOL_KEYS) or next((x for x in texts if looks_tool(f[x])), None)
    if not dates or not eq:
        raise ValueError("FDC 표에 날짜/시각 열과 설비 열이 필요합니다.")
    fm, _ = auto_map_fdc(f.drop(columns=[eq]), num, dates)
    param, step, val = fm["파라미터"], fm["Step"], fm["값"]
    if param and val:
        label = as_label(f[param])
        if step:
            label = as_label(f[step]) + ":" + label
        out = pd.DataFrame({"시각": f[dates[0]], "설비": f[eq].astype(str), "파라미터": label, "값": f[val]})
    else:
        num = [x for x in num if x not in (fm["LOT"], fm["Wafer"])]
        out = (f[[dates[0], eq] + num].rename(columns={dates[0]: "시각", eq: "설비"})
               .melt(id_vars=["시각", "설비"], var_name="파라미터", value_name="값"))
        out["설비"] = out["설비"].astype(str)
    return out.dropna()


def fdc_change(fl, eq, when, days) -> pd.DataFrame:
    """교체일 전 days일 vs 교체 다음 날부터 days일: 파라미터별 평균 이동(σ)과 산포비."""
    s = fl[fl["설비"] == eq]
    day, w = pd.Timestamp(when).normalize(), pd.Timedelta(days=days)
    before = s[(s["시각"] >= day - w) & (s["시각"] < day)]
    after = s[(s["시각"] >= day + pd.Timedelta(days=1)) & (s["시각"] < day + pd.Timedelta(days=1) + w)]
    rows = []
    for p in sorted(set(before["파라미터"]) & set(after["파라미터"])):
        b, a = before.loc[before["파라미터"] == p, "값"], after.loc[after["파라미터"] == p, "값"]
        if len(b) < 3 or len(a) < 3:
            continue
        sd = np.sqrt((b.var() + a.var()) / 2)
        shift = (a.mean() - b.mean()) / sd if sd else 0.0
        ratio = a.std() / b.std() if b.std() else np.nan
        p_mean = stats.ttest_ind(b, a, equal_var=False).pvalue
        p_var = stats.levene(b, a).pvalue
        big = (abs(shift) >= 1 or not (1 / 1.5 < ratio < 1.5)) and min(p_mean, p_var) < 0.05
        what = []
        if abs(shift) >= 0.5:
            what.append(f"평균 {shift:+.1f}σ 이동")
        if ratio <= 0.8:
            what.append(f"산포 ×{ratio:.2f} (안정화)")
        elif ratio >= 1.25:
            what.append(f"산포 ×{ratio:.2f} (불안정)")
        rows.append({"파라미터": p, "교체 전 n": len(b), "교체 후 n": len(a), "교체 전 평균": b.mean(),
                     "교체 후 평균": a.mean(), "평균 이동(σ)": shift, "교체 전 σ": b.std(), "교체 후 σ": a.std(),
                     "산포비(후/전)": ratio, "평균 p": p_mean, "산포 p": p_var,
                     "판정": "🔴 변화 큼" if big else "", "변화": ", ".join(what) or "변화 작음"})
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    return res.reindex((res["평균 이동(σ)"].abs() + (np.log(res["산포비(후/전)"])).abs())
                       .sort_values(ascending=False).index)


def fdc_events(ev, fl, days) -> pd.DataFrame:
    """FDC 데이터 기간 안에 있는 교체 이벤트마다 변화 큰 파라미터 수."""
    lo, hi = fl["시각"].min(), fl["시각"].max()
    cand = ev[ev["설비"].isin(set(fl["설비"])) & (ev["교체일"] > lo) & (ev["교체일"] < hi)]
    rows = []
    for _, e in cand.iterrows():
        ch = fdc_change(fl, e["설비"], e["교체일"], days)
        if ch.empty:
            continue
        big = ch[ch["판정"] != ""]
        top = (big if len(big) else ch).iloc[0]
        rows.append({"교체일": e["교체일"], "설비": e["설비"], "부품": e["부품"], "비교 파라미터 수": len(ch),
                     "변화 큰 파라미터 수": len(big), "가장 큰 변화": f"{top['파라미터']}: {top['변화']}"})
    out = pd.DataFrame(rows)
    return out.sort_values(["변화 큰 파라미터 수", "교체일"], ascending=[False, True]) if len(out) else out


# 자동 리포트

def parts_summary(R) -> list:
    out = []
    t = R.total
    keys = list(t)
    delta = t[keys[-1]] - t[keys[0]]
    pct = delta / t[keys[0]] * 100 if t[keys[0]] else np.nan
    effects = {k: v for k, v in t.items() if k not in (keys[0], keys[-1])}
    main = max(effects, key=lambda k: abs(effects[k]))
    out.append(f"{R.pb} 비용 {won(t[keys[-1]])}: {R.pa} 대비 {won(delta)} ({pct:+.1f}%) — "
               + ", ".join(f"{k.replace(' 효과', '')} {won(v)}" for k, v in effects.items() if abs(v) >= 1)
               + f" → 가장 큰 요인: {main}")
    up = R.parts[R.parts["증감"] > 0].head(3)
    if len(up):
        out.append("증가 기여 상위 부품: " + ", ".join(
            f"{i} {won(r['증감'])} ({r['주요 요인']})" for i, r in up.iterrows()))
    if R.eq_delta is not None and len(R.eq_delta) and R.eq_delta.iloc[0] > 0:
        out.append("증가 상위 설비: " + ", ".join(f"{i} {won(v)}" for i, v in R.eq_delta.head(3).items() if v > 0))
    early = R.cycle_summary[R.cycle_summary["조기 교체"] > 0].sort_values("조기 교체", ascending=False)
    if len(early):
        out.append(f"조기 교체 {int(early['조기 교체'].sum())}건 ({won(early['조기 교체 비용'].sum())}): " + ", ".join(
            f"{r['설비']} {r['부품']} {int(r['조기 교체'])}건 (주기 {r['주기 비율(%)']:.0f}%)" for _, r in early.head(3).iterrows()))
    if len(R.upcoming):
        out.append(f"향후 30일 교체 예정 {len(R.upcoming)}건, 교체 시기 지남 {len(R.overdue)}건")
    if len(R.anom):
        kinds = R.anom["사유"].str.extract(r"^(단가 이상|금액 ≠ 수량×단가|중복 입력 의심)")[0].value_counts()
        out.append(f"이상 비용 건 {len(R.anom)}건 (" + ", ".join(f"{k} {v}" for k, v in kinds.items()) + ") → 입력 확인 필요")
    f = R.fc_info
    line = f"{R.year}년 예상 연간 비용 {won(f['추세 예측 연간'])} (1~{f['마지막 실적 월']}월 실적 {won(f['실적 누계'])})"
    if "예산" in f:
        line += f", 예산 대비 {f['예산 대비(%)']:.0f}%" + (f" → {f['초과 예상 월']}월 초과 예상" if f["초과 예상 월"] else "")
    out.append(line)
    if R.wafer is not None and len(R.wafer):
        top = R.wafer.iloc[0]
        out.append(f"Wafer당 부품비 최고: {R.wafer.index[0]} {top['원/Wafer']:,.0f}원 (전체 대비 {top['전체 대비(%)']:+.0f}%)")
    if R.fdc_ev is not None and len(R.fdc_ev):
        hit = R.fdc_ev[R.fdc_ev["변화 큰 파라미터 수"] > 0]
        for _, e in hit.head(2).iterrows():
            out.append(f"FDC 연계: {e['교체일']:%Y-%m-%d} {e['설비']} {e['부품']} 교체 후 {e['가장 큰 변화']}")
        if not len(hit):
            out.append("FDC 연계: 교체 전후로 FDC 파라미터에 뚜렷한 변화가 있는 교체 이벤트가 없습니다.")
    return out


def parts_report(R, headline):
    fmt_parts = {c: WON for c in ("금액_A", "금액_B", "단가_A", "단가_B", "단가 효과", "수량 효과", "증감")}
    fmt_parts.update({"수량_A": "{:,.0f}", "수량_B": "{:,.0f}"})
    sections = [
        {"title": f"비용 증감 원인 ({R.pa} → {R.pb})", "figs": {"증감 분해": waterfall(R.total)},
         "tables": [html_safe(R.parts.head(10)).style.format(fmt_parts, na_rep="-").to_html()],
         "note": "단가 효과: 같은 부품의 단가 변화 / 수량 효과: 전체 사용량 변화 / 구성 효과: 비싼 부품 비중 변화"},
        {"title": "교체 주기",
         "tables": [html_safe(R.cycle_summary.sort_values(["조기 교체", "주기 비율(%)"], ascending=[False, True])
                              .head(15)).style.format({"평균 주기(일)": "{:.0f}", "부품 기준 주기(일)": "{:.0f}", "조기 교체 비용": WON,
                                   "주기 비율(%)": "{:.0f}", "마지막 교체": "{:%Y-%m-%d}", "다음 예상일": "{:%Y-%m-%d}"},
                                  na_rep="-").hide(axis="index").to_html()],
         "note": f"조기 교체: 교체 간격이 부품 기준 주기(전체 설비 중앙값)의 {R.early_pct}% 미만"},
        {"title": "연간 예측", "figs": {f"{R.year}년 월별 실적·예측": R.fc_fig}},
    ]
    if len(R.anom):
        sections.insert(2, {"title": f"이상 비용 건 {len(R.anom)}건",
                            "tables": [html_safe(R.anom).style.format({"날짜": "{:%Y-%m-%d}", "단가": WON, "금액": WON,
                                                            "부품 중앙 단가": WON, "수량": "{:,.0f}"})
                                       .hide(axis="index").to_html()]})
    if R.wafer is not None and len(R.wafer):
        bar = px.bar(R.wafer.reset_index(), x="설비", y="원/Wafer", text_auto=",.0f", template=REPORT_THEME)
        sections.append({"title": "Wafer당 부품비", "figs": {"설비별 원/Wafer": bar}})
    if R.fdc_ev is not None and len(R.fdc_ev):
        sec = {"title": "FDC 연계 (교체 전후 변화)", "tables": [html_safe(R.fdc_ev).style.format({"교체일": "{:%Y-%m-%d}"})
                                                              .hide(axis="index").to_html()]}
        if R.fdc_fig is not None:
            sec["figs"] = {R.fdc_fig_title: R.fdc_fig}
        sections.append(sec)
    return html_page("부품 비용 분석 리포트", f"{R.c['날짜'].min():%Y-%m-%d} ~ {R.c['날짜'].max():%Y-%m-%d} · "
                     f"{len(R.c)}건 · 총 {won(R.c['금액'].sum())}", headline, sections,
                     "※ 예측은 올해 월별 실적의 직선 추세 기준입니다. 이상 비용 건은 입력 오류 후보이며 확인이 필요합니다. "
                     "FDC 연계는 교체 전후 비교로, 같은 시기의 다른 작업(PM, 레시피 변경 등)과 겹칠 수 있습니다.")


def parts_excel(R, headline) -> bytes:
    sheets = {"요약": pd.DataFrame({"핵심 요약": headline}),
              "증감 분해(합계)": pd.Series(R.total, name="금액").to_frame(),
              "증감 분해(부품)": R.parts,
              "교체 주기": R.cycle_summary.set_index(["설비", "부품"]),
              "교체 이벤트": R.events.set_index(["설비", "부품"]),
              "교체 예정·지연": pd.concat([R.overdue.assign(상태="교체 시기 지남"), R.upcoming.assign(상태="30일 내 예정")])
              .set_index(["설비", "부품"]),
              "이상 비용 건": R.anom.set_index("날짜") if len(R.anom) else pd.DataFrame({"결과": ["없음"]}),
              f"{R.year}년 예측": R.fc_plan.set_index("월")}
    if R.wafer is not None:
        sheets["Wafer당 비용"] = R.wafer
    if R.fdc_ev is not None and len(R.fdc_ev):
        sheets["FDC 연계"] = R.fdc_ev.set_index("교체일")
    sheets["데이터"] = R.c.drop(columns=[x for x in ("_계산금액",) if x in R.c])
    return to_excel(sheets)


def page_parts(df, num_cols, cat_cols, date_cols):
    st.subheader("🔧 부품 비용 분석")
    cols = list(df.columns)
    auto, how = auto_map_cost(df, num_cols, date_cols)
    ok = auto["날짜"] and auto["부품"] and (auto["금액"] or (auto["수량"] and auto["단가"]))
    # 데이터(열 구성)가 바뀌면 이전 선택이 남지 않도록 키에 열 구성을 넣음
    sig = abs(hash(tuple(cols))) % 10 ** 8
    with st.expander("✏️ 열 매핑 바꾸기", expanded=not ok):
        m, boxes = {}, st.columns(4)
        for i, (role, (_, required)) in enumerate(COST_ROLES.items()):
            pool = date_cols if role == "날짜" else (num_cols if role in ("수량", "단가", "금액") else
                                                    [x for x in cols if x not in num_cols + date_cols])
            options = ([] if required else ["(없음)"]) + (pool or cols)
            default = auto[role] or "(없음)"
            pick = boxes[i % 4].selectbox(role + (" *" if required else ""), options,
                                          index=options.index(default) if default in options else 0,
                                          key=f"cost_{role}_{sig}", help=f"자동 인식: {how.get(role, '못 찾음')}")
            m[role] = None if pick == "(없음)" else pick
    shown = [f"{r} = `{m[r]}`" + (" *" if how.get(r, "이름") != "이름" and m[r] == auto[r] else "")
             for r in COST_ROLES if m[r]]
    missing = [r for r in COST_ROLES if not m[r]]
    st.caption("🧭 열 자동 인식: " + " · ".join(shown)
               + (f" · 없음: {', '.join(missing)}" if missing else "")
               + "  (* = 열 이름이 아니라 값 모양으로 추정 — 틀리면 '열 매핑 바꾸기'에서 수정)")
    if not m["날짜"] or not m["부품"] or not (m["금액"] or (m["수량"] and m["단가"])):
        st.warning("날짜 · 부품 열, 그리고 금액(또는 수량+단가) 열이 필요합니다. '열 매핑 바꾸기'에서 지정하세요.")
        return
    if not m["구분"]:
        st.info("구분(교체/수리) 열이 없어 교체와 수리가 함께 계산됩니다. 수리 건이 섞여 있으면 "
                "조기 교체·단가 이상이 실제보다 많이 잡힐 수 있습니다.")
    c = cost_frame(df, m)
    if c.empty:
        st.warning("분석할 비용 데이터가 없습니다.")
        return

    # ── 분석 설정 (리포트와 탭이 같은 값을 쓰도록 탭 위에 둠)
    with st.expander("⚙️ 분석 설정", expanded=True):
        s1, s2, s3, s4 = st.columns(4)
        unit = s1.radio("비교 기간 단위", ["월", "분기"], horizontal=True)
        c["기간"] = c["날짜"].dt.to_period({"월": "M", "분기": "Q"}[unit]).astype(str)
        periods = sorted(c["기간"].unique())
        if len(periods) < 2:
            st.warning("비교하려면 2개 이상의 기간 데이터가 필요합니다.")
            return
        pa = s2.selectbox("기준 기간 (A)", periods, index=len(periods) - 2)
        pb = s3.selectbox("비교 기간 (B)", periods, index=len(periods) - 1)
        kinds_all = sorted(c["구분"].unique())
        kinds = s4.multiselect("교체로 볼 구분", kinds_all,
                               default=[k for k in kinds_all if "교체" in k] or kinds_all)
        s5, s6, s7, s8 = st.columns(4)
        early_pct = s5.slider("조기 교체 기준 (부품 기준 주기의 %)", 20, 90, 50, 5)
        years = sorted(c["날짜"].dt.year.unique())
        year = s6.selectbox("예측 연도", years, index=len(years) - 1)
        budget = parse_float(s7.text_input("연간 예산 (원, 선택)", placeholder="예: 600,000,000"))
        days = s8.number_input("FDC 비교 구간 (교체 전후 일수)", 1, 60, 7)

    R = SimpleNamespace(c=c, pa=pa, pb=pb, early_pct=early_pct, year=year)
    R.total, R.parts = decompose(c, pa, pb)
    R.eq_delta = (c[c["기간"] == pb].groupby("설비")["금액"].sum()
                  .sub(c[c["기간"] == pa].groupby("설비")["금액"].sum(), fill_value=0).sort_values(ascending=False))
    R.events, R.cycle_summary = replacement_cycles(c, kinds, early_pct)
    ref_day = c["날짜"].max().normalize()
    nxt = R.cycle_summary["다음 예상일"]
    R.upcoming = R.cycle_summary[(nxt >= ref_day) & (nxt <= ref_day + pd.Timedelta(days=30))].sort_values("다음 예상일")
    R.overdue = R.cycle_summary[nxt < ref_day].sort_values("다음 예상일")
    R.anom = cost_anomalies(c)
    R.fc_plan, R.fc_info, R.fc_fig, R.fc_eq = year_forecast(c, year, budget)

    R.wafer = R.wafer_monthly = None
    prod_text = st.session_state.get("prod_raw", "")
    if prod_text.strip():
        try:
            prod, by_eq = parse_production(prod_text)
            R.wafer_monthly, R.wafer = cost_per_wafer(c, prod, by_eq and m["설비"] is not None)
        except Exception as e:  # noqa: BLE001
            st.error(f"생산량 데이터를 읽지 못했습니다: {e}")
    R.fdc_ev = R.fdc_fig = R.fl = None
    fdc_text = st.session_state.get("fdc_aux_raw", "")
    if fdc_text.strip():
        try:
            R.fl = parse_fdc_aux(fdc_text)
            R.fdc_ev = fdc_events(R.events, R.fl, days)
        except Exception as e:  # noqa: BLE001
            st.error(f"FDC 데이터를 읽지 못했습니다: {e}")

    def fdc_trend(eq, when, param):
        s = R.fl[(R.fl["설비"] == eq) & (R.fl["파라미터"] == param)]
        day = pd.Timestamp(when).normalize()
        s = s[(s["시각"] >= day - pd.Timedelta(days=days)) & (s["시각"] < day + pd.Timedelta(days=days + 1))]
        fig = px.scatter(s, x="시각", y="값", template=REPORT_THEME)
        fig.add_vline(x=day, line=dict(color="#EF7B6E", dash="dash"))
        fig.add_annotation(x=day, y=1, yref="paper", text="교체일", showarrow=False, yanchor="bottom")
        return fig.update_layout(yaxis_title=param)

    if R.fdc_ev is not None and len(R.fdc_ev) and R.fdc_ev["변화 큰 파라미터 수"].iloc[0] > 0:
        e = R.fdc_ev.iloc[0]
        top = fdc_change(R.fl, e["설비"], e["교체일"], days).iloc[0]["파라미터"]
        R.fdc_fig_title = f"{e['교체일']:%Y-%m-%d} {e['설비']} {e['부품']} 교체 전후 {top}"
        R.fdc_fig = fdc_trend(e["설비"], e["교체일"], top)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("기간", f"{c['날짜'].min():%Y-%m} ~ {c['날짜'].max():%Y-%m}")
    m2.metric("총 비용", won(c["금액"].sum()))
    keys = list(R.total)
    m3.metric(f"{pb} 비용", won(R.total[keys[-1]]),
              f"{(R.total[keys[-1]] / R.total[keys[0]] - 1) * 100:+.1f}% (vs {pa})" if R.total[keys[0]] else None,
              delta_color="inverse")
    m4.metric("조기 교체", f"{int(R.cycle_summary['조기 교체'].sum())}건")
    m5.metric("이상 비용 건", f"{len(R.anom)}건")

    tabs = st.tabs(["📄 자동 리포트", "📊 증감 원인", "🔁 교체 주기", "🚨 이상 비용 건", "📈 연간 예측·예산",
                    "🏭 Wafer당 비용", "🔗 FDC 연계"])
    headline = parts_summary(R)

    with tabs[0]:
        st.markdown("#### 핵심 요약\n" + "\n".join(f"- {t}" for t in headline))
        sig = (len(c), pa, pb, tuple(kinds), early_pct, year, budget, days, len(prod_text), len(fdc_text))
        if st.button("📄 리포트 파일 만들기", type="primary", key="parts_report_btn"):
            with st.spinner("리포트 만드는 중…"):
                st.session_state["parts_report"] = (sig, parts_report(R, headline), parts_excel(R, headline))
        saved = st.session_state.get("parts_report")
        if saved and saved[0] == sig:
            stamp = f"{datetime.now():%Y%m%d_%H%M}"
            d1, d2 = st.columns(2)
            d1.download_button("🌐 HTML 리포트 (그래프 포함 · 인쇄 → PDF 저장)", saved[1],
                               file_name=f"부품비용_리포트_{stamp}.html", mime="text/html", width="stretch")
            d2.download_button("📊 엑셀 리포트", saved[2], file_name=f"부품비용_리포트_{stamp}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")
            st.caption("HTML은 인터넷 없이 열리며, 브라우저에서 Ctrl+P → 'PDF로 저장' 하면 PDF 보고서가 됩니다.")
        if not prod_text.strip() or not fdc_text.strip():
            st.info("위 '🏭 생산량 / FDC' 탭에 생산량·FDC 데이터를 붙여넣으면 Wafer당 비용과 FDC 연계도 리포트에 들어갑니다.")

    with tabs[1]:
        st.plotly_chart(waterfall(R.total).update_layout(title=f"{pa} → {pb} 비용 증감 분해"), width="stretch")
        st.caption("단가 효과: 같은 부품의 단가가 바뀐 영향 · 수량 효과: 전체 사용량이 바뀐 영향 · "
                   "구성 효과: 비싼 부품 비중이 바뀐 영향 · A/B에만 사용: 한쪽 기간에만 쓴 항목 (ESC처럼 가끔 교체하는 고가 부품)")
        fmt = {x: WON for x in ("금액_A", "금액_B", "단가_A", "단가_B", "단가 효과", "수량 효과", "증감")}
        fmt.update({"수량_A": "{:,.0f}", "수량_B": "{:,.0f}"})
        st.dataframe(R.parts.style.format(fmt, na_rep="-")
                     .background_gradient(subset=["증감"], cmap="RdBu_r"), width="stretch")
        st.write("**설비별 증감**")
        st.plotly_chart(px.bar(R.eq_delta.reset_index(), x="설비", y="금액", text_auto=",.0f",
                               color=np.where(R.eq_delta.values > 0, "증가", "감소"),
                               color_discrete_map={"증가": "#EF7B6E", "감소": "#5B8DEF"})
                        .update_layout(showlegend=False, yaxis_title="증감 (원)"), width="stretch")

    with tabs[2]:
        cs = R.cycle_summary
        if cs.empty:
            st.info("선택한 '교체' 구분의 이벤트가 없습니다.")
        else:
            heat = cs.pivot(index="설비", columns="부품", values="주기 비율(%)")
            fig = px.imshow(heat, text_auto=".0f", aspect="auto", color_continuous_scale="RdYlGn",
                            zmin=0, zmax=150, title="부품 기준 주기 대비 평균 교체 주기 (%) — 낮을수록 자주 교체")
            st.plotly_chart(fig, width="stretch")
            st.write("**설비·부품별 교체 주기**")
            st.dataframe(cs.sort_values(["조기 교체", "주기 비율(%)"], ascending=[False, True]).style
                         .format({"평균 주기(일)": "{:.0f}", "부품 기준 주기(일)": "{:.0f}", "조기 교체 비용": WON,
                                  "주기 비율(%)": "{:.0f}", "마지막 교체": "{:%Y-%m-%d}", "다음 예상일": "{:%Y-%m-%d}"},
                                 na_rep="-"), width="stretch", hide_index=True)
            e1, e2 = st.columns(2)
            e1.write(f"**⏰ 교체 시기 지남 ({len(R.overdue)}건, 기준일 {ref_day:%Y-%m-%d})**")
            e1.dataframe(R.overdue[["설비", "부품", "마지막 교체", "다음 예상일"]], width="stretch", hide_index=True)
            e2.write(f"**📅 30일 내 교체 예정 ({len(R.upcoming)}건)**")
            e2.dataframe(R.upcoming[["설비", "부품", "마지막 교체", "다음 예상일"]], width="stretch", hide_index=True)
            early = R.events[R.events["조기 교체"]]
            st.write(f"**조기 교체 이벤트 {len(early)}건**")
            st.dataframe(early.style.format({"교체일": "{:%Y-%m-%d}", "금액": WON, "간격(일)": "{:.0f}",
                                             "부품 기준 주기(일)": "{:.0f}", "기준 대비(%)": "{:.0f}"}),
                         width="stretch", hide_index=True)
            pick = st.selectbox("교체 이력 보기 (부품)", sorted(R.events["부품"].unique()))
            evp = R.events[R.events["부품"] == pick]
            st.plotly_chart(px.scatter(evp, x="교체일", y="설비", color="조기 교체", size="금액",
                                       color_discrete_map={True: "#EF7B6E", False: "#5B8DEF"},
                                       title=f"{pick} 교체 이력 (빨강 = 조기 교체)"), width="stretch")

    with tabs[3]:
        if R.anom.empty:
            st.success("이상 비용 건이 없습니다.")
        else:
            st.dataframe(R.anom.style.format({"날짜": "{:%Y-%m-%d}", "단가": WON, "금액": WON,
                                              "부품 중앙 단가": WON, "수량": "{:,.0f}"}),
                         width="stretch", hide_index=True)
        st.caption("단가 이상: 같은 부품·구분의 중앙 단가의 2배 이상 또는 절반 이하 (단가 인상·설비별 수리비 차이는 제외) · "
                   "금액 ≠ 수량×단가: 1% 이상 차이 · 중복 입력: 날짜·설비·부품·구분·수량·금액이 모두 같은 행")

    with tabs[4]:
        f = R.fc_info
        k1, k2, k3, k4 = st.columns(4)
        k1.metric(f"1~{f['마지막 실적 월']}월 실적", won(f["실적 누계"]))
        k2.metric("추세 예측 연간", won(f["추세 예측 연간"]))
        k3.metric("단순 연환산", won(f["단순 연환산"]))
        if "예산" in f:
            k4.metric("예산 대비", f"{f['예산 대비(%)']:.0f}%",
                      f"{f['초과 예상 월']}월 초과 예상" if f["초과 예상 월"] else "예산 내", delta_color="off")
        st.plotly_chart(R.fc_fig, width="stretch")
        st.caption("추세 예측: 올해 월별 실적의 직선 추세로 남은 달을 예측 · 단순 연환산: 월평균 × 12. "
                   "마지막 달이 아직 진행 중이면 실적이 낮게 잡힐 수 있습니다.")
        st.write("**설비별 실적·연환산**")
        st.dataframe(R.fc_eq.style.format({"실적 누계": WON, "비중(%)": "{:.1f}", "연환산": WON}), width="stretch")

    with tabs[5]:
        if R.wafer is None:
            st.info("위 '🏭 생산량 / FDC' 탭에 `월 | 설비 | 생산량` 표를 붙여넣으면 Wafer당 부품비를 계산합니다.")
        else:
            st.dataframe(R.wafer.style.format({"금액": WON, "생산량": "{:,.0f}", "원/Wafer": "{:,.0f}",
                                               "전체 대비(%)": "{:+.1f}"})
                         .background_gradient(subset=["전체 대비(%)"], cmap="RdBu_r", vmin=-50, vmax=50),
                         width="stretch")
            st.plotly_chart(px.line(R.wafer_monthly, x="월", y="원/Wafer", color="설비", markers=True,
                                    title="월별 Wafer당 부품비"), width="stretch")
            st.caption("생산량이 적은 설비는 같은 부품비라도 Wafer당 비용이 높게 나옵니다. 비용 총액과 함께 보세요.")

    with tabs[6]:
        if R.fl is None:
            st.info("위 '🏭 생산량 / FDC' 탭에 FDC 데이터(시각·설비·파라미터·값 포함)를 붙여넣으면 "
                    "부품 교체 전후의 FDC 변화를 비교합니다.")
        elif R.fdc_ev is None or R.fdc_ev.empty:
            st.info("FDC 데이터 기간 안에 있는 교체 이벤트(같은 설비)가 없습니다. 설비 이름이 두 데이터에서 같은지 확인하세요.")
        else:
            st.dataframe(R.fdc_ev.style.format({"교체일": "{:%Y-%m-%d}"}), width="stretch", hide_index=True)
            labels = [f"{r['교체일']:%Y-%m-%d} · {r['설비']} · {r['부품']}" for _, r in R.fdc_ev.iterrows()]
            i = st.selectbox("교체 이벤트", range(len(labels)), format_func=lambda k: labels[k])
            e = R.fdc_ev.iloc[i]
            ch = fdc_change(R.fl, e["설비"], e["교체일"], days)
            st.dataframe(ch.style.format({"교체 전 평균": "{:.4g}", "교체 후 평균": "{:.4g}", "평균 이동(σ)": "{:+.2f}",
                                          "교체 전 σ": "{:.3g}", "교체 후 σ": "{:.3g}", "산포비(후/전)": "{:.2f}",
                                          "평균 p": "{:.4f}", "산포 p": "{:.4f}"}),
                         width="stretch", hide_index=True)
            param = st.selectbox("파라미터 추이", list(ch["파라미터"]))
            st.plotly_chart(fdc_trend(e["설비"], e["교체일"], param), width="stretch")
            st.caption(f"교체일 전 {days}일 vs 교체 다음 날부터 {days}일 비교. 🔴 변화 큼: 평균 1σ 이상 이동 또는 "
                       "산포 1.5배 이상 변화이면서 통계적으로 유의(p < 0.05). 교체 후 '안정화'면 교체 효과가 있었다는 뜻입니다. "
                       "같은 시기의 다른 작업(PM, 레시피 변경 등)과 겹칠 수 있으니 작업 이력과 함께 확인하세요.")

    download({"표준 비용표": c.drop(columns=[x for x in ("_계산금액",) if x in c])}, "부품비용_데이터.xlsx")
