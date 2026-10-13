from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from proofresearch.analysis import (
    REVENUE_TAGS, analyze_company, calculate_growth, calculate_margin,
    extract_metric, parse_filings, select_filings,
)
from proofresearch.models import ResearchRequest
from proofresearch.workflow import run_research


def revenue_rows(sample):
    return sample["companyfacts"]["facts"]["us-gaap"][REVENUE_TAGS[0]]["units"]["USD"]


def test_sample_calculates_known_financial_results_and_preserves_evidence():
    report = run_research(ResearchRequest(tickers=["SAMPLE_A", "SAMPLE_B"], as_of=date(2026, 3, 1)))
    assert report.comparison.status == "PASS"
    assert len(report.companies[0].years) == 2
    revenue, income, margin, growth = report.companies[0].years[-1].metrics
    assert (revenue.value, income.value, margin.value, growth.value) == (
        Decimal("125000000"), Decimal("25000000"), Decimal("20"), Decimal("25"))
    assert growth.evidence[1].value == Decimal("100000000")
    assert growth.evidence[0].filing == growth.evidence[1].filing
    assert all(item.source_url is None for metric in [revenue, income, margin, growth] for item in metric.evidence)


def test_different_fiscal_periods_hold_cross_company_comparison():
    report = run_research(ResearchRequest(tickers=["SAMPLE_A", "SAMPLE_C"], as_of=date(2026, 3, 1)))
    assert report.comparison.status == "HOLD"
    assert "결산기간" in report.comparison.reason
    assert all(metric.status == "PASS" for company in report.companies for metric in company.years[-1].metrics)


def test_as_of_excludes_future_filings(sample):
    cutoff = date(2025, 12, 31)
    selected = select_filings(parse_filings(sample["submissions"], "9000000001", cutoff), cutoff)
    assert [filing.accession for filing in selected] == ["9000000001-25-000001"]
    result = analyze_company("SAMPLE_A", "예제", selected, sample["companyfacts"], cutoff, sample=True)
    assert result.years[-1].metrics[0].value == Decimal("100000000")
    assert result.years[-1].metrics[-1].status == "HOLD"


def test_amendment_is_selected_as_of_and_does_not_inherit_unverified_values(sample, latest):
    amended = latest.model_copy(update={"accession": "9000000001-26-000002", "form": "10-K/A", "filed": date(2026, 3, 2)})
    assert select_filings([latest, amended], date(2026, 3, 1)) == [latest]
    assert select_filings([latest, amended], date(2026, 3, 3)) == [amended]
    metric = extract_metric(sample["companyfacts"], amended, "revenue")
    assert metric.status == "HOLD" and "수정본" in metric.reason


def test_conflicting_filing_metadata_is_rejected(latest):
    with pytest.raises(ValueError, match="식별자"):
        select_filings([latest, latest.model_copy(update={"document": "different.htm"})], date(2026, 3, 1))


@pytest.mark.parametrize("value", ["NaN", "Infinity", True, None])
def test_invalid_values_are_held(sample, latest, value):
    revenue_rows(sample)[-1]["val"] = value
    assert extract_metric(sample["companyfacts"], latest, "revenue").status == "HOLD"


def test_missing_currency_does_not_default_to_usd(sample, latest):
    units = sample["companyfacts"]["facts"]["us-gaap"][REVENUE_TAGS[0]]["units"]
    units["EUR"] = units.pop("USD")
    assert extract_metric(sample["companyfacts"], latest, "revenue").status == "HOLD"


def test_conflicting_values_hold_but_identical_duplicates_are_allowed(sample, latest):
    rows = revenue_rows(sample)
    rows.append(deepcopy(rows[-1]))
    assert extract_metric(sample["companyfacts"], latest, "revenue").status == "PASS"
    rows[-1]["val"] += 1
    assert extract_metric(sample["companyfacts"], latest, "revenue").status == "HOLD"


def test_quarterly_context_does_not_replace_annual_value(sample, latest):
    row = deepcopy(revenue_rows(sample)[-1])
    row.update(start="2025-10-01", val=42)
    revenue_rows(sample).append(row)
    assert extract_metric(sample["companyfacts"], latest, "revenue").value == Decimal("125000000")


def test_submission_and_fact_dates_must_match(sample, latest):
    revenue_rows(sample)[-1]["filed"] = "2026-04-01"
    assert extract_metric(sample["companyfacts"], latest, "revenue").status == "HOLD"


def test_zero_denominator_and_period_mismatch_hold(sample, latest):
    revenue_rows(sample)[-1]["val"] = 0
    revenue = extract_metric(sample["companyfacts"], latest, "revenue")
    income = extract_metric(sample["companyfacts"], latest, "operating_income")
    assert calculate_margin(revenue, income).status == "HOLD"
    revenue_rows(sample)[-1]["val"] = 100
    revenue = extract_metric(sample["companyfacts"], latest, "revenue")
    income.evidence[0].start += timedelta(days=1)
    assert calculate_margin(revenue, income).status == "HOLD"


def test_growth_uses_restated_comparative_from_same_filing(sample, latest):
    revenue_rows(sample)[1]["val"] = 90000000
    current = extract_metric(sample["companyfacts"], latest, "revenue")
    previous = extract_metric(sample["companyfacts"], latest, "revenue", end=date(2024, 12, 31))
    growth = calculate_growth(current, previous)
    assert growth.status == "PASS"
    assert growth.value == Decimal("38.88888888888888888888888890")
    assert previous.evidence[0].value == Decimal("90000000")


def test_missing_comparative_does_not_fall_back_to_older_filing(sample):
    del revenue_rows(sample)[1]
    filings = parse_filings(sample["submissions"], "9000000001", date(2026, 3, 1))
    result = analyze_company("SAMPLE_A", "예제", filings, sample["companyfacts"], date(2026, 3, 1))
    assert result.years[-1].metrics[-1].status == "HOLD"


def test_both_reporting_years_use_their_own_filing_comparatives(sample):
    rows = revenue_rows(sample)
    older_prior = {**rows[0], "start":"2023-01-01", "end":"2023-12-31", "val":80000000}
    rows.append(older_prior)
    filings = parse_filings(sample["submissions"], "9000000001", date(2026,3,1))
    result = analyze_company("SAMPLE_A", "예제", filings, sample["companyfacts"], date(2026,3,1))
    assert [len(y.metrics) for y in result.years] == [4,4]
    assert [y.metrics[-1].value for y in result.years] == [Decimal("25"),Decimal("25")]
    for year in result.years:
        assert {e.filing.accession for e in year.metrics[-1].evidence} == {year.filing.accession}
    older_prior["accn"] = filings[0].accession
    result = analyze_company("SAMPLE_A", "예제", filings, sample["companyfacts"], date(2026,3,1))
    assert result.years[0].metrics[-1].status == "HOLD"
    assert result.years[-1].metrics[-1].status == "PASS"


def test_nonconsecutive_growth_periods_and_zero_previous_revenue_hold(sample, latest):
    current = extract_metric(sample["companyfacts"], latest, "revenue")
    previous = extract_metric(sample["companyfacts"], latest, "revenue", end=date(2024, 12, 31))
    previous.evidence[0].end -= timedelta(days=1)
    assert calculate_growth(current, previous).status == "HOLD"
    previous.evidence[0].end += timedelta(days=1)
    previous.evidence[0].value = Decimal(0)
    assert calculate_growth(current, previous).status == "HOLD"


def test_malformed_parallel_submission_arrays_are_rejected(sample):
    sample["submissions"]["filingDate"].pop()
    with pytest.raises(ValueError, match="열 길이"):
        parse_filings(sample["submissions"], "9000000001", date(2026, 3, 1))


@pytest.mark.parametrize("kwargs", [
    {"tickers": []}, {"tickers": ["SAMPLE_A", "sample_a"]}, {"tickers": ["AMD"]},
    {"mode": "sec", "tickers": ["UNKNOWN"]}, {"as_of": date.today() + timedelta(days=1)},
    {"query": " \n\t "}, {"query": "\u00a0\u3000"},
])
def test_request_boundary_validation(kwargs):
    args = {"tickers": ["SAMPLE_A"], "as_of": date(2026, 3, 1), **kwargs}
    with pytest.raises(ValidationError):
        ResearchRequest(**args)
