"""试用期：期限上限、次数、工资下限、违法约定的赔偿金。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from laborcheck.core import Basis, Finding, dec, yuan

LCL = "labor_contract_law"


class TermKind(StrEnum):
    FIXED = "fixed"  # 固定期限
    OPEN_ENDED = "open_ended"  # 无固定期限
    TASK = "task"  # 以完成一定工作任务为期限


def max_probation_months(kind: TermKind, term_months: int | None) -> tuple[int, list[Basis]]:
    """法定试用期上限（月）。

    第十九条："以上"包含本数（民法典第一千二百五十九条），所以期限恰好一年适用"一年以上不满三年"。
    """
    basis = [Basis(LCL, "第十九条")]
    if kind is TermKind.TASK:
        return 0, basis
    if kind is TermKind.OPEN_ENDED:
        return 6, basis
    if term_months is None:
        raise ValueError("固定期限劳动合同需要给出合同期限（月）")
    if term_months < 3:
        return 0, basis
    if term_months < 12:
        return 1, basis
    if term_months < 36:
        return 2, basis
    return 6, basis


def _term_text(kind: TermKind, term_months: int | None) -> str:
    if kind is TermKind.TASK:
        return "以完成一定工作任务为期限的劳动合同"
    if kind is TermKind.OPEN_ENDED:
        return "无固定期限劳动合同"
    return f"{term_months} 个月的固定期限劳动合同"


def check_probation(
    kind: TermKind,
    term_months: int | None,
    probation_months: int,
    *,
    previous_probation: bool = False,
    probation_only: bool = False,
) -> Finding:
    """检查试用期约定是否合法。"""
    basis = [Basis(LCL, "第十九条")]
    if probation_only:
        return Finding(
            "probation.only",
            False,
            "劳动合同仅约定试用期的，试用期不成立，该期限为劳动合同期限。",
            basis,
            {"probation_months": probation_months},
        )
    if previous_probation and probation_months > 0:
        return Finding(
            "probation.once",
            False,
            "同一用人单位与同一劳动者只能约定一次试用期，再次约定违法。",
            basis,
            {"probation_months": probation_months},
        )
    cap, basis = max_probation_months(kind, term_months)
    text = _term_text(kind, term_months)
    if probation_months <= cap:
        return Finding(
            "probation.term",
            True,
            f"{text}试用期最长 {cap} 个月，约定 {probation_months} 个月合法。",
            basis,
            {"max_months": cap, "probation_months": probation_months, "excess_months": 0},
        )
    msg = (
        f"{text}不得约定试用期，约定 {probation_months} 个月违法。"
        if cap == 0
        else f"{text}试用期最长 {cap} 个月，约定 {probation_months} 个月超出 "
        f"{probation_months - cap} 个月，违法。"
    )
    return Finding(
        "probation.term",
        False,
        msg,
        basis,
        {
            "max_months": cap,
            "probation_months": probation_months,
            "excess_months": probation_months - cap,
        },
    )


def min_probation_wage(agreed_wage, *, min_wage) -> Finding:
    """试用期工资下限：不低于约定工资的 80%，并不低于当地最低工资标准。

    第二十条和实施条例第十五条还允许以"本单位相同岗位最低档工资的 80%"作为比较对象，
    这一项需要单位内部工资信息，引擎只按约定工资计算。
    """
    agreed = dec(agreed_wage)
    mw = dec(min_wage)
    eighty = yuan(agreed * Decimal("0.8"))
    floor, binding = (eighty, "agreed_80") if eighty >= mw else (yuan(mw), "min_wage")
    return Finding(
        "probation.wage_floor",
        None,
        f"试用期工资不得低于约定工资的 80%（{eighty} 元）且不低于当地最低工资（{yuan(mw)} 元），"
        f"下限为 {floor} 元。",
        [Basis(LCL, "第二十条"), Basis("labor_contract_law_regulation", "第十五条")],
        {"floor": floor, "agreed_80": eighty, "min_wage": yuan(mw), "binding": binding},
    )


def check_probation_wage(agreed_wage, probation_wage, *, min_wage) -> Finding:
    base = min_probation_wage(agreed_wage, min_wage=min_wage)
    floor = base.values["floor"]
    pw = yuan(probation_wage)
    ok = pw >= floor
    msg = (
        f"试用期工资 {pw} 元不低于下限 {floor} 元，合法。"
        if ok
        else f"试用期工资 {pw} 元低于下限 {floor} 元，差 {floor - pw} 元，违法。"
    )
    return Finding(
        "probation.wage",
        ok,
        msg,
        base.basis,
        {**base.values, "probation_wage": pw, "shortfall": max(floor - pw, Decimal("0.00"))},
    )


def illegal_probation_damages(
    kind: TermKind,
    term_months: int | None,
    *,
    agreed_probation: int,
    served_probation: int,
    regular_wage,
) -> Finding:
    """违法约定的试用期已经履行的，按试用期满月工资、超过法定试用期的已履行期间支付赔偿金。"""
    cap, _ = max_probation_months(kind, term_months)
    excess = max(0, min(agreed_probation, served_probation) - cap)
    amount = yuan(dec(regular_wage) * excess)
    return Finding(
        "probation.damages",
        None,
        f"法定试用期最长 {cap} 个月，已履行的超出部分为 {excess} 个月，"
        f"赔偿金 = {yuan(regular_wage)} × {excess} = {amount} 元。",
        [Basis(LCL, "第十九条"), Basis(LCL, "第八十三条")],
        {"max_months": cap, "months": excess, "amount": amount},
    )
