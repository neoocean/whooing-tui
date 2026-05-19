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


# ---- CL #52910+ : 일괄 import 실패 메시지 capture --------------------------


def test_error_report_modal_class_exists():
    """_ErrorReportModal 이 ModalScreen 으로 정의 — 회귀 가드."""
    from textual.screen import ModalScreen
    from whooing_tui.screens.statement_import import _ErrorReportModal
    assert issubclass(_ErrorReportModal, ModalScreen)


def test_error_report_modal_stores_title_summary_body():
    """생성자가 인자를 그대로 보관 — UI 자체는 textual run_test 가 필요해
    여기서는 attribute 만 확인.
    """
    from whooing_tui.screens.statement_import import _ErrorReportModal
    m = _ErrorReportModal(
        title="t", summary="s",
        body="line1\nline2",
        log_path="/tmp/foo.log",
    )
    assert m._title == "t"
    assert m._summary == "s"
    assert "line1" in m._body
    assert m._log_path == "/tmp/foo.log"


def test_error_log_writes_lines_to_tmp_path(tmp_path, monkeypatch):
    """_write_error_log — 임시 파일 경로 + 헤더 + 라인들 포함."""
    from pathlib import Path as _Path
    from whooing_tui.screens.statement_import import StatementImportScreen

    # /tmp 대신 tmp_path 사용 — 실 사용자 /tmp 오염 방지.
    real_path = _Path

    class _FakePath(_Path):
        # /tmp 만 가로채 redirect.
        def __new__(cls, *args, **kwargs):
            if args and str(args[0]) == "/tmp":
                return real_path(tmp_path)
            return real_path(*args, **kwargs)

    monkeypatch.setattr(
        "whooing_tui.screens.statement_import.Path", _FakePath,
    )

    # 실제 인스턴스 — file_path / kind / issuer / r_account_id 만 보면 됨.
    scr = StatementImportScreen.__new__(StatementImportScreen)
    scr.file_path = "/some/statement.html"
    scr.kind = "html"
    scr.issuer = "hyundai"
    scr.r_account_id = "x153"

    p = scr._write_error_log(["err1", "err2: detail"])
    text = p.read_text()
    assert "statement.html" in text
    assert "hyundai" in text
    assert "x153" in text
    assert "err1" in text
    assert "err2: detail" in text


# ---- CL #52912+ : suspect detection + 선택 토글 -------------------------


def test_suspect_same_amount_at_5day_diff():
    """같은 금액 + 3~7일 차이 → fuzzy 의심 매칭."""
    from whooing_tui.screens.statement_import import _compute_suspect_map
    rows = [
        CSVRow(date="20260520", merchant="스타벅스", amount=12000, raw={}),
    ]
    ledger = [
        {"entry_id": "e1", "entry_date": "20260515", "money": 12000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스"},
    ]
    suspect = _compute_suspect_map(rows, ledger)
    assert 0 in suspect
    assert "ledger e1" in suspect[0]


def test_suspect_amount_within_1pct_at_close_date():
    """금액 ±1% + 날짜 ±2일 → 의심 (수수료/환율 차)."""
    from whooing_tui.screens.statement_import import _compute_suspect_map
    rows = [
        CSVRow(date="20260520", merchant="네이버페이", amount=10_000, raw={}),
    ]
    ledger = [
        # 10,050 = 10,000 * 1.005 — 0.5% 차이, 이틀 차.
        {"entry_id": "e2", "entry_date": "20260522", "money": 10_050,
         "l_account_id": "x20", "r_account_id": "x11", "item": "네이버페이"},
    ]
    suspect = _compute_suspect_map(rows, ledger)
    assert 0 in suspect
    assert "유사" in suspect[0]


def test_suspect_skips_when_no_close_ledger():
    """ledger 가 비어있거나 거리가 멀면 의심 X — strict matching 영역도 아님."""
    from whooing_tui.screens.statement_import import _compute_suspect_map
    rows = [
        CSVRow(date="20260520", merchant="X", amount=5000, raw={}),
    ]
    ledger = [
        # 30일 차 — 너무 멀어.
        {"entry_id": "e3", "entry_date": "20260420", "money": 5000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "X"},
    ]
    suspect = _compute_suspect_map(rows, ledger)
    assert 0 not in suspect


def test_suspect_skips_when_amount_exact_and_within_strict_window():
    """같은 금액 + ±2일 = strict dedup 영역. 본 fuzzy detector 는 잡지 않음
    (이미 _dedup 의 matched_existing 으로 분류돼야).
    """
    from whooing_tui.screens.statement_import import _compute_suspect_map
    rows = [
        CSVRow(date="20260520", merchant="X", amount=5000, raw={}),
    ]
    ledger = [
        {"entry_id": "e4", "entry_date": "20260521", "money": 5000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "X"},
    ]
    # day_diff=1 + same money — fuzzy detector 조건 (3~7 또는 ±1% 비-동일) 에
    # 안 맞으므로 의심 X.
    suspect = _compute_suspect_map(rows, ledger)
    assert 0 not in suspect


# ---- CL #52917+ : dedup 강화 (merchant 유사, within-batch) ---------------


def test_dedup_within_batch_dedup_same_normalized():
    """같은 명세서 안에서 (date + amount + 정규화 merchant) 동일 row 가 두
    번 추출되면 첫 1건만 proposals, 나머지는 previously_imported.
    """
    rows = [
        CSVRow(date="20260520", merchant="스타벅스 강남점", amount=4500, raw={}),
        # 정규화 후 동일 (공백 차이만).
        CSVRow(date="20260520", merchant="스타벅스강남점", amount=4500, raw={}),
        # 다른 거래 — 같은 가맹점이지만 다른 amount.
        CSVRow(date="20260520", merchant="스타벅스 강남점", amount=5000, raw={}),
    ]
    matched, prev, new = _dedup(rows, ledger=[], check_import_log=False)
    assert len(new) == 2  # 첫 4500 + 다른 amount 5000.
    assert len(prev) == 1  # 두 번째 4500 (정규화 동일) → prev 로.
    assert len(matched) == 0


def test_dedup_ledger_match_prefers_merchant_similar():
    """ledger 에 같은 (date, amount) 후보가 여러 개일 때 *merchant 유사*
    한 entry 를 우선 채택.
    """
    rows = [
        CSVRow(date="20260520", merchant="스타벅스 강남점", amount=4500, raw={}),
    ]
    ledger = [
        # 첫 후보: 같은 금액·날짜지만 다른 가맹점.
        {"entry_id": "e_other", "entry_date": "20260520", "money": 4500,
         "l_account_id": "x20", "r_account_id": "x11", "item": "버거킹"},
        # 두 번째: 같은 금액·날짜 + 가맹점도 유사.
        {"entry_id": "e_starbucks", "entry_date": "20260520", "money": 4500,
         "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스"},
    ]
    matched, prev, new = _dedup(rows, ledger, check_import_log=False)
    # merchant 유사 ledger entry 가 채택돼야.
    assert len(matched) == 1
    assert matched[0]["ledger"]["entry_id"] == "e_starbucks"
    assert len(new) == 0


def test_dedup_ledger_first_candidate_when_no_merchant_match():
    """ledger 후보 중 merchant 유사 없으면 기존 동작 (첫 후보 채택)."""
    rows = [
        CSVRow(date="20260520", merchant="네이버페이", amount=4500, raw={}),
    ]
    ledger = [
        {"entry_id": "e1", "entry_date": "20260520", "money": 4500,
         "l_account_id": "x20", "r_account_id": "x11", "item": "버거킹"},
    ]
    matched, prev, new = _dedup(rows, ledger, check_import_log=False)
    assert len(matched) == 1
    assert matched[0]["ledger"]["entry_id"] == "e1"


def test_suspect_merchant_similar_amount_off():
    """merchant 유사 + 금액 ±10% + 날짜 ±7일 → 의심."""
    from whooing_tui.screens.statement_import import _compute_suspect_map
    rows = [
        CSVRow(date="20260520", merchant="스타벅스 강남점", amount=5000, raw={}),
    ]
    ledger = [
        # 같은 가맹점 표기 변형 + 5% 차이 + 3일 차.
        {"entry_id": "e1", "entry_date": "20260523", "money": 5250,
         "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스"},
    ]
    suspect = _compute_suspect_map(rows, ledger)
    assert 0 in suspect
    assert "가맹점 유사" in suspect[0]


# ---- CL #52935+ : confirm 키 바인딩 ------------------------------------


def test_statement_import_confirm_bindings_include_ctrl_s():
    """ctrl+enter 는 대부분 터미널에서 안 옴 — ctrl+s / f5 도 confirm 에
    바인딩돼있어야 사용자가 실제로 입력 확정 가능.
    """
    from whooing_tui.screens.statement_import import StatementImportScreen
    actions = [
        (b.key, b.action) for b in StatementImportScreen.BINDINGS
    ]
    confirm_keys = [k for k, a in actions if a == "confirm"]
    # 적어도 ctrl+s + ctrl+enter + f5 셋 다 노출.
    assert "ctrl+s" in confirm_keys
    assert "ctrl+enter" in confirm_keys
    assert "f5" in confirm_keys


def test_statement_import_confirm_bindings_priority_true():
    """confirm 키들은 priority=True — DataTable 의 default 키 처리 보다 우선."""
    from whooing_tui.screens.statement_import import StatementImportScreen
    for b in StatementImportScreen.BINDINGS:
        if b.action == "confirm":
            assert b.priority is True, (
                f"confirm key {b.key!r} priority=False — DataTable 가 가로채면 안 됨"
            )
