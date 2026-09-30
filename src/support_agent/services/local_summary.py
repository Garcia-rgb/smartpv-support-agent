"""在没有生成模型时，把检索片段压缩成可核对的简短摘录。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .rag import SearchHit

_MARKUP = re.compile(r"^(?:#{1,6}\s*|[-*]\s*|\d+[.)、]\s*)")
_CREDENTIALS = re.compile(r"密码|口令|密钥|\bPSW\b|\bAPI[_ -]?KEY\b", re.I)
_ACTION = re.compile(r"检查|排查|定位|断开|下电|上电|确认|修复|测量|核对|更换")
_GENERIC = re.compile(r"请问|对应|哪个|哪些|应该|怎么|如何|处理|是什么|什么|告诉我|一下")


def _plain(line: str) -> str:
    line = _MARKUP.sub("", line.strip())
    line = line.replace("**", "").replace("`", "")
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -|：")


def _short(text: str, limit: int = 120) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    boundary = max(text.rfind(char, 0, limit) for char in "。；，、")
    if boundary >= limit // 2:
        return text[: boundary + 1]
    return text[: limit - 1].rstrip() + "…"


def _query_terms(question: str) -> set[str]:
    core = _GENERIC.sub("", question)
    core = re.sub(r"[？?！!，,。\s]", "", core)
    terms = {core} if len(core) >= 2 else set()
    terms.update(core[index : index + 2] for index in range(max(0, len(core) - 1)))
    return terms


def _score(line: str, terms: set[str]) -> int:
    return sum(3 if len(term) > 2 else 1 for term in terms if term in line)


def _lines(hits: Sequence[SearchHit]) -> list[str]:
    found: list[str] = []
    for hit in hits[:5]:
        content = hit.chunk.content.replace("\\n", "\n")
        for raw in content.splitlines():
            line = _plain(raw)
            if len(line) < 8 or _CREDENTIALS.search(line):
                continue
            if line.startswith("|---") or line.startswith("<"):
                continue
            if line not in found:
                found.append(line)
    return found


def _alarm_conclusion(lines: list[str], terms: set[str]) -> str | None:
    for line in lines:
        if "|" not in line or _score(line, terms) == 0:
            continue
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        if len(cells) < 3 or not re.fullmatch(r"\d{4,6}", cells[0]):
            continue
        if "告警" not in line and "绝缘阻抗" not in line:
            continue
        result = f"{cells[1]}对应 **{cells[0]}** 告警"
        if cells[2] in {"重要", "次要", "提示"}:
            result += f"，级别为{cells[2]}"
        return result + "。"
    return None


def summarize_explicit_case(question: str, hits: Sequence[SearchHit]) -> str | None:
    """同一故障描述紧邻“解决方法”时，直接使用案例原文。"""
    core = _GENERIC.sub("", question).strip(" ？?！!，,。")
    if len(core) < 6:
        return None
    for hit in hits[:2]:
        lines = [_plain(raw) for raw in hit.chunk.content.replace("\\n", "\n").splitlines()]
        for index, line in enumerate(lines):
            if core not in line or _CREDENTIALS.search(line):
                continue
            following = lines[index + 1 : index + 4]
            if not following or not re.match(r"(?:解决|处理)方法[：:]", following[0]):
                continue
            steps = [re.split(r"[：:]", following[0], maxsplit=1)[-1].strip()]
            for extra in following[1:]:
                if re.match(r"\d+[）.)、]", extra) or _CREDENTIALS.search(extra):
                    break
                if extra and len(extra) >= 8:
                    steps.append(extra)
                if len(steps) == 3:
                    break
            return "\n".join(
                ["**结论**", "资料记录了同类故障的处理案例。", "", "**处理方法**"]
                + [f"{number}. {_short(step, 140)}" for number, step in enumerate(steps, 1)]
            )
    return None


def summarize_local_retrieval(question: str, hits: Sequence[SearchHit]) -> str:
    """只摘录命中的事实；不靠规则补写设备参数或操作结论。"""
    if not hits:
        return "当前资料库没有找到足够依据。请补充设备型号、告警码或现场现象。"
    explicit = summarize_explicit_case(question, hits)
    if explicit:
        return explicit
    terms = _query_terms(question)
    lines = _lines(hits)
    if not lines:
        return "找到相关资料，但没有可安全摘录的文字。请查看下方引用。"

    conclusion = _alarm_conclusion(lines, terms)
    answer_line = next(
        (
            line.removeprefix("参考答案：")
            for line in lines
            if line.startswith("参考答案：") and _score(line, terms) > 0
        ),
        None,
    )
    if conclusion is None and answer_line:
        conclusion = _short(answer_line, 180)
    if conclusion is None:
        facts = sorted(
            lines, key=lambda line: (_score(line, terms), len(line) <= 140), reverse=True
        )
        conclusion = _short(facts[0], 150)

    parts = ["**结论**", conclusion]
    if re.search(r"怎么|如何|处理|排查|解决|步骤", question):
        numbered = [line for line in lines if re.match(r"步骤\s*[1-9]", line)]
        if numbered:
            actions = sorted(
                numbered,
                key=lambda line: int(re.match(r"步骤\s*([1-9])", line).group(1)),
            )
        else:
            actions = [
                line
                for line in lines
                if _ACTION.search(line)
                and line != conclusion
                and not re.match(r"(?:触发|适用|注意|\d+\.\d+)", line)
            ]
            actions.sort(key=lambda line: _score(line, terms), reverse=True)
        chosen: list[str] = []
        for line in actions:
            step = _short(line, 105)
            if step not in chosen and step not in conclusion:
                chosen.append(step)
            if len(chosen) == 3:
                break
        if chosen:
            parts += ["", "**排查要点**"]
            if numbered:
                parts.extend(chosen)
            else:
                parts.extend(f"{index}. {step}" for index, step in enumerate(chosen, start=1))
    parts += ["", "具体操作请核对下方引用中的机型和资料版本。"]
    return "\n".join(parts)
