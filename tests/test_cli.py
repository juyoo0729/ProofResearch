import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import proofresearch.cli as cli


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
