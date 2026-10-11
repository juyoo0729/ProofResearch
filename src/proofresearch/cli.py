import argparse
import os
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .models import ResearchRequest
from .report import save_report
from .sec import SecError
from .workflow import run_research


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ProofResearch 공시 비교 검산")
    parser.add_argument("mode", choices=["sample", "sec"])
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args()
    try:
        request = ResearchRequest(mode=args.mode,
            tickers=args.tickers or (["SAMPLE_A", "SAMPLE_B"] if args.mode == "sample" else ["AMD", "INTC", "NVDA"]),
            as_of=args.as_of or (date(2026, 3, 1) if args.mode == "sample" else date.today()))
        report = run_research(request, user_agent=os.environ.get("SEC_USER_AGENT", ""))
        folder = save_report(report, args.output)
    except (ValidationError, SecError, OSError) as error:
        print(f"실행 실패: {error}", file=sys.stderr)
        return 1
    print(f"보고서: {folder.resolve() / 'report.html'}")
    print(f"기업 간 비교: {report.comparison.status} — {report.comparison.reason}")
    if args.mode == "sample":
        print("가상 예제입니다. 실제 기업 재무정보가 아닙니다.")
    if not any(company.years for company in report.companies):
        print("확보된 공시가 없습니다. 보고서의 보류 사유를 확인하세요.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
