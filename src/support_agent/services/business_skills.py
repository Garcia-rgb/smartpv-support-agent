"""Trusted, packaged business skills. User documents are never executable skills."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .retrieval_quality import product_models

ROOT = Path(__file__).resolve().parents[1] / "business_skills"


@dataclass(frozen=True)
class BusinessSkill:
    identifier: str
    title: str
    version: str = "1.0.0"


SKILLS = {
    "point_table": BusinessSkill("point_table", "点表制作"),
    "site_troubleshooting": BusinessSkill("site_troubleshooting", "现场排查"),
}


@lru_cache(maxsize=2)
def instructions(identifier: str) -> str:
    if identifier not in SKILLS:
        raise ValueError("未注册的业务skill")
    text = (ROOT / identifier / "SKILL.md").read_text(encoding="utf-8")
    if len(text) > 6000:
        raise ValueError("业务skill说明超出长度限制")
    return text


def select_skill(text: str) -> BusinessSkill | None:
    if "点表" in text and re.search(r"制作|生成|导出|做.{0,4}点表|上传.*协议", text):
        return SKILLS["point_table"]
    if re.search(
        r"挂不上|挂载不上|不刷新|不更新|数据.{0,5}不对|无直流|没数据|无数据|"
        r"通信中断|通讯中断|离线|断联|告警|故障|异常|已检查|检查了|已反馈",
        text,
    ):
        return SKILLS["site_troubleshooting"]
    return None


def starts_issue(text: str, history: list[dict[str, str]]) -> bool:
    skill = select_skill(text)
    if not skill or skill.identifier != "site_troubleshooting":
        return False
    if not history or re.search(r"新问题|换个问题|另一个问题|不说这个", text):
        return True
    if re.search(r"这个|那个|它|还是|仍然|已经|检查|试了|下一步|接下来|刚才|上面|前面", text):
        return False
    return bool(product_models(text) or re.search(r"怎么|如何|逆变器|数采|采集器|电表", text))


def issue_state(text: str, history: list[dict[str, str]]) -> str:
    """Build a compact factual ledger from user messages only, never suggestions."""
    users = [m["content"] for m in history if m["role"] == "user"] + [text]
    for index in range(len(users) - 1, -1, -1):
        if re.search(r"另一个问题|换个问题|新问题|不说这个", users[index]):
            users = users[index:]
            break
    if len(users) < 2:
        return ""
    models = list(dict.fromkeys(m for value in users for m in product_models(value)))
    observed = [
        value[:500]
        for value in users
        if re.search(r"检查|已|测得|测了|尝试|试了|灯亮|灯不亮|正常|恢复|无效|还是|仍然", value)
    ]
    lines = [
        "当前问题状态（只记录用户描述，不把助手建议当执行事实）：",
        "最初现象：" + users[0][:700],
    ]
    if models:
        lines.append("用户提及型号：" + "、".join(models))
    if observed:
        lines.append("最近实际反馈（仍须区分用户假设与检查确认）：\n" + "\n".join(observed[-5:]))
    lines.append("当前追问：" + text[:700])
    return "\n".join(lines)
