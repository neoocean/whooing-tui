"""cli.py 의 헤드리스 dispatch 단위 테스트.

`whooing_tui.cli.main(argv)` 를 직접 호출하면서 `WhooingClient` 와
`load_auth_from_env` 를 fake 로 교체. 실 후잉 호출 없이 종료 코드 / 출력
형식을 검증한다.

검증 포인트:
  - sections list / accounts list / entries list 각각 정상 흐름.
  - --json 출력 (sanitize_for_log 적용 후 deserialize 가능한 JSON).
  - --start / --end 검증 (한 쪽만 줄 때 USER_INPUT, 잘못된 YYYYMMDD).
  - 활성 섹션 결정 (--section > $WHOOING_SECTION_ID > 첫 섹션).
  - ToolError 별 종료 코드 (USER_INPUT=2 / AUTH=3 / RATE_LIMIT=4 /
    UPSTREAM=5).
  - 토큰 누락 (load_auth_from_env 가 ValueError) → exit 2.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from whooing_tui import cli as cli_mod
from whooing_tui.auth import WhooingAuth
from whooing_tui.models import ToolError


# ---- helpers -----------------------------------------------------------


class FakeClient:
    """cli.py 가 호출하는 WhooingClient 의 read 메서드만 흉내."""

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        accounts: dict[str, Any] | None = None,
        entries: list[dict[str, Any]] | None = None,
        sections_error: ToolError | None = None,
        accounts_error: ToolError | None = None,
        entries_error: ToolError | None = None,
    ) -> None:
        self.sections = (
            sections if sections is not None
            else [{"section_id": "s1", "title": "main"}]
        )
        self.accounts = accounts if accounts is not None else {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [{"account_id": "x20", "title": "식비"}],
        }
        self.entries = entries if entries is not None else []
        self.sections_error = sections_error
        self.accounts_error = accounts_error
        self.entries_error = entries_error
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self):
        if self.sections_error:
            raise self.sections_error
        return list(self.sections)

    async def list_accounts(self, section_id):
        if self.accounts_error:
            raise self.accounts_error
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        self.list_entries_calls.append((section_id, start_date, end_date))
        if self.entries_error:
            raise self.entries_error
        return list(self.entries)

    @staticmethod
    def flatten_accounts(raw):
        # WhooingClient 의 staticmethod 와 동일하게 동작
        from whooing_tui.client import WhooingClient
        return WhooingClient.flatten_accounts(raw)


def _patch_client_and_auth(monkeypatch, fake: FakeClient) -> None:
    """`load_auth_from_env` 와 `WhooingClient` 를 cli 모듈에서 fake 로 교체.

    cli.py 는 `WhooingClient(auth)` 인스턴스화도 하고 `WhooingClient.
    flatten_accounts(raw)` 의 staticmethod 도 호출한다. 둘 다 통과시키려면
    교체 객체가 callable + 같은 staticmethod 를 가져야 한다.
    """
    from whooing_tui.client import WhooingClient as _RealClient

    monkeypatch.setattr(
        cli_mod, "load_auth_from_env",
        lambda: WhooingAuth(token="__eyJhfaketokenfortests1234"),
    )

    class _FakeWhooingClient:
        # __new__ 가 미리 만든 fake 를 그대로 반환 — `WhooingClient(auth)`
        # 호출의 결과로 우리 FakeClient 인스턴스가 들어온다.
        def __new__(cls, *args, **kwargs):  # noqa: D401
            return fake

        # cli.py 가 `WhooingClient.flatten_accounts(raw)` 로 부르므로 진짜
        # staticmethod 를 그대로 노출.
        flatten_accounts = staticmethod(_RealClient.flatten_accounts)

    monkeypatch.setattr(cli_mod, "WhooingClient", _FakeWhooingClient)


# ---- sections list ------------------------------------------------------


def test_sections_list_table_output(monkeypatch, capsys):
    fake = FakeClient(sections=[
        {"section_id": "s1", "title": "main"},
        {"section_id": "s2", "title": "side"},
    ])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["sections", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "section_id" in out
    assert "s1" in out and "main" in out
    assert "s2" in out and "side" in out


def test_sections_list_json_output(monkeypatch, capsys):
    fake = FakeClient(sections=[
        {"section_id": "s1", "title": "main", "webhook_token": "secret"},
    ])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["--json", "sections", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed[0]["section_id"] == "s1"
    # secret 은 sanitize_for_log 로 마스크
    assert parsed[0]["webhook_token"] == "***masked***"


def test_sections_list_auth_error_returns_3(monkeypatch, capsys):
    fake = FakeClient(sections_error=ToolError("AUTH", "토큰 만료"))
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["sections", "list"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "AUTH" in err


def test_sections_list_rate_limit_returns_4(monkeypatch, capsys):
    fake = FakeClient(sections_error=ToolError("RATE_LIMIT", "분당 한도"))
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["sections", "list"])
    assert rc == 4


def test_sections_list_upstream_returns_5(monkeypatch, capsys):
    fake = FakeClient(sections_error=ToolError("UPSTREAM", "5xx"))
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["sections", "list"])
    assert rc == 5


def test_token_missing_returns_2(monkeypatch, capsys):
    """load_auth_from_env 가 ValueError 면 USER_INPUT (exit 2)."""
    def _raise():
        raise ValueError("WHOOING_AI_TOKEN 미설정")
    monkeypatch.setattr(cli_mod, "load_auth_from_env", _raise)
    # client 는 만들어지지 않음 — 그러나 대비 안전성 위해 patch
    monkeypatch.setattr(cli_mod, "WhooingClient", lambda *a, **k: None)
    rc = cli_mod.main(["sections", "list"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "USER_INPUT" in err


# ---- accounts list -----------------------------------------------------


def test_accounts_list_uses_first_section_when_no_explicit(monkeypatch, capsys):
    """--section 미지정 + WHOOING_SECTION_ID 미설정 → 첫 섹션 자동 선택."""
    monkeypatch.delenv("WHOOING_SECTION_ID", raising=False)
    fake = FakeClient(sections=[
        {"section_id": "sFirst", "title": "first"},
        {"section_id": "sSecond", "title": "second"},
    ])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["accounts", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "x11" in out  # 우리 fake 의 accounts 가 첫 섹션의 것으로 사용됨
    assert "현금" in out


def test_accounts_list_explicit_section_skips_sections_call(monkeypatch, capsys):
    """--section 명시 → list_sections 안 부르고 바로 list_accounts."""
    fake = FakeClient(
        # sections_error 를 set 해도 호출되지 않아야 통과
        sections_error=ToolError("UPSTREAM", "이건 호출되면 안 됨"),
    )
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["accounts", "--section", "sX", "list"])
    assert rc == 0


def test_accounts_list_env_section_id_used(monkeypatch, capsys):
    """WHOOING_SECTION_ID 가 set 이면 그 값을 활성 섹션으로."""
    monkeypatch.setenv("WHOOING_SECTION_ID", "sEnv")
    fake = FakeClient(
        sections_error=ToolError("UPSTREAM", "호출되면 안 됨"),
    )
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["accounts", "list"])
    assert rc == 0


# ---- entries list -----------------------------------------------------


def test_entries_list_default_window_used(monkeypatch, capsys):
    """--days / --start / --end 미지정 → config.default_window_days."""
    fake = FakeClient(entries=[{
        "entry_id": "e1", "entry_date": "20260510", "money": 1000,
        "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스",
    }])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["entries", "--section", "s1", "list"])
    assert rc == 0
    assert len(fake.list_entries_calls) == 1
    sid, start, end = fake.list_entries_calls[0]
    assert sid == "s1"
    assert len(start) == 8 and start.isdigit()
    assert len(end) == 8 and end.isdigit()
    out = capsys.readouterr().out
    assert "스타벅스" in out
    # cli 의 _print_table 은 raw str(money) 출력 (콤마 미적용 — TUI
    # EntriesScreen 의 _fmt_money 와 다름).
    assert "1000" in out
    assert "총 1건" in out


def test_entries_list_explicit_date_range(monkeypatch, capsys):
    fake = FakeClient(entries=[])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main([
        "entries", "--section", "s1", "list",
        "--start", "20260501", "--end", "20260510",
    ])
    assert rc == 0
    sid, start, end = fake.list_entries_calls[0]
    assert (start, end) == ("20260501", "20260510")


def test_entries_list_only_start_returns_user_input_error(monkeypatch, capsys):
    """--start 만 주면 USER_INPUT (--end 없음)."""
    fake = FakeClient()
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main([
        "entries", "--section", "s1", "list", "--start", "20260501",
    ])
    assert rc == 2  # USER_INPUT


def test_entries_list_invalid_date_returns_user_input_error(monkeypatch, capsys):
    """--start 가 잘못된 형식이면 USER_INPUT (parse_yyyymmdd ValueError)."""
    fake = FakeClient()
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main([
        "entries", "--section", "s1", "list",
        "--start", "2026-05-01", "--end", "2026-05-10",
    ])
    assert rc == 2


def test_entries_list_negative_days_rejected(monkeypatch, capsys):
    fake = FakeClient()
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main([
        "entries", "--section", "s1", "list", "--days", "-3",
    ])
    assert rc == 2


def test_entries_list_json_output(monkeypatch, capsys):
    fake = FakeClient(entries=[{"entry_id": "e1", "money": 1000}])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["--json", "entries", "--section", "s1", "list", "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["section_id"] == "s1"
    assert isinstance(parsed["entries"], list)
    assert parsed["entries"][0]["entry_id"] == "e1"


def test_entries_list_empty_table(monkeypatch, capsys):
    fake = FakeClient(entries=[])
    _patch_client_and_auth(monkeypatch, fake)
    rc = cli_mod.main(["entries", "--section", "s1", "list", "--days", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    # _print_table 이 빈 결과에는 "(empty)" 출력
    assert "(empty)" in out
    # footer 의 "총 0건" 도 확인
    assert "총 0건" in out


# ---- argparse -----------------------------------------------------------


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "sections" in out
    assert "accounts" in out
    assert "entries" in out


def test_no_subcommand_attempts_tui_launch(monkeypatch, capsys):
    """서브커맨드 없이 호출하면 TUI 실행 시도 — 토큰 없으면 3 (또는 6).

    실 환경 (monorepo) 에서는 .env 가 있어 GUI 가 뜰 수 있으니, 확실한
    테스트를 위해 load_auth_from_env 를 fail 시켜 GUI 부팅 진입 직전에
    멈추게 한다 (run_app 내부의 USER_INPUT exit 3).
    """
    def _no_token():
        raise ValueError("WHOOING_AI_TOKEN 미설정")
    # cli.py 의 main 은 서브커맨드 없을 때 from whooing_tui.app import run_app
    # 후 run_app() 호출. run_app 도 load_auth_from_env 를 다시 부른다.
    import whooing_tui.app as app_mod
    monkeypatch.setattr(app_mod, "load_auth_from_env", _no_token)
    rc = cli_mod.main([])
    assert rc == 3
