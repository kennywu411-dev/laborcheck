"""个人所得税：综合所得年度计算、累计预扣法、全年一次性奖金。"""

from __future__ import annotations

from decimal import Decimal

from laborcheck.core import Basis, Finding, dec, yuan

INF = Decimal("Infinity")
BASIC_ANNUAL = Decimal("60000")
BASIC_MONTHLY = Decimal("5000")

# 综合所得年度税率表 / 居民个人工资薪金预扣率表（累计）：(上限, 税率, 速算扣除数)
ANNUAL_TABLE = (
    (Decimal("36000"), Decimal("0.03"), Decimal("0")),
    (Decimal("144000"), Decimal("0.10"), Decimal("2520")),
    (Decimal("300000"), Decimal("0.20"), Decimal("16920")),
    (Decimal("420000"), Decimal("0.25"), Decimal("31920")),
    (Decimal("660000"), Decimal("0.30"), Decimal("52920")),
    (Decimal("960000"), Decimal("0.35"), Decimal("85920")),
    (INF, Decimal("0.45"), Decimal("181920")),
)

# 按月换算后的综合所得税率表（全年一次性奖金单独计税用）
MONTHLY_TABLE = (
    (Decimal("3000"), Decimal("0.03"), Decimal("0")),
    (Decimal("12000"), Decimal("0.10"), Decimal("210")),
    (Decimal("25000"), Decimal("0.20"), Decimal("1410")),
    (Decimal("35000"), Decimal("0.25"), Decimal("2660")),
    (Decimal("55000"), Decimal("0.30"), Decimal("4410")),
    (Decimal("80000"), Decimal("0.35"), Decimal("7160")),
    (INF, Decimal("0.45"), Decimal("15160")),
)

TAX_LAW = Basis("iit_law", "第二条、第三条、第六条")


def _lookup(amount: Decimal, table) -> tuple[Decimal, Decimal]:
    for upper, rate, quick in table:
        if amount <= upper:
            return rate, quick
    raise AssertionError("unreachable")


def _tax(amount: Decimal, table) -> tuple[Decimal, Decimal, Decimal]:
    if amount <= 0:
        return Decimal("0.00"), Decimal("0"), Decimal("0")
    rate, quick = _lookup(amount, table)
    return yuan(amount * rate - quick), rate, quick


def comprehensive_tax(taxable_income) -> Finding:
    """年度综合所得应纳税额，taxable_income 为已减除各项扣除后的应纳税所得额。"""
    ti = dec(taxable_income)
    t, rate, quick = _tax(ti, ANNUAL_TABLE)
    return Finding(
        "tax.annual",
        None,
        f"应纳税所得额 {yuan(max(ti, Decimal(0)))} 元，适用税率 {rate:%}，速算扣除数 {quick}，"
        f"应纳税额 {t} 元。",
        [TAX_LAW],
        {"tax": t, "rate": rate, "quick": quick},
    )


def bonus_tax_separate(bonus) -> Finding:
    """全年一次性奖金单独计税：奖金 ÷ 12 查月度税率表，奖金 × 税率 − 速算扣除数。"""
    b = dec(bonus)
    if b <= 0:
        return Finding(
            "tax.bonus",
            None,
            "无年终奖。",
            [Basis("bonus_2023_30")],
            {"tax": Decimal("0.00"), "rate": Decimal("0")},
        )
    rate, quick = _lookup(b / 12, MONTHLY_TABLE)
    t = yuan(b * rate - quick)
    return Finding(
        "tax.bonus",
        None,
        f"年终奖 {yuan(b)} 元 ÷ 12 = {yuan(b / 12)} 元，适用税率 {rate:%}，速算扣除数 {quick}，"
        f"单独计税应纳税额 {t} 元。",
        [Basis("bonus_2023_30")],
        {"tax": t, "rate": rate, "quick": quick},
    )


def monthly_withholding(
    *, monthly_gross, monthly_si_hf, monthly_special=0, months: int = 12
) -> Finding:
    """累计预扣法：每月工资不变时逐月预扣税额。"""
    gross, sihf, sp = dec(monthly_gross), dec(monthly_si_hf), dec(monthly_special)
    per_month: list[Decimal] = []
    paid = Decimal("0.00")
    for m in range(1, months + 1):
        cum_taxable = (gross - BASIC_MONTHLY - sihf - sp) * m
        cum_tax, _, _ = _tax(cum_taxable, ANNUAL_TABLE)
        this = max(cum_tax - paid, Decimal("0.00"))
        per_month.append(this)
        paid += this
    return Finding(
        "tax.withholding",
        None,
        "按累计预扣法逐月预扣：" + "、".join(f"{i + 1}月 {t}" for i, t in enumerate(per_month)),
        [Basis("iit_withholding_2018_61", "第六条"), TAX_LAW],
        {"per_month": per_month, "total": paid},
    )


def compare_bonus_methods(
    *, annual_salary, bonus, annual_si_hf, annual_special=0, other_deductions=0
) -> Finding:
    """比较年终奖单独计税与并入综合所得两种方式的全年税额。"""
    base = (
        dec(annual_salary)
        - BASIC_ANNUAL
        - dec(annual_si_hf)
        - dec(annual_special)
        - dec(other_deductions)
    )
    salary_tax = comprehensive_tax(base).values["tax"]
    sep = yuan(salary_tax + bonus_tax_separate(bonus).values["tax"])
    comb = comprehensive_tax(base + dec(bonus)).values["tax"]
    better = "separate" if sep <= comb else "combined"
    return Finding(
        "tax.bonus_compare",
        None,
        f"单独计税全年合计 {sep} 元，并入综合所得 {comb} 元，"
        f"{'单独计税' if better == 'separate' else '并入综合所得'}更省 {abs(sep - comb)} 元。",
        [Basis("bonus_2023_30"), TAX_LAW],
        {
            "separate_total": sep,
            "combined_total": comb,
            "better": better,
            "saving": abs(sep - comb),
        },
    )
