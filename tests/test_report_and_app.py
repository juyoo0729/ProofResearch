import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import proofresearch.agent as agent_module
import proofresearch.workflow as workflow_module
from proofresearch.llm import AgentError, CostLedger, Settings
from proofresearch.models import ResearchRequest, RunReport
from proofresearch.report import render_csv, render_html, save_report
from proofresearch.workflow import run_research


def test_report_files_round_trip_and_csv_matches_known_values(tmp_path):
    report = run_research(ResearchRequest(tickers=["SAMPLE_A"], as_of=date(2026, 3, 1)))
    folder = save_report(report, tmp_path)
    assert {path.name for path in folder.iterdir()} == {"report.html", "comparison.csv", "evidence.json", "events.jsonl"}
    reloaded = RunReport.model_validate_json((folder / "evidence.json").read_text(encoding="utf-8"))
    assert reloaded == report
    rows = list(csv.DictReader(io.StringIO((folder / "comparison.csv").read_text(encoding="utf-8-sig"))))
    margin = next(row for row in rows if row["metric"] == "operating_margin" and row["period_end"] == "2025-12-31")
    assert margin["value"] == "20.0" and margin["status"] == "PASS"
    assert all(row["mode"] == "sample" and not row["source_urls"] for row in rows)
    html = (folder / "report.html").read_text(encoding="utf-8")
    assert "가상 예제" in html and "125,000,000.00 USD" in html
    assert "https://www.sec.gov/Archives/" not in html


def test_html_and_csv_escape_untrusted_text():
    report = run_research(ResearchRequest(tickers=["SAMPLE_A"], as_of=date(2026, 3, 1)))
    report.companies[0].name = '<script>alert("x")</script>'
    html = render_html(report)
    assert "<script>" not in html and "&lt;script&gt;" in html
    report.companies[0].name = '=HYPERLINK("https://example.invalid")'
    rows = list(csv.DictReader(io.StringIO(render_csv(report).decode("utf-8-sig"))))
    assert rows[0]["company"].startswith("'=")


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_app_sample_journey_and_period_mismatch():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    app.button[0].click().run()
    assert not app.exception and len(app.dataframe) == 1 and len(app.success) == 1
    assert len(app.get("download_button")) == 3
    app.multiselect[0].set_value(["SAMPLE_A", "SAMPLE_C"])
    app.button[0].click().run()
    assert not app.exception and "결산기간" in app.warning[0].value


def test_app_rejects_empty_selection_and_disables_unconfigured_sec(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.multiselect[0].set_value([])
    app.button[0].click().run()
    assert not app.exception and len(app.error) == 1
    assert "기업을 1~3개" in app.error[0].value
    assert len(app.dataframe) == 0
    app.multiselect[0].set_value(["SAMPLE_A"])
    app.date_input[0].set_value(None)
    app.button[0].click().run()
    assert not app.exception and len(app.error) == 1 and not app.dataframe
    assert "분석 기준일" in app.error[0].value
    app.radio[0].set_value("SEC 실제 공시").run()
    assert not app.exception and app.button[0].disabled


def test_app_rejects_whitespace_query_before_running_research(monkeypatch):
    research = Mock(wraps=run_research)
    monkeypatch.setattr(workflow_module, "run_research", research)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.text_area[0].set_value(" \n\t ")
    app.button[0].click().run()
    research.assert_not_called()
    assert not app.exception and len(app.error) == 1
    assert "공백만으로는 분석할 수 없습니다" in app.error[0].value
    assert not app.dataframe and not app.get("download_button")
    app.text_area[0].set_value("  매출을 비교해 주세요.  ")
    app.button[0].click().run()
    assert not app.exception and len(app.dataframe) == 1
    assert research.call_args.args[0].query == "매출을 비교해 주세요."
    assert not any("입력 조건이 바뀌었습니다" in item.value for item in app.info)


def test_app_agent_failure_and_mode_switch_do_not_reuse_old_report(monkeypatch):
    settings = Settings("synthetic-key-only", "gpt-6-luna", Decimal("500"), Decimal("50000"))
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setenv("SEC_USER_AGENT", "ProofResearch test@invalid.test")
    agent = Mock(side_effect=AgentError("API 잔액을 확인하세요."))
    monkeypatch.setattr(agent_module, "run_agent", agent)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.button[0].click().run()
    assert len(app.dataframe) == 1
    app.radio[0].set_value("SEC 실제 공시").run()
    assert not app.dataframe and not app.get("download_button")
    app.button[0].click().run()
    assert agent.call_count == 1 and not app.exception
    assert "API 잔액" in app.error[0].value
    assert not app.dataframe and not app.get("download_button")
    app.radio[0].set_value("가상 예제").run()
    app.button[0].click().run()
    assert not app.exception and len(app.dataframe) == 1
    assert agent.call_count == 1


@pytest.mark.parametrize("use_agent", [False, True])
def test_app_handles_corrupt_storage_without_exposing_details(tmp_path, monkeypatch, use_agent):
    settings = Settings("synthetic-key-only", "gpt-6-luna", Decimal("500"), Decimal("50000"))
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setenv("SEC_USER_AGENT", "ProofResearch test@invalid.test")
    cache = tmp_path / "private-storage.db"
    cache.write_bytes(b"invalid database content")

    def corrupt_research(request, **kwargs):
        return run_research(request, cache_path=cache, **kwargs)

    def corrupt_agent(*args, **kwargs):
        CostLedger(cache, settings)
        pytest.fail("Corrupt ledger must prevent analysis")

    monkeypatch.setattr(workflow_module, "run_research", corrupt_research)
    monkeypatch.setattr(agent_module, "run_agent", corrupt_agent)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio[0].set_value("SEC 실제 공시").run()
    app.checkbox[0].set_value(use_agent)
    app.button[0].click().run()
    assert not app.exception and len(app.error) == 1
    assert "데이터 저장소" in app.error[0].value
    assert "private-storage" not in app.error[0].value and "file is not a database" not in app.error[0].value
    assert not app.dataframe and not app.get("download_button")
    assert cache.read_bytes() == b"invalid database content"
    app.radio[0].set_value("가상 예제").run()
    app.button[0].click().run()
    assert not app.exception and len(app.dataframe) == 1
