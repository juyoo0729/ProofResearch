from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


COMPANIES = {
    "AMD": ("Advanced Micro Devices", "0000002488"),
    "INTC": ("Intel", "0000050863"),
    "NVDA": ("NVIDIA", "0001045810"),
}
SAMPLE_COMPANIES = {
    "SAMPLE_A": ("예제 기업 A", "9000000001"),
    "SAMPLE_B": ("예제 기업 B", "9000000002"),
    "SAMPLE_C": ("예제 기업 C", "9000000003"),
}
METRICS = {
    "revenue": "매출",
    "operating_income": "영업이익",
    "operating_margin": "영업이익률",
    "revenue_growth": "전년 대비 매출증가율",
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ResearchRequest(Model):
    mode: Literal["sample", "sec"] = "sample"
    tickers: list[str] = Field(min_length=1, max_length=3)
    as_of: date
    query: str = Field(default="매출과 수익성의 변화, 비교 제약과 주요 사업 위험을 근거와 함께 설명해 주세요.", min_length=1, max_length=1000)

    @field_validator("query")
    @classmethod
    def check_query(cls, value):
        if not value.strip():
            raise ValueError("공백만으로는 분석할 수 없습니다.")
        return value

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, value):
        result = [ticker.strip().upper() for ticker in value]
        if len(set(result)) != len(result):
            raise ValueError("기업을 중복해서 선택할 수 없습니다.")
        return result

    @model_validator(mode="after")
    def check_scope(self):
        allowed = SAMPLE_COMPANIES if self.mode == "sample" else COMPANIES
        if any(ticker not in allowed for ticker in self.tickers):
            raise ValueError("현재 모드에서 지원하지 않는 기업입니다.")
        if not date(1994, 1, 1) <= self.as_of <= date.today():
            raise ValueError("기준일은 1994년 이후부터 오늘까지 선택하세요.")
        return self


class Filing(Model):
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    form: Literal["10-K", "10-K/A"]
    filed: date
    report_date: date
    document: str = Field(pattern=r"^[a-zA-Z0-9_.-]+\.(?:htm|html)$")
    cik: str = Field(pattern=r"^\d{10}$")

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.report_date > self.filed:
            raise ValueError("보고기간 종료일이 제출일보다 늦습니다.")
        return self

    def url(self, sample: bool = False) -> str | None:
        if sample:
            return None
        accession = self.accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{accession}/{quote(self.document)}"


class Evidence(Model):
    id: str
    tag: str
    value: Decimal
    unit: Literal["USD"]
    start: date
    end: date
    filing: Filing
    source_url: str | None
    sample: bool
    source_location: "SourceLocation | None" = None


class SourceLocation(Model):
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    xpath: str
    element_id: str | None
    table: int = Field(ge=1)
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    row_label: str
    displayed_text: str
    cell_text: str
    scale: int
    sign: str


class Metric(Model):
    name: Literal["revenue", "operating_income", "operating_margin", "revenue_growth"]
    status: Literal["PASS", "HOLD"]
    value: Decimal | None = None
    unit: Literal["USD", "%"]
    reason: str
    formula: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class AmendmentNote(Model):
    filing: Filing
    document_sha256: str
    note_xpath: str
    scope: str
    reviewed_on: date


class YearResult(Model):
    filing: Filing
    metrics: list[Metric]
    amendment: AmendmentNote | None = None


class CompanyResult(Model):
    ticker: str
    name: str
    years: list[YearResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Comparison(Model):
    status: Literal["PASS", "HOLD"]
    reason: str


class Paragraph(Model):
    id: str
    ticker: str
    filing: Filing
    section: str
    text: str
    xpath: str
    start_offset: int
    end_offset: int
    document_sha256: str
    source_url: str


class NarrativeClaim(Model):
    ticker: str
    text: str = Field(max_length=600)
    evidence_id: str
    quote: str = Field(min_length=20, max_length=350)


class RunReport(Model):
    run_id: str
    created_at: datetime
    request: ResearchRequest
    companies: list[CompanyResult]
    comparison: Comparison
    snapshots: list[dict]
    events: list[dict]
    warnings: list[str]
    paragraphs: list[Paragraph] = Field(default_factory=list)
    narratives: list[NarrativeClaim] = Field(default_factory=list)
    agent: dict = Field(default_factory=dict)
