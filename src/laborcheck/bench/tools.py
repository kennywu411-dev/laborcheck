"""把规则引擎包装成大模型可调用的工具（JSON Schema + 分发函数）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from laborcheck.cities import CITY_CODES, CityPolicy, MissingData, load_city
from laborcheck.core import Finding, _jsonable
from laborcheck.rules import (
    leave,
    noncompete,
    overtime,
    payroll,
    penalty,
    probation,
    severance,
    wage,
)
from laborcheck.rules import tax as taxr

CITY_NAMES = {"北京": "beijing", "上海": "shanghai", "深圳": "shenzhen", "杭州": "hangzhou"}


def _city(v: str) -> CityPolicy:
    code = CITY_NAMES.get(v.strip().removesuffix("市"), v.strip().lower())
    return load_city(code)


def _num(v: Any) -> Decimal:
    return Decimal(str(v))


CITY = {
    "type": "string",
    "description": "城市：beijing / shanghai / shenzhen / hangzhou（也接受中文名）",
}
NUM = {"type": "number"}
INT = {"type": "integer"}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def city_policy(city: str) -> dict[str, Any]:
    c = _city(city)
    return {
        "city": c.name,
        "period": c.period,
        "min_wage_monthly": c.min_wage_monthly,
        "avg_monthly_wage_for_severance_cap": c.avg_monthly_wage,
        "social_insurance": [
            {
                "name": i.name,
                "base_lower": i.base_lower,
                "base_upper": i.base_upper,
                "employee_rate": i.employee_rate,
                "employee_fixed": i.employee_fixed,
                "employer_rate": i.employer_rate,
            }
            for i in c.si_items
        ],
        "housing_fund": {
            "base_lower": c.hf_base_lower,
            "base_upper": c.hf_base_upper,
            "rate_min": c.hf_rate_min,
            "rate_max": c.hf_rate_max,
        },
        "noncompete_min_ratio": c.noncompete_min_ratio,
        "notes": list(c.notes),
    }


TOOLS: list[tuple[str, str, dict[str, Any], Callable[..., Any]]] = [
    (
        "get_city_policy",
        "查询城市的最低工资、社保和公积金缴费基数上下限与比例、经济补偿封顶用的社平工资、地方竞业限制补偿最低比例。",
        _obj({"city": CITY}, ["city"]),
        city_policy,
    ),
    (
        "check_probation",
        "检查试用期期限是否合法（劳动合同法第十九条）。term_kind：fixed 固定期限 / open_ended 无固定期限 / task 以完成一定工作任务为期限。",
        _obj(
            {
                "term_kind": {"type": "string", "enum": ["fixed", "open_ended", "task"]},
                "term_months": INT,
                "probation_months": INT,
                "previous_probation": {
                    "type": "boolean",
                    "description": "此前是否已与同一单位约定过试用期",
                },
                "probation_only": {"type": "boolean", "description": "合同是否只约定了试用期"},
            },
            ["term_kind", "probation_months"],
        ),
        lambda term_kind, probation_months, term_months=None, previous_probation=False, probation_only=False: (
            probation.check_probation(
                probation.TermKind(term_kind),
                term_months,
                probation_months,
                previous_probation=previous_probation,
                probation_only=probation_only,
            )
        ),
    ),
    (
        "probation_wage_floor",
        "计算试用期工资下限（约定工资 80% 与当地最低工资取高者）；给出 probation_wage 时同时判断是否合法。",
        _obj({"agreed_wage": NUM, "city": CITY, "probation_wage": NUM}, ["agreed_wage", "city"]),
        lambda agreed_wage, city, probation_wage=None: (
            probation.check_probation_wage(
                agreed_wage, probation_wage, min_wage=_city(city).need("min_wage_monthly")
            )
            if probation_wage is not None
            else probation.min_probation_wage(
                agreed_wage, min_wage=_city(city).need("min_wage_monthly")
            )
        ),
    ),
    (
        "illegal_probation_damages",
        "违法约定的试用期已经履行时，用人单位应支付的赔偿金（第八十三条）。",
        _obj(
            {
                "term_kind": {"type": "string", "enum": ["fixed", "open_ended", "task"]},
                "term_months": INT,
                "agreed_probation": INT,
                "served_probation": INT,
                "regular_wage": NUM,
            },
            ["term_kind", "agreed_probation", "served_probation", "regular_wage"],
        ),
        lambda term_kind, agreed_probation, served_probation, regular_wage, term_months=None: (
            probation.illegal_probation_damages(
                probation.TermKind(term_kind),
                term_months,
                agreed_probation=agreed_probation,
                served_probation=served_probation,
                regular_wage=regular_wage,
            )
        ),
    ),
    (
        "check_penalty_kind",
        "判断某类违约金约定是否合法（第二十五条）。kind：service_period 单位出资专项技术培训的服务期 / non_compete 竞业限制 / early_resignation 提前离职 / probation_resignation 试用期离职 / no_notice 未提前通知离职 / ordinary_training 普通入职培训。",
        _obj(
            {"kind": {"type": "string", "enum": [k.value for k in penalty.PenaltyKind]}}, ["kind"]
        ),
        lambda kind: penalty.check_penalty_kind(penalty.PenaltyKind(kind)),
    ),
    (
        "service_penalty_cap",
        "服务期违约金上限：按尚未履行的服务期分摊培训费用；给出 agreed 时同时判断约定金额是否超限。",
        _obj(
            {"training_cost": NUM, "service_months": INT, "served_months": INT, "agreed": NUM},
            ["training_cost", "service_months", "served_months"],
        ),
        lambda training_cost, service_months, served_months, agreed=None: (
            penalty.check_service_penalty(
                training_cost, service_months, served_months, agreed=agreed
            )
            if agreed is not None
            else penalty.service_period_penalty_cap(training_cost, service_months, served_months)
        ),
    ),
    (
        "check_noncompete",
        "竞业限制检查。可分别给出：duration_months 检查期限；person（senior_manager / senior_tech / confidential / ordinary）检查人员范围；avg_wage_12m 加 city 计算未约定补偿时的法定月补偿；再给出 agreed_monthly 检查约定补偿是否符合地方标准。",
        _obj(
            {
                "duration_months": INT,
                "person": {"type": "string", "enum": list(noncompete.PERSONS)},
                "avg_wage_12m": NUM,
                "city": CITY,
                "agreed_monthly": NUM,
            },
            [],
        ),
        lambda **kw: _noncompete(**kw),
    ),
    (
        "noncompete_release_extra",
        "竞业限制期限内用人单位解除协议时，劳动者可要求的额外三个月补偿。",
        _obj({"monthly_compensation": NUM}, ["monthly_compensation"]),
        lambda monthly_compensation: noncompete.release_extra_compensation(monthly_compensation),
    ),
    (
        "overtime_pay",
        "计算加班费。kind：workday 工作日延时 / rest_day 休息日 / holiday 法定节假日；comp_leave 表示休息日加班已安排补休。",
        _obj(
            {
                "monthly_base": NUM,
                "kind": {"type": "string", "enum": [k.value for k in overtime.OvertimeKind]},
                "hours": NUM,
                "comp_leave": {"type": "boolean"},
            },
            ["monthly_base", "kind", "hours"],
        ),
        lambda monthly_base, kind, hours, comp_leave=False: overtime.overtime_pay(
            monthly_base, overtime.OvertimeKind(kind), hours, comp_leave=comp_leave
        ),
    ),
    (
        "economic_compensation",
        "计算经济补偿（第四十七条，含社平工资三倍封顶和最低工资保底）；illegal=true 时计算违法解除赔偿金（二倍）。"
        "城市缺少官方封顶基数、或题目给定了社平工资时，用 local_avg_monthly_wage 传入当地上年度职工月平均工资。",
        _obj(
            {
                "avg_monthly_wage": NUM,
                "months_worked": INT,
                "city": CITY,
                "illegal": {"type": "boolean"},
                "local_avg_monthly_wage": {
                    "type": "number",
                    "description": "当地上年度职工月平均工资（可选，覆盖城市数据）",
                },
            },
            ["avg_monthly_wage", "months_worked", "city"],
        ),
        lambda avg_monthly_wage, months_worked, city, illegal=False, local_avg_monthly_wage=None: (
            severance.illegal_termination_damages if illegal else severance.economic_compensation
        )(
            avg_monthly_wage,
            months_worked,
            replace(_city(city), avg_monthly_wage=_num(local_avg_monthly_wage))
            if local_avg_monthly_wage is not None
            else _city(city),
        ),
    ),
    (
        "no_contract_double_wage",
        "未签书面劳动合同的二倍工资差额（按整月计）。months_without_contract 为自用工之日起已工作的整月数（期间一直未签书面合同），函数内部自动扣除第一个月并封顶 11 个月。",
        _obj(
            {"monthly_wage": NUM, "months_without_contract": INT},
            ["monthly_wage", "months_without_contract"],
        ),
        lambda monthly_wage, months_without_contract: wage.no_contract_double_wage(
            monthly_wage, months_without_contract
        ),
    ),
    (
        "annual_leave",
        "年休假：给出 cumulative_years 得到全年天数；再给出 start_date（YYYY-MM-DD）得到新入职当年折算天数；给出 monthly_wage 和 unused_days 得到未休年假应额外支付的工资。",
        _obj(
            {
                "cumulative_years": INT,
                "start_date": {"type": "string"},
                "monthly_wage": NUM,
                "unused_days": NUM,
            },
            [],
        ),
        lambda **kw: _leave(**kw),
    ),
    (
        "social_insurance",
        "按月工资计算城市社保个人和单位缴费（缴费基数按上下限截取）。",
        _obj({"monthly_gross": NUM, "city": CITY}, ["monthly_gross", "city"]),
        lambda monthly_gross, city: payroll.social_insurance(monthly_gross, _city(city)),
    ),
    (
        "housing_fund",
        "按月工资和缴存比例计算城市住房公积金（基数按上下限截取，比例须在城市允许范围内）。",
        _obj({"monthly_gross": NUM, "city": CITY, "rate": NUM}, ["monthly_gross", "city", "rate"]),
        lambda monthly_gross, city, rate: payroll.housing_fund(
            monthly_gross, _city(city), _num(rate)
        ),
    ),
    (
        "bonus_tax",
        "全年一次性奖金单独计税的税额。",
        _obj({"bonus": NUM}, ["bonus"]),
        lambda bonus: taxr.bonus_tax_separate(bonus),
    ),
    (
        "compare_bonus_methods",
        "比较年终奖单独计税和并入综合所得两种方式的全年个税。",
        _obj(
            {"annual_salary": NUM, "bonus": NUM, "annual_si_hf": NUM, "annual_special": NUM},
            ["annual_salary", "bonus", "annual_si_hf"],
        ),
        lambda annual_salary, bonus, annual_si_hf, annual_special=0: taxr.compare_bonus_methods(
            annual_salary=annual_salary,
            bonus=bonus,
            annual_si_hf=annual_si_hf,
            annual_special=annual_special,
        ),
    ),
    (
        "monthly_withholding",
        "累计预扣法下每月预扣个税（工资每月相同）。",
        _obj(
            {"monthly_gross": NUM, "monthly_si_hf": NUM, "monthly_special": NUM},
            ["monthly_gross", "monthly_si_hf"],
        ),
        lambda monthly_gross, monthly_si_hf, monthly_special=0: taxr.monthly_withholding(
            monthly_gross=monthly_gross,
            monthly_si_hf=monthly_si_hf,
            monthly_special=monthly_special,
        ),
    ),
    (
        "annual_take_home",
        "计算城市 offer 的全年税后到手收入（社保公积金按月薪作为基数）。bonus_method：separate / combined / best。",
        _obj(
            {
                "monthly_gross": NUM,
                "city": CITY,
                "hf_rate": NUM,
                "annual_bonus": NUM,
                "bonus_method": {"type": "string", "enum": ["separate", "combined", "best"]},
                "monthly_special": NUM,
            },
            ["monthly_gross", "city", "hf_rate"],
        ),
        lambda monthly_gross, city, hf_rate, annual_bonus=0, bonus_method="separate", monthly_special=0: (
            payroll.annual_take_home(
                monthly_gross,
                _city(city),
                hf_rate=_num(hf_rate),
                annual_bonus=annual_bonus,
                bonus_method=bonus_method,
                monthly_special=monthly_special,
            )
        ),
    ),
]


def _noncompete(
    duration_months=None, person=None, avg_wage_12m=None, city=None, agreed_monthly=None
) -> list[Finding]:
    out: list[Finding] = []
    if duration_months is not None:
        out.append(noncompete.check_duration(duration_months))
    if person is not None:
        out.append(noncompete.check_person(person))
    if avg_wage_12m is not None and city is not None:
        c = _city(city)
        out.append(
            noncompete.default_monthly_compensation(
                avg_wage_12m, min_wage=c.need("min_wage_monthly"), **c.noncompete_local()
            )
        )
        if agreed_monthly is not None:
            out.append(
                noncompete.check_agreed_compensation(
                    avg_wage_12m, agreed=agreed_monthly, **c.noncompete_local()
                )
            )
    if not out:
        raise ValueError("至少给出 duration_months、person 或 avg_wage_12m+city 中的一项")
    return out


def _leave(cumulative_years=None, start_date=None, monthly_wage=None, unused_days=None):
    out: list[Finding] = []
    if cumulative_years is not None:
        out.append(leave.annual_leave_days(cumulative_years))
        if start_date:
            out.append(leave.new_hire_leave_days(date.fromisoformat(start_date), cumulative_years))
    if monthly_wage is not None and unused_days is not None:
        out.append(leave.unused_leave_pay(monthly_wage, unused_days))
    if not out:
        raise ValueError("参数不足")
    return out


_BY_NAME = {name: fn for name, _, _, fn in TOOLS}


def tool_specs() -> list[dict[str, Any]]:
    """通用格式：[{name, description, parameters}]。"""
    return [{"name": n, "description": d, "parameters": p} for n, d, p, _ in TOOLS]


def call_tool(name: str, args: dict[str, Any]) -> str:
    """执行工具，返回 JSON 字符串；出错时返回 {"error": ...}，不抛异常。"""
    try:
        if name not in _BY_NAME:
            raise KeyError(f"未知工具：{name}")
        result = _BY_NAME[name](**args)
        if isinstance(result, Finding):
            payload: Any = result.to_dict()
        elif isinstance(result, list):
            payload = [r.to_dict() for r in result]
        else:
            payload = _jsonable(result)
    except (KeyError, ValueError, TypeError, MissingData) as e:
        payload = {"error": str(e)}
    return json.dumps(payload, ensure_ascii=False, default=str)


assert set(CITY_NAMES.values()) == set(CITY_CODES)
