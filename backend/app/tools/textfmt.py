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


# 받침 유무에 따라 갈리는 조사 쌍 — (받침 있음, 받침 없음)
_JOSA = {
    "이": ("이", "가"), "가": ("이", "가"),
    "은": ("은", "는"), "는": ("은", "는"),
    "을": ("을", "를"), "를": ("을", "를"),
    "과": ("과", "와"), "와": ("과", "와"),
}
# 숫자로 끝나는 경우의 읽는 소리 받침 유무 (1 일, 3 삼, 6 육, 7 칠, 8 팔, 0 영)
_DIGIT_FINAL = {"1": True, "2": False, "3": True, "4": False, "5": False,
                "6": True, "7": True, "8": True, "9": False, "0": True}


def has_final_consonant(word: str) -> bool | None:
    """마지막 글자에 받침이 있으면 True, 없으면 False, 판단 불가면 None."""
    # 따옴표·괄호로 끝나는 경우('농림지역') 그 안쪽 글자로 판단한다.
    text = (word or "").strip().rstrip("'\"’”)]}」』>")
    if not text:
        return None
    last = text[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28 != 0
    if last in _DIGIT_FINAL:
        return _DIGIT_FINAL[last]
    return None


def josa(word: str, particle: str) -> str:
    """앞말 받침에 맞는 조사를 골라 'word + 조사' 로 돌려준다.

    "…교육연구시설 등" + "가" 처럼 무조건 붙이면 '등가' 가 된다('등' 은 받침이
    있으므로 '등이' 가 맞다). 판단 불가한 문자로 끝나면 받침 있는 쪽을 쓴다.
    """
    pair = _JOSA.get(particle)
    if not pair:
        return f"{word}{particle}"
    final = has_final_consonant(word)
    return f"{word}{pair[0] if final is not False else pair[1]}"
