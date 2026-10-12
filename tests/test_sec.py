import hashlib
import io
import json
import multiprocessing
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest

import proofresearch.sec as sec_module
from proofresearch.sec import SecClient, SecError, SecRedirect, validate_url, validate_user_agent


URL = "https://data.sec.gov/submissions/CIK0000002488.json"
# Synthetic test contact; every HTTP call in this file is intercepted.
AGENT = "ProofResearch test@invalid.test"


class Response(io.BytesIO):
    url = URL


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_module.time, "sleep", lambda _: None)
    with SecClient(AGENT, tmp_path / "cache.sqlite3") as value:
        yield value


@pytest.mark.parametrize("value", ["", "name", "ProofResearch example@example.com", "bot a@b.org\nInjected: yes", "한글 a@b.org"])
def test_invalid_user_agents_rejected(value):
    with pytest.raises(SecError):
        validate_user_agent(value)


@pytest.mark.parametrize("url", [
    "http://data.sec.gov/submissions/a.json", "https://data.sec.gov.evil.test/submissions/a.json",
    "https://other.test/submissions/a.json", "https://data.sec.gov/submissions/../a.json",
    "https://data.sec.gov/submissions/a.json?redirect=other", "https://data.sec.gov:443/submissions/a.json",
])
def test_disallowed_addresses(url):
    with pytest.raises(SecError):
        validate_url(url)


def test_redirect_cannot_send_headers_to_another_host():
    with pytest.raises(SecError):
        SecRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test/submissions/a.json")


def test_live_response_is_hashed_cached_and_not_retrieved_again(client, monkeypatch):
    body = b'{"cik":2488}'
    calls = []
    def open_response(request, timeout):
        calls.append(request)
        return Response(body)
    monkeypatch.setattr(client.opener, "open", open_response)
    fresh = client.fetch(URL)
    cached = client.fetch(URL)
    assert (fresh.source, cached.source) == ("live", "cache")
    assert fresh.sha256 == hashlib.sha256(body).hexdigest()
    assert cached.data == {"cik": 2488}
    assert len(calls) == 1
    assert AGENT not in json.dumps(fresh.metadata())


def test_transient_errors_retry_twice_then_stop(client, monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError(URL, 503, "unavailable", {}, None)
    monkeypatch.setattr(client.opener, "open", fail)
    with pytest.raises(SecError, match="503"):
        client.fetch(URL)
    assert client.requests == 3


def test_forbidden_is_not_retried(client, monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError(URL, 403, "forbidden", {}, None)
    monkeypatch.setattr(client.opener, "open", fail)
    with pytest.raises(SecError, match="403"):
        client.fetch(URL)
    assert client.requests == 1


def test_old_cache_fallback_is_explicit_and_preserves_timestamp(client, monkeypatch):
    body = b'{"cik":2488}'
    old_time = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    client.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                      (URL, hashlib.sha256(body).hexdigest(), old_time, body))
    client.db.commit()
    def fail(*args, **kwargs):
        raise URLError("private transport details must not reach logs")
    monkeypatch.setattr(client.opener, "open", fail)
    result = client.fetch(URL)
    assert result.source == "cache" and result.retrieved_at == old_time
    assert "이전 캐시" in result.warning and "private" not in result.warning


def test_corrupt_cache_is_not_used(client, monkeypatch):
    client.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                      (URL, "wrong-hash", datetime.now(timezone.utc).isoformat(), b'{}'))
    client.db.commit()
    monkeypatch.setattr(client.opener, "open", lambda *args, **kwargs: Response(b'{"fresh":true}'))
    assert client.fetch(URL).data == {"fresh": True}


def test_invalid_json_is_not_cached(client, monkeypatch):
    monkeypatch.setattr(client.opener, "open", lambda *args, **kwargs: Response(b'<html>error</html>'))
    with pytest.raises(SecError, match="JSON"):
        client.fetch(URL)
    assert client.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0


def test_request_budget_stops_new_connections(client, monkeypatch):
    client.requests = 24
    monkeypatch.setattr(client.opener, "open", lambda *a, **kw: pytest.fail("network call after limit"))
    with pytest.raises(SecError, match="한도"):
        client.fetch(URL)


ARCHIVE = "https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm"


def test_archive_html_cache_and_exact_response_address(client, monkeypatch):
    def response(request, timeout):
        assert request.get_header("Accept") == "text/html"
        result = Response(b'<html><body>Filing</body></html>')
        result.url = ARCHIVE
        return result
    monkeypatch.setattr(client.opener, "open", response)
    live = client.fetch(ARCHIVE)
    cached = client.fetch(ARCHIVE)
    assert live.data == cached.data == '<html><body>Filing</body></html>'
    assert (live.source, cached.source) == ("live", "cache")
    with pytest.raises(SecError, match="주소"):
        client.fetch(ARCHIVE.replace("000018", "000019"))


@pytest.mark.parametrize("url", [
    ARCHIVE.replace("www.sec.gov", "data.sec.gov"), ARCHIVE.replace("/2488/", "/../"),
    ARCHIVE.replace("https://", "http://"), ARCHIVE + "?redirect=x", ARCHIVE + "#f-1",
    ARCHIVE.replace(".htm", ".exe"), ARCHIVE.replace("www.sec.gov", "www.sec.gov.evil.test"),
])
def test_archive_address_allowlist(url):
    with pytest.raises(SecError):
        validate_url(url)


def test_frozen_cache_never_fetches_or_changes_even_when_refresh_requested(tmp_path, monkeypatch):
    path = tmp_path / "frozen.db"
    with SecClient(AGENT, path) as client:
        body = b'{"cik":2488}'
        client.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                          (URL, hashlib.sha256(body).hexdigest(), "2020-01-01T00:00:00+00:00", body))
        client.db.commit()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with SecClient(AGENT, path, offline=True) as client:
        monkeypatch.setattr(client.opener, "open", lambda *a, **k: pytest.fail("frozen evaluation made an HTTP request"))
        assert client.fetch(URL, refresh=True).source == "frozen"
        with pytest.raises(SecError, match="동결"):
            client.fetch(ARCHIVE)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def reserve_in_process(path, connection):
    with SecClient(AGENT, path) as client:
        client._wait_for_slot()
        connection.send(time.monotonic())
    connection.close()


def test_sec_slots_are_shared_between_spawned_workers(tmp_path):
    context=multiprocessing.get_context('spawn')
    path=tmp_path/'shared.db'
    with SecClient(AGENT,path):
        pass
    workers=[]
    for _ in range(3):
        receiver,sender=context.Pipe(duplex=False)
        process=context.Process(target=reserve_in_process,args=(path,sender))
        process.start()
        sender.close()
        workers.append((process,receiver))
    slots=[]
    try:
        for process,receiver in workers:
            assert receiver.poll(10)
            slots.append(receiver.recv())
            process.join(5)
            assert process.exitcode==0
        slots.sort()
        assert all(b-a>=0.40 for a,b in zip(slots,slots[1:]))
        assert slots[-1]-slots[0]>=0.90
    finally:
        for process,receiver in workers:
            if process.is_alive():
                process.terminate()
                process.join(5)
            receiver.close()
