import io
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from time import monotonic
from urllib.error import URLError

import pytest

from proofresearch.llm import AgentError, CostLedger, LimitReached, Limits, OpenAIClient, Settings, budget_microwon


@pytest.fixture
def settings():
    return Settings("synthetic-key-only", "gpt-6-luna", Decimal("500"), Decimal("50000"))


@pytest.mark.parametrize(("kind", "maximum"), [("tools", 12), ("llm", 6), ("retries", 2)])
def test_limits_refuse_the_next_call(kind, maximum):
    limits = Limits(monotonic() + 10)
    for _ in range(maximum):
        limits.consume(kind)
    with pytest.raises(LimitReached):
        limits.consume(kind)
    assert getattr(limits, kind) == maximum
    with pytest.raises(LimitReached):
        Limits(monotonic() - 1).consume("tools")


def test_reserve_before_network_and_keep_unknown_cost(tmp_path, settings, monkeypatch):
    ledger = CostLedger(tmp_path / "cost.db", settings)
    client = OpenAIClient(settings, ledger, Limits(monotonic()+10), "run")
    def fail(request, timeout):
        assert ledger.summary("run")["unsettled_calls"] == 1
        raise URLError("PRIVATE transport information")
    monkeypatch.setattr(client.opener, "open", fail)
    with pytest.raises(AgentError) as error:
        client.complete("rules", [], [])
    assert "PRIVATE" not in str(error.value) and settings.api_key not in str(error.value)
    assert ledger.summary("run")["unsettled_calls"] == 1
    ledger.close()


def test_actual_usage_settles_and_malformed_usage_retains_reserve(tmp_path, settings, monkeypatch):
    ledger = CostLedger(tmp_path / "cost.db", settings)
    client = OpenAIClient(settings, ledger, Limits(monotonic()+10), "run")
    def response(request, timeout):
        body = json.loads(request.data)
        assert body["store"] is False and body["parallel_tool_calls"] is False
        assert body["model"] == "gpt-6-luna" and body["max_output_tokens"] == 3000
        return io.BytesIO(json.dumps({"status":"completed", "output":[],
            "usage":{"input_tokens":100,"output_tokens":20}}).encode())
    monkeypatch.setattr(client.opener, "open", response)
    client.complete("rules", [], [])
    summary = ledger.summary("run")
    assert summary["input_tokens"] == 100 and summary["unsettled_calls"] == 0
    assert Decimal(summary["reserved_or_charged_krw"]) == Decimal(budget_microwon(100,20))/1_000_000
    call_id = ledger.reserve("run", 1000)
    with pytest.raises(AgentError):
        ledger.settle(call_id, {"input_tokens": True, "output_tokens": 20})
    assert ledger.summary("run")["unsettled_calls"] == 1
    ledger.close()


def test_per_request_and_total_budget_survive_new_connections(tmp_path, settings):
    settings.per_request_krw = settings.total_krw = Decimal("4")
    path = tmp_path / "cost.db"
    first = CostLedger(path, settings)
    first.reserve("run", 100)
    with pytest.raises(LimitReached):
        first.reserve("run", 100)
    first.close()
    second = CostLedger(path, settings)
    with pytest.raises(LimitReached):
        second.reserve("another", 100)
    second.close()


def test_concurrent_reservations_cannot_spend_same_budget(tmp_path, settings):
    settings.per_request_krw = settings.total_krw = Decimal("4")
    path = tmp_path / "cost.db"
    CostLedger(path, settings).close()
    def reserve(index):
        ledger = CostLedger(path, settings)
        try:
            ledger.reserve(str(index), 100)
            return True
        except LimitReached:
            return False
        finally:
            ledger.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, range(2))) == 1
