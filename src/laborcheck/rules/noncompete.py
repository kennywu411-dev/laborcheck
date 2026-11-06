"""竞业限制：人员范围、期限、经济补偿。"""

from __future__ import annotations

from decimal import Decimal

from laborcheck.core import Basis, Finding, dec, yuan

LCL = "labor_contract_law"
SPC = "spc_labor_interp_1"

PERSONS = {
    "senior_manager": "高级管理人员",
    "senior_tech": "高级技术人员",
    "confidential": "其他负有保密义务的人员",
    "ordinary": "不负有保密义务的普通员工",
}


def check_person(person: str) -> Finding:
    ok = person != "ordinary"
    label = PERSONS[person]
    msg = (
        f"{label}属于可以约定竞业限制的人员范围。"
        if ok
        else f"{label}不属于竞业限制人员范围（限于高级管理人员、高级技术人员和其他负有保密义务的人员）。"
    )
    basis = [Basis(LCL, "第二十四条")] + ([] if ok else [Basis("spc_labor_interp_2", "第十三条")])
    return Finding("noncompete.person", ok, msg, basis, {"person": person})


def check_duration(months: int) -> Finding:
    ok = months <= 24
    msg = (
        f"竞业限制期限 {months} 个月未超过二年。"
        if ok
        else f"竞业限制期限 {months} 个月超过法定上限二年（24 个月），超出部分无效。"
    )
    return Finding(
        "noncompete.duration",
        ok,
        msg,
        [Basis(LCL, "第二十四条")],
        {"months": months, "max_months": 24},
    )


def default_monthly_compensation(
    avg_wage_12m,
    *,
    min_wage,
    local_min_ratio: Decimal | None = None,
    local_basis: Basis | None = None,
) -> Finding:
    """未约定经济补偿时的月补偿额。

    全国：离职前十二个月平均工资的 30%，低于当地最低工资的按最低工资（司法解释一第三十六条）。
    地方性法规规定了更高比例且未约定按该比例计算的（如深圳企业技术秘密保护条例第二十四条），按地方比例。
    """
    avg = dec(avg_wage_12m)
    thirty = yuan(avg * Decimal("0.3"))
    mw = yuan(min_wage)
    basis = [Basis(LCL, "第二十三条"), Basis(SPC, "第三十六条")]
    values = {"thirty_pct": thirty, "min_wage": mw}
    if local_min_ratio is not None:
        local = yuan(avg * local_min_ratio)
        monthly = max(local, mw)
        basis = [Basis(LCL, "第二十三条"), *([local_basis] if local_basis else [])]
        msg = (
            f"按地方规定，补偿为前十二个月月平均工资的 {local_min_ratio:%}，即 {local} 元"
            f"（当地最低工资 {mw} 元），每月补偿 {monthly} 元。"
        )
        values["local"] = local
    else:
        monthly = max(thirty, mw)
        msg = f"前十二个月平均工资的 30% 为 {thirty} 元，当地最低工资 {mw} 元，每月补偿 {monthly} 元。"
    return Finding("noncompete.default_comp", None, msg, basis, {"monthly": monthly, **values})


def check_agreed_compensation(
    avg_wage_12m,
    *,
    agreed,
    local_min_ratio: Decimal | None = None,
    local_basis: Basis | None = None,
) -> Finding:
    """检查约定的月补偿额。

    全国性法律没有规定约定补偿的最低数额；地方有规定时（local_min_ratio）按地方标准检查，
    没有地方规定时只给出提示，ok 为 None。
    """
    a = yuan(agreed)
    if local_min_ratio is None:
        return Finding(
            "noncompete.agreed_comp",
            None,
            f"约定月补偿 {a} 元。全国性法律未规定约定补偿的最低数额，需结合地方规定判断。",
            [Basis(LCL, "第二十三条")],
            {"agreed": a},
        )
    floor = yuan(dec(avg_wage_12m) * local_min_ratio)
    ok = a >= floor
    basis = [Basis(LCL, "第二十三条")] + ([local_basis] if local_basis else [])
    msg = (
        f"约定月补偿 {a} 元，不低于地方标准 {floor} 元。"
        if ok
        else f"约定月补偿 {a} 元，低于地方标准（前十二个月月平均工资的 {local_min_ratio:%}）{floor} 元。"
    )
    return Finding("noncompete.agreed_comp", ok, msg, basis, {"agreed": a, "floor": floor})


def release_extra_compensation(monthly_comp) -> Finding:
    """竞业限制期限内用人单位请求解除协议的，劳动者可请求额外支付三个月的竞业限制经济补偿。"""
    amount = yuan(dec(monthly_comp) * 3)
    return Finding(
        "noncompete.release",
        None,
        f"额外三个月补偿 = {yuan(monthly_comp)} × 3 = {amount} 元。",
        [Basis(SPC, "第三十九条")],
        {"amount": amount},
    )
