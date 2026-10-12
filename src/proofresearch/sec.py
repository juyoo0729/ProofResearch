import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SecError(Exception):
    """An actionable SEC access or data error, without headers or credentials."""


def validate_user_agent(value: str) -> str:
    value = value.strip()
    if (len(value.split()) < 2 or len(value) > 250 or not value.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or not re.search(r"\S+@[^\s@]+\.[^\s@]+", value)
            or "example.com" in value.lower()):
        raise SecError("SEC_USER_AGENT에 서비스 이름과 실제 연락 이메일을 설정하세요.")
    return value


def validate_url(url: str):
    parsed = urlsplit(url)
    data_path = (parsed.netloc == "data.sec.gov" and re.fullmatch(
        r"/(?:submissions/[A-Za-z0-9_-]+|api/xbrl/companyfacts/CIK\d{10})\.json", parsed.path))
    archive_path = (parsed.netloc == "www.sec.gov" and re.fullmatch(
        r"/Archives/edgar/data/\d{1,10}/\d{18}/[A-Za-z0-9_.-]+\.(?:htm|html)", parsed.path))
    if (parsed.scheme != "https" or parsed.query or parsed.fragment or not (data_path or archive_path)):
        raise SecError("허용된 SEC 데이터 주소가 아닙니다.")


def decode_body(url: str, body: bytes) -> dict | str:
    if url.endswith(".json"):
        data = json.loads(body, parse_float=Decimal)
        if not isinstance(data, dict):
            raise SecError("SEC 응답이 JSON 객체가 아닙니다.")
        return data
    text = body.decode("utf-8-sig")
    if not re.search(r"<html(?:\s|>)", text, re.IGNORECASE):
        raise SecError("SEC 응답이 공시 HTML이 아닙니다.")
    return text


class SecRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Snapshot:
    data: dict | str
    url: str
    retrieved_at: str
    sha256: str
    source: str
    warning: str | None = None

    def metadata(self):
        return {key: value for key, value in vars(self).items() if key != "data"}


_rate_lock = threading.Lock()
_last_request = 0.0


class SecClient:
    def __init__(self, user_agent: str, cache_path: Path):
        self.user_agent = validate_user_agent(user_agent)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache_path, timeout=10)
        self.db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            url TEXT NOT NULL, sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            body BLOB NOT NULL, PRIMARY KEY (url, sha256, retrieved_at))""")
        self.opener = build_opener(SecRedirect())
        self.deadline = time.monotonic() + 180
        self.requests = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def _cached(self, url):
        row = self.db.execute(
            "SELECT sha256, retrieved_at, body FROM snapshots WHERE url=? "
            "ORDER BY retrieved_at DESC LIMIT 1", (url,),
        ).fetchone()
        if row is None or hashlib.sha256(row[2]).hexdigest() != row[0]:
            return None
        try:
            data = decode_body(url, row[2])
            if datetime.fromisoformat(row[1]).tzinfo is None:
                return None
        except (ValueError, TypeError, SecError):
            return None
        return Snapshot(data, url, row[1], row[0], "cache")

    def _wait_for_slot(self):
        global _last_request
        with _rate_lock:
            time.sleep(max(0, 0.5 - (time.monotonic() - _last_request)))
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.requests >= 24:
                raise SecError("조회 한도(180초 또는 HTTP 요청 24회)에 도달했습니다.")
            _last_request = time.monotonic()
            self.requests += 1
            return min(15, remaining)

    def fetch(self, url: str, *, refresh=False) -> Snapshot:
        validate_url(url)
        cached = self._cached(url)
        if cached and not refresh:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached.retrieved_at)
            if 0 <= age.total_seconds() < 86400:
                return cached
        failure = "SEC 데이터를 가져오지 못했습니다."
        for attempt in range(3):
            try:
                timeout = self._wait_for_slot()
                request = Request(url, headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json" if url.endswith(".json") else "text/html",
                })
                with self.opener.open(request, timeout=timeout) as response:
                    validate_url(response.url)
                    if response.url != url:
                        raise SecError("응답 주소가 요청한 공시 주소와 다릅니다.")
                    chunks, size = [], 0
                    while True:
                        # shortcut: an in-flight socket read can add up to 15s; use cancellable I/O before hosting.
                        if time.monotonic() >= self.deadline:
                            raise SecError("조회 시간 한도에 도달했습니다.")
                        chunk = response.read1(min(65536, 30_000_001 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > 30_000_000:
                            raise SecError("SEC 응답 크기가 허용 범위를 초과했습니다.")
                    body = b"".join(chunks)
                if len(body) > 30_000_000:
                    raise SecError("SEC 응답 크기가 허용 범위를 초과했습니다.")
                data = decode_body(url, body)
                digest = hashlib.sha256(body).hexdigest()
                retrieved = datetime.now(timezone.utc).isoformat()
                with self.db:
                    self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                                    (url, digest, retrieved, body))
                return Snapshot(data, url, retrieved, digest, "live")
            except HTTPError as error:
                failure = f"SEC 응답 오류: HTTP {error.code}."
                error.close()
                if error.code not in (429, 500, 502, 503, 504):
                    break
            except (URLError, TimeoutError, OSError):
                failure = "SEC 연결이 실패했거나 응답 시간이 초과되었습니다."
            except (ValueError, SecError) as error:
                failure = str(error) if isinstance(error, SecError) else "SEC JSON 응답을 해석하지 못했습니다."
                break
            if attempt < 2:
                time.sleep(min(2 ** attempt, max(0, self.deadline - time.monotonic())))
        if cached:
            cached.warning = failure + " 이전 캐시를 사용했습니다. 수집 시각을 확인하세요."
            return cached
        raise SecError(failure)
