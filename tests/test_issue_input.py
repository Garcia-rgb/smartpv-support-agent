from support_agent.services.issue_input import normalize_issue_input


def test_service_followup_search_uses_facts_not_generation_instructions():
    prompt = ("现场问题：逆变器断联\n设备：华为\n已检查结果（最近三步）：\n"
              "供电正常\n请结合结果给下一步")
    assert "供电正常" in normalize_issue_input(prompt, include_fields=False)
    assert "请结合" not in normalize_issue_input(prompt, include_fields=False)
    assert "请结合" in normalize_issue_input(prompt)


def test_chat_and_nameplate_keep_fault_address_and_model():
    raw = ("zb\n这台逆变器一直挂不上\n地址是2\n扫进去跑出来一个地址6的\n"
           "补充图片识别文字：\n合格证\n生产日期2025-09-16\n"
           "HUAWEI\nModel:SUN2000-100KTL-M2\n质检员00112208")
    query = normalize_issue_input(raw)
    assert "SUN2000-100KTL-M2" in query
    assert "地址是2" in query and "地址6" in query
    assert "挂不上" in query
    assert "2025-09-16" not in query and "00112208" not in query


def test_goodwe_specs_are_not_used_as_fault_query():
    raw = ("客户说数据不刷新\n不要重启设备\n补充图片识别文字：\n"
           "固德威 GOODWE\n型号: GW80K-MT\n最大直流工作电流44/44/44/44A")
    query = normalize_issue_input(raw)
    assert "GW80K-MT" in query and "数据不刷新" in query
    assert "不要重启设备" in query
    assert "44/44" not in query


def test_platform_table_does_not_invent_pv_mapping():
    raw = ("数据都不对了\n菜单切换\nPV1\nPV3\n输入电压(V)\n578.8\n570.9\n"
           "功率因数\n0\n有功功率(kW)\n61.31")
    query = normalize_issue_input(raw)
    assert "功率因数 0" in query and "有功功率(kW) 61.31" in query
    assert "PV1" not in query and "570.9" not in query
    assert "顺序未核实" in query


def test_plain_questions_and_nameplate_without_issue_are_unchanged():
    for raw in ("SUN2000绝缘阻抗低怎么排查？", "合格证\n华为\n型号SUN2000-M2\n2025-09-16"):
        assert normalize_issue_input(raw) == raw


def test_retrieval_query_excludes_table_values_but_preserves_customer_constraints():
    raw = ("数据都不对了\n不要复位\n菜单切换\n设备详情NB02\n"
           "告警信息\n暂无数据\n功率因数\n0\n有功功率(kW)\n61.31")
    query = normalize_issue_input(raw, include_fields=False)
    assert "NB02" not in query and "点表" in query and "不要复位" in query
    assert "NB02" in normalize_issue_input(raw)
    assert "暂无数据" not in query and "告警信息" not in query
    assert "61.31" not in query
    assert "61.31" in normalize_issue_input(raw)
