"""하나카드 보안메일 HTML adapter — table-aware parser unit tests.

CL #52940+. 종전 line-offset 기반 parser 는 외화 / 매출취소 / 다중 카드
섹션에서 잘못된 row 추출 (사용자 보고: "USA" / "일시불" 가맹점). 본 테스트는
합성 HTML 으로 각 시나리오를 검증.

실제 사용자 명세서는 결제정보가 들어있어 fixture 로 P4 / GitHub 에 올리지
않는다. 합성 HTML 은 동일한 테이블 구조만 재현.
"""

from __future__ import annotations

from whooing_core.html_adapters.hanacard_secure_mail import (
    extract_rows_from_decrypted,
)


# ---- 합성 HTML 빌더 -----------------------------------------------------


def _build_main_table_html(rows: list[list[str]]) -> str:
    """메인 거래 표 HTML — header + rows.

    각 row: cells 의 list. 짧으면 빈 cell 로 패딩.
    """
    header_html = """
    <tr>
      <td>이용 일자</td>
      <td>이용가맹점(은행)</td>
      <td>이용금액</td>
      <td>할부 기간</td>
      <td>이번 달 결제하실 금액</td>
      <td>이용 혜택</td>
      <td>혜택 금액</td>
      <td>결제후잔액</td>
      <td>포인트</td>
    </tr>
    """
    row_html = ""
    for r in rows:
        cells = (r + [""] * 11)[:11]
        row_html += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return f"<table>{header_html}{row_html}</table>"


def _build_cancellation_table_html(rows: list[list[str]]) -> str:
    header_html = """
    <tr>
      <td>이용일자</td>
      <td>취소일자</td>
      <td>이용 가맹점명(은행)</td>
      <td>사용카드</td>
      <td>이용금액</td>
      <td>미화금액(US$)</td>
    </tr>
    """
    row_html = ""
    for r in rows:
        cells = (r + [""] * 6)[:6]
        row_html += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return f"<table>{header_html}{row_html}</table>"


def _build_foreign_table_html(rows: list[list[str]]) -> str:
    """해외이용 표 — *parser 는 무시* 해야. (메인 표가 KRW 환산 포함.)"""
    header_html = """
    <tr>
      <td>이용 일자</td>
      <td>국가</td>
      <td>도시</td>
      <td>이용 가맹점명</td>
      <td>통화 구분</td>
      <td>현지 이용금액</td>
      <td>미화금액 (US$)</td>
      <td>환율 (원/US$)</td>
      <td>이용원금</td>
      <td>수수료</td>
    </tr>
    """
    row_html = ""
    for r in rows:
        cells = (r + [""] * 10)[:10]
        row_html += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return f"<table>{header_html}{row_html}</table>"


_BASE_PROLOGUE = """
<html><head><title>하나카드 이용대금명세서</title></head><body>
<p>2026년 5월 27일</p>
<p>일시불 및 할부: 2026. 04. 15 ~ 2026. 05. 14</p>
"""

_BASE_EPILOGUE = "</body></html>"


# ---- 기본 case --------------------------------------------------------


def test_main_table_extracts_simple_purchase():
    """가장 단순한 행 — date + merchant + amount."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/18", "365후레쉬마트", "10,800", "", "", "10,800", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 1
    assert rows[0].date == "20260418"
    assert rows[0].amount == 10800
    assert rows[0].merchant == "365후레쉬마트"


def test_main_table_handles_discount_row():
    """할인 row: 이용금액 (양수) + 할인 + 혜택금액 (음수) → 음수 amount.

    명세서에 "SKT통신요금할인받으신금액 15,000 할인 -15,000" 식의 row.
    실제 effective transaction 은 -15,000 (크레딧).
    """
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/15", "SKT통신요금할인받으신금액", "15,000", "", "", "",
         "", "할인", "-15,000", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 1
    assert rows[0].amount == -15000
    assert rows[0].merchant == "SKT통신요금할인받으신금액"


def test_main_table_skips_non_date_rows():
    """섹션 헤더 / 소계 / 가맹점 상세 등 — 첫 cell 이 MM/DD 가 아닌 row 는 skip."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["(본인신용카드) 김*진 고객님_CLUB SK VISA3698"],  # section header
        ["04/15", "교통-지하철003건", "6,700", "", "", "6,700", "", "", "", "", ""],
        ["이용가맹점 상세정보", "02-1234-5678"],  # detail row
        ["카드소계 1건", "", "", "", "6,700", "", "", "", "", "", ""],  # subtotal
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    # 교통 1건만.
    assert len(rows) == 1
    assert rows[0].merchant == "교통-지하철003건"


def test_main_table_handles_partial_cancellation_negative():
    """메인 표 안의 음수 금액 (부분 취소) — 그대로 양수 아닌 값으로 추출.

    예: APPLE_KCP 가 같은 날 30,000 / -27,000 으로 두 row.
    """
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/15", "APPLE_KCP_엔에이치엔케이씨피", "30,000", "", "", "3,000",
         "", "", "", "", ""],
        ["04/15", "APPLE_KCP_엔에이치엔케이씨피", "-27,000", "", "", "",
         "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 2
    assert rows[0].amount == 30000
    assert rows[1].amount == -27000


def test_cancellation_table_extracts_negative():
    """매출취소 표: cells[0]=이용일자, cells[2]=가맹점, cells[4]=음수 금액."""
    html = _BASE_PROLOGUE + _build_cancellation_table_html([
        ["04/30", "05/11", "대중교통할인", "VISA3698", "-5,000", "-0.00"],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 1
    assert rows[0].date == "20260430"
    assert rows[0].amount == -5000
    assert rows[0].merchant == "대중교통할인"


def test_foreign_table_is_skipped():
    """해외이용 표는 *건너뜀* — 메인 표가 KRW 환산 row 를 이미 포함.

    이중 추출 방지 + "USA" 가 가맹점으로 잘못 잡히는 회귀 가드.
    """
    html = _BASE_PROLOGUE + _build_foreign_table_html([
        ["04/19", "USA", "4029357733", "PAYPAL *ARLOTECHNOL",
         "USD", "19.99", "20.19", "1,490.00", "30,083", "59"],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert rows == []


def test_foreign_row_already_in_main_table_no_duplication():
    """메인 + 해외 표 양쪽에 같은 거래가 있을 때 메인의 row 만 추출.

    실세계 시나리오: 외화 거래는 메인 표에 KRW 환산 row 가 있고 해외 표에
    상세 (현지 금액 / 환율) 가 있다. parser 는 메인 만 추출.
    """
    main = _build_main_table_html([
        ["04/19", "PAYPAL *ARLOTECHNOL", "30,083", "", "", "30,083",
         "59", "", "", "", ""],
    ])
    foreign = _build_foreign_table_html([
        ["04/19", "USA", "4029357733", "PAYPAL *ARLOTECHNOL",
         "USD", "19.99", "20.19", "1,490.00", "30,083", "59"],
    ])
    html = _BASE_PROLOGUE + main + foreign + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 1
    assert rows[0].merchant == "PAYPAL *ARLOTECHNOL"
    assert rows[0].amount == 30083


def test_multiple_card_sections_in_main_table():
    """한 메인 표 안에 두 카드 섹션 (VISA / MASTER) — 둘 다 추출."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["(본인신용카드) ... VISA3698 ..."],
        ["04/15", "스타벅스", "5,000", "", "", "5,000", "", "", "", "", ""],
        ["카드소계 1건", "", "", "", "5,000", "", "", "", "", "", ""],
        ["(본인신용카드) ... MASTER2991 ..."],
        ["04/20", "쿠팡", "12,000", "", "", "12,000", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 2
    merchants = [r.merchant for r in rows]
    assert "스타벅스" in merchants
    assert "쿠팡" in merchants


def test_year_inferred_from_statement_date():
    """이용대금명세서 작성일 (2026년 5월 27일) 의 연도를 모든 row 에 적용."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/18", "X", "1,000", "", "", "1,000", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert rows[0].date == "20260418"


def test_year_rolls_back_for_distant_future_month():
    """명세서 5월인데 row MM=11 (11월) → 작년 11월로 추정."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["11/30", "작년 거래", "1,000", "", "", "1,000", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    # 명세서 2026년 5월 → MM=11 은 2025년 11월.
    assert rows[0].date == "20251130"


def test_zero_amount_row_skipped():
    """금액 0 row 는 헤더 잔재 — skip."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/15", "X", "0", "", "", "0", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert rows == []


def test_dedup_same_date_amount_merchant_prefix():
    """같은 (date, amount, merchant prefix) row 가 두 번 나오면 dedup."""
    html = _BASE_PROLOGUE + _build_main_table_html([
        ["04/15", "스타벅스 강남점", "5,000", "", "", "5,000", "", "", "", "", ""],
        ["04/15", "스타벅스 강남점", "5,000", "", "", "5,000", "", "", "", "", ""],
    ]) + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    assert len(rows) == 1


def test_empty_html_returns_empty():
    rows = extract_rows_from_decrypted("<html><body></body></html>")
    assert rows == []


def test_html_without_tables_returns_empty():
    rows = extract_rows_from_decrypted(
        "<html><body><p>그냥 텍스트</p></body></html>"
    )
    assert rows == []


def test_other_tables_in_document_are_skipped():
    """헤더 / 요약 / 한도 표 등 거래 외 table 은 모두 skip."""
    other = """
    <table>
      <tr><td>원결제일</td><td>2026년 05월 27일</td></tr>
      <tr><td>작성기준일</td><td>2026년 05월 15일</td></tr>
    </table>
    <table>
      <tr><td>04/15</td><td>X</td></tr>
    </table>
    """
    html = _BASE_PROLOGUE + other + _BASE_EPILOGUE
    rows = extract_rows_from_decrypted(html)
    # date 가 있어도 main / cancellation header 가 아니라 classify 가 "other" 로 잡힘 → skip.
    assert rows == []
