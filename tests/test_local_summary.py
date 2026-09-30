from types import SimpleNamespace

from support_agent.services.local_summary import summarize_local_retrieval


def hit(content: str):
    return SimpleNamespace(chunk=SimpleNamespace(content=content))


def test_alarm_answer_extracts_code_and_short_steps() -> None:
    hits = [
        hit(
            "## 绝缘阻抗故障位置定位\\n"
            "- 步骤 1 定位故障组串：逐串接入，查看是否上报告警。\\n"
            "- 步骤 2 定位故障组件：下电后断开疑似组件，上电查看告警。"
        ),
        hit("| 2062 | 绝缘阻抗低 | 重要 | 光伏阵列对地绝缘不良 |"),
    ]
    answer = summarize_local_retrieval("绝缘阻抗低对应哪个告警？应该怎么处理？", hits)

    assert "2062" in answer
    assert "**结论**" in answer
    assert "**排查要点**" in answer
    assert "步骤 1" in answer
    assert "步骤 2" in answer
    assert answer.index("步骤 1") < answer.index("步骤 2")
    assert "1. 步骤 1" not in answer
    assert "[资料" not in answer
    assert len(answer) < 500


def test_no_hits_does_not_invent_answer() -> None:
    assert "没有找到足够依据" in summarize_local_retrieval("未知设备", [])
