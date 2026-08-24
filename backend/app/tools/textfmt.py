"""표시용 문자열 정규화."""

from __future__ import annotations

import re
from datetime import date

_YMD8 = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def kdate(value: str | None) -> str:
    """법령·조례 시행일자 YYYYMMDD -> YYYY-MM-DD.

    날짜 '필드' 값에만 쓴다. 문장 전체를 정규식으로 훑어 8자리 숫자를 바꾸면
    조례 원문에 든 다른 번호(예: 별표 12345678)까지 망가지므로 그렇게 하지 않는다.
    구분자가 이미 있거나, 8자리가 아니거나, 실재하지 않는 날짜면 원문 그대로 둔다
    ('?'·빈 값 포함).
    """
    text = str(value or "").strip()
    match = _YMD8.match(text)
    if not match:
        return text
    try:
        date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return text
    return f"{match[1]}-{match[2]}-{match[3]}"
