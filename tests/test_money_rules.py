from datetime import date
from decimal import Decimal

import pytest

from laborcheck.cities import CityPolicy, SIItem
from laborcheck.rules import leave, overtime, payroll, severance, tax, wage


@pytest.fixture
def city() -> CityPolicy:
    """构造的测试城市，数字不对应任何真实城市。"""
    return CityPolicy(
        code="test",
        name="测试市",
        period="测试年度",
        min_wage_monthly=Decimal("2500"),
        min_wage_hourly=Decimal("15"),
        avg_monthly_wage=Decimal("12000"),
        si_items=(
            SIItem(
                "pension",
                "养老",
                Decimal("7000"),
                Decimal("35000"),
                Decimal("0.08"),
                Decimal("0.16"),
            ),
            SIItem(
                "medical",
                "医疗",
                Decimal("7000"),
                Decimal("35000"),
                Decimal("0.02"),
                Decimal("0.098"),
                employee_fixed=Decimal("3"),
            ),
            SIItem(
                "unemployment",
                "失业",
                Decimal("7000"),
                Decimal("35000"),
                Decimal("0.005"),
                Decimal("0.005"),
            ),
        ),
        hf_base_lower=Decimal("2500"),
        hf_base_upper=Decimal("30000"),
        hf_rate_min=Decimal("0.05"),
        hf_rate_max=Decimal("0.12"),
    )


# ---------- 加班费（劳动法第四十四条） ----------
def test_hourly_wage():
    assert overtime.hourly_wage(21750).values["hourly"] == Decimal("125.00")


@pytest.mark.parametrize(
    ("kind", "hours", "amount"),
    [
        (overtime.OvertimeKind.WORKDAY, 10, "1875.00"),
        (overtime.OvertimeKind.REST_DAY, 8, "2000.00"),
        (overtime.OvertimeKind.HOLIDAY, 8, "3000.00"),
    ],
)
def test_overtime_pay(kind, hours, amount):
    f = overtime.overtime_pay(21750, kind, hours)
    assert f.values["amount"] == Decimal(amount)
    assert any(b.article == "第四十四条" for b in f.basis)


def test_rest_day_with_comp_leave_no_pay():
    f = overtime.overtime_pay(21750, overtime.OvertimeKind.REST_DAY, 8, comp_leave=True)
    assert f.values["amount"] == Decimal("0.00")


# ---------- 经济补偿（第四十七条） ----------
@pytest.mark.parametrize(
    ("months", "n"),
    [(0, "0"), (3, "0.5"), (6, "1"), (11, "1"), (12, "1"), (17, "1.5"), (18, "2"), (43, "4")],
)
def test_severance_years(months, n):
    assert severance.compensation_years(months) == Decimal(n)


def test_severance_basic(city):
    f = severance.economic_compensation(15000, 43, city)
    assert f.values["amount"] == Decimal("60000.00")


def test_severance_high_wage_cap(city):
    # 月工资 50000 > 3 × 12000，基数按 36000，年限最高 12 年
    f = severance.economic_compensation(50000, 15 * 12, city)
    assert f.values["base"] == Decimal("36000.00")
    assert f.values["years"] == Decimal("12")
    assert f.values["amount"] == Decimal("432000.00")


def test_severance_no_cap_when_below(city):
    f = severance.economic_compensation(30000, 15 * 12, city)
    assert f.values["amount"] == Decimal("450000.00")


def test_severance_min_wage_floor(city):
    f = severance.economic_compensation(2000, 24, city)
    assert f.values["base"] == Decimal("2500.00")


def test_illegal_termination_double(city):
    f = severance.illegal_termination_damages(15000, 43, city)
    assert f.values["amount"] == Decimal("120000.00")


def test_notice_pay():
    assert severance.notice_pay(15000).values["amount"] == Decimal("15000.00")


# ---------- 未签书面合同二倍工资（第八十二条） ----------
@pytest.mark.parametrize(("months", "extra"), [(1, 0), (2, 1), (8, 7), (12, 11), (20, 11)])
def test_double_wage_months(months, extra):
    f = wage.no_contract_double_wage(10000, months)
    assert f.values["months"] == extra
    assert f.values["amount"] == Decimal(10000 * extra).quantize(Decimal("0.01"))


# ---------- 年休假 ----------
@pytest.mark.parametrize(("years", "days"), [(0, 0), (1, 5), (9, 5), (10, 10), (19, 10), (20, 15)])
def test_annual_leave_days(years, days):
    assert leave.annual_leave_days(years).values["days"] == days


def test_new_hire_prorated():
    f = leave.new_hire_leave_days(date(2025, 7, 1), cumulative_years=3)
    assert f.values["remaining_days"] == 184
    assert f.values["days"] == 2


def test_unused_leave_pay():
    f = leave.unused_leave_pay(21750, 3)
    assert f.values["amount"] == Decimal("6000.00")


# ---------- 社保公积金 ----------
@pytest.mark.parametrize(
    ("gross", "total"), [(10000, "1053.00"), (5000, "738.00"), (50000, "3678.00")]
)
def test_si_employee(city, gross, total):
    f = payroll.social_insurance(gross, city)
    assert f.values["employee_total"] == Decimal(total)


def test_hf(city):
    f = payroll.housing_fund(10000, city, Decimal("0.12"))
    assert f.values["employee"] == Decimal("1200.00")
    assert f.values["employer"] == Decimal("1200.00")
    with pytest.raises(ValueError):
        payroll.housing_fund(10000, city, Decimal("0.15"))


def test_hf_base_clamp(city):
    assert payroll.housing_fund(40000, city, Decimal("0.1")).values["base"] == Decimal("30000.00")


# ---------- 个税 ----------
def test_annual_tax():
    f = tax.comprehensive_tax(Decimal("132000"))
    assert f.values["tax"] == Decimal("10680.00")


def test_annual_tax_zero_when_negative():
    assert tax.comprehensive_tax(Decimal("-100")).values["tax"] == Decimal("0.00")


@pytest.mark.parametrize(("bonus", "t"), [(36000, "1080.00"), (36001, "3390.10"), (0, "0.00")])
def test_bonus_separate(bonus, t):
    assert tax.bonus_tax_separate(bonus).values["tax"] == Decimal(t)


def test_monthly_withholding():
    f = tax.monthly_withholding(
        monthly_gross=20000, monthly_si_hf=2000, monthly_special=2000, months=12
    )
    per_month = f.values["per_month"]
    assert per_month[0] == Decimal("330.00")
    assert per_month[3] == Decimal("890.00")
    assert sum(per_month) == Decimal("10680.00")


def test_bonus_method_compare():
    f = tax.compare_bonus_methods(
        annual_salary=240000, bonus=36000, annual_si_hf=24000, annual_special=24000
    )
    assert f.values["separate_total"] == Decimal("11760.00")
    assert f.values["combined_total"] == Decimal("16680.00")
    assert f.values["better"] == "separate"


def test_take_home(city):
    f = payroll.annual_take_home(10000, city, hf_rate=Decimal("0.12"))
    assert f.values["annual_tax"] == Decimal("988.92")
    assert f.values["annual_net"] == Decimal("91975.08")
