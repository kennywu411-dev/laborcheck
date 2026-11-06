"""经济补偿、违法解除赔偿金、代通知金。"""

from __future__ import annotations

from decimal import Decimal

from laborcheck.cities import CityPolicy
from laborcheck.core import Basis, Finding, dec, yuan

LCL = "labor_contract_law"


def compensation_years(months_worked: int) -> Decimal:
    """每满一年支付一个月；六个月以上不满一年按一年；不满六个月支付半个月。"""
    full, rem = divmod(months_worked, 12)
    if rem == 0:
        extra = Decimal("0")
    elif rem >= 6:
        extra = Decimal("1")
    else:
        extra = Decimal("0.5")
    return Decimal(full) + extra


def economic_compensation(avg_monthly_wage, months_worked: int, city: CityPolicy) -> Finding:
    """经济补偿金。

    月工资为解除或终止前十二个月的平均工资；高于当地上年度职工月平均工资三倍的，
    按三倍计算且年限最高不超过十二年；低于当地最低工资标准的，按最低工资标准计算。
    """
    wage = dec(avg_monthly_wage)
    years = compensation_years(months_worked)
    cap_wage = yuan(city.need("avg_monthly_wage") * 3)
    mw = yuan(city.need("min_wage_monthly"))
    basis = [Basis(LCL, "第四十七条"), Basis("labor_contract_law_regulation", "第二十七条")]
    capped = wage > cap_wage
    if capped:
        base = cap_wage
        years = min(years, Decimal("12"))
        note = f"月工资高于{city.name}社平工资三倍（{cap_wage} 元），按三倍计算，年限最高 12 年"
    elif wage < mw:
        base = mw
        note = f"月工资低于{city.name}最低工资，按最低工资 {mw} 元计算"
    else:
        base = yuan(wage)
        note = "月工资按实际平均工资计算"
    amount = yuan(base * years)
    return Finding(
        "severance.compensation",
        None,
        f"{note}；工作 {months_worked} 个月折合 {years} 个月工资；经济补偿 = {base} × {years} = {amount} 元。",
        basis,
        {"base": base, "years": years, "amount": amount, "capped": capped},
    )


def illegal_termination_damages(avg_monthly_wage, months_worked: int, city: CityPolicy) -> Finding:
    """违法解除或终止：按经济补偿标准的二倍支付赔偿金。"""
    ec = economic_compensation(avg_monthly_wage, months_worked, city)
    amount = yuan(ec.values["amount"] * 2)
    return Finding(
        "severance.illegal",
        None,
        f"违法解除赔偿金 = 经济补偿 {ec.values['amount']} × 2 = {amount} 元。",
        [*ec.basis, Basis(LCL, "第八十七条")],
        {**ec.values, "amount": amount, "compensation": ec.values["amount"]},
    )


def notice_pay(last_month_wage) -> Finding:
    """第四十条情形未提前三十日书面通知的，额外支付一个月工资。"""
    amount = yuan(last_month_wage)
    return Finding(
        "severance.notice_pay",
        None,
        f"代通知金为一个月工资 {amount} 元。",
        [Basis(LCL, "第四十条"), Basis("labor_contract_law_regulation", "第二十条")],
        {"amount": amount},
    )
