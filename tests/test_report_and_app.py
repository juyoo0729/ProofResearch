import csv
import io
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

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
    assert len(app.dataframe) == 0
    app.radio[0].set_value("SEC 실제 공시").run()
    assert not app.exception and app.button[0].disabled
