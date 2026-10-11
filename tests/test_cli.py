import os
import subprocess
import sys
from pathlib import Path


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
