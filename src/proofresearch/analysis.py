import hashlib
from datetime import date, timedelta
from decimal import Decimal, localcontext

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import CompanyResult, Comparison, Evidence, Filing, Metric, YearResult


REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)


def parse_filings(columns: dict, cik: str, as_of: date) -> list[Filing]:
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    arrays = [columns.get(key) for key in keys]
    if any(not isinstance(values, list) for values in arrays):
        raise ValueError("SEC 제출 이력의 필수 열이 없습니다.")
    if len({len(values) for values in arrays}) != 1:
        raise ValueError("SEC 제출 이력의 열 길이가 일치하지 않습니다.")
    filings = []
    for accession, form, filed, report_date, document in zip(*arrays, strict=True):
        if form not in ("10-K", "10-K/A"):
            continue
        if date.fromisoformat(filed) > as_of:
            continue
        filings.append(Filing(accession=accession, form=form, filed=filed,
                              report_date=report_date, document=document, cik=cik))
    return filings


def select_filings(filings: list[Filing], as_of: date) -> list[Filing]:
    by_accession = {}
    for filing in filings:
        if filing.filed > as_of:
            continue
        if filing.accession in by_accession and by_accession[filing.accession] != filing:
            raise ValueError("같은 공시 식별자에 서로 다른 제출 정보가 있습니다.")
        by_accession[filing.accession] = filing
    by_period = {}
    for filing in sorted(by_accession.values(), key=lambda f: (f.filed, f.accession)):
        by_period[filing.report_date] = filing
    return [by_period[end] for end in sorted(by_period, reverse=True)[:2]]


class FactRow(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    start: date
    end: date
    val: Decimal
    filed: date
    form: str
    accn: str


def hold(name: str, reason: str) -> Metric:
    return Metric(name=name, status="HOLD", unit="USD" if name in (
        "revenue", "operating_income") else "%", reason=reason)


def extract_metric(facts: dict, filing: Filing, name: str, *,
                   sample=False, end: date | None = None, tag: str | None = None) -> Metric:
    target_end = end or filing.report_date
    tags = (tag,) if tag else (REVENUE_TAGS if name == "revenue" else ("OperatingIncomeLoss",))
    try:
        taxonomy = facts["facts"]["us-gaap"]
        for candidate_tag in tags:
            raw_rows = taxonomy.get(candidate_tag, {}).get("units", {}).get("USD", [])
            candidates = []
            for raw in raw_rows:
                if raw.get("accn") != filing.accession or raw.get("end") != str(target_end):
                    continue
                row = FactRow.model_validate(raw)
                if row.filed != filing.filed or row.form != filing.form:
                    return hold(name, "공시 이력과 재무값의 제출일 또는 서식이 일치하지 않습니다.")
                # shortcut: annual durations only; review transition years before expanding this window.
                if not 350 <= (row.end - row.start).days + 1 <= 380:
                    continue
                candidates.append(row)
            if not candidates:
                continue
            unique = {(row.start, row.end, row.val) for row in candidates}
            if len(unique) != 1:
                return hold(name, "같은 공시·계정·종료일에 서로 다른 값 또는 시작일이 있습니다.")
            row = candidates[0]
            if name == "revenue" and row.val < 0:
                return hold(name, "음수 매출은 현재 검산 범위에서 해석할 수 없습니다.")
            fingerprint = f"{filing.accession}|{candidate_tag}|{row.start}|{row.end}|{row.val}|USD"
            evidence = Evidence(
                id=hashlib.sha256(fingerprint.encode()).hexdigest()[:16],
                tag=candidate_tag, value=row.val, unit="USD", start=row.start, end=row.end,
                filing=filing, source_url=filing.url(sample), sample=sample,
            )
            return Metric(name=name, status="PASS", value=row.val, unit="USD",
                          reason="선택 공시의 연간 USD 표준 계정을 확인했습니다.", evidence=[evidence])
    except (KeyError, TypeError, AttributeError, ValidationError):
        return hold(name, "재무 응답의 필수 필드 또는 값 형식이 유효하지 않습니다.")
    reason = "선택 공시에서 해당 기간의 연간 USD 표준 계정을 찾지 못했습니다."
    if filing.form == "10-K/A":
        reason += " 수정본에 없는 수치를 이전 공시에서 자동으로 가져오지 않습니다."
    return hold(name, reason)


def calculate_margin(revenue: Metric, income: Metric) -> Metric:
    if revenue.status != "PASS" or income.status != "PASS":
        return hold("operating_margin", "매출 또는 영업이익 근거가 부족합니다.")
    rev, inc = revenue.evidence[0], income.evidence[0]
    if (rev.start, rev.end, rev.unit, rev.filing.accession) != (
            inc.start, inc.end, inc.unit, inc.filing.accession):
        return hold("operating_margin", "매출과 영업이익의 기간·통화·공시가 다릅니다.")
    if rev.value <= 0:
        return hold("operating_margin", "매출이 0 이하이므로 비율을 계산하지 않습니다.")
    with localcontext() as context:
        context.prec = 28
        value = inc.value / rev.value * 100
    return Metric(name="operating_margin", status="PASS", value=value, unit="%",
                  reason="동일 공시·기간의 값으로 계산했습니다.",
                  formula=f"({inc.value} / {rev.value}) × 100", evidence=[inc, rev])


def calculate_growth(current: Metric, previous: Metric) -> Metric:
    if current.status != "PASS" or previous.status != "PASS":
        return hold("revenue_growth", "동일 공시에서 당기·전기 매출 근거를 모두 확인하지 못했습니다.")
    cur, prev = current.evidence[0], previous.evidence[0]
    if (cur.unit, cur.tag, cur.filing.accession) != (prev.unit, prev.tag, prev.filing.accession):
        return hold("revenue_growth", "당기·전기의 통화·계정·공시가 다릅니다.")
    if cur.start != prev.end + timedelta(days=1) or abs(
            (cur.end - cur.start).days - (prev.end - prev.start).days) > 7:
        return hold("revenue_growth", "연속된 비교 가능한 연간 기간이 아닙니다.")
    if prev.value <= 0:
        return hold("revenue_growth", "전기 매출이 0 이하이므로 증가율을 계산하지 않습니다.")
    with localcontext() as context:
        context.prec = 28
        value = (cur.value / prev.value - 1) * 100
    return Metric(name="revenue_growth", status="PASS", value=value, unit="%",
                  reason="해당 공시에 함께 보고된 당기·전기 매출을 사용했습니다.",
                  formula=f"({cur.value} / {prev.value} - 1) × 100", evidence=[cur, prev])


def analyze_company(ticker: str, name: str, filings: list[Filing], facts: dict,
                    as_of: date, *, sample=False) -> CompanyResult:
    selected = select_filings(filings, as_of)
    result = CompanyResult(ticker=ticker, name=name)
    if len(selected) < 2:
        result.warnings.append("기준일까지 제출된 연차보고서 2개 기간을 확보하지 못했습니다.")
    for filing in reversed(selected):
        revenue = extract_metric(facts, filing, "revenue", sample=sample)
        income = extract_metric(facts, filing, "operating_income", sample=sample)
        metrics = [revenue, income, calculate_margin(revenue, income)]
        if not revenue.evidence:
            growth = hold("revenue_growth", "당기·전기 매출 비교에 필요한 근거가 부족합니다.")
        else:
            current = revenue.evidence[0]
            previous = extract_metric(facts, filing, "revenue", sample=sample,
                                      end=current.start - timedelta(days=1), tag=current.tag)
            growth = calculate_growth(revenue, previous)
        metrics.append(growth)
        result.years.append(YearResult(filing=filing, metrics=metrics))
    return result


def compare_companies(companies: list[CompanyResult]) -> Comparison:
    if len(companies) < 2:
        return Comparison(status="HOLD", reason="기업 간 비교에는 2개 이상의 기업이 필요합니다.")
    signatures = []
    for company in companies:
        if not company.years or any(metric.status != "PASS" for metric in company.years[-1].metrics):
            return Comparison(status="HOLD", reason="일부 기업의 최신 지표 근거가 부족해 기업 간 직접 비교를 보류합니다.")
        revenue, income = company.years[-1].metrics[:2]
        rev, inc = revenue.evidence[0], income.evidence[0]
        signatures.append((rev.start, rev.end, rev.unit, rev.tag, inc.tag))
    if len(set(signatures)) != 1:
        return Comparison(status="HOLD", reason="기업별 결산기간 또는 표준 계정이 달라 동일 조건의 비교·순위를 보류합니다.")
    return Comparison(status="PASS", reason="기간·통화·표준 계정 조건이 일치합니다. 사업구조의 동질성은 별도로 검토하세요.")
