import json
import multiprocessing
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import proofresearch.agent as agent
from proofresearch.llm import CostLedger, Limits, Settings
from proofresearch.models import Paragraph, ResearchRequest
from proofresearch.retrieval import SourceIndex
from proofresearch.workflow import run_research


def blocking_worker(connection, deadline, serialized, marker):
    connection.send(("progress", serialized))
    while True:
        Path(marker).write_text("running", encoding="utf-8")
        time.sleep(0.03)


def test_supervisor_terminates_stalled_work_and_keeps_partial_report(tmp_path):
    request = ResearchRequest(tickers=["SAMPLE_A"], as_of=date(2026,3,1))
    report = run_research(request)
    marker = tmp_path / "alive"
    before = {p.pid for p in multiprocessing.active_children()}
    result = agent.supervise(blocking_worker, (report.model_dump_json(), str(marker)), agent.empty_report(request), seconds=2)
    assert result.agent["status"] == "limited" and result.companies
    assert result.agent["elapsed_seconds"] < 4.5
    stamp = marker.stat().st_mtime_ns
    time.sleep(0.15)
    assert marker.stat().st_mtime_ns == stamp
    assert {p.pid for p in multiprocessing.active_children()} == before


def run_fake(tmp_path, monkeypatch, actions, result):
    settings = Settings("synthetic-key-only", "gpt-6-luna", Decimal("500"), Decimal("50000"))
    ledger = CostLedger(tmp_path / "cost.db", settings)
    limits = Limits(time.monotonic()+30)
    messages_seen = []
    class FakeClient:
        def __init__(self, *args):
            pass
        def complete(self, instructions, messages, tools):
            limits.consume("llm")
            messages_seen.append(list(messages))
            name, args = actions[min(limits.llm-1, len(actions)-1)]
            return {"output":[{"type":"function_call", "name":name, "arguments":json.dumps(args), "call_id":str(limits.llm)}]}
    monkeypatch.setattr(agent, "OpenAIClient", FakeClient)
    monkeypatch.setattr(agent, "run_research", lambda *a, **kw: result.model_copy(deep=True))
    try:
        final = agent.run_loop(result.request, settings, "unused", tmp_path / "sec.db", ledger, limits, "run", lambda _:None)
        return final, messages_seen
    finally:
        ledger.close()


def finance_args():
    return {"plan":["공시와 원문 수집", "동일 기간 검산", "관련 위험 문단 검색"], "tickers":["SAMPLE_A"],
            "as_of":"2026-03-01", "metrics":["revenue","operating_income","operating_margin","revenue_growth"], "refresh":False}


@pytest.fixture
def report():
    return run_research(ResearchRequest(tickers=["SAMPLE_A"],as_of=date(2026,3,1)))


def test_agent_replans_after_empty_search_and_only_accepts_real_citation(tmp_path, monkeypatch, report):
    filing = report.companies[0].years[-1].filing
    paragraph = Paragraph(id="p1", ticker="SAMPLE_A", filing=filing, section="Risk Factors",
        text="Supply constraints and export restrictions may impair our operations and cause delivery delays.",
        xpath="/html/body/p[1]",start_offset=0,end_offset=94,document_sha256="a"*64,source_url="https://www.sec.gov/")
    monkeypatch.setattr(agent,"build_index",lambda *a:SourceIndex([paragraph]))
    search={"reason":"위험 근거 검색", "query":"notfoundword", "tickers":["SAMPLE_A"],"period":"latest"}
    claim={"ticker":"SAMPLE_A","text":"공급 제약과 수출 규제로 운영에 차질이 발생할 수 있습니다.","evidence_id":"p1", "quote":paragraph.text}
    actions=[("research_financials",finance_args()),("search_original",search),
             ("search_original",{**search,"query":"supply export", "reason":"첫 검색에 근거가 없어 검색어를 바꿉니다."}),
             ("finish_report",{"stop_reason":"관련 원문 확보", "claims":[claim],"holds":[]})]
    final, messages = run_fake(tmp_path,monkeypatch,actions,report)
    assert final.agent["status"] == "completed" and len(final.narratives)==1
    assert final.agent["counts"] == {"tools":4,"llm":4,"retries":1}
    assert any(e["step"]=="retry" for e in final.events)
    assert "missing_companies" in messages[2][-1]["output"]


def test_invalid_company_and_fabricated_quote_cannot_be_exported(tmp_path, monkeypatch, report):
    search={"reason":"outside", "query":"risk", "tickers":["NVDA"],"period":"latest"}
    actions=[("research_financials",finance_args()),("search_original",search),
             ("finish_report",{"stop_reason":"finish", "claims":[{"ticker":"SAMPLE_A","text":"조작된 설명",
                "evidence_id":"invented", "quote":"This quotation was not in any retrieved evidence."}],"holds":[]})]
    final,_=run_fake(tmp_path,monkeypatch,actions,report)
    assert final.narratives == [] and final.agent["status"]=="limited"
    assert final.agent["counts"]["llm"]==6
    assert any("요청 기업 밖" in e["message"] for e in final.events)


def test_research_retries_stop_at_two(tmp_path, monkeypatch, report):
    monkeypatch.setattr(agent,"build_index",lambda *a:SourceIndex([]))
    search={"reason":"근거 부족으로 재검색", "query":"risk", "tickers":["SAMPLE_A"],"period":"latest"}
    actions=[("research_financials",finance_args()),("search_original",search)]
    final,_=run_fake(tmp_path,monkeypatch,actions,report)
    assert final.agent["status"]=="limited" and final.agent["counts"]["retries"]==2
    assert final.agent["counts"]["llm"]==5
