"""Smoke tests for the Streamlit app using Streamlit's AppTest framework.

These actually execute the app script and assert no exceptions are raised,
including after interacting with the Advisor form.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture
def at():
    test = AppTest.from_file(APP, default_timeout=120)
    test.run()
    return test


def test_app_boots_without_exception(at):
    assert not at.exception
    assert at.title[0].value == "📈 Trading Assistant"


def test_advisor_flow(at):
    assert not at.exception
    # Fill the profile form and submit.
    at.slider[0].set_value(7)            # risk tolerance
    at.number_input[0].set_value(12.0)   # target growth
    at.number_input[2].set_value(25000.0)  # capital
    at.checkbox[0].set_value(True)       # include crypto
    at.button[1].set_value(True).run(timeout=180)  # form submit (button[0] = refresh)
    assert not at.exception
    # Recommendations should be in session state and rendered.
    report = at.session_state["advice"]
    assert report is not None and len(report.picks) > 0
    assert abs(sum(r.weight for r in report.picks) - 1.0) < 1e-6
