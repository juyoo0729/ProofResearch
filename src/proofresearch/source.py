"""Read visible annual USD facts from SEC inline XBRL; never execute document content."""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from importlib.resources import files
from urllib.parse import quote

from lxml import html

from .analysis import REVENUE_TAGS
from .models import AmendmentNote, CompanyResult, Evidence, Filing, SourceLocation
from .sec import Snapshot


def text_content(element) -> str:
    return " ".join(element.text_content().split())


def local_name(element) -> str:
    return str(element.tag).rsplit(":", 1)[-1].lower()


def namespace(element, prefix: str) -> str:
    for ancestor in [element, *element.iterancestors()]:
        if value := ancestor.get("xmlns:" + prefix):
            return value
    return ""


def hidden(element) -> bool:
    for ancestor in [element, *element.iterancestors()]:
        style = re.sub(r"\s+", "", ancestor.get("style", "").lower())
        if (local_name(ancestor) in ("hidden", "header", "script", "style")
                or "hidden" in ancestor.attrib or "display:none" in style or "visibility:hidden" in style):
            return True
    return False


def parse_period(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        months = "January February March April May June July August September October November December".split()
        match = re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{4})", text)
        if not match or match[1] not in months:
            raise ValueError("공시 표지의 보고 종료일 형식을 확인할 수 없습니다.") from None
        return date(int(match[3]), months.index(match[1]) + 1, int(match[2]))


@dataclass
class TableFact:
    tag: str
    start: date
    end: date
    value: Decimal | None
    location: SourceLocation


class SourceDocument:
    def __init__(self, snapshot: Snapshot, filing: Filing):
        if not isinstance(snapshot.data, str) or snapshot.url != filing.url():
            raise ValueError("요청 공시와 원문 주소가 일치하지 않습니다.")
        self.snapshot, self.filing = snapshot, filing
        parser = html.HTMLParser(encoding="utf-8", no_network=True, remove_comments=True)
        self.tree = html.fromstring(snapshot.data.encode("utf-8"), parser=parser)
        self.elements = list(self.tree.iter())
        ids = [element.get("id") for element in self.elements if element.get("id")]
        if len(ids) != len(set(ids)):
            raise ValueError("원문에 중복된 위치 식별자가 있어 근거를 특정할 수 없습니다.")
        self._check_identity()
        self.facts = self._table_facts()

    def _check_identity(self):
        identity = {"DocumentType": set(), "DocumentPeriodEndDate": set(), "EntityCentralIndexKey": set()}
        for element in self.elements:
            prefix, _, name = element.get("name", "").partition(":")
            if name in identity and re.fullmatch(r"https?://xbrl.sec.gov/dei/\d{4}", namespace(element, prefix)):
                identity[name].add(text_content(element))
        try:
            valid = (identity["DocumentType"] == {self.filing.form}
                     and {parse_period(value) for value in identity["DocumentPeriodEndDate"]} == {self.filing.report_date}
                     and {int(value) for value in identity["EntityCentralIndexKey"]} == {int(self.filing.cik)})
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("원문 표지의 기업·공시 종류·보고 종료일이 제출 이력과 다릅니다.")

    def _table_facts(self) -> list[TableFact]:
        contexts, units = {}, {}
        for element in self.elements:
            if local_name(element) == "context":
                fields = {}
                for child in element.iterdescendants():
                    fields.setdefault(local_name(child), []).append(child)
                if any(key in fields for key in ("segment", "scenario", "explicitmember", "typedmember")):
                    continue
                try:
                    identifier, start, end = (fields[key] for key in ("identifier", "startdate", "enddate"))
                    if len(identifier) != 1 or len(start) != 1 or len(end) != 1:
                        continue
                    if identifier[0].get("scheme") != "http://www.sec.gov/CIK" or int(text_content(identifier[0])) != int(self.filing.cik):
                        continue
                    contexts[element.get("id")] = (date.fromisoformat(text_content(start[0])), date.fromisoformat(text_content(end[0])))
                except (KeyError, ValueError):
                    continue
            elif local_name(element) == "unit":
                measures = [child for child in element.iterdescendants() if local_name(child) == "measure"]
                if len(measures) == 1 and not any(local_name(child) == "divide" for child in element.iterdescendants()):
                    prefix, _, name = text_content(measures[0]).partition(":")
                    if name == "USD" and namespace(measures[0], prefix) == "http://www.xbrl.org/2003/iso4217":
                        units[element.get("id")] = "USD"

        tables = {element: index for index, element in enumerate(self.tree.iter("table"), 1)}
        facts = []
        for element in self.elements:
            if local_name(element) != "nonfraction" or hidden(element):
                continue
            prefix, _, tag = element.get("name", "").partition(":")
            if tag not in (*REVENUE_TAGS, "OperatingIncomeLoss") or not re.fullmatch(
                    r"https?://fasb.org/us-gaap/\d{4}", namespace(element, prefix)):
                continue
            period = contexts.get(element.get("contextref"))
            if period is None or not 350 <= (period[1] - period[0]).days + 1 <= 380 or element.get("unitref") not in units:
                continue
            ancestors = list(element.iterancestors())
            cell = next((a for a in ancestors if a.tag in ("td", "th")), None)
            row = next((a for a in ancestors if a.tag == "tr"), None)
            table = next((a for a in ancestors if a.tag == "table"), None)
            if cell is None or row is None or table is None:
                continue
            cells = [e for e in row if e.tag in ("td", "th")]
            rows = [e for e in table.iter("tr") if next(e.iterancestors("table"), None) is table]
            if cell not in cells or row not in rows:
                continue
            displayed = text_content(element)
            value, scale, sign = self._number(element, displayed)
            location = SourceLocation(document_sha256=self.snapshot.sha256,
                xpath=self.tree.getroottree().getpath(element), element_id=element.get("id"),
                table=tables[table], row=rows.index(row) + 1, column=cells.index(cell) + 1,
                row_label=next((text_content(c) for c in cells[:cells.index(cell)] if text_content(c)), "")[:300],
                displayed_text=displayed, cell_text=text_content(cell)[:300], scale=scale, sign=sign)
            facts.append(TableFact(tag, period[0], period[1], value, location))
        return facts

    @staticmethod
    def _number(element, displayed: str) -> tuple[Decimal | None, int, str]:
        sign, scale = element.get("sign", "+"), 0
        try:
            scale = int(element.get("scale", "0"))
            if not -12 <= scale <= 12 or sign not in ("+", "-"):
                return None, scale, sign
            if element.get("xsi:nil") or element.get("continuedat") or any(local_name(e) == "exclude" for e in element.iterdescendants()):
                return None, scale, sign
            transformation = element.get("format", "").rsplit(":", 1)[-1]
            if transformation == "num-dot-decimal":
                if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", displayed):
                    return None, scale, sign
                displayed = displayed.replace(",", "")
            elif transformation or not re.fullmatch(r"\d+(?:\.\d+)?", displayed):
                return None, scale, sign
            with localcontext() as context:
                context.prec = 40
                value = Decimal(displayed).scaleb(scale) * (-1 if sign == "-" else 1)
            if value.is_finite():
                return value, scale, sign
        except (ValueError, InvalidOperation):
            pass
        return None, scale, sign

    def verify(self, evidence: Evidence) -> tuple[SourceLocation | None, str]:
        if evidence.filing != self.filing:
            return None, "다른 공시의 원문은 근거로 사용할 수 없습니다."
        candidates = [fact for fact in self.facts if (fact.tag, fact.start, fact.end) ==
                      (evidence.tag, evidence.start, evidence.end)]
        if not candidates:
            return None, "공시 원문 표에서 같은 계정·기간·USD 수치를 찾지 못했습니다."
        if any(fact.value is None for fact in candidates):
            return None, "원문 표의 숫자 형식·배율을 해석할 수 없어 보류합니다."
        if {fact.value for fact in candidates} != {evidence.value}:
            return None, "Company Facts와 원문 표의 수치가 다르거나 원문 안에서 충돌합니다."
        return candidates[0].location, "구조화 값과 공시 원문 표의 계정·기간·단위·수치가 일치합니다."


def verify_company(company: CompanyResult, documents: dict[str, SourceDocument], errors: dict[str, str]):
    for year in company.years:
        document = documents.get(year.filing.accession)
        for metric in year.metrics:
            if metric.status != "PASS":
                continue
            reasons = []
            for evidence in metric.evidence:
                if document is None:
                    reasons.append(errors.get(year.filing.accession, "공시 원문을 확보하지 못했습니다."))
                    continue
                location, reason = document.verify(evidence)
                if location is None:
                    reasons.append(reason)
                else:
                    evidence.source_location = location
                    if location.element_id:
                        evidence.source_url = evidence.filing.url() + "#" + quote(location.element_id, safe="")
            if reasons:
                metric.status, metric.value = "HOLD", None
                metric.reason = " ".join(dict.fromkeys(reasons))
            else:
                metric.reason += " 공시 원문 표의 수치와 대조했습니다."


def reviewed_original(document: SourceDocument, filings: list[Filing]) -> tuple[Filing, AmendmentNote] | None:
    reviews = json.loads(files("proofresearch").joinpath("amendments.json").read_text(encoding="utf-8"))
    amendment = document.filing
    review = reviews.get(amendment.accession)
    if amendment.form != "10-K/A" or not review or review["document_sha256"] != document.snapshot.sha256:
        return None
    matches = [filing for filing in filings if filing.accession == review["original_accession"]
               and filing.form == "10-K" and filing.cik == amendment.cik
               and filing.report_date == amendment.report_date and filing.filed <= amendment.filed]
    if len(matches) != 1:
        return None
    return matches[0], AmendmentNote(filing=amendment, **{key: value for key, value in review.items()
                                                       if key != "original_accession"})
