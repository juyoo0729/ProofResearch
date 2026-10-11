import json
from datetime import date
from importlib.resources import files

import pytest

from proofresearch.analysis import parse_filings, select_filings


@pytest.fixture
def sample():
    return json.loads(files("proofresearch").joinpath("sample.json").read_text(encoding="utf-8"))["SAMPLE_A"]


@pytest.fixture
def latest(sample):
    return select_filings(parse_filings(sample["submissions"], "9000000001", date(2026, 3, 1)), date(2026, 3, 1))[0]
