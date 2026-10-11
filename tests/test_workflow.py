import io
import json
from copy import deepcopy
from datetime import date
from urllib.error import HTTPError
from urllib.request import OpenerDirector

import pytest

import proofresearch.sec as sec_module
from proofresearch.models import ResearchRequest
from proofresearch.report import render_csv
from proofresearch.workflow import run_research


@pytest.fixture
def fake_sec(sample, monkeypatch):
    recent = {key: values[:1] for key, values in sample["submissions"].items()}
    history = {key: values[1:] for key, values in sample["submissions"].items()}
    companyfacts = deepcopy(sample["companyfacts"])
    companyfacts["cik"] = 2488
    base = "https://data.sec.gov/"
    responses = {
        base + "submissions/CIK0000002488.json": {
            "cik": "0000002488", "tickers": ["AMD"],
            "filings": {"recent": recent, "files": [{"name": "CIK0000002488-submissions-001.json",
                                                       "filingFrom": "2025-01-01", "filingTo": "2025-12-31"}]}},
        base + "submissions/CIK0000002488-submissions-001.json": history,
        base + "api/xbrl/companyfacts/CIK0000002488.json": companyfacts,
    }
    calls = []
    def fake_open(self, request, timeout):
        calls.append(request.full_url)
        if request.full_url not in responses:
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        response = io.BytesIO(json.dumps(responses[request.full_url]).encode())
        response.url = request.full_url
        return response
    monkeypatch.setattr(OpenerDirector, "open", fake_open)
    monkeypatch.setattr(sec_module.time, "sleep", lambda _: None)
    return responses, calls


def test_sec_workflow_loads_older_submissions_and_calculates(tmp_path, fake_sec):
    _, calls = fake_sec
    report = run_research(ResearchRequest(mode="sec", tickers=["AMD"], as_of=date(2026, 3, 1)),
                          user_agent="ProofResearch test@invalid.test", cache_path=tmp_path / "cache.db")
    assert len(calls) == 3 and len(report.snapshots) == 3
    assert len(report.companies[0].years) == 2
    assert all(metric.status == "PASS" for metric in report.companies[0].years[-1].metrics)
    assert report.companies[0].years[-1].metrics[0].evidence[0].source_url.startswith("https://www.sec.gov/Archives/edgar/data/2488/")
    assert "test@invalid.test" not in report.model_dump_json()


def test_sec_partial_failure_keeps_successful_company_and_reports_hold(tmp_path, fake_sec):
    report = run_research(ResearchRequest(mode="sec", tickers=["AMD", "NVDA"], as_of=date(2026, 3, 1)),
                          user_agent="ProofResearch test@invalid.test", cache_path=tmp_path / "cache.db")
    assert len(report.companies[0].years) == 2
    assert report.companies[1].years == [] and "404" in report.companies[1].warnings[0]
    assert report.comparison.status == "HOLD"
    csv = render_csv(report).decode("utf-8-sig")
    assert "NVDA" in csv and "HOLD" in csv


def test_company_identity_mismatch_is_held(tmp_path, fake_sec):
    responses, _ = fake_sec
    responses["https://data.sec.gov/api/xbrl/companyfacts/CIK0000002488.json"]["cik"] = 999
    report = run_research(ResearchRequest(mode="sec", tickers=["AMD"], as_of=date(2026, 3, 1)),
                          user_agent="ProofResearch test@invalid.test", cache_path=tmp_path / "cache.db")
    assert report.companies[0].years == []
    assert "식별자" in report.companies[0].warnings[0]
