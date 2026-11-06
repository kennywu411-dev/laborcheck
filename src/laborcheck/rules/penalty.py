"""违约金：只能在服务期和竞业限制两种情形约定。"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from laborcheck.core import Basis, Finding, dec, yuan

LCL = "labor_contract_law"


class PenaltyKind(StrEnum):
    SERVICE_PERIOD = "service_period"  # 用人单位出资专项技术培训并约定服务期
    NON_COMPETE = "non_compete"  # 违反竞业限制约定
    EARLY_RESIGNATION = "early_resignation"  # 合同期内提前离职
    PROBATION_RESIGNATION = "probation_resignation"  # 试用期内离职
    NO_NOTICE = "no_notice"  # 未提前通知离职
    ORDINARY_TRAINING = "ordinary_training"  # 普通岗前培训、入职培训


_LABEL = {
    PenaltyKind.SERVICE_PERIOD: "违反服务期约定（单位出资专项技术培训）",
    PenaltyKind.NON_COMPETE: "违反竞业限制约定",
    PenaltyKind.EARLY_RESIGNATION: "合同期内提前离职",
    PenaltyKind.PROBATION_RESIGNATION: "试用期内离职",
    PenaltyKind.NO_NOTICE: "未提前三十日通知离职",
    PenaltyKind.ORDINARY_TRAINING: "参加普通入职培训后离职",
}


def check_penalty_kind(kind: PenaltyKind) -> Finding:
    allowed = kind in (PenaltyKind.SERVICE_PERIOD, PenaltyKind.NON_COMPETE)
    basis = [Basis(LCL, "第二十五条")]
    if kind is PenaltyKind.SERVICE_PERIOD:
        basis.append(Basis(LCL, "第二十二条"))
    elif kind is PenaltyKind.NON_COMPETE:
        basis.append(Basis(LCL, "第二十三条"))
    label = _LABEL[kind]
    msg = (
        f"「{label}」属于法律允许约定违约金的情形。"
        if allowed
        else f"「{label}」不属于服务期或竞业限制情形，用人单位不得与劳动者约定由劳动者承担违约金。"
    )
    return Finding("penalty.kind", allowed, msg, basis, {"kind": str(kind)})


def service_period_penalty_cap(training_cost, service_months: int, served_months: int) -> Finding:
    """服务期违约金上限：不超过培训费用，且不超过服务期尚未履行部分所应分摊的培训费用。"""
    if service_months <= 0:
        raise ValueError("服务期必须大于 0")
    cost = dec(training_cost)
    remaining = max(0, service_months - served_months)
    cap = yuan(cost * Decimal(remaining) / Decimal(service_months))
    return Finding(
        "penalty.service_cap",
        None,
        f"培训费 {yuan(cost)} 元按服务期 {service_months} 个月分摊，尚未履行 {remaining} 个月，"
        f"违约金最多 {cap} 元。",
        [Basis(LCL, "第二十二条")],
        {"cap": cap, "remaining_months": remaining},
    )


def check_service_penalty(training_cost, service_months, served_months, *, agreed) -> Finding:
    cap = service_period_penalty_cap(training_cost, service_months, served_months)
    a = yuan(agreed)
    ok = a <= cap.values["cap"]
    msg = (
        f"要求的违约金 {a} 元未超过上限 {cap.values['cap']} 元。"
        if ok
        else f"要求的违约金 {a} 元超过上限 {cap.values['cap']} 元，超出部分无依据。"
    )
    return Finding("penalty.service", ok, msg, cap.basis, {**cap.values, "agreed": a})
