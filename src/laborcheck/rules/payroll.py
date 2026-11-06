"""社保公积金个人和单位缴费、年度到手收入。"""

from __future__ import annotations

from decimal import Decimal

from laborcheck.cities import CityPolicy
from laborcheck.core import Basis, Finding, dec, yuan
from laborcheck.rules import tax


def social_insurance(gross, city: CityPolicy) -> Finding:
    """按月工资计算社保。缴费基数 = 月工资，低于下限按下限、高于上限按上限。

    实际缴费基数通常按上年度月平均工资申报，新入职按首月工资；这里统一用题目给出的月工资。
    """
    g = dec(gross)
    items = city.need("si_items")
    detail = {}
    emp_total = Decimal("0")
    er_total = Decimal("0")
    for it in items:
        base = min(max(g, it.base_lower), it.base_upper)
        e = yuan(base * it.employee_rate + it.employee_fixed)
        r = yuan(base * it.employer_rate)
        detail[it.code] = {"name": it.name, "base": yuan(base), "employee": e, "employer": r}
        emp_total += e
        er_total += r
    srcs = {k: v for k, v in city.sources.items() if k.startswith("si.")}
    return Finding(
        "payroll.si",
        None,
        f"{city.name}社保个人合计 {yuan(emp_total)} 元/月，单位合计 {yuan(er_total)} 元/月"
        f"（{city.period}）。",
        [],
        {
            "employee_total": yuan(emp_total),
            "employer_total": yuan(er_total),
            "items": detail,
            "sources": srcs,
        },
    )


def housing_fund(gross, city: CityPolicy, rate: Decimal) -> Finding:
    """住房公积金，单位和个人按同一比例缴存。"""
    lo, hi = city.need("hf_base_lower"), city.need("hf_base_upper")
    rmin, rmax = city.need("hf_rate_min"), city.need("hf_rate_max")
    r = dec(rate)
    if not rmin <= r <= rmax:
        raise ValueError(f"{city.name}公积金比例须在 {rmin:%} 至 {rmax:%} 之间")
    base = yuan(min(max(dec(gross), lo), hi))
    amt = yuan(base * r)
    return Finding(
        "payroll.hf",
        None,
        f"{city.name}公积金基数 {base} 元，比例 {r:%}，个人和单位各 {amt} 元/月。",
        [],
        {
            "base": base,
            "employee": amt,
            "employer": amt,
            "rate": r,
            "sources": {k: city.source_of(k) for k in ("hf_base_lower", "hf_base_upper")},
        },
    )


def annual_take_home(
    monthly_gross,
    city: CityPolicy,
    *,
    hf_rate: Decimal,
    monthly_special=0,
    annual_bonus=0,
    bonus_method: str = "separate",
    months: int = 12,
) -> Finding:
    """年度税后到手收入（工资 + 年终奖 − 个人社保公积金 − 个税）。

    假设全年月薪和缴费基数不变；bonus_method 为 separate / combined / best。
    """
    g = dec(monthly_gross)
    si = social_insurance(g, city).values["employee_total"]
    hf = housing_fund(g, city, hf_rate).values["employee"]
    sihf = si + hf
    salary = g * months
    bonus = dec(annual_bonus)
    special = dec(monthly_special) * 12
    cmp = tax.compare_bonus_methods(
        annual_salary=salary, bonus=bonus, annual_si_hf=sihf * months, annual_special=special
    )
    method = cmp.values["better"] if bonus_method == "best" else bonus_method
    total_tax = (
        cmp.values["separate_total"] if method == "separate" else cmp.values["combined_total"]
    )
    if bonus == 0:
        total_tax = cmp.values["combined_total"]
    net = yuan(salary + bonus - sihf * months - total_tax)
    return Finding(
        "payroll.take_home",
        None,
        f"{city.name}月薪 {yuan(g)} 元：个人社保 {si} 元/月、公积金 {hf} 元/月；"
        f"全年个税 {total_tax} 元；全年税后到手 {net} 元。",
        [Basis("iit_law", "第六条"), Basis("bonus_2023_30")]
        if bonus
        else [Basis("iit_law", "第六条")],
        {
            "monthly_si": si,
            "monthly_hf": hf,
            "annual_tax": total_tax,
            "annual_net": net,
            "annual_net_plus_hf": yuan(net + hf * 2 * months),
            "bonus_method": method if bonus else None,
        },
    )
