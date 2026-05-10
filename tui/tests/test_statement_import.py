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
    matched, prev, new = _dedup(rows, ledger=[], check_import_log=False)
    assert len(matched) == 0
    assert len(prev) == 0
    assert len(new) == 1


def test_dedup_matches_same_date_amount():
    rows = [_row("20260501", 1000, "스타벅스")]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 1000}]
    matched, prev, new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 1
    assert len(prev) == 0
    assert len(new) == 0
    assert matched[0]["ledger"]["entry_id"] == "e1"


def test_dedup_within_tolerance_window():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260503", "money": 1000}]
    matched, _prev, _new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 1


def test_dedup_outside_tolerance_window():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260510", "money": 1000}]
    matched, _prev, new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 0
    assert len(new) == 1


def test_dedup_amount_mismatch_no_match():
    rows = [_row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 999}]
    matched, _prev, _new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 0


def test_dedup_each_ledger_used_once():
    rows = [_row("20260501", 1000), _row("20260501", 1000)]
    ledger = [{"entry_id": "e1", "entry_date": "20260501", "money": 1000}]
    matched, _prev, new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 1
    assert len(new) == 1  # second row leftover


# ---- CL #51129+ import_log 기반 dedup -----------------------------------


def test_dedup_skips_previously_imported_via_log():
    """import_log 에 같은 (date, amount, merchant) 이 있으면 'previously_
    imported' 로 분류 — ledger 에 없어도 신규 후보 X."""
    from whooing_core import db as core_db
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()
    rows = [_row("20260501", 1000, "스타벅스")]
    # log 시드 — 이전에 successfully import 됐다고 기록.
    with tui_data.open_rw() as conn:
        core_db.log_import(
            conn, source_file="/tmp/x.csv", source_kind="csv",
            statement_period_start=None, statement_period_end=None,
            issuer="x", card_label=None,
            entry_date="20260501", merchant="스타벅스",
            original_amount=1000, fee_amount=0, total_amount=1000,
            currency="KRW", foreign_amount=None, exchange_rate=None,
            section_id="s1", l_account_id="x50", r_account_id="x153",
            whooing_entry_id="e_old", status="inserted",
            error_message=None, notes=None,
        )
    # ledger 는 비어 있어도 — log 가 잡아냄.
    matched, prev, new = _dedup(rows, ledger=[], section_id="s1")
    assert len(matched) == 0
    assert len(prev) == 1
    assert prev[0].merchant == "스타벅스"
    assert len(new) == 0


def test_dedup_log_section_filter():
    """다른 섹션의 import_log 는 매칭하지 않음."""
    from whooing_core import db as core_db
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()
    rows = [_row("20260501", 1000, "스타벅스")]
    with tui_data.open_rw() as conn:
        core_db.log_import(
            conn, source_file="/tmp/x.csv", source_kind="csv",
            statement_period_start=None, statement_period_end=None,
            issuer="x", card_label=None,
            entry_date="20260501", merchant="스타벅스",
            original_amount=1000, fee_amount=0, total_amount=1000,
            currency="KRW", foreign_amount=None, exchange_rate=None,
            section_id="s_other", l_account_id="x50", r_account_id="x153",
            whooing_entry_id="e_old", status="inserted",
            error_message=None, notes=None,
        )
    matched, prev, new = _dedup(rows, ledger=[], section_id="s1")
    # s1 섹션에선 매칭 X.
    assert len(prev) == 0
    assert len(new) == 1


def test_dedup_log_takes_priority_over_ledger():
    """row 가 ledger 매칭도 되고 import_log 매칭도 되면 — log 우선
    (previously_imported 로 분류)."""
    from whooing_core import db as core_db
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()
    rows = [_row("20260501", 1000, "스타벅스")]
    ledger = [
        {"entry_id": "e1", "entry_date": "20260501", "money": 1000},
    ]
    with tui_data.open_rw() as conn:
        core_db.log_import(
            conn, source_file="/tmp/x.csv", source_kind="csv",
            statement_period_start=None, statement_period_end=None,
            issuer="x", card_label=None,
            entry_date="20260501", merchant="스타벅스",
            original_amount=1000, fee_amount=0, total_amount=1000,
            currency="KRW", foreign_amount=None, exchange_rate=None,
            section_id="s1", l_account_id="x50", r_account_id="x153",
            whooing_entry_id="e1", status="inserted",
            error_message=None, notes=None,
        )
    matched, prev, new = _dedup(rows, ledger, section_id="s1")
    assert len(prev) == 1     # log 매칭이 강함.
    assert len(matched) == 0  # ledger 매칭은 log 가 가져간 row 빼고는 없음.
    assert len(new) == 0


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
