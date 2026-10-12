import hashlib
import json
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .analysis import analyze_company, compare_companies, parse_filings, select_filings
from .models import COMPANIES, SAMPLE_COMPANIES, CompanyResult, ResearchRequest, RunReport
from .sec import SecClient, SecError
from .source import SourceDocument, reviewed_original, verify_company


def run_research(request: ResearchRequest, *, user_agent="",
                 cache_path: Path = Path(".cache/sec.sqlite3"), refresh=False, offline=False) -> RunReport:
    started = monotonic()
    events, snapshots, warnings, results = [], [], [], []

    def event(step, message, **details):
        events.append({"step": step, "message": message, "elapsed_seconds": round(monotonic() - started, 3), **details})

    def collect(client, url, ticker):
        snapshot = client.fetch(url, refresh=refresh)
        snapshots.append(snapshot.metadata())
        if snapshot.warning:
            warnings.append(f"{ticker}: {snapshot.warning}")
        event("collect", "SEC 응답 확보", ticker=ticker, source=snapshot.source,
              sha256=snapshot.sha256, url=url)
        return snapshot

    event("plan", "기준일 이전의 최근 2개 연차보고 기간과 4개 지표를 검사합니다.")
    if request.mode == "sample":
        raw = files("proofresearch").joinpath("sample.json").read_bytes()
        dataset = json.loads(raw)
        snapshots.append({"source": "sample", "sha256": hashlib.sha256(raw).hexdigest(),
                          "retrieved_at": datetime.now(timezone.utc).isoformat(),
                          "url": None, "warning": "합성 예제이며 실제 기업 재무정보가 아닙니다."})
        warnings.append("모든 예제 기업·수치는 기능 확인용으로 만든 가상 데이터입니다.")
        for ticker in request.tickers:
            name, cik = SAMPLE_COMPANIES[ticker]
            sample = dataset[ticker]
            filings = parse_filings(sample["submissions"], cik, request.as_of)
            results.append(analyze_company(ticker, name, filings, sample["companyfacts"], request.as_of, sample=True))
            event("validate", "예제 자료 계산·검사 완료", ticker=ticker)
    else:
        with SecClient(user_agent, cache_path, offline=offline) as client:
            for ticker in request.tickers:
                name, cik = COMPANIES[ticker]
                try:
                    submissions = collect(client, f"https://data.sec.gov/submissions/CIK{cik}.json", ticker).data
                    if int(submissions["cik"]) != int(cik) or ticker not in submissions["tickers"]:
                        raise ValueError("요청 기업과 제출 이력의 식별자가 다릅니다.")
                    filings = parse_filings(submissions["filings"]["recent"], cik, request.as_of)
                    history = submissions["filings"].get("files", [])
                    for page in sorted(history, key=lambda item: item["filingTo"], reverse=True):
                        if len(select_filings(filings, request.as_of)) >= 2:
                            break
                        if date.fromisoformat(page["filingFrom"]) > request.as_of:
                            continue
                        columns = collect(client, "https://data.sec.gov/submissions/" + page["name"], ticker).data
                        filings.extend(parse_filings(columns, cik, request.as_of))
                    facts = collect(client, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ticker).data
                    if int(facts["cik"]) != int(cik):
                        raise ValueError("요청 기업과 재무 응답의 식별자가 다릅니다.")
                    selected = select_filings(filings, request.as_of)
                    documents, errors, amendments = {}, {}, {}
                    for index, filing in enumerate(selected):
                        try:
                            document = SourceDocument(collect(client, filing.url(), ticker), filing)
                            documents[filing.accession] = document
                            if resolution := reviewed_original(document, filings):
                                original, note = resolution
                                documents[original.accession] = SourceDocument(
                                    collect(client, original.url(), ticker), original)
                                selected[index] = original
                                amendments[original.accession] = note
                                event("amendment", "해시가 일치하는 수정 범위 검토 기록에 따라 원본 재무 표를 대조합니다.",
                                      ticker=ticker, amendment=filing.accession, original=original.accession)
                        except (SecError, ValueError) as error:
                            errors[filing.accession] = str(error)
                            event("source", "원문 확보·검증 실패; 관련 수치는 보류", ticker=ticker,
                                  accession=filing.accession, reason=str(error))
                    result = analyze_company(ticker, name, selected, facts, request.as_of)
                    verify_company(result, documents, errors)
                    for year in result.years:
                        year.amendment = amendments.get(year.filing.accession)
                    results.append(result)
                    event("validate", "공시·기간·계정 및 원문 표 대조 완료", ticker=ticker,
                          passed=sum(metric.status == "PASS" for year in result.years for metric in year.metrics),
                          held=sum(metric.status == "HOLD" for year in result.years for metric in year.metrics))
                except SecError as error:
                    results.append(CompanyResult(ticker=ticker, name=name, warnings=[str(error)]))
                    event("collect", "SEC 자료 확보 실패; 해당 기업은 보류", ticker=ticker)
                except (ValueError, KeyError, TypeError, AttributeError):
                    results.append(CompanyResult(ticker=ticker, name=name,
                                                 warnings=["SEC 응답의 구조 또는 기업·공시 식별자가 유효하지 않습니다."]))
                    event("validate", "자료 검증 실패; 해당 기업은 보류", ticker=ticker)
    comparison = compare_companies(results)
    event("compare", comparison.reason, status=comparison.status)
    warnings.append("PASS는 자료·계산 규칙 통과를 뜻합니다. SEC 모드는 공시 원문 표의 수치도 자동 대조합니다. 독립적인 정확도 평가는 아직 수행하지 않았습니다.")
    event("report", "검사 결과와 근거·계산식으로 보고서를 구성했습니다.")
    return RunReport(run_id=uuid4().hex, created_at=datetime.now(timezone.utc), request=request,
                     companies=results, comparison=comparison, snapshots=snapshots,
                     events=events, warnings=warnings)
