import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "samples"
BEFORE_FILE = SAMPLES / "8-1주.xlsx"
AFTER_FILE = SAMPLES / "8-2주.xlsx"


def _load(path: Path):
    from core.loader import load_plan

    if not path.exists():
        pytest.skip(f"샘플 파일이 없습니다: {path}")
    return load_plan(path.read_bytes(), source_name=path.name)


@pytest.fixture(scope="session")
def before():
    """변동 전 (8-1주)"""
    return _load(BEFORE_FILE)


@pytest.fixture(scope="session")
def after():
    """변동 후 (8-2주)"""
    return _load(AFTER_FILE)
