"""带薪年休假。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from laborcheck.core import Basis, Finding, dec, yuan


def annual_leave_days(cumulative_years: int) -> Finding:
    """累计工作已满 1 年不满 10 年 5 天，满 10 年不满 20 年 10 天，满 20 年 15 天。"""
    if cumulative_years < 1:
        days = 0
    elif cumulative_years < 10:
        days = 5
    elif cumulative_years < 20:
        days = 10
    else:
        days = 15
    return Finding(
        "leave.days",
        None,
        f"累计工作 {cumulative_years} 年，每年年休假 {days} 天。",
        [Basis("annual_leave_reg", "第二条"), Basis("annual_leave_reg", "第三条")],
        {"days": days},
    )


def new_hire_leave_days(start: date, cumulative_years: int) -> Finding:
    """新进单位当年：剩余日历天数 ÷ 365 × 全年应休天数，不足 1 整天的部分不享受。"""
    full = annual_leave_days(cumulative_years).values["days"]
    remaining = (date(start.year, 12, 31) - start).days + 1
    days = int(Decimal(remaining) / Decimal(365) * full)
    return Finding(
        "leave.new_hire",
        None,
        f"{start.isoformat()} 入职，当年剩余 {remaining} 天，折算 {remaining}/365 × {full} 天，"
        f"取整为 {days} 天。",
        [Basis("annual_leave_measures", "第五条")],
        {"days": days, "remaining_days": remaining, "full_days": full},
    )


def unused_leave_pay(monthly_wage, unused_days) -> Finding:
    """未休年休假：按日工资收入的 300% 支付，其中含正常工作期间的工资收入，即额外支付 200%。"""
    daily = dec(monthly_wage) / Decimal("21.75")
    amount = yuan(daily * 2 * dec(unused_days))
    return Finding(
        "leave.unused_pay",
        None,
        f"日工资 {yuan(daily)} 元，未休 {unused_days} 天，额外支付 200%：{amount} 元。",
        [
            Basis("annual_leave_reg", "第五条"),
            Basis("annual_leave_measures", "第十条"),
            Basis("annual_leave_measures", "第十一条"),
        ],
        {"amount": amount, "daily": yuan(daily)},
    )
