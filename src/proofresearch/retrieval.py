import hashlib
import re
import sqlite3
from urllib.parse import quote

from .models import Paragraph
from .source import SourceDocument, hidden, text_content


def paragraphs(document: SourceDocument, ticker: str) -> list[Paragraph]:
    result, seen, section = [], set(), "본문"
    for element in document.elements:
        if element.tag not in ("div", "p", "h1", "h2", "h3", "h4") or hidden(element):
            continue
        if any(child.tag in ("div", "p", "table") for child in element.iterdescendants()):
            continue
        text = text_content(element)
        if len(text) < 180 and (re.match(r"^item\s*\d{1,2}[a-c]?(?:\.|\s)", text, re.I)
                               or text.lower() in ("risk factors", "business", "explanatory note")):
            section = text
        if any(parent.tag == "table" for parent in element.iterancestors()):
            continue
        if len(text) < 80 or text in seen:
            continue
        seen.add(text)
        path = document.tree.getroottree().getpath(element)
        offset = 0
        while offset < len(text):
            end = min(offset + 1400, len(text))
            if end < len(text):
                split = text.rfind(" ", offset + 800, end)
                if split > offset:
                    end = split
            chunk = text[offset:end]
            identity = f"{ticker}|{document.filing.accession}|{document.snapshot.sha256}|{path}|{offset}|{end}"
            url = document.filing.url()
            if element.get("id"):
                url += "#" + quote(element.get("id"), safe="")
            result.append(Paragraph(id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                ticker=ticker, filing=document.filing, section=section, text=chunk, xpath=path,
                start_offset=offset, end_offset=end, document_sha256=document.snapshot.sha256, source_url=url))
            offset = end + 1 if end < len(text) and text[end] == " " else end
    return result


class SourceIndex:
    """A request-local FTS index; every search additionally binds company and filing."""

    def __init__(self, items: list[Paragraph]):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE VIRTUAL TABLE passages USING fts5(id UNINDEXED, ticker UNINDEXED, accession UNINDEXED, text, section)")
        self.items = {item.id: item for item in items}
        self.db.executemany("INSERT INTO passages VALUES (?, ?, ?, ?, ?)",
            [(p.id, p.ticker, p.filing.accession, p.text, p.section) for p in self.items.values()])

    def close(self):
        self.db.close()

    def search(self, query: str, scope: dict[str, set[str]], *, per_company=2) -> list[Paragraph]:
        if not query.strip() or len(query) > 240 or not 1 <= per_company <= 3:
            raise ValueError("검색어와 검색 개수를 확인하세요.")
        company_names = {name for ticker in scope for name in {'AMD': ('amd',), 'INTC': ('intel','intc'), 'NVDA': ('nvidia','nvda')}.get(ticker, ())}
        terms = [term for term in dict.fromkeys(re.findall(r"[a-zA-Z]{3,30}", query.lower())) if term not in company_names][:16]
        for korean, english in {"위험": "risk", "수출": "export", "규제": "regulatory", "공급": "supply suppliers",
                               "경쟁": "competition competitive competitors", "수요": "demand", "제조": "manufacturing manufacture",
                               "의존": "dependency suppliers", "관세": "tariffs", "기술": "technology", "경기": "cyclical",
                               "가격": "pricing prices", "무역": "trade", "파운드리": "foundries foundry"}.items():
            if korean in query:
                terms.extend(english.split())
        if not terms:
            return []
        match = " OR ".join('"' + term + '"' for term in dict.fromkeys(terms))
        result = []
        for ticker, accessions in scope.items():
            if not accessions:
                continue
            placeholders = ",".join("?" for _ in accessions)
            rows = self.db.execute(
                f"SELECT id FROM passages WHERE passages MATCH ? AND ticker=? AND accession IN ({placeholders}) "
                "ORDER BY bm25(passages), id LIMIT ?", (match, ticker, *sorted(accessions), per_company))
            result.extend(self.items[row[0]] for row in rows)
        return result
