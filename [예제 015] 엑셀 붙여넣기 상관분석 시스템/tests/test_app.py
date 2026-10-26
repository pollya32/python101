"""화면 전체가 각 분석 모드에서 오류 없이 그려지는지 (Streamlit AppTest)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")
MODES = ["🔗 상관분석", "🧪 FDC LOT/Wafer 분석", "🔧 부품 비용 분석", "📅 월별 비용 추이", "⚖️ 그룹 평균 비교"]


@pytest.mark.parametrize("sample", [0, 1, 2])
def test_every_mode_renders_with_each_sample(sample):
    at = AppTest.from_file(APP, default_timeout=240).run()
    at.button[sample].click().run()
    for mode in MODES:
        at.sidebar.radio[0].set_value(mode).run()
        assert not at.exception, (mode, [e.message for e in at.exception])


@pytest.mark.parametrize("sample, label", [(1, "리포트 파일"), (2, "리포트 파일")])
def test_reports_build(sample, label):
    at = AppTest.from_file(APP, default_timeout=240).run()
    at.button[sample].click().run()
    [b for b in at.button if label in b.label][0].click().run()
    assert not at.exception
    key = "fdc_report" if sample == 1 else "parts_report"
    _, html, xlsx = at.session_state[key]
    assert html.startswith("<!doctype html>") and len(xlsx) > 10_000
