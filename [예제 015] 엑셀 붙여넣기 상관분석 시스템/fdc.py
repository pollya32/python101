"""FDC LOT/Wafer 분석 모드: 세로형 변환, 원인 파라미터, 관리도, LOT 간·내 변동, 챔버 매칭, 자동 리포트."""

from datetime import datetime
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
    bh,
    classify_columns,
    correlation_ratio,
    download,
    fmt_number,
    guess,
    html_page,
    html_safe,
    looks_tool,
    parse_float,
    read_pasted,
    to_excel,
    to_number,
    unique_names,
)


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

    fig = go.Figure(go.Scatter(x=d["_x"], y=v, mode="lines", line=dict(color="#D5DAE3"),
                               showlegend=False, hoverinfo="skip"))
    if group != "(없음)":
        for g, gd in d.groupby(group):
            fig.add_trace(go.Scatter(x=gd["_x"], y=gd[param], mode="markers", name=str(g)))
    else:
        fig.add_trace(go.Scatter(x=d["_x"], y=v, mode="markers", name=param,
                                 marker=dict(color="#5B8DEF")))
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


LOT_KEYS = ("lot_id", "lotid", "lot no", "lot", "로트")


WAFER_KEYS = ("wafer_id", "waferid", "wafer", "웨이퍼", "slot", "슬롯", "wf")


STEP_KEYS = ("step", "스텝", "공정", "oper", "process", "stage", "단계")


PARAM_KEYS = ("파라미터", "parameter", "param", "항목", "sensor", "센서", "svid", "item", "tag", "변수")


VALUE_KEYS = ("value", "측정값", "값", "val", "data", "reading")


RESULT_KEYS = ("수율", "yield", "불량", "defect", "두께", "thickness", "thk", "cd", "결과", "계측", "measure")


FDC_ROLES = {"LOT": LOT_KEYS, "Wafer": WAFER_KEYS, "Step": STEP_KEYS, "파라미터": PARAM_KEYS, "값": VALUE_KEYS}


SLOT = "Slot번호"


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


def _digit_ratio(s: pd.Series) -> float:
    v = s.dropna().astype(str)
    return float((v.str.count(r"\d") / v.str.len().clip(lower=1)).mean()) if len(v) else 0.0


def wafer_key(s: pd.Series) -> pd.Series:
    """W07 · 07 · 7 · LOT001-07 → '7' (두 표의 Wafer 표기가 달라도 맞도록 끝 번호만 사용)."""
    lab = as_label(s)
    num = lab.str.extract(r"(\d+)\D*$")[0]
    return num.map(lambda v: str(int(v)), na_action="ignore").fillna(lab)


def _looks_wafer(df, col, lot=None) -> bool:
    lab = as_label(df[col].dropna())
    if not (2 <= lab.nunique() <= 50 and lab.str.fullmatch(r"(?:[A-Za-z]{1,3}[-_#]?)?\d{1,2}").mean() >= 0.9):
        return False
    return lot is None or df.groupby(lot)[col].nunique().median() >= 2  # 한 LOT 안에 여러 장


def auto_map_fdc(df, num_cols, date_cols):
    """세로형 FDC 열 역할(LOT·Wafer·Step·파라미터·값)을 이름 → 값 모양 → 구조 순으로 추정.
    반환: (역할 → 열 또는 None, 역할 → 찾은 방법)"""
    m, how, used = dict.fromkeys(FDC_ROLES), {}, set(date_cols)
    cols = list(df.columns)
    n = len(df)

    def take(role, col, why):
        m[role], how[role] = col, why
        used.add(col)

    def free(pool):
        return [c for c in pool if c not in used]

    texts = [c for c in cols if c not in num_cols + date_cols]
    ints = [c for c in num_cols if (df[c].dropna() % 1 == 0).all()]
    repeated = [c for c in free(texts) + ints if df[c].nunique() >= 2 and n / max(df[c].nunique(), 1) >= 2]

    # LOT: 이름 ('slot' 은 제외) → 숫자가 많이 섞인 반복 ID 열
    named = guess([c for c in free(cols) if not any(w in str(c).lower() for w in ("slot", "슬롯"))], LOT_KEYS)
    ids = [c for c in repeated if c not in used and _digit_ratio(df[c]) >= 0.3 and not looks_tool(df[c])
           and not _looks_wafer(df, c)]
    if named:
        take("LOT", named, "이름")
    elif ids:
        take("LOT", max(ids, key=lambda c: (df[c].nunique(), _digit_ratio(df[c]))), "값 패턴 (반복되는 ID)")

    # Wafer: 이름 → 1~2자리 번호 모양이면서 한 LOT 안에 여러 장
    named = guess(free(cols), WAFER_KEYS)
    by_value = [c for c in free(texts + ints) if _looks_wafer(df, c, m["LOT"])]
    if named and (_looks_wafer(df, named) or not by_value):
        take("Wafer", named, "이름")
    elif by_value:
        take("Wafer", by_value[0], "값 패턴 (W01·1~25 번호)")

    # 파라미터: 이름 → 글자 위주이면서 값 종류가 가장 많은 반복 열
    named = guess(free(texts), PARAM_KEYS)
    words = [c for c in free(texts) if c in repeated and _digit_ratio(df[c]) < 0.3 and not looks_tool(df[c])]
    if named:
        take("파라미터", named, "이름")
    elif words:
        take("파라미터", max(words, key=lambda c: df[c].nunique()), "값 패턴 (반복되는 항목 이름)")

    # Step: 이름 → 구조로 추정 (설비 모양 열은 제외)
    #   ① 같은 LOT·Wafer·파라미터 안에서 값이 바뀜 (같은 파라미터가 여러 Step에 있음)
    #   ② 파라미터를 몇 묶음으로 나눔 (Step마다 파라미터가 다름)
    named = guess(free(texts + ints), STEP_KEYS)
    if named:
        take("Step", named, "이름")
    elif m["LOT"] and m["파라미터"]:
        key = [m["LOT"]] + ([m["Wafer"]] if m["Wafer"] else []) + [m["파라미터"]]
        n_param = df[m["파라미터"]].nunique()
        cand = [c for c in free(texts + ints) if 2 <= df[c].nunique() <= 50 and not looks_tool(df[c])]
        varying = {c: (df.groupby(key)[c].nunique() > 1).mean() for c in cand}
        best = max(varying, key=varying.get) if varying else None
        if best and varying[best] >= 0.5:
            take("Step", best, "구조 (같은 LOT·파라미터 안에서 바뀌는 열)")
        else:
            splits = [c for c in cand if df[c].nunique() < n_param
                      and (df.groupby(m["파라미터"])[c].nunique() == 1).mean() >= 0.9]
            if splits:
                take("Step", min(splits, key=lambda c: df[c].nunique()), "구조 (파라미터를 묶음으로 나누는 열)")

    # 값: 이름 → 남은 숫자 열 중 값 종류가 가장 많은 열
    named = guess(free(num_cols), VALUE_KEYS)
    if named:
        take("값", named, "이름")
    elif free(num_cols):
        take("값", max(free(num_cols), key=lambda c: df[c].nunique()), "값 패턴 (연속 측정값)")
    return m, how


def merge_result(wide, keys, text):
    """별도로 붙여넣은 결과표(LOT | Wafer | 수율 …)를 키 기준으로 합침.
    결과표의 키 열은 이름이 아니라 FDC 쪽 값과 가장 많이 겹치는 열로 찾음 (Wafer는 끝 번호로 비교)."""
    res = read_pasted(text)
    res.columns = unique_names(str(c).strip() for c in res.columns)
    norm = [as_label, wafer_key]
    rename = {}
    for pos, k in enumerate(keys):
        target = set(norm[pos](wide[k]))
        overlap = {c: norm[pos](res[c]).isin(target).mean() for c in res.columns if c not in rename}
        best = max(overlap, key=overlap.get)
        if overlap[best] < 0.3:  # 겹치는 열이 없으면 이름·위치로
            best = k if k in res.columns else guess([c for c in res.columns if c not in rename],
                                                    (LOT_KEYS, WAFER_KEYS)[pos], res.columns[pos])
        rename[best] = k
    res = res.rename(columns=rename)
    left = wide.assign(**{f"_k{i}": norm[i](wide[k]) for i, k in enumerate(keys)})
    res = res.assign(**{f"_k{i}": norm[i](res[k]) for i, k in enumerate(keys)}).drop(columns=keys)
    kk = [f"_k{i}" for i in range(len(keys))]
    res = res.drop_duplicates(kk)
    merged = left.merge(res, on=kk, how="left", suffixes=("", "_결과"))
    matched = int(left.set_index(kk).index.isin(res.set_index(kk).index).sum())
    return merged.drop(columns=kk), matched


def page_fdc(df, num_cols, cat_cols, date_cols):
    st.subheader("🧪 FDC LOT / Wafer 분석")
    cols = list(df.columns)
    auto, how = auto_map_fdc(df, num_cols, date_cols)
    # 반복되는 글자 파라미터 열 + 측정값 열이 있으면 세로형
    is_long = bool(auto["파라미터"] and auto["값"])
    shape = st.radio("데이터 형태", ["세로형 (LOT | Wafer | Step | 파라미터 | 값)", "가로형 (한 행 = 한 LOT/Wafer)"],
                     index=0 if is_long else 1, horizontal=True)
    if shape.startswith("가로"):
        st.caption("한 행 = 한 LOT(또는 Wafer). FDC 요약값과 결과값(수율·CD·두께·불량 등)을 함께 붙여넣으세요.")
        fdc_analysis(df, num_cols, cat_cols, date_cols, {})
        return

    st.caption("세로형 FDC를 Wafer(또는 LOT)별 한 행으로 펼칩니다. "
               "수율·계측값은 위 '🎯 결과 데이터' 탭에 따로 붙여넣으면 LOT·Wafer 기준으로 합쳐집니다.")

    sig = abs(hash(tuple(cols))) % 10 ** 8  # 열 구성이 바뀌면 이전 선택이 남지 않도록

    def pick(box, role, fallback, optional=False):
        options = (["(없음)"] if optional else []) + cols
        default = auto[role] or fallback
        val = box.selectbox(f"{role} 열", options, index=options.index(default) if default in options else 0,
                            key=f"fdc_{role}_{sig}", help=f"자동 인식: {how.get(role, '못 찾음')}")
        return None if val == "(없음)" else val

    with st.expander("✏️ 열 매핑 바꾸기", expanded=not (auto["LOT"] and auto["파라미터"] and auto["값"])):
        c1, c2, c3, c4, c5 = st.columns(5)
        lot = pick(c1, "LOT", cols[0])
        wafer = pick(c2, "Wafer", "(없음)", optional=True)
        step = pick(c3, "Step", "(없음)", optional=True)
        param = pick(c4, "파라미터", cols[min(2, len(cols) - 1)])
        val = pick(c5, "값", num_cols[-1] if num_cols else cols[-1])
    chosen_map = {"LOT": lot, "Wafer": wafer, "Step": step, "파라미터": param, "값": val}
    st.caption("🧭 열 자동 인식: " + " · ".join(
        f"{r} = `{c}`" + (" *" if how.get(r, "이름") != "이름" and c == auto[r] else "")
        for r, c in chosen_map.items() if c)
        + "".join(f" · {r} 없음" for r, c in chosen_map.items() if not c)
        + "  (* = 열 이름이 아니라 값 모양·구조로 추정 — 틀리면 '열 매핑 바꾸기'에서 수정)")
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
                 unit=unit, lot_col=lot if unit == "Wafer" else None, id_col=lot)


def gb_table(df, params, target, good, bad) -> pd.DataFrame:
    """Good vs Bad 파라미터 비교표 (|효과크기 d| 큰 순)."""
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
        return res
    res["보정 p-value"] = bh(res["p-value"])
    ad = res["효과크기 d"].abs()
    res["판정"] = np.select([(ad >= 0.8) & (res["보정 p-value"] < 0.05), ad >= 0.5],
                          ["🔴 주요 의심", "🟡 관찰"], "")
    return res.reindex(ad.sort_values(ascending=False).index).reset_index(drop=True)


GB_FORMAT = {"Good 평균": "{:.4g}", "Bad 평균": "{:.4g}", "차이(%)": "{:+.2f}", "효과크기 d": "{:+.2f}",
             "p-value": "{:.4f}", "보정 p-value": "{:.4f}", "결과와 상관 r": "{:+.2f}"}


def gb_styled(res):
    return (res.style.format(GB_FORMAT)
            .background_gradient(subset=["효과크기 d"], cmap="RdBu_r", vmin=-2, vmax=2))


def lot_average(df, lot_col, cols, keep):
    """Wafer 행을 LOT 평균으로 묶음 (keep 열은 LOT의 첫 값)."""
    agg = {c: "mean" for c in cols}
    agg.update({c: "first" for c in keep if c in df.columns and c not in agg and c != lot_col})
    return df.groupby(lot_col, sort=False).agg(agg).reset_index()


def lot_share(df, lot_col, col) -> float:
    """전체 변동 중 LOT 간 차이로 설명되는 비율 (%)."""
    return correlation_ratio(df[lot_col], df[col]) ** 2 * 100


def origin_text(share: float) -> str:
    if share >= 60:
        return f"변동의 {share:.0f}%가 LOT 간 차이 → LOT 단위 원인 (설비·챔버 상태, PM 시점, 레시피) 점검"
    if share <= 30:
        return f"LOT 간 차이는 {share:.0f}%뿐 → 같은 LOT 안 Wafer 단위 원인 (슬롯 위치, Wafer 순서) 점검"
    return f"LOT 간 차이 {share:.0f}% → LOT 단위와 Wafer 단위 원인이 섞여 있음"


# ── 챔버 매칭 ──

MATCH_ORDER = {"🔴 불일치": 0, "🟡 주의": 1, "✅ 일치": 2}


def chamber_key(df, cols) -> pd.Series:
    """챔버 구분 열이 여러 개면 '설비 / 챔버' 처럼 조합."""
    if len(cols) == 1:
        return df[cols[0]]
    return df[cols].astype(str).agg(" / ".join, axis=1).where(df[cols].notna().all(axis=1))


def step_params(col, params) -> list:
    """'ETCH:설비' 로 매칭하면 같은 Step의 'ETCH:…' 파라미터만 비교."""
    params = [p for p in params if p != SLOT]
    if ":" in str(col):
        own = [p for p in params if p.startswith(str(col).split(":")[0] + ":")]
        if own:
            return own
    return params


def _pooled_sd(st_) -> float:
    dof = (st_["count"] - 1).clip(lower=0)
    return float(np.sqrt((st_["std"].fillna(0) ** 2 * dof).sum() / dof.sum())) if dof.sum() else np.nan


def match_judge(offset: float, ratio: float) -> str:
    # 산포가 기준보다 작은 것은 문제가 아니므로 최대 '주의'
    if abs(offset) >= 1 or ratio >= 1.5:
        return "🔴 불일치"
    if abs(offset) >= 0.5 or not (0.8 < ratio < 1.25):
        return "🟡 주의"
    return "✅ 일치"


def chamber_matching(df, key, params, golden=None, min_n=3) -> pd.DataFrame:
    """챔버별 평균 오프셋(σ 단위)과 산포비를 기준(전체 챔버 중앙값 또는 골든 챔버)과 비교.
    중앙값 기준은 튀는 챔버 하나가 기준을 끌고 가서 정상 챔버까지 불일치로 보이는 것을 막음."""
    rows = []
    for p in params:
        sub = pd.DataFrame({"c": key, "v": df[p]}).dropna()
        st_ = sub.groupby("c")["v"].agg(["count", "mean", "std"])
        st_ = st_[st_["count"] >= min_n]
        if len(st_) < 2 or (golden is not None and golden not in st_.index):
            continue
        sigma = _pooled_sd(st_)  # 챔버 내부 산포를 공통 잣대로 사용
        if not sigma:
            continue
        if golden is not None:
            ref_mean, ref_sd = st_.at[golden, "mean"], st_.at[golden, "std"]
        else:
            ref_mean, ref_sd = st_["mean"].median(), st_["std"].median()
        for c, r in st_.iterrows():
            offset = (r["mean"] - ref_mean) / sigma
            ratio = r["std"] / ref_sd if ref_sd else np.nan
            if golden is not None and c == golden:
                offset, ratio = 0.0, 1.0
            reason = [f"평균 {offset:+.1f}σ"] if abs(offset) >= 0.5 else []
            reason += [f"산포 ×{ratio:.1f}"] if not (0.8 < ratio < 1.25) else []
            rows.append({"챔버": c, "파라미터": p, "건수": int(r["count"]), "평균": r["mean"], "표준편차": r["std"],
                         "기준 평균": ref_mean, "오프셋(σ)": offset, "산포비": ratio,
                         "판정": match_judge(offset, ratio), "차이": ", ".join(reason)})
    return pd.DataFrame(rows)


def prioritize(m: pd.DataFrame) -> pd.DataFrame:
    """불일치 우선, 그중 수율 영향 파라미터 우선, 오프셋 큰 순."""
    order = m["판정"].map(MATCH_ORDER) * 10 + m["수율 영향"].map({"🔴 주요 의심": 0, "🟡 관찰": 1}).fillna(2)
    return m.assign(_o=order, _a=-m["오프셋(σ)"].abs()).sort_values(["_o", "_a"]).drop(columns=["_o", "_a"])


def match_heatmap(m: pd.DataFrame):
    off = m.pivot(index="파라미터", columns="챔버", values="오프셋(σ)")
    ratio = m.pivot(index="파라미터", columns="챔버", values="산포비").reindex_like(off)
    text = off.map(lambda v: "" if pd.isna(v) else f"{v:+.1f}σ") + ratio.map(
        lambda v: "" if pd.isna(v) or 0.8 < v < 1.25 else f"<br>산포×{v:.1f}")
    fig = px.imshow(off.clip(-3, 3), zmin=-3, zmax=3, aspect="auto", color_continuous_scale="RdBu_r")
    fig.update_traces(text=text.values, texttemplate="%{text}")
    fig.update_layout(height=max(300, 40 * len(off)), coloraxis_colorbar=dict(title="평균 오프셋"))
    return fig


def match_scores(m: pd.DataFrame, key, df, target) -> pd.DataFrame:
    sc = m.groupby("챔버")["판정"].value_counts().unstack(fill_value=0)
    for col in MATCH_ORDER:
        sc[col] = sc[col] if col in sc else 0
    sc = sc[list(MATCH_ORDER)]
    sc["매칭률(%)"] = sc["✅ 일치"] / sc.sum(axis=1) * 100
    sc[f"{target} 평균"] = df.groupby(key)[target].mean()
    return sc.sort_values("매칭률(%)")


def analyze_param(ctx, row) -> dict:
    """원인 후보 파라미터 하나에 대한 해석 문장 + 그래프."""
    df, p = ctx.df, row["파라미터"]
    d = row["효과크기 d"]
    if p == SLOT:
        lines = [f"Bad {ctx.unit}가 {'뒤쪽' if d > 0 else '앞쪽'} 슬롯에 몰려 있음 "
                 f"(효과크기 d {d:+.2f}) → 슬롯 위치와 관련된 파라미터를 함께 확인"]
    else:
        lines = [f"Bad {ctx.unit}에서 평균이 {row['차이(%)']:+.2f}% {'높음' if d > 0 else '낮음'} "
                 f"(효과크기 d {d:+.2f}, 결과와 상관 r {row['결과와 상관 r']:+.2f})"]
    info = {"파라미터": p, "판정": row["판정"] or "-", "효과크기 d": d, "차이(%)": row["차이(%)"]}
    figs = {}

    label = df.loc[ctx.y.notna(), [p]].assign(판정=np.where(ctx.good[ctx.y.notna()], "Good", "Bad"))
    figs["Good vs Bad 분포"] = px.box(label, x="판정", y=p, color="판정", points="all", template=REPORT_THEME,
                                     color_discrete_map={"Good": "#5B8DEF", "Bad": "#EF7B6E"})
    sc = df[[p, ctx.target] + ([ctx.group] if ctx.group in df.columns else [])].dropna(subset=[p, ctx.target])
    figs[f"{p} ↔ {ctx.target}"] = px.scatter(sc, x=p, y=ctx.target,
                                             color=ctx.group if ctx.group in df.columns else None,
                                             template=REPORT_THEME)

    if ctx.lot_col and p != SLOT:
        share = lot_share(df, ctx.lot_col, p)
        info["LOT 간 변동(%)"] = share
        lines.append(origin_text(share))
    if SLOT in df.columns and p != SLOT:
        eta2 = correlation_ratio(df[SLOT], df[p]) ** 2 * 100
        info["슬롯 효과(%)"] = eta2
        if eta2 >= 20:
            z = (df.groupby(SLOT)[p].mean() - df[p].mean()) / df[p].std()
            worst = z.reindex(z.abs().sort_values(ascending=False).index[:2])
            lines.append(f"슬롯 효과 {eta2:.0f}%: " + ", ".join(f"{fmt_number(k)}번 슬롯 {v:+.1f}σ" for k, v in worst.items()))
        figs["슬롯 번호별"] = px.box(df, x=SLOT, y=p, points="all", template=REPORT_THEME)
    if ctx.group in df.columns:
        z = (df.groupby(ctx.group)[p].mean() - df[p].mean()) / df[p].std()
        if z.notna().any():
            k = z.abs().idxmax()
            info["가장 다른 설비"] = f"{k} ({z[k]:+.1f}σ)"
            if abs(z[k]) >= 0.5:
                lines.append(f"{ctx.group} = {k} 평균이 전체보다 {z[k]:+.1f}σ {'높음' if z[k] > 0 else '낮음'}")

    flagged = pd.DataFrame()
    if p != SLOT:
        cd = lot_average(df, ctx.lot_col, [p], {ctx.xcol, ctx.group}) if ctx.lot_col else df
        fig, flagged, _ = control_chart(cd, p, ctx.xcol, ctx.group, None, None)
        figs["관리도" + (" (LOT 평균)" if ctx.lot_col else "")] = fig
        info["관리도 이상"] = len(flagged)
        if len(flagged):
            kinds = ", ".join(f"{k} {v}건" for k, v in flagged["판정"].value_counts().items())
            ids = flagged[ctx.id_col].astype(str).tolist() if ctx.id_col in flagged.columns else []
            lines.append(f"관리도 이상 {len(flagged)}건 ({kinds})"
                         + (f": {', '.join(ids[:5])}{' …' if len(ids) > 5 else ''}" if ids else ""))
        flagged = flagged.assign(파라미터=p)
    return {"param": p, "judge": row["판정"], "lines": lines, "info": info, "figs": figs, "flagged": flagged}


def summarize(ctx, details) -> list:
    """리포트 맨 위 핵심 요약 문장."""
    out = [f"{ctx.target}: Good {int(ctx.good.sum())} / Bad {int(ctx.bad.sum())} {ctx.unit} "
           f"(기준 {ctx.thr:.4g} {'이상' if ctx.good_high else '이하'} = Good)"]
    res = ctx.res
    red = res[res["판정"] == "🔴 주요 의심"]
    yellow = res[res["판정"] == "🟡 관찰"]
    if len(red):
        out.append(f"주요 의심 파라미터 {len(red)}개: "
                   + ", ".join(f"{p} (d {d:+.2f})" for p, d in zip(red["파라미터"], red["효과크기 d"])))
    elif len(yellow):
        out.append("뚜렷한 주요 의심은 없고, 관찰 대상: " + ", ".join(yellow["파라미터"]))
    else:
        out.append("Good / Bad 사이에 뚜렷한 차이를 보이는 파라미터가 없습니다. 기준값이나 결과 항목을 바꿔 보세요.")
    for det in details[:3]:
        if det["judge"]:
            out.append(f"{det['param']}: " + " · ".join(det["lines"][1:3] or det["lines"][:1]))
    if ctx.lot_col:
        out.append(f"{ctx.target} 자체는 " + origin_text(lot_share(ctx.df, ctx.lot_col, ctx.target)))
    if ctx.match is not None and len(ctx.match):
        red = prioritize(ctx.match)
        red = red[red["판정"] == "🔴 불일치"]
        if len(red):
            out.append(f"챔버 매칭 불일치 {len(red)}건: " + "; ".join(
                f"{r['챔버']} {r['파라미터']} {r['차이']}" + (" (수율 영향)" if r["수율 영향"] else "")
                for _, r in red.head(3).iterrows()))
        else:
            out.append("챔버 매칭: 모든 챔버가 기준 범위 안에 있습니다.")
    return out


def render_html(ctx, headline, details) -> str:
    sections = [{"title": "원인 파라미터 순위", "tables": [gb_styled(html_safe(ctx.res)).hide(axis="index").to_html()]}]
    sections += [{"title": f"{i}. {det['param']}", "judge": det["judge"], "lines": det["lines"], "figs": det["figs"]}
                 for i, det in enumerate(details, 1)]
    if ctx.lot_col:
        items = [ctx.target] + [d["param"] for d in details if d["param"] != SLOT]
        share = pd.Series({c: lot_share(ctx.df, ctx.lot_col, c) for c in items}).dropna().sort_values()
        bar = go.Figure(go.Bar(x=share.values, y=share.index, orientation="h",
                               text=[f"{v:.0f}%" for v in share.values], textposition="outside"))
        bar.update_xaxes(range=[0, 110])
        sections.append({"title": "LOT 간 · LOT 내 변동", "figs": {"LOT 간 차이로 설명되는 비율 (%)": bar},
                         "note": "높을수록 LOT 단위 원인, 낮을수록 Wafer 단위 원인"})
    if ctx.match is not None and len(ctx.match):
        mm = prioritize(ctx.match)
        bad_rows = mm[mm["판정"] != "✅ 일치"].head(15)
        sections.append({
            "title": f"챔버 매칭 ({ctx.group})",
            "figs": {"챔버별 평균 오프셋 (σ) · 산포비": match_heatmap(ctx.match)},
            "tables": [html_safe(bad_rows).style.format({"평균": "{:.4g}", "표준편차": "{:.3g}", "기준 평균": "{:.4g}",
                                              "오프셋(σ)": "{:+.2f}", "산포비": "{:.2f}"}).hide(axis="index").to_html()
                       if len(bad_rows) else "<p>불일치 항목 없음</p>"],
            "note": "오프셋: 전체 챔버 중앙값과의 차이를 챔버 내부 산포(σ)로 나눈 값. |오프셋| ≥ 1σ 또는 산포비 ≥ 1.5배면 불일치."})
    return html_page("FDC 자동 분석 리포트",
                     f"분석 단위 {ctx.unit} {len(ctx.df)}개 · 결과 항목 {ctx.target} · 파라미터 {len(ctx.res)}개",
                     headline, sections,
                     "※ 통계적 연관성이며 인과관계를 보장하지 않습니다. 효과크기 d: |d| ≥ 0.8 큼, ≥ 0.5 중간. "
                     "보정 p-value: 여러 파라미터 동시 검정 보정(BH-FDR). 관리도: I-관리도(이동범위 σ), 연속 7점 드리프트 규칙.")


def render_excel(ctx, headline, details) -> bytes:
    flagged = [d["flagged"] for d in details if len(d["flagged"])]
    sheets = {"요약": pd.DataFrame({"핵심 요약": headline}),
              "원인 순위": ctx.res.set_index("파라미터"),
              "파라미터별 해석": pd.DataFrame([{**d["info"], "해석": " / ".join(d["lines"])} for d in details]
                                       ).set_index("파라미터")}
    if flagged:
        sheets["관리도 이상"] = pd.concat(flagged).set_index("파라미터")
    if ctx.match is not None and len(ctx.match):
        sheets["챔버 매칭"] = prioritize(ctx.match).set_index(["챔버", "파라미터"])
    sheets["데이터"] = ctx.df
    return to_excel(sheets)


def fdc_analysis(df, num_cols, cat_cols, date_cols, extra_sheets, unit="LOT", lot_col=None, id_col=None):
    if len(num_cols) < 2:
        st.warning("FDC 파라미터와 결과값, 수치형 항목이 2개 이상 필요합니다.")
        return

    c1, c2, c3 = st.columns(3)
    # 결과 항목: 이름(수율·CD·두께·불량 …) → 없으면 마지막 숫자 열 (결과표는 FDC 뒤에 붙음)
    target_default = guess([c for c in num_cols if c != SLOT], RESULT_KEYS, num_cols[-1])
    target = c1.selectbox("결과 항목 (수율·계측값)", num_cols, index=num_cols.index(target_default))
    lower_better = any(w in str(target).lower() for w in ("불량", "defect", "loss", "fail", "particle"))
    good_high = c2.radio("양호 방향", ["높을수록 양호", "낮을수록 양호"], horizontal=True,
                         index=1 if lower_better else 0) == "높을수록 양호"
    # 설비/챔버: 이름 또는 'ETCH-01' 같은 설비 모양 열 중, 여러 개면 결과값과 가장 관련 큰 열
    tools = [c for c in cat_cols if guess([c], TOOL_KEYS) or looks_tool(df[c])]
    tool_default = max(tools, key=lambda c: correlation_ratio(df[c], df[target]) if df[target].notna().any() else 0,
                       default=None)
    group_opts = ["(없음)"] + cat_cols
    group = c3.selectbox("설비/챔버 구분", group_opts,
                         index=group_opts.index(tool_default) if tool_default in group_opts else 0)
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
    res = gb_table(df, params, target, good, bad)

    impact = dict(zip(res["파라미터"], res["판정"])) if not res.empty else {}

    def matching(key, mparams, golden=None):
        m = chamber_matching(df, key, mparams, golden)
        return m.assign(**{"수율 영향": m["파라미터"].map(impact).fillna("")}) if len(m) else m

    names = ["📄 자동 리포트", "🔎 Good vs Bad 원인 파라미터", "🏭 설비별 상관", "⚖️ 챔버 매칭", "📉 트렌드·관리도"]
    if lot_col:
        names.append("🧩 LOT 간 · LOT 내 변동")
    tab_rep, tab_gb, tab_eq, tab_match, tab_cc, *tab_var = st.tabs(names)

    with tab_rep:
        if res.empty:
            st.info(f"Good/Bad 그룹에 각각 2 {unit} 이상이 있어야 리포트를 만들 수 있습니다.")
        else:
            c1, c2 = st.columns(2)
            top_n = (c1.slider("리포트에 담을 상위 파라미터 수", 1, min(10, len(res)), min(5, len(res)))
                     if len(res) > 1 else 1)
            xcol = c2.selectbox("관리도 X축", [SEQ] + date_cols, index=1 if date_cols else 0, key="rep_x")
            if id_col is None and len(df.columns) and df.columns[0] not in num_cols:
                id_col = df.columns[0]
            ctx = SimpleNamespace(df=df, res=res, target=target, y=y, good=good, bad=bad, thr=thr,
                                  good_high=good_high, unit=unit, lot_col=lot_col, id_col=id_col,
                                  group=group, xcol=xcol,
                                  match=matching(df[group], step_params(group, params)) if group != "(없음)" else None)
            details = [analyze_param(ctx, row) for _, row in res.head(top_n).iterrows()]
            headline = summarize(ctx, details)

            st.markdown("#### 핵심 요약\n" + "\n".join(f"- {t}" for t in headline))
            for i, det in enumerate(details, 1):
                with st.expander(f"{i}. {det['param']} {det['judge']}", expanded=i == 1):
                    st.markdown("\n".join(f"- {t}" for t in det["lines"]))
                    cols = st.columns(2)
                    for j, (title, fig) in enumerate(det["figs"].items()):
                        cols[j % 2].plotly_chart(fig.update_layout(title=title, height=320), width="stretch",
                                                 key=f"rep_{i}_{j}")

            sig = (target, thr, good_high, group, tuple(params), top_n, xcol, len(df), unit)
            if st.button("📄 리포트 파일 만들기", type="primary"):
                with st.spinner("리포트 만드는 중…"):
                    st.session_state["fdc_report"] = (sig, render_html(ctx, headline, details),
                                                      render_excel(ctx, headline, details))
            saved = st.session_state.get("fdc_report")
            if saved and saved[0] == sig:
                stamp = f"{datetime.now():%Y%m%d_%H%M}"
                d1, d2 = st.columns(2)
                d1.download_button("🌐 HTML 리포트 (그래프 포함 · 인쇄 → PDF 저장)", saved[1],
                                   file_name=f"FDC_리포트_{stamp}.html", mime="text/html", width="stretch")
                d2.download_button("📊 엑셀 리포트", saved[2], file_name=f"FDC_리포트_{stamp}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   width="stretch")
                st.caption("HTML은 인터넷 없이 열리며, 브라우저에서 Ctrl+P → 'PDF로 저장' 하면 PDF 보고서가 됩니다.")

    with tab_gb:
        if res.empty:
            st.info(f"Good/Bad 그룹에 각각 2 {unit} 이상이 있어야 비교할 수 있습니다.")
        else:
            st.dataframe(gb_styled(res), width="stretch", hide_index=True)
            st.caption("효과크기 d: Bad가 Good보다 표준편차 몇 배만큼 다른지 (|d| ≥ 0.8 큼, 0.5 중간). "
                       "상관 r은 이상치에 강한 스피어만 순위상관.")
            top = st.selectbox("분포 비교할 파라미터", res["파라미터"])
            d = df.loc[y.notna(), [top]].assign(판정=np.where(good[y.notna()], "Good", "Bad"))
            st.plotly_chart(px.box(d, x="판정", y=top, color="판정", points="all",
                                   color_discrete_map={"Good": "#5B8DEF", "Bad": "#EF7B6E"}),
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

    with tab_match:
        if not cat_cols:
            st.info("챔버·설비를 구분하는 열이 있어야 챔버 매칭을 할 수 있습니다.")
        else:
            c1, c2 = st.columns(2)
            default = group if group in cat_cols else guess(cat_cols, CHAMBER_KEYS, None)
            if default is None:  # 이름으로 못 찾으면 값 종류가 가장 적은 열
                default = min(cat_cols, key=lambda c: df[c].nunique())
            mcols = c1.multiselect("챔버 구분 열 (여러 개면 조합, 예: 설비 + 챔버)", cat_cols, default=[default])
            if not mcols:
                st.warning("챔버 구분 열을 선택하세요.")
            else:
                key = chamber_key(df, mcols)
                chambers = sorted(key.dropna().unique(), key=str)
                ref = c2.selectbox("비교 기준", ["전체 챔버 중앙값"] + [f"골든 챔버: {c}" for c in chambers],
                                   help="전체 챔버 중앙값: 튀는 챔버 하나에 기준이 끌려가지 않음 / "
                                        "골든 챔버: 기준 챔버 하나와 비교")
                golden = None if ref == "전체 챔버 중앙값" else ref.split(": ", 1)[1]
                if golden is None and len(chambers) == 2:
                    st.caption("⚠️ 챔버가 2개면 어느 쪽이 벗어났는지 구분할 수 없어 둘 다 표시됩니다. 골든 챔버를 지정해 보세요.")
                mparams = st.multiselect("매칭할 파라미터", [p for p in params if p != SLOT],
                                         default=step_params(mcols[0], params),
                                         help="'ETCH:설비' 로 매칭하면 ETCH Step 파라미터만 기본 선택됩니다.")
                m = matching(key, mparams, golden) if mparams else pd.DataFrame()
                if m.empty:
                    st.info("챔버 2개 이상, 챔버마다 3건 이상의 데이터가 필요합니다.")
                else:
                    st.write("**챔버별 매칭 점수**")
                    st.dataframe(match_scores(m, key, df, target).style
                                 .format({"매칭률(%)": "{:.0f}", f"{target} 평균": "{:.4g}"})
                                 .background_gradient(subset=["매칭률(%)"], cmap="RdYlGn", vmin=0, vmax=100),
                                 width="stretch")
                    st.plotly_chart(match_heatmap(m).update_layout(title="챔버별 평균 오프셋 (σ) · 산포비"),
                                    width="stretch")
                    st.caption("오프셋 = (챔버 평균 − 기준 평균) ÷ 챔버 내부 산포(σ). "
                               "|오프셋| ≥ 1σ 또는 산포비 ≥ 1.5배면 🔴 불일치, ≥ 0.5σ 또는 산포비 1.25배 이상·0.8배 이하면 🟡 주의. "
                               "기준은 전체 챔버 중앙값(또는 골든 챔버).")
                    mm = prioritize(m)
                    issues = mm[mm["판정"] != "✅ 일치"]
                    st.write(f"**불일치·주의 항목 {len(issues)}건** (수율 영향 파라미터 우선)")
                    st.dataframe(issues.style.format({"평균": "{:.4g}", "표준편차": "{:.3g}", "기준 평균": "{:.4g}",
                                                      "오프셋(σ)": "{:+.2f}", "산포비": "{:.2f}"}),
                                 width="stretch", hide_index=True)
                    sel = st.selectbox("챔버별 분포 보기", mparams,
                                       index=mparams.index(issues["파라미터"].iloc[0]) if len(issues) else 0)
                    view = df.assign(챔버=key).dropna(subset=["챔버"])
                    d1, d2 = st.columns(2)
                    fig = px.box(view, x="챔버", y=sel, color="챔버", points="all",
                                 category_orders={"챔버": chambers}, title=f"챔버별 {sel}")
                    if golden is not None:
                        fig.add_hline(y=view.loc[view["챔버"] == golden, sel].mean(), line=dict(dash="dash"),
                                      annotation_text="골든 평균")
                    d1.plotly_chart(fig.update_layout(showlegend=False), width="stretch")
                    d2.plotly_chart(px.box(view, x="챔버", y=target, color="챔버", points="all",
                                           category_orders={"챔버": chambers}, title=f"챔버별 {target}")
                                    .update_layout(showlegend=False), width="stretch")
                    st.caption("오른쪽 결과값 분포로 '불일치 챔버가 실제로 결과도 나쁜지' 함께 확인하세요.")

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
            cd, cc_unit = lot_average(df, lot_col, [param], {xcol, group}), "LOT"
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
            share = pd.Series({c: lot_share(df, lot_col, c) for c in items}).dropna().sort_values()
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
