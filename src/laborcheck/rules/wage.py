"""工资相关：未签书面劳动合同的二倍工资。"""

from __future__ import annotations

from laborcheck.core import Basis, Finding, dec, yuan


def no_contract_double_wage(monthly_wage, months_without_contract: int) -> Finding:
    """自用工之日起超过一个月不满一年未订立书面合同，每月支付二倍工资。

    额外部分从第二个月起算，最多 11 个月（满一年视为已订立无固定期限劳动合同）。
    这里按整月计，跨月不足整月的部分需按日折算，不在本函数范围内。
    """
    extra = max(0, min(months_without_contract, 12) - 1)
    amount = yuan(dec(monthly_wage) * extra)
    return Finding(
        "wage.no_contract",
        None,
        f"未签合同 {months_without_contract} 个月，第二个月起每月多付一倍工资，共 {extra} 个月，"
        f"金额 {yuan(monthly_wage)} × {extra} = {amount} 元。",
        [
            Basis("labor_contract_law", "第八十二条"),
            Basis("spc_labor_interp_2", "第六条"),
            Basis("labor_contract_law_regulation", "第六条"),
            Basis("labor_contract_law_regulation", "第七条"),
        ],
        {"months": extra, "amount": amount},
    )
