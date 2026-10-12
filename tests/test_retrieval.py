from proofresearch.retrieval import SourceIndex, paragraphs
from test_source import document


def test_scope_fts_query_escaping_and_real_offsets(latest, source_html):
    risk = "Export restrictions and supply constraints could harm our business operations and limit access to our customers."
    data = source_html(latest).replace("</body>", f'<h2>Item 1A. Risk Factors</h2><p id="risk">{risk}</p><h2>Item 7. Management Discussion</h2><p>{risk} Extra.</p></body>')
    items = paragraphs(document(data, latest), "AMD")
    other = items[0].model_copy(update={"id":"other", "ticker":"NVDA"})
    older = items[0].model_copy(update={"id":"old", "filing":latest.model_copy(update={"accession":"9000000001-25-000099"})})
    index = SourceIndex([*items, other, older])
    try:
        matches = index.search('export " OR supply --', {"AMD":{latest.accession}}, per_company=3)
        assert matches and all(p.ticker == "AMD" and p.filing == latest for p in matches)
        assert items[0].section == "Item 1A. Risk Factors" and items[1].section.startswith("Item 7")
        assert items[0].text == risk[items[0].start_offset:items[0].end_offset]
        assert index.search("위험 수출", {"AMD":{latest.accession}})
        assert index.search("export", {"AMD":{"nonexistent"}}) == []
    finally:
        index.close()


def test_korean_manufacturing_query_uses_topic_terms_not_the_filtered_company(latest, source_html):
    text='Dependency on third-party suppliers for manufacturing products reduces control over delivery schedules and quality.'
    html=source_html(latest).replace('</body>',f'<p>{text}</p><p>NVIDIA provides products and services to customers in many global markets and has several business segments.</p></body>')
    items=paragraphs(document(html,latest),'NVDA')
    index=SourceIndex(items)
    try:
        matches=index.search('NVIDIA의 제삼자 제조업체 의존 위험',{'NVDA':{latest.accession}},per_company=1)
        assert len(matches)==1 and matches[0].text==text
        assert index.search('NVIDIA',{'NVDA':{latest.accession}})==[]
    finally:
        index.close()
