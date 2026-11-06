"""统一提示词。所有模型使用同一份系统提示和题面格式。"""

from __future__ import annotations

SYSTEM = """你是熟悉中国劳动法律法规和薪酬、社保、个税计算的助手。请按中国大陆现行法律法规和政策回答问题；题目涉及地方标准的，以题面指明的城市和年度为准。

回答要求：先简要写出分析和计算过程，最后单独一行输出一个 JSON 对象，字段如下：
- "verdict"：判断题和改写题填 "合法" 或 "违法"；计算题填 null
- "amount"：计算题填最终数字（只写数字，不带单位和千分位逗号，金额保留两位小数）；其他题填 null
- "key_value"：改写题按题目要求填一个数字；其他题填 null
- "rewrite"：改写题填改写后的条款原文；其他题填 null
- "basis"：所依据的法律法规或政策文件名称及条号，字符串列表"""

TOOLS_ADDENDUM = """

你可以调用劳动法规则引擎提供的工具来查询城市政策数据和计算金额。工具结果由确定性规则计算，带有法条依据；需要时请调用工具后再作答。"""

FORMAT_HINT = {
    "verdict": "（这是判断题：verdict 必填。）",
    "amount": "（这是计算题：amount 必填。）",
    "rewrite": "（这是改写题：verdict、key_value 必填；如认为合法，key_value 填原条款中的对应数值，rewrite 填 null。）",
}


def system_prompt(with_tools: bool) -> str:
    return SYSTEM + (TOOLS_ADDENDUM if with_tools else "")


def user_prompt(item: dict) -> str:
    inp = item["input"]
    return f"{inp['question']}\n{FORMAT_HINT[inp['format']]}"
