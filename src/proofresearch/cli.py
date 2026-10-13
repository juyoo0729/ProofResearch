import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .models import ResearchRequest
from .report import save_report
from .sec import SecError
from .workflow import run_research
from .agent import run_agent
from .llm import AgentError


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ProofResearch 공시 비교 검산")
    parser.add_argument("mode", choices=["sample", "sec"])
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--agent", action="store_true", help="원문 검색과 AI 해석을 포함합니다. 로컬 키·비용 한도가 필요합니다.")
    parser.add_argument("--query", default=ResearchRequest.model_fields["query"].default)
    args = parser.parse_args()
    try:
        request = ResearchRequest(mode=args.mode,
            tickers=args.tickers or (["SAMPLE_A", "SAMPLE_B"] if args.mode == "sample" else ["AMD", "INTC", "NVDA"]),
            as_of=args.as_of or (date(2026, 3, 1) if args.mode == "sample" else date.today()), query=args.query)
        report = (run_agent if args.agent else run_research)(request, user_agent=os.environ.get("SEC_USER_AGENT", ""))
        folder = save_report(report, args.output)
    except sqlite3.DatabaseError:
        print("실행 실패: 데이터 저장소를 읽거나 쓰지 못했습니다. 폴더 권한·디스크 공간과 저장소 상태를 확인한 뒤 다시 시도하세요.", file=sys.stderr)
        return 1
    except (ValidationError, SecError, AgentError, OSError) as error:
        print(f"실행 실패: {error}", file=sys.stderr)
        return 1
    print(f"보고서: {folder.resolve() / 'report.html'}")
    print(f"기업 간 비교: {report.comparison.status} — {report.comparison.reason}")
    if report.agent:
        print(f"AI 실행: {report.agent.get('status')} / {report.agent.get('counts')} / 예산 환산 {report.agent.get('cost', {}).get('reserved_or_charged_krw', '?')}원")
    if args.mode == "sample":
        print("가상 예제입니다. 실제 기업 재무정보가 아닙니다.")
    if not any(company.years for company in report.companies):
        print("확보된 공시가 없습니다. 보고서의 보류 사유를 확인하세요.", file=sys.stderr)
        return 2
    if report.agent and report.agent.get("status") != "completed":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
