"""ListCrudMixin — DataTable 기반 목록 화면의 공통 cursor/render 로직.

감사 2026-06 §1-C 부분 적용. `monthly_entries`·`frequent_entries`·
`report_customs` 가 각자 복제하던 (1) cursor → `self._rows` dict 해석,
(2) 테이블 clear + add_row 루프 를 한 곳으로. compose/CSS/worker 골격은
화면마다 특수성이 커 그대로 둔다(전체 베이스 추출은 4번째 유사 화면 전까지
보류 — internal 감사 문서 참조).

서브클래스 계약:
- `TABLE_ID`  : DataTable 의 `#id` (예: `"#m_table"`).
- `self._rows`: 현재 표시 중인 행 dict 리스트.
- `_row_id(row) -> str`   : 행의 고유 id (삭제·cursor 매칭 키).
- `_row_cells(row) -> Sequence`: add_row 에 넘길 셀 값들.
"""

from __future__ import annotations

from typing import Any, Sequence

from textual.widgets import DataTable


class ListCrudMixin:
    """DataTable 목록 화면 공통 — cursor 해석 + 행 렌더."""

    TABLE_ID: str = "#table"
    _rows: list[dict[str, Any]]

    def _row_id(self, row: dict[str, Any]) -> str:  # pragma: no cover - 추상
        raise NotImplementedError

    def _row_cells(self, row: dict[str, Any]) -> Sequence[Any]:  # pragma: no cover
        raise NotImplementedError

    def _cursor_row(self) -> dict[str, Any] | None:
        """현재 cursor 행에 대응하는 `self._rows` dict (없으면 None)."""
        table = self.query_one(self.TABLE_ID, DataTable)  # type: ignore[attr-defined]
        if not table.row_count:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            rid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return None
        return next(
            (r for r in self._rows if self._row_id(r) == rid), None,
        )

    def _render_rows(self) -> None:
        """`self._rows` 로 테이블 재구성 (clear + add_row, key=행 id)."""
        table = self.query_one(self.TABLE_ID, DataTable)  # type: ignore[attr-defined]
        table.clear()
        for r in self._rows:
            table.add_row(*self._row_cells(r), key=self._row_id(r))
