"""기본 분석 모드: 상관분석 · 월별 비용 추이 · 그룹 평균 비교."""

from itertools import combinations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

from common import METHODS, TESTS, bh, correlation_ratio, download, strength


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
