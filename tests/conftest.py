import json
from datetime import date
from importlib.resources import files

import pytest

from proofresearch.analysis import parse_filings, select_filings


@pytest.fixture
def sample():
    return json.loads(files("proofresearch").joinpath("sample.json").read_text(encoding="utf-8"))["SAMPLE_A"]


@pytest.fixture
def latest(sample):
    return select_filings(parse_filings(sample["submissions"], "9000000001", date(2026, 3, 1)), date(2026, 3, 1))[0]


@pytest.fixture
def source_html():
    def build(filing):
        year = filing.report_date.year
        revenue, income = ("125", "25") if year == 2025 else ("100", "15")
        return f'''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
        xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2025"
        xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
        xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"><body>
        <ix:header><ix:resources>
        <xbrli:context id="current"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{filing.cik}</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>{year}-01-01</xbrli:startDate><xbrli:endDate>{year}-12-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:context id="previous"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{filing.cik}</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>{year-1}-01-01</xbrli:startDate><xbrli:endDate>{year-1}-12-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
        </ix:resources></ix:header>
        <ix:nonNumeric name="dei:DocumentType">{filing.form}</ix:nonNumeric>
        <ix:nonNumeric name="dei:DocumentPeriodEndDate">{filing.report_date}</ix:nonNumeric>
        <ix:nonNumeric name="dei:EntityCentralIndexKey">{filing.cik}</ix:nonNumeric>
        <table><tr><td>Revenue</td>
        <td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="current" unitRef="usd" scale="6" format="ixt:num-dot-decimal" id="revenue">{revenue}</ix:nonFraction></td>
        <td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="previous" unitRef="usd" scale="6" format="ixt:num-dot-decimal" id="prior">100</ix:nonFraction></td></tr>
        <tr><td>Operating income</td><td><ix:nonFraction name="us-gaap:OperatingIncomeLoss" contextRef="current" unitRef="usd" scale="6" format="ixt:num-dot-decimal" id="income">{income}</ix:nonFraction></td></tr></table>
        </body></html>'''
    return build
