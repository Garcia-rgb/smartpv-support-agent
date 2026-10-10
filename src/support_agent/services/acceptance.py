"""Answer acceptance with rule checks and explicit remote-data opt-in."""

import re
import time

from .agent import SupportAgent
from .quiz import answer_question


def judge_case(case, answer, status, citations, selected=None):
    rules = case.get("rules", {})
    reasons = []
    if rules.get("status") and status not in rules["status"]:
        reasons.append("回答状态不符：" + status)
    for group in rules.get("include_any", []):
        if not any(word.lower() in answer.lower() for word in group):
            reasons.append("缺少检查内容：" + "/".join(group))
    for pattern in rules.get("forbidden_patterns", []):
        if re.search(pattern, answer):
            reasons.append("出现禁止结论：" + pattern)
    for name in rules.get("source_names", []):
        if not any(name in citation.filename for citation in citations):
            reasons.append("未引用期望资料：" + name)
    if "selected" in rules and sorted(selected or []) != sorted(rules["selected"]):
        reasons.append("选项不符")
    return reasons


async def run_acceptance(db, cases, settings, *, remote=False):
    if len(cases) > 500 or not cases:
        raise ValueError("验收集需包含1至500条案例")
    if not remote:
        settings = settings.model_copy(update={"allow_remote_llm": False,
                                               "local_llm_model": None,
                                               "public_search_api_key": None})
    agent = SupportAgent(db, settings)
    results = []
    for case in cases:
        started = time.perf_counter()
        try:
            if case.get("kind") == "quiz":
                outcome = await answer_question(db, case["question"], llm_client=agent.llm)
                answer, status, citations = outcome.explanation, outcome.status, outcome.citations
                selected = outcome.selected_options
            else:
                outcome = await agent.run_turn(case["question"], case.get("history", []))
                answer, status, citations = outcome.answer, outcome.status, outcome.citations
                selected = None
            reasons = judge_case(case, answer, status, citations, selected)
            result = {"id": case["id"], "passed": not reasons, "reasons": reasons,
                      "answer": answer, "status": status,
                      "sources": [c.filename for c in citations]}
        except Exception as exc:
            result = {"id": case["id"], "passed": False, "reasons": [str(exc)[:300]]}
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        results.append(result)
    return {"mode": "remote" if remote else "offline",
            "total": len(results), "passed": sum(r["passed"] for r in results),
            "results": results,
            "note": "规则验收辅助检查，不能代替工程师确认或证明模型答案准确率。"}
