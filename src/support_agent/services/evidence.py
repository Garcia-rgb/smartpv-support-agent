"""Preserve case outcomes and prevent failed operations becoming recommendations."""

import re

_FAILED = re.compile(
    r"无效|未生效|未解决|尚未解决|未恢复|失败|无改善|问题还在调试|"
    r"仍(?:然)?(?:无法|未|没有)|还是未|未找到下挂|尚在调试"
)
_BOUNDARY = re.compile(r"\n(?=\s*(?:[-*]\s|#{1,6}\s|\d+[）.)、]))")
_ACTIONS = {
    "恢复出厂": re.compile(r"恢复出厂|出厂重置|重置出厂"),
    "复位": re.compile(r"复位"),
    "重启": re.compile(r"重启"),
    "删除设备": re.compile(r"删除.{0,12}(?:设备|数采|采集器)|移除.{0,12}(?:设备|数采)"),
    "修改点表": re.compile(r"修改.{0,8}点表|上传.{0,8}点表|发送.{0,8}点表"),
}
_TAG = "[资料状态："


def case_blocks(content: str) -> list[str]:
    return _BOUNDARY.split(content.replace("\\n", "\n"))


def has_failed_case(content: str) -> bool:
    return bool(_FAILED.search(content))


def failed_actions(content: str) -> set[str]:
    actions = set()
    for block in case_blocks(content):
        if _FAILED.search(block):
            actions.update(name for name, pattern in _ACTIONS.items() if pattern.search(block))
    # An outcome tag is retained even if the original failure sentence was truncated.
    for tag in re.findall(r"\[不可作为解决步骤的操作：([^]]*)\]", content):
        actions.update(name for name in _ACTIONS if name in tag)
    return actions


def usable_case_text(content: str) -> str:
    """Local extracts must not promote unresolved records to successful solutions."""
    return "\n".join(block for block in case_blocks(content) if not _FAILED.search(block))


def prepare_evidence_context(content: str, limit: int | None = None) -> str:
    if content.startswith(_TAG):
        return content if limit is None else content[:limit]
    actions = failed_actions(content)
    if _FAILED.search(content):
        tag = "[资料状态：含失败尝试或未解决案例；这些操作不代表有效解决办法]"
    else:
        tag = "[资料状态：按原文核对结果；只有明确解决且适用的案例可作为处理依据]"
    if actions:
        tag += "\n[不可作为解决步骤的操作：" + "、".join(sorted(actions)) + "]"
    prefix = tag + "\n"
    return prefix + (content if limit is None else content[:max(0, limit - len(prefix))])


def guard_failed_recommendations(answer: str, contexts: list[str]) -> str:
    blocked = set().union(*(failed_actions(context) for context in contexts))
    if not blocked:
        return answer
    clauses = re.split(r"(?<=[。；;])|\n", answer)
    kept, removed = [], set()
    for clause in clauses:
        unsafe = set()
        for name in blocked:
            match = _ACTIONS[name].search(clause)
            if not match:
                continue
            surrounding = clause[max(0, match.start() - 24):match.end() + 24]
            # A warning or a description of a failed attempt may remain in the answer.
            warning = re.search(r"不要|不能|不应|不得|禁止|不建议|无效|失败|未解决", surrounding)
            if not warning:
                unsafe.add(name)
        if unsafe:
            removed.update(unsafe)
        else:
            kept.append(clause)
    if not removed:
        return answer
    note = ("资料中的" + "、".join(sorted(removed))
            + "属于失败尝试或未解决记录，不能作为已验证的处理办法。")
    body = "\n".join(part.strip() for part in kept if part.strip())
    return (body + "\n\n" if body else "") + note
