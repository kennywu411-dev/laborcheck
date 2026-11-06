"""加班费。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from laborcheck.core import Basis, Finding, dec, yuan

MONTH_PAY_DAYS = Decimal("21.75")


class OvertimeKind(StrEnum):
    WORKDAY = "workday"  # 工作日延长工作时间：150%
    REST_DAY = "rest_day"  # 休息日工作又不能安排补休：200%
    HOLIDAY = "holiday"  # 法定休假日：300%


RATE = {
    OvertimeKind.WORKDAY: Decimal("1.5"),
    OvertimeKind.REST_DAY: Decimal("2"),
    OvertimeKind.HOLIDAY: Decimal("3"),
}
LABEL = {
    OvertimeKind.WORKDAY: "工作日延时加班",
    OvertimeKind.REST_DAY: "休息日加班",
    OvertimeKind.HOLIDAY: "法定节假日加班",
}


def hourly_wage(monthly_base) -> Finding:
    """小时工资 = 月工资 ÷ 21.75 ÷ 8。"""
    hourly = yuan(dec(monthly_base) / MONTH_PAY_DAYS / 8)
    return Finding(
        "overtime.hourly",
        None,
        f"小时工资 = {yuan(monthly_base)} ÷ 21.75 ÷ 8 = {hourly} 元。",
        [Basis("wage_days_2025_2")],
        {"hourly": hourly},
    )


def overtime_pay(monthly_base, kind: OvertimeKind, hours, *, comp_leave: bool = False) -> Finding:
    """加班费。休息日加班已安排补休的不再支付加班费；法定节假日和工作日加班不能以补休替代。

    金额按"月工资 ÷ 21.75 ÷ 8 × 倍数 × 小时数"一次算出再四舍五入到分，不先对小时工资取整。
    """
    basis = [Basis("labor_law", "第四十四条"), Basis("wage_days_2025_2")]
    if kind is OvertimeKind.REST_DAY and comp_leave:
        return Finding(
            "overtime.pay",
            None,
            "休息日加班已安排同等时间补休，不再支付加班费。",
            basis,
            {"amount": Decimal("0.00"), "rate": RATE[kind]},
        )
    rate = RATE[kind]
    amount = yuan(dec(monthly_base) / MONTH_PAY_DAYS / 8 * rate * dec(hours))
    return Finding(
        "overtime.pay",
        None,
        f"{LABEL[kind]} {hours} 小时：{yuan(monthly_base)} ÷ 21.75 ÷ 8 × {rate:%} × {hours} = {amount} 元。",
        basis,
        {"amount": amount, "rate": rate},
    )
