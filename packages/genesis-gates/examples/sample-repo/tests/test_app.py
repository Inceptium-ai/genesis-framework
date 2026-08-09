import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import add, build_report  # noqa: E402


def test_add():
    assert add(2, 3) == 5


def test_build_report():
    r = build_report([{"amount": 10}, {"amount": 5}])
    assert r == {"count": 2, "total": 15}
