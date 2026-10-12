import hashlib
import json
from datetime import date
from decimal import Decimal
from importlib.resources import files
from types import SimpleNamespace

import pytest

from proofresearch.analysis import analyze_company, extract_metric, parse_filings
from proofresearch.models import Filing
from proofresearch.report import render_csv, render_html
from proofresearch.sec import Snapshot
from proofresearch.source import SourceDocument, reviewed_original, verify_company


def document(body, filing):
    return SourceDocument(Snapshot(body, filing.url(), "2026-10-11T00:00:00+00:00",
                                  hashlib.sha256(body.encode()).hexdigest(), "cache"), filing)


def test_visible_table_matches_exact_api_value_and_has_coordinates(sample, latest, source_html):
    doc = document(source_html(latest), latest)
    evidence = extract_metric(sample["companyfacts"], latest, "revenue").evidence[0]
    location, _ = doc.verify(evidence)
    assert (location.table, location.row, location.column) == (1, 1, 2)
    assert (location.element_id, location.displayed_text, location.scale) == ("revenue", "125", 6)
    assert location.row_label == "Revenue" and location.xpath.endswith("ix:nonfraction")


@pytest.mark.parametrize(("before", "after"), [
    ('id="revenue">125', 'id="revenue">126'),
    ('id="revenue">125', 'id="revenue">NaN'),
    ('scale="6"', 'scale="3"'),
    ('ixt:num-dot-decimal', 'ixt:unknown-format'),
    ('iso4217:USD</', 'iso4217:EUR</'),
    ('contextRef="current"', 'contextRef="unknown"'),
    ('2025-01-01', '2025-10-01'),
    ('<table>', '<table style="display: none">'),
    ('<xbrli:period>', '<xbrli:scenario>segment detail</xbrli:scenario><xbrli:period>'),
    ('scheme="http://www.sec.gov/CIK">9000000001', 'scheme="http://www.sec.gov/CIK">9000000002'),
])
def test_wrong_values_units_periods_scope_and_hidden_tables_are_held(sample, latest, source_html, before, after):
    doc = document(source_html(latest).replace(before, after), latest)
    evidence = extract_metric(sample["companyfacts"], latest, "revenue").evidence[0]
    assert doc.verify(evidence)[0] is None


def test_negative_income_uses_inline_sign_without_float(sample, latest, source_html):
    body = source_html(latest).replace('id="income"', 'sign="-" id="income"')
    evidence = extract_metric(sample["companyfacts"], latest, "operating_income").evidence[0]
    evidence.value = Decimal("-25000000")
    location, _ = document(body, latest).verify(evidence)
    assert location.sign == "-" and location.displayed_text == "25"


def test_conflicting_visible_duplicates_are_not_silently_selected(sample, latest, source_html):
    body = source_html(latest)
    row = body[body.index('<tr><td>Revenue'):body.index('</tr>') + len('</tr>')]
    extra = row.replace('id="revenue"', 'id="other"').replace('id="prior"', 'id="other-prior"').replace('>125<', '>126<')
    evidence = extract_metric(sample["companyfacts"], latest, "revenue").evidence[0]
    assert document(body.replace('</table>', extra + '</table>'), latest).verify(evidence)[0] is None
    identical = extra.replace('>126<', '>125<')
    assert document(body.replace('</table>', identical + '</table>'), latest).verify(evidence)[0] is not None
    with pytest.raises(ValueError, match="중복"):
        document(body.replace('</table>', row + '</table>'), latest)


def test_cover_identity_and_filing_scope_are_enforced(sample, latest, source_html):
    body = source_html(latest)
    with pytest.raises(ValueError, match="기업"):
        document(body.replace('name="dei:EntityCentralIndexKey">9000000001', 'name="dei:EntityCentralIndexKey">9000000002'), latest)
    with pytest.raises(ValueError, match="보고"):
        document(body.replace('name="dei:DocumentPeriodEndDate">2025-12-31', 'name="dei:DocumentPeriodEndDate">2024-12-31'), latest)
    evidence = extract_metric(sample["companyfacts"], latest, "revenue").evidence[0]
    evidence.filing = latest.model_copy(update={"accession": "9000000001-26-000099"})
    assert document(body, latest).verify(evidence)[0] is None


def test_bad_source_also_holds_ratios_and_growth_without_losing_other_year(sample, latest, source_html):
    filings = parse_filings(sample["submissions"], latest.cik, date(2026, 3, 1))
    company = analyze_company("SAMPLE_A", "Example", filings, sample["companyfacts"], date(2026, 3, 1))
    documents = {f.accession: document(source_html(f), f) for f in filings}
    documents[latest.accession] = document(source_html(latest).replace('id="revenue">125', 'id="revenue">126'), latest)
    verify_company(company, documents, {})
    assert [m.status for m in company.years[-1].metrics] == ["HOLD", "PASS", "HOLD", "HOLD"]
    assert all(m.status == "PASS" for m in company.years[0].metrics[:3])
    assert company.years[0].metrics[3].status == "HOLD"  # Fixture omits that filing's prior revenue.
    assert all(m.value is None for m in company.years[-1].metrics if m.status == "HOLD")


def test_only_pinned_reviewed_amendment_can_use_matching_original():
    review = json.loads(files("proofresearch").joinpath("amendments.json").read_text(encoding="utf-8"))["0000002488-26-000021"]
    original = Filing(accession=review["original_accession"], form="10-K", filed=date(2026, 2, 4),
                      report_date=date(2025, 12, 27), document="amd-20251227.htm", cik="0000002488")
    amendment = original.model_copy(update={"accession": "0000002488-26-000021", "form": "10-K/A"})
    doc = SimpleNamespace(filing=amendment, snapshot=SimpleNamespace(sha256=review["document_sha256"]))
    assert reviewed_original(doc, [original, amendment])[0] == original
    assert reviewed_original(doc, [amendment]) is None
    assert reviewed_original(doc, [original.model_copy(update={"report_date": date(2024, 12, 28)})]) is None
    doc.snapshot.sha256 = "changed"
    assert reviewed_original(doc, [original, amendment]) is None
