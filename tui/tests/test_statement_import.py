"""statement_import.py — pure helper 단위 테스트.

Pilot driven 화면 시나리오는 별도 (Playwright + 실 후잉 호출 필요해 e2e 분류).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_core.csv_adapters.base import CSVRow
from whooing_tui.screens.statement_import import _detect_format, _dedup, _find_account_type


# ---- _detect_format -------------------------------------------------


def test_detect_format_unknown_extension(tmp_path):
    p = tmp_path / "foo.zip"
    p.write_bytes(b"")
    with pytest.raises(ValueError):
        _detect_format(str(p))


def test_detect_format_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _detect_format(str(tmp_path / "nope.html"))


# ---- _dedup --------------------------------------------------------


def _row(date: str, amount: int, merchant: str = "x") -> CSVRow:
    return CSVRow(date=date, amount=amount, merchant=merchant, raw={})


def test_dedup_all_new_when_ledger_empty():
    rows = [_row("20260501", 1000, "스타벅스")]
    matched, new = _dedup(rows, ledger=[])
    assert len(matched) == 0
    assert len(new) == 1


def test_dedup_matches_same_date_amount():
    rows = [_row("20260501", 1000, "스타벅스")]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 1000}]
    matched, new = _dedup(rows, ledger)
    assert len(matched) == 1
    assert len(new) == 0
    assert matched[0]["ledger"]["entry_id"] == "e1"


def test_dedup_within_tolerance_window():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260503", "money": 1000}]
    # default tolerance=2
    matched, new = _dedup(rows, ledger)
    assert len(matched) == 1


def test_dedup_outside_tolerance_window():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260510", "money": 1000}]
    matched, new = _dedup(rows, ledger)
    assert len(matched) == 0
    assert len(new) == 1


def test_dedup_amount_mismatch_no_match():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 999}]
    matched, new = _dedup(rows, ledger)
    assert len(matched) == 0


def test_dedup_each_ledger_used_once():
    rows = [_row("20260501", 1000), _row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 1000}]
    matched, new = _dedup(rows, ledger)
    assert len(matched) == 1
    assert len(new) == 1  # second row leftover


# ---- _find_account_type ------------------------------------------


def test_find_account_type_in_dict_form():
    accounts = {
        "expenses": {"x50": {"account_id": "x50", "title": "식비"}},
        "liabilities": {"x153": {"account_id": "x153", "title": "현대"}},
    }
    assert _find_account_type(accounts, "x50") == "expenses"
    assert _find_account_type(accounts, "x153") == "liabilities"


def test_find_account_type_in_list_form():
    accounts = {
        "expenses": [{"account_id": "x50", "title": "식비"}],
        "liabilities": [{"account_id": "x153"}],
    }
    assert _find_account_type(accounts, "x50") == "expenses"
    assert _find_account_type(accounts, "xUNKNOWN") is None
