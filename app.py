import os
from datetime import date
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from proofresearch.models import COMPANIES, METRICS, SAMPLE_COMPANIES, ResearchRequest
from proofresearch.report import comparison_rows, display_value, financial_interpretations, render_csv, render_html
from proofresearch.sec import SecError, validate_user_agent
from proofresearch.workflow import run_research
from proofresearch.agent import run_agent
from proofresearch.llm import AgentError, Settings


st.set_page_config(page_title="ProofResearch | 공시 비교", page_icon="📑", layout="wide")
st.html(f"<style>{(Path(__file__).parent / 'ui' / 'styles.css').read_text(encoding='utf-8')}</style>")
st.html("""
<header class="pr-brand">
  <div class="pr-wordmark"><span class="pr-mark" aria-hidden="true">P</span>ProofResearch</div>
  <span class="pr-purpose">공시 리서치 워크스페이스</span>
</header>
<section class="pr-intro" aria-labelledby="pr-title">
  <div><h1 id="pr-title">공시를 비교하고, 근거를 확인하세요.</h1>
  <p>공시를 나란히 살펴보고, 비교할 수 있는 값과 보류할 항목을 구분합니다.</p></div>
  <div class="pr-scope"><span>연차공시에서 확인할 지표</span>
  <p>매출 · 영업이익<br>영업이익률 · 매출증가율</p></div>
</section>
""")

mode_label = st.radio("자료 선택", ["가상 예제", "SEC 실제 공시"], horizontal=True)
mode = "sample" if mode_label == "가상 예제" else "sec"
companies = SAMPLE_COMPANIES if mode == "sample" else COMPANIES
user_agent = os.environ.get("SEC_USER_AGENT", "")
ready = True
if mode == "sample":
    st.info("가상 기업의 합성 자료입니다. 예제 기업 C는 결산기간이 달라 비교가 보류됩니다.")
else:
    try:
        validate_user_agent(user_agent)
    except SecError:
        ready = False
        st.info("실제 조회를 준비 중입니다. 운영자 연락처를 설정한 뒤 사용할 수 있습니다. 지금은 가상 예제로 살펴보세요.")

with st.form(f"research_{mode}"):
    st.subheader("비교 조건")
    left, right = st.columns([3, 1])
    with left:
        tickers = st.multiselect("비교할 기업", list(companies), default=list(companies)[:2],
                                format_func=lambda ticker: companies[ticker][0], max_selections=3)
    with right:
        as_of = st.date_input("분석 기준일", value=date(2026, 3, 1) if mode == "sample" else date.today(),
                              min_value=date(1994, 1, 1), max_value=date.today())
    st.caption("기준일 이전 최근 2개 연차기간을 비교합니다. 증가율은 같은 공시에 실린 전기 매출로 계산합니다.")
    query = st.text_area("확인하고 싶은 내용", value=ResearchRequest.model_fields["query"].default,
                         max_chars=1000, height=75)
    use_agent = False
    if mode == "sec":
        try:
            settings = Settings.from_env()
            use_agent = st.checkbox("AI 원문 검색·사업 위험 설명 포함", value=True)
            st.caption(f"AI 분석은 요청당 최대 {settings.per_request_krw:,.0f}원, 전체 실험 {settings.total_krw:,.0f}원 한도 안에서 실행합니다.")
        except AgentError:
            st.caption("현재는 원문 표 검산을 사용할 수 있습니다. AI 분석에는 운영자의 키·모델·비용 한도 설정이 필요합니다.")
    submitted = st.form_submit_button("비교 보고서 만들기", type="primary", disabled=not ready, width="stretch")

if submitted:
    st.session_state.pop("report", None)
    try:
        request = ResearchRequest(mode=mode, tickers=tickers, as_of=as_of, query=query)
        with st.spinner("자료를 확인하고 기간·계정·계산식을 검사하고 있습니다..."):
            st.session_state.report = (run_agent if use_agent else run_research)(request, user_agent=user_agent)
    except (ValidationError, SecError, AgentError) as error:
        message = error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)
        st.error(f"보고서를 만들지 못했습니다. {message}")
    except OSError:
        st.error("데이터를 저장하거나 읽지 못했습니다. 프로젝트 폴더의 접근 권한과 디스크 공간을 확인하세요.")

report = st.session_state.get("report")
if report and report.request.mode == mode:
    st.divider()
    st.subheader("비교 보고서")
    st.caption(f"적용 기준일 {report.request.as_of} | 기업 {', '.join(company.name for company in report.companies)}")
    if report.request.tickers != tickers or report.request.as_of != as_of or report.request.query != query:
        st.info("입력 조건이 바뀌었습니다. 새 조건을 적용하려면 보고서를 다시 만들어 주세요.")
    if report.comparison.status == "PASS":
        st.success("비교 조건 일치: " + report.comparison.reason)
    else:
        st.warning(report.comparison.reason)
    for warning in report.warnings:
        st.caption(warning)
    summary_tab, evidence_tab, trace_tab = st.tabs(["비교 요약", "원문 근거", "실행 기록"])
    with summary_tab:
        st.dataframe(comparison_rows(report), hide_index=True, width="stretch")
        st.caption("USD 원단위입니다. 각 기간의 증가율은 같은 공시의 비교 수치로 계산하며, 전기 근거가 없으면 보류합니다.")
        st.subheader("수치 해석")
        for text in financial_interpretations(report):
            st.write(text)
        if report.narratives:
            st.subheader("사업 위험 설명 · AI 초안")
            by_id = {p.id: p for p in report.paragraphs}
            for claim in report.narratives:
                paragraph = by_id[claim.evidence_id]
                st.write(f"{claim.ticker}: {claim.text}")
                with st.expander(f"{claim.ticker} | 인용 원문 확인"):
                    st.text(claim.quote)
                    st.caption(f"{paragraph.filing.accession} | {paragraph.section} | {paragraph.xpath} | 문자 {paragraph.start_offset}~{paragraph.end_offset}")
                    st.link_button("원문 문단 열기", paragraph.source_url)
    with evidence_tab:
        st.caption("기업과 보고기간을 선택해 계산식과 공시 원문 위치를 확인하세요.")
        for company in report.companies:
            for warning in company.warnings:
                st.warning(f"{company.name}: {warning}")
            for year in reversed(company.years):
                with st.expander(f"{company.name} | 보고 종료일 {year.filing.report_date} | 근거 확인"):
                    st.write(f"제출일 {year.filing.filed} / {year.filing.form} / 공시 {year.filing.accession}")
                    if year.amendment:
                        st.info(year.amendment.scope)
                        st.link_button("수정 공시 설명 확인", year.amendment.filing.url())
                    for metric in year.metrics:
                        st.markdown(f"**{METRICS[metric.name]}: {display_value(metric)}**")
                        st.write(metric.reason)
                        if metric.formula:
                            st.code(metric.formula, language=None)
                        for evidence in metric.evidence:
                            st.caption(f"{evidence.start} ~ {evidence.end} | {evidence.tag} | {evidence.value} {evidence.unit}")
                            if location := evidence.source_location:
                                st.caption(f"원문 표 {location.table}, 행 {location.row}, 셀 {location.column} | {location.row_label} | 표시값 {location.displayed_text}, 배율 10^{location.scale}, 부호 {location.sign}")
                            if evidence.source_url:
                                st.link_button("SEC 공시 원문 열기", evidence.source_url)
    with trace_tab:
        if report.agent:
            st.caption(f"AI 실행: {report.agent.get('status')} / {report.agent.get('stop_reason', '')}")
        st.json({"snapshots": report.snapshots, "agent": report.agent, "events": report.events})
    st.divider()
    st.subheader("보고서 저장")
    html_column, csv_column, evidence_column = st.columns(3)
    with html_column:
        st.download_button("HTML 보고서", render_html(report), "proofresearch-report.html", "text/html", width="stretch")
    with csv_column:
        st.download_button("CSV 비교표", render_csv(report), "proofresearch-comparison.csv", "text/csv", width="stretch")
    with evidence_column:
        st.download_button("근거·실행 기록", report.model_dump_json(indent=2), "proofresearch-evidence.json", "application/json", width="stretch")
else:
    st.html("""<section class="pr-empty" aria-labelledby="pr-empty-title">
    <h2 id="pr-empty-title">비교 보고서가 여기에 표시됩니다.</h2>
    <p>기업과 기준일을 선택하고 보고서를 만들어 보세요.</p>
    <div class="pr-reading-order"><span>재무 수치 비교</span><span>공시 원문 확인</span><span>보류 사유와 기록 저장</span></div>
    </section>""")
st.caption("ProofResearch는 리서치 검토를 돕습니다. 계산 규칙 통과는 독립적인 정확도 검증을 뜻하지 않습니다.")
