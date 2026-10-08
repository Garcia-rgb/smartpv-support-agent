"""Route normalized text to quiz only when it clearly has an exam structure."""

import re

from .quiz import parse_question

_QUIZ_LABEL = re.compile(
    r"^(?:第\s*\d+\s*题|(?:单选|多选|判断|选择)(?:题)?|正确或错误)(?:\s|$)"
)
_QUESTION_END = re.compile(r"[？?]|[（(]\s*[）)]")
_ISSUE_CONTEXT = re.compile(
    r"(?:客户|现场|工单|告警截图|报错截图|聊天记录|请帮我排查|多图资料联合分析)"
)


def is_quiz(text: str) -> bool:
    """Conservatively distinguish exam screenshots from customer messages.

    Two arbitrary A/B lines in a chat screenshot are insufficient. A quiz label,
    true/false pair, or a question with at least three labeled options is needed.
    """
    stripped = text.strip()
    if not stripped:
        return False
    try:
        kind, question, options = parse_question(stripped)
    except ValueError:
        return False
    if not question or _ISSUE_CONTEXT.search(stripped[:80]):
        return False
    if _QUIZ_LABEL.match(stripped):
        return True
    if kind == "判断" and {v.replace(" ", "") for v in options.values()} == {"正确", "错误"}:
        return True
    return len(options) >= 3 and bool(_QUESTION_END.search(question))
