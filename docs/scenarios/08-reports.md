# 08. 보고서 + 통계

## 목적

후잉의 통계 (재무상태표 / 손익 / 월별 추이 / 캘린더 / 예산 대비 실적 등)
를 같은 화면 안의 좌/우 패널로 빠르게 둘러본다.

## 주요 경로

```
EntriesScreen
  └─ t (또는 한글 ㅅ) | 메뉴 → 화면 → 보고서/통계
      └─ ReportsScreen (모달, 단일)
          ├─ 좌측 OptionList (11 항목)
          └─ 우측 status + VerticalScroll(Static)
              ├─ ↑/↓ : 항목 highlight → 자동 fetch worker
              ├─ Enter : 강제 refresh
              └─ Esc / q : 닫기 → EntriesScreen
```

## 지원하는 보고서

| 항목 | 후잉 endpoint / type | 비고 |
|---|---|---|
| 재무상태표 | `report-get` (type=report) | 자산/부채/자본 누계. |
| 손익 요약 | `report-get` (type=report_summary) | 수입·지출 분류. |
| 월별 추이 | `report-get` (type=report, rows_type=month) | 시계열. |
| 항목별 증감 | `report-get` (type=report) | 카테고리별. |
| 캘린더 | `report-get` (type=calendar) | 일별. |
| 최근 거래 20건 | `report-get` (type=entries_latest) | 빠른 조회. |
| 사용자 정의 BS | `report_customs-get` | 사용자 정의 BS. |
| 사용자 정의 PL | `report_customs-get` | 사용자 정의 PL. |
| 예산 대비 실적 (지출) | `budget-get` pl=expenses | 정해진 월. |
| 예산 대비 실적 (수입) | `budget-get` pl=income | 정해진 월. |
| 장기 목표 | `budget_goal-get` | 자본 목표 vs 현재. |

## 단계별 흐름

1. EntriesScreen 에서 `t` → ReportsScreen.
2. on_mount 가 첫 항목 (재무상태표) 즉시 fetch — 사용자 진입 시점에
   결과 노출.
3. ↑/↓ 로 highlight 이동 → `on_option_list_option_highlighted` 가 새
   항목 fetch (`@work(exclusive=True, group="reports_fetch")`).
   - 빠른 이동 시 이전 fetch 가 자동 cancel.
   - race 방지: worker 가 `_current_item_id` 비교 후 렌더.
4. Enter — 강제 refresh (캐시 무시).
5. Esc / q — EntriesScreen 복귀.

## 위임 정책 (공식 후잉 MCP)

후잉 REST 의 `report-get` 등은 endpoint 가 매우 fragile (account
enum / csv vs YYYYMM 차이). 본 도구는 종전에 직접 호출하다가 403 회귀
다발 → CL #52755 부터 **공식 후잉 MCP 서버** (`https://whooing.com/mcp`)
에 JSON-RPC 로 위임.

- 위임 클라이언트: [`official_mcp.py`](../../tui/src/whooing_tui/official_mcp.py).
- `OfficialMcpError` 가 ToolError 와 별개 — UI 가 분기해 안내.

## 렌더링

응답 shape 가 다양해 종전엔 raw JSON 그대로 표시했으나 CL #52790+
부터 *항목별 사람-친화 표* 로:
- `balance_sheet`: "자산 / 부채 / 자본" 행에 천단위 콤마 금액.
- `report_summary`: 카테고리별 수입·지출.
- `budget`: 월별 목표 vs 실적.
- `calendar`: 일별 거래 수 + 합계.

새 보고서를 지원하려면 `_build_menu()` 에 항목 + fetch fn 추가, 그리고
`_render_payload()` 에 분기 추가. 예시는 reports.py 의 기존 패턴.

## 관련 코드

- [`screens/reports.py`](../../tui/src/whooing_tui/screens/reports.py) —
  `ReportsScreen` (CL #52792+ 통합 패널), `_build_menu`, `_render_payload`.
- [`official_mcp.py`](../../tui/src/whooing_tui/official_mcp.py) —
  공식 후잉 MCP JSON-RPC 클라이언트.
- [`client.py: call_official_tool`](../../tui/src/whooing_tui/client.py)
  — CachedWhooingClient 의 pass-through wrapper.
- [`screens/budget_edit.py`](../../tui/src/whooing_tui/screens/budget_edit.py),
  [`screens/goal_edit.py`](../../tui/src/whooing_tui/screens/goal_edit.py)
  — 보고서가 가리키는 값을 *편집* 하는 별도 화면.
