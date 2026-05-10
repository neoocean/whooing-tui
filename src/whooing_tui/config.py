"""TUI 설정 — TOML 파일 기반.

탐색 우선순위 (먼저 발견된 1개):
  1. $WHOOING_TUI_CONFIG (절대 경로 override)
  2. <project root>/whooing-tui.toml
  3. ~/.config/whooing-tui/config.toml

파일 없거나 읽기 실패 → 기본값 (안전한 OFF).

스키마 (TOML):
  [ui]
  theme = "textual-dark" | "textual-light"
  entries_page_size = 50

  [entries]
  default_window_days = 30
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    theme: str = "textual-dark"
    entries_page_size: int = 50
    default_window_days: int = 30

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        ui = data.get("ui") or {}
        en = data.get("entries") or {}
        return cls(
            theme=str(ui.get("theme") or "textual-dark"),
            entries_page_size=int(ui.get("entries_page_size") or 50),
            default_window_days=int(en.get("default_window_days") or 30),
        )


_CACHED: Config | None = None


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    explicit = os.getenv("WHOOING_TUI_CONFIG")
    if explicit:
        out.append(Path(explicit).expanduser())
    # project root: src/whooing_tui/config.py → parents[2]
    try:
        out.append(Path(__file__).resolve().parents[2] / "whooing-tui.toml")
    except IndexError:
        pass
    out.append(Path.home() / ".config" / "whooing-tui" / "config.toml")
    return out


def load_config(force_reload: bool = False) -> Config:
    """첫 호출 시 캐시. force_reload=True 면 재로드."""
    global _CACHED
    if _CACHED is not None and not force_reload:
        return _CACHED

    for p in _candidate_paths():
        if p.exists():
            try:
                with open(p, "rb") as f:
                    data = tomllib.load(f)
                _CACHED = Config.from_dict(data)
                log.info("loaded config from %s (%s)", p, _CACHED)
                return _CACHED
            except (tomllib.TOMLDecodeError, OSError) as e:
                log.warning("config 파일 %s 읽기 실패: %s — default 사용", p, e)
                break

    _CACHED = Config()
    log.info("config 파일 없음 — default (%s)", _CACHED)
    return _CACHED


def reset_cache() -> None:
    """테스트용. 다음 load_config() 호출 시 재로드."""
    global _CACHED
    _CACHED = None
