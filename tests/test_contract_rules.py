from decimal import Decimal

import pytest

from laborcheck.rules import noncompete, penalty, probation
from laborcheck.rules.probation import TermKind


# ---------- 试用期期限（劳动合同法第十九条） ----------
@pytest.mark.parametrize(
    ("kind", "term", "expected"),
    [
        (TermKind.FIXED, 2, 0),  # 不满三个月，不得约定
        (TermKind.FIXED, 3, 1),  # 三个月以上（含本数）
        (TermKind.FIXED, 11, 1),
        (TermKind.FIXED, 12, 2),  # 一年以上（含本数）
        (TermKind.FIXED, 35, 2),
        (TermKind.FIXED, 36, 6),  # 三年以上
        (TermKind.FIXED, 60, 6),
        (TermKind.OPEN_ENDED, None, 6),
        (TermKind.TASK, None, 0),
    ],
)
def test_max_probation_months(kind, term, expected):
    months, basis = probation.max_probation_months(kind, term)
    assert months == expected
    assert any(b.article == "第十九条" for b in basis)


def test_fixed_term_requires_months():
    with pytest.raises(ValueError):
        probation.max_probation_months(TermKind.FIXED, None)


def test_check_probation_legal_and_illegal():
    assert probation.check_probation(TermKind.FIXED, 36, 6).ok is True
    f = probation.check_probation(TermKind.FIXED, 24, 3)
    assert f.ok is False
    assert f.values["max_months"] == 2
    assert f.values["excess_months"] == 1


def test_probation_second_time_illegal():
    f = probation.check_probation(TermKind.FIXED, 36, 3, previous_probation=True)
    assert f.ok is False
    assert "一次" in f.message


def test_probation_only_contract():
    f = probation.check_probation(TermKind.FIXED, 6, 6, probation_only=True)
    assert f.ok is False
    assert "试用期不成立" in f.message


def test_probation_wage_floor():
    # 约定 10000，80% 为 8000，高于最低工资
    f = probation.min_probation_wage(10000, min_wage=2540)
    assert f.values["floor"] == Decimal("8000.00")
    assert f.values["binding"] == "agreed_80"
    # 约定 3000，80% 为 2400，低于最低工资 2540
    f = probation.min_probation_wage(3000, min_wage=2540)
    assert f.values["floor"] == Decimal("2540.00")
    assert f.values["binding"] == "min_wage"


def test_check_probation_wage():
    assert probation.check_probation_wage(10000, 8000, min_wage=2540).ok is True
    f = probation.check_probation_wage(10000, 7000, min_wage=2540)
    assert f.ok is False
    assert f.values["shortfall"] == Decimal("1000.00")


def test_probation_damages():
    # 违法约定的试用期已经履行的，按试用期满月工资标准、超过法定期限的期间支付赔偿金（第八十三条）
    f = probation.illegal_probation_damages(
        TermKind.FIXED, 24, agreed_probation=4, served_probation=4, regular_wage=12000
    )
    assert f.values["months"] == 2
    assert f.values["amount"] == Decimal("24000.00")


# ---------- 违约金（第二十二、二十三、二十五条） ----------
@pytest.mark.parametrize(
    ("kind", "ok"),
    [
        (penalty.PenaltyKind.SERVICE_PERIOD, True),
        (penalty.PenaltyKind.NON_COMPETE, True),
        (penalty.PenaltyKind.EARLY_RESIGNATION, False),
        (penalty.PenaltyKind.PROBATION_RESIGNATION, False),
        (penalty.PenaltyKind.NO_NOTICE, False),
        (penalty.PenaltyKind.ORDINARY_TRAINING, False),
    ],
)
def test_penalty_allowed(kind, ok):
    f = penalty.check_penalty_kind(kind)
    assert f.ok is ok
    assert any(b.article == "第二十五条" for b in f.basis)


def test_service_penalty_cap():
    # 培训费 36000，服务期 36 个月，已履行 12 个月 -> 最多 24000
    f = penalty.service_period_penalty_cap(36000, 36, 12)
    assert f.values["cap"] == Decimal("24000.00")
    assert penalty.check_service_penalty(36000, 36, 12, agreed=30000).ok is False
    assert penalty.check_service_penalty(36000, 36, 12, agreed=24000).ok is True


def test_service_penalty_cap_never_exceeds_cost():
    f = penalty.service_period_penalty_cap(10000, 24, 0)
    assert f.values["cap"] == Decimal("10000.00")


# ---------- 竞业限制（第二十三、二十四条；司法解释一第三十六条以下） ----------
def test_noncompete_duration():
    assert noncompete.check_duration(24).ok is True
    f = noncompete.check_duration(36)
    assert f.ok is False
    assert f.values["max_months"] == 24


def test_noncompete_person_scope():
    assert noncompete.check_person("senior_tech").ok is True
    assert noncompete.check_person("senior_manager").ok is True
    assert noncompete.check_person("confidential").ok is True
    assert noncompete.check_person("ordinary").ok is False


def test_noncompete_default_compensation():
    # 未约定补偿：前十二个月平均工资的 30%，低于最低工资按最低工资
    f = noncompete.default_monthly_compensation(20000, min_wage=2540)
    assert f.values["monthly"] == Decimal("6000.00")
    f = noncompete.default_monthly_compensation(6000, min_wage=2540)
    assert f.values["monthly"] == Decimal("2540.00")


def test_noncompete_default_local_ratio():
    from laborcheck.core import Basis

    f = noncompete.default_monthly_compensation(
        20000,
        min_wage=2540,
        local_min_ratio=Decimal("0.5"),
        local_basis=Basis("sz_tech_secret_reg", "第二十四条"),
    )
    assert f.values["monthly"] == Decimal("10000.00")
    assert any(b.source == "sz_tech_secret_reg" for b in f.basis)


def test_noncompete_local_floor():
    # 地方要求补偿不低于前十二个月平均工资的一定比例时，以地方标准检查约定额
    f = noncompete.check_agreed_compensation(20000, agreed=6000, local_min_ratio=Decimal("0.5"))
    assert f.ok is False
    assert f.values["floor"] == Decimal("10000.00")
    assert noncompete.check_agreed_compensation(20000, agreed=6000).ok is None


def test_noncompete_release_extra():
    # 用人单位在竞业限制期限内解除协议，劳动者可要求额外三个月补偿
    f = noncompete.release_extra_compensation(5000)
    assert f.values["amount"] == Decimal("15000.00")
