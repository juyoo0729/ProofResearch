import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


# Reviewed against the official model page on 2026-10-11; recheck before expiry.
PRICE_REVIEWED = date(2026, 10, 11)
INPUT_USD_PER_MILLION = Decimal("0.10")
OUTPUT_USD_PER_MILLION = Decimal("0.50")
# Budget cushion, not a quoted exchange rate or the card issuer's settlement rate.
BUDGET_KRW_PER_USD = Decimal("2000")
MAX_OUTPUT_TOKENS = 3000


class AgentError(Exception):
    pass


class LimitReached(AgentError):
    pass


@dataclass
class Settings:
    api_key: str = field(repr=False)
    model: str
    per_request_krw: Decimal
    total_krw: Decimal

    @classmethod
    def from_env(cls):
        try:
            settings = cls(os.environ["OPENAI_API_KEY"], os.environ["OPENAI_MODEL"],
                Decimal(os.environ["PROOFRESEARCH_MAX_COST_KRW"]),
                Decimal(os.environ["PROOFRESEARCH_TOTAL_BUDGET_KRW"]))
            valid = (settings.model == "gpt-6-luna" and settings.api_key.isascii()
                     and len(settings.api_key) >= 12 and not any(c.isspace() or ord(c) < 32 for c in settings.api_key)
                     and settings.per_request_krw.is_finite() and settings.total_krw.is_finite()
                     and 0 < settings.per_request_krw <= settings.total_krw)
        except (KeyError, InvalidOperation):
            valid = False
        if not valid:
            raise AgentError("로컬 API 키·GPT-6 Luna 모델·요청당 및 전체 비용 한도를 확인하세요.")
        if not 0 <= (date.today() - PRICE_REVIEWED).days <= 30:
            raise AgentError("가격 검토 기록이 만료되었습니다. 모델 가격과 환산 여유를 확인한 뒤 유료 호출을 재개하세요.")
        return settings


@dataclass
class Limits:
    deadline: float
    tools: int = 0
    llm: int = 0
    retries: int = 0

    def check(self):
        if monotonic() >= self.deadline:
            raise LimitReached("요청 전체의 시간 한도에 도달했습니다.")

    def consume(self, kind):
        self.check()
        maximum = {"tools": 12, "llm": 6, "retries": 2}[kind]
        if getattr(self, kind) >= maximum:
            raise LimitReached(f"요청 전체의 {kind} 한도({maximum}회)에 도달했습니다.")
        setattr(self, kind, getattr(self, kind) + 1)

    def counts(self):
        return {name: getattr(self, name) for name in ("tools", "llm", "retries")}


def budget_microwon(input_tokens: int, output_tokens: int) -> int:
    # Include the maximum cache-write rate and a 10% regional pricing premium.
    usd = (Decimal(input_tokens) * INPUT_USD_PER_MILLION * Decimal("1.25")
           + Decimal(output_tokens) * OUTPUT_USD_PER_MILLION) * Decimal("1.10") / 1_000_000
    return int((usd * BUDGET_KRW_PER_USD * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class CostLedger:
    def __init__(self, path: Path, settings: Settings):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.settings = settings
        self.db.execute("""CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL,
            reserved INTEGER NOT NULL, charged INTEGER, usage_json TEXT, model TEXT NOT NULL)""")

    def close(self):
        self.db.close()

    def reserve(self, run_id: str, input_bound: int) -> str:
        amount = budget_microwon(input_bound, MAX_OUTPUT_TOKENS)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            total, run = self.db.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0), "
                "COALESCE(SUM(CASE WHEN run_id=? THEN COALESCE(charged,reserved) ELSE 0 END),0) FROM calls",
                (run_id,)).fetchone()
            if (total + amount > self.settings.total_krw * 1_000_000
                    or run + amount > self.settings.per_request_krw * 1_000_000):
                raise LimitReached("요청당 또는 전체 실험 비용 한도를 초과할 수 있어 호출을 중단했습니다.")
            call_id = uuid4().hex
            self.db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                (call_id, run_id, datetime.now(timezone.utc).isoformat(), amount, self.settings.model))
        return call_id

    def settle(self, call_id: str, usage: dict):
        counts = [usage.get(name) for name in ("input_tokens", "output_tokens")]
        if any(type(value) is not int or not 0 <= value <= 2_000_000 for value in counts):
            raise AgentError("API 사용량을 확인할 수 없어 예약 비용을 유지하고 추가 호출을 중단했습니다.")
        charged = budget_microwon(*counts)
        with self.db:
            reserved = self.db.execute("SELECT reserved FROM calls WHERE id=?", (call_id,)).fetchone()[0]
            self.db.execute("UPDATE calls SET charged=?, usage_json=? WHERE id=?",
                            (charged, json.dumps(usage), call_id))
        if charged > reserved:
            raise LimitReached("API 사용량이 예약 상한을 넘어 추가 호출을 중단했습니다.")

    def summary(self, run_id: str) -> dict:
        rows = self.db.execute("SELECT reserved, charged, usage_json FROM calls WHERE run_id=?", (run_id,)).fetchall()
        usages = [json.loads(row[2]) for row in rows if row[2]]
        input_tokens = sum(value["input_tokens"] for value in usages)
        output_tokens = sum(value["output_tokens"] for value in usages)
        return {"calls": len(rows), "input_tokens": input_tokens, "output_tokens": output_tokens,
                "standard_uncached_usd_estimate": str((input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000),
                "response_models": sorted({u["response_model"] for u in usages if isinstance(u.get("response_model"), str)}),
                "reserved_or_charged_krw": str(Decimal(sum(row[0] if row[1] is None else row[1] for row in rows)) / 1_000_000),
                "unsettled_calls": sum(row[1] is None for row in rows),
                "budget_krw_per_usd": str(BUDGET_KRW_PER_USD), "price_reviewed": str(PRICE_REVIEWED),
                "note": "사용량 기반 보수적 예산 환산입니다. 미확정 호출은 예약액을 유지하며 실제 카드 청구액과 다를 수 있습니다."}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AgentError("API 주소 변경을 허용하지 않습니다.")


class OpenAIClient:
    def __init__(self, settings: Settings, ledger: CostLedger, limits: Limits, run_id: str):
        self.settings, self.ledger, self.limits, self.run_id = settings, ledger, limits, run_id
        self.opener = build_opener(NoRedirect())

    def complete(self, instructions: str, messages: list, tools: list) -> dict:
        self.limits.consume("llm")
        payload = {"model": self.settings.model, "instructions": instructions, "input": messages,
                   "tools": tools, "parallel_tool_calls": False, "max_output_tokens": MAX_OUTPUT_TOKENS,
                   "reasoning": {"effort": "low"}, "store": False, "service_tier": "default",
                   "include": ["reasoning.encrypted_content"]}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # Include a bound for replayed reasoning, whose encrypted form can be shorter than its token count.
        input_bound = len(body) + 4096 + 6 * MAX_OUTPUT_TOKENS
        if input_bound > 200_000:
            raise LimitReached("근거와 대화 길이가 입력 한도를 초과했습니다.")
        call_id = self.ledger.reserve(self.run_id, input_bound)
        self.limits.check()
        request = Request("https://api.openai.com/v1/responses", data=body, headers={
            "Authorization": "Bearer " + self.settings.api_key, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=min(40, self.limits.deadline - monotonic())) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise AgentError("API 응답 크기 한도를 초과했습니다.")
            result = json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            if code in (400, 401, 403, 404, 422, 429):
                self.ledger.settle(call_id, {"input_tokens": 0, "output_tokens": 0})
            raise AgentError(f"OpenAI 요청 오류 HTTP {code}. 키·모델 권한·API 잔액과 요청 규격을 확인하세요.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise AgentError("API 연결 또는 응답 해석에 실패했습니다. 예약 비용을 유지합니다.") from None
        if not isinstance(result, dict):
            raise AgentError("API 응답 구조가 올바르지 않습니다.")
        usage = result.get("usage") or {}
        if not isinstance(usage, dict):
            raise AgentError("API 사용량 형식이 올바르지 않아 예약 비용을 유지합니다.")
        self.ledger.settle(call_id, {**usage, "response_model": result.get("model"), "response_id": result.get("id")})
        self.limits.check()
        if result.get("status") != "completed" or not isinstance(result.get("output"), list):
            raise AgentError("모델 응답이 완료되지 않았습니다. 근거 없는 설명을 만들지 않고 중단합니다.")
        return result
