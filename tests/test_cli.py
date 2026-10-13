import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from urllib.request import OpenerDirector

import pytest

import proofresearch.cli as cli
from proofresearch.llm import CostLedger, Settings


ROOT = Path(__file__).resolve().parents[1]


def test_cli_runs_under_legacy_windows_pipe_encoding(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    process = subprocess.run([sys.executable, "-m", "proofresearch.cli", "sample", "--output", str(tmp_path)],
                             cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=30)
    assert process.returncode == 0, process.stderr
    assert "가상 예제" in process.stdout and "PASS" in process.stdout
    assert len(list(tmp_path.glob("*/report.html"))) == 1


def test_unconfigured_sec_exits_without_network_or_report(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "SEC_USER_AGENT"}
    process = subprocess.run([sys.executable, "-m", "proofresearch.cli", "sec", "--output", str(tmp_path)],
                             cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=30)
    assert process.returncode == 1 and "SEC_USER_AGENT" in process.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode,flags", [("sample", []), ("sec", []), ("sec", ["--agent"])])
def test_blank_query_stops_before_research_or_agent(tmp_path, monkeypatch, capsys, mode, flags):
    research = Mock(side_effect=AssertionError("Blank query reached research"))
    agent = Mock(side_effect=AssertionError("Blank query reached paid Agent"))
    monkeypatch.setattr(cli, "run_research", research)
    monkeypatch.setattr(cli, "run_agent", agent)
    monkeypatch.setattr(sys, "argv", ["proofresearch", mode, *flags, "--query", " \n\t ",
                                     "--output", str(tmp_path)])

    assert cli.main() == 1
    research.assert_not_called()
    agent.assert_not_called()
    assert "공백만으로는 분석할 수 없습니다" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("use_agent", [False, True])
def test_corrupt_storage_is_reported_and_preserved(tmp_path, monkeypatch, capsys, use_agent):
    network = Mock(side_effect=AssertionError("Storage failure must not fetch network"))
    monkeypatch.setattr(OpenerDirector, "open", network)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEC_USER_AGENT", "ProofResearch test@invalid.test")
    storage = tmp_path / ".cache" / ("usage.sqlite3" if use_agent else "sec.sqlite3")
    storage.parent.mkdir()
    original = b"not a SQLite database"
    storage.write_bytes(original)

    def corrupt_agent(*args, **kwargs):
        CostLedger(storage, Settings("synthetic-key-only", "gpt-6-luna", Decimal("500"), Decimal("50000")))
        pytest.fail("Corrupt ledger must prevent analysis")

    monkeypatch.setattr(cli, "run_agent", corrupt_agent)
    monkeypatch.setattr(sys, "argv", ["proofresearch", "sec", *(["--agent"] if use_agent else [])])
    assert cli.main() == 1
    output = capsys.readouterr()
    assert "데이터 저장소" in output.err
    assert "file is not a database" not in output.err and "Traceback" not in output.err
    assert not output.out and not (tmp_path / "reports").exists()
    assert storage.read_bytes() == original

    monkeypatch.setattr(sys, "argv", ["proofresearch", "sample"])
    assert cli.main() == 0
    assert len(list((tmp_path / "reports").glob("*/report.html"))) == 1
    assert storage.read_bytes() == original
    network.assert_not_called()
