import csv
import io
import json
from html import escape
from pathlib import Path

from .models import METRICS, Metric, RunReport


def display_value(metric: Metric | None) -> str:
    if metric is None:
        return "대상 밖"
    if metric.value is None:
        return "보류"
    return f"{metric.value:,.2f}" + ("%" if metric.unit == "%" else " USD")


def comparison_rows(report: RunReport) -> list[dict]:
    rows = []
    for company in report.companies:
        for year in reversed(company.years):
            metrics = {metric.name: metric for metric in year.metrics}
            evidence = metrics["revenue"].evidence
            period = f"{evidence[0].start} ~ {evidence[0].end}" if evidence else f"종료 {year.filing.report_date}"
            rows.append({"기업": company.name, "보고기간": period,
                         **{label: display_value(metrics.get(name)) for name, label in METRICS.items()}})
    return rows


def financial_interpretations(report: RunReport) -> list[str]:
    result = []
    for company in report.companies:
        if not company.years:
            continue
        year = company.years[-1]
        metrics = {m.name: m for m in year.metrics}
        parts = []
        for name in ("operating_margin", "revenue_growth"):
            metric = metrics.get(name)
            if metric:
                parts.append(f"{METRICS[name]}은 {display_value(metric)}입니다." if metric.status == "PASS"
                             else f"{METRICS[name]}은 근거 부족으로 보류했습니다.")
        result.append(f"{company.name}의 {year.filing.report_date} 종료 기간: " + " ".join(parts))
    return result


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(report: RunReport) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["mode", "as_of", "ticker", "company", "period_end", "metric", "value", "unit",
                     "status", "reason", "formula", "filing", "evidence_ids", "source_urls",
                     "source_locations", "amendment", "amendment_scope"])
    for company in report.companies:
        if not company.years:
            writer.writerow([csv_cell(value) for value in [report.request.mode, str(report.request.as_of),
                company.ticker, company.name, "", "", "", "", "HOLD", "; ".join(company.warnings), "", "", "", "", "", "", ""]])
        for year in company.years:
            for metric in year.metrics:
                writer.writerow([csv_cell(value) for value in [
                    report.request.mode, str(report.request.as_of), company.ticker, company.name,
                    str(year.filing.report_date), metric.name,
                    metric.value if metric.value is not None else "", metric.unit, metric.status,
                    metric.reason, metric.formula or "", year.filing.accession,
                    ";".join(item.id for item in metric.evidence),
                    ";".join(dict.fromkeys(item.source_url for item in metric.evidence if item.source_url)),
                    json.dumps({item.id: item.source_location.model_dump() for item in metric.evidence
                                if item.source_location}, ensure_ascii=False),
                    year.amendment.filing.accession if year.amendment else "",
                    year.amendment.scope if year.amendment else "",
                ]])
    return stream.getvalue().encode("utf-8-sig")


def render_html(report: RunReport) -> str:
    e = lambda value: escape(str(value), quote=True)
    sample = report.request.mode == "sample"
    pieces = ["""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ProofResearch 공시 비교 보고서</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#edf3fa;color:#17314d;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;line-height:1.7}
main{max-width:1120px;margin:auto;padding:36px;background:white}h1{font-size:32px;line-height:1.3}h2{margin-top:36px}p{max-width:78ch}a{color:#195cb0}a:focus-visible{outline:3px solid #a66a00;outline-offset:3px}
.notice{border-left:4px solid #a66a00;padding:12px 18px;background:#fff6dd}.pass{border-left-color:#195cb0;background:#edf3fa}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px;text-align:left;border-bottom:1px solid #ccd9e8;white-space:nowrap}th{background:#edf3fa}td{font-variant-numeric:tabular-nums}
.metric{border-top:1px solid #ccd9e8;padding:12px 0}code{overflow-wrap:anywhere}small{color:#405974}li{margin-bottom:8px}@media(max-width:640px){main{padding:20px}h1{font-size:26px}}@media print{body{background:white}main{padding:0}.scroll{overflow:visible}table{font-size:10px}td,th{padding:5px}a{color:inherit}}
</style></head><body><main><p>ProofResearch</p><h1>공시 근거를 함께 읽는 기업 비교</h1>"""]
    pieces.append(f"<p>기준일 {e(report.request.as_of)} / 실행 {e(report.run_id)}<br>작성 시각 {e(report.created_at.isoformat())}</p>")
    pieces.append(f"<p>질의: {e(report.request.query)}</p>")
    if sample:
        pieces.append('<p class="notice"><strong>가상 예제</strong> — 모든 기업과 수치는 기능 확인용 합성 데이터입니다. 실제 공시나 투자정보가 아닙니다.</p>')
    comparison_class = "notice pass" if report.comparison.status == "PASS" else "notice"
    pieces.append(f'<p class="{comparison_class}"><strong>{"비교 조건 일치" if report.comparison.status == "PASS" else "기업 간 직접 비교 보류"}</strong><br>{e(report.comparison.reason)}</p>')
    for warning in report.warnings:
        pieces.append(f"<p><small>{e(warning)}</small></p>")
    pieces.append('<h2>재무 비교표</h2><div class="scroll"><table><caption>각 기업의 선택된 2개 연차보고 기간. 수치를 누르면 근거와 계산식으로 이동합니다.</caption><thead><tr><th scope="col">기업</th><th scope="col">보고 종료일</th>')
    pieces.extend(f'<th scope="col">{e(label)}</th>' for label in METRICS.values())
    pieces.append("</tr></thead><tbody>")
    for ci, company in enumerate(report.companies):
        for yi, year in reversed(list(enumerate(company.years))):
            metrics = {metric.name: (mi, metric) for mi, metric in enumerate(year.metrics)}
            pieces.append(f"<tr><th scope=\"row\">{e(company.name)}</th><td>{e(year.filing.report_date)}</td>")
            for name in METRICS:
                if name not in metrics:
                    pieces.append("<td>대상 밖</td>")
                else:
                    mi, metric = metrics[name]
                    pieces.append(f'<td><a href="#m{ci}-{yi}-{mi}">{e(display_value(metric))}</a></td>')
            pieces.append("</tr>")
    pieces.append("</tbody></table></div><p><small>직전 연도의 증가율은 전전기 공시가 대상 밖이므로 표시하지 않습니다. USD 원단위, 비율은 소수 둘째 자리 표시입니다.</small></p><h2>수치 해석</h2>")
    pieces.extend(f"<p>{e(text)}</p>" for text in financial_interpretations(report))
    if report.agent:
        pieces.append(f"<p>AI 실행 상태: {e(report.agent.get('status'))} / {e(report.agent.get('stop_reason', ''))}</p>")
    if report.narratives:
        pieces.append("<h2>사업 위험 설명 · AI 초안</h2><p>원문 인용과 함께 검토하세요. 의미 타당성은 독립 평가 전입니다.</p>")
        by_id = {p.id: p for p in report.paragraphs}
        for claim in report.narratives:
            paragraph = by_id[claim.evidence_id]
            pieces.append(f'<h3>{e(claim.ticker)}</h3><p>{e(claim.text)}</p><blockquote>{e(claim.quote)}</blockquote><p><a href="{e(paragraph.source_url)}">SEC 원문 문단 확인</a> / {e(paragraph.section)}<br>공시 {e(paragraph.filing.accession)} / 위치 {e(paragraph.xpath)} / 본문 문자 {paragraph.start_offset}~{paragraph.end_offset}<br><small>문서 SHA-256 {e(paragraph.document_sha256)}</small></p>')
    pieces.append("<h2>검토 근거</h2>")
    for ci, company in enumerate(report.companies):
        pieces.append(f"<h3>{e(company.name)} ({e(company.ticker)})</h3>")
        for warning in company.warnings:
            pieces.append(f'<p class="notice">보류: {e(warning)}</p>')
        for yi, year in enumerate(company.years):
            pieces.append(f"<h4>보고 종료일 {e(year.filing.report_date)} / 제출일 {e(year.filing.filed)} / {e(year.filing.form)}</h4>")
            if year.amendment:
                note = year.amendment
                pieces.append(f'<p class="notice">수정 공시 {e(note.filing.accession)} / 제출 {e(note.filing.filed)}<br>{e(note.scope)}<br><a href="{e(note.filing.url())}">수정 공시 설명 확인</a><br>원문 위치 {e(note.note_xpath)} / 검토 {e(note.reviewed_on)}</p>')
            for mi, metric in enumerate(year.metrics):
                pieces.append(f'<section class="metric" id="m{ci}-{yi}-{mi}"><strong>{e(METRICS[metric.name])}: {e(display_value(metric))}</strong><p>{e(metric.reason)}</p>')
                if metric.formula:
                    pieces.append(f"<p>계산식: <code>{e(metric.formula)}</code></p>")
                for evidence in metric.evidence:
                    pieces.append(f"<p>근거 {e(evidence.id)}: {e(evidence.start)} ~ {e(evidence.end)}<br><code>{e(evidence.tag)}</code> = {e(evidence.value)} {e(evidence.unit)}<br>공시 {e(evidence.filing.accession)} / 제출 {e(evidence.filing.filed)}")
                    if evidence.source_url:
                        pieces.append(f'<br><a href="{e(evidence.source_url)}">SEC 공시 원문 열기</a>')
                    if location := evidence.source_location:
                        pieces.append(f'<br>원문 표 {location.table}, 행 {location.row}, 셀 {location.column}: {e(location.row_label)}<br>표시값 {e(location.displayed_text)} / 배율 10^{location.scale} / 부호 {e(location.sign)}<br><small>위치 {e(location.xpath)}<br>문서 SHA-256 {e(location.document_sha256)}</small>')
                    pieces.append("</p>")
                pieces.append("</section>")
    pieces.append("<h2>데이터 기록</h2><ul>")
    for snapshot in report.snapshots:
        pieces.append(f"<li>{e(snapshot['source'])} / {e(snapshot['retrieved_at'])}<br>{e(snapshot.get('url') or '합성 예제')}<br><code>{e(snapshot['sha256'])}</code></li>")
    pieces.append("</ul><h2>실행 이력</h2><ol>")
    for event in report.events:
        pieces.append(f"<li>{e(event['message'])} ({e(event['elapsed_seconds'])}초)</li>")
    if report.agent:
        pieces.append(f"</ol><h2>호출·비용 기록</h2><pre>{e(json.dumps(report.agent, ensure_ascii=False, indent=2))}</pre><ol>")
    pieces.append("</ol></main></body></html>")
    return "".join(pieces)


def save_report(report: RunReport, output: Path) -> Path:
    folder = output / report.run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "report.html").write_text(render_html(report), encoding="utf-8")
    (folder / "comparison.csv").write_bytes(render_csv(report))
    (folder / "evidence.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (folder / "events.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in report.events) + "\n", encoding="utf-8")
    return folder
