"""评测集生成器。

题目中的合同、offer、公司和人物全部是构造的；标准答案由规则引擎计算。
同一个种子生成的结果完全相同。

每行格式：{"id", "input", "expected", "tags", "split"}
- input.question：题面
- input.format：verdict（判断合法性）/ amount（计算金额或天数）/ rewrite（判断并改写）
- expected：verdict / amount / tolerance / constraint / basis
- tags：type、topic、city、needs_local_data（答题是否依赖题面没有给出的地方数据）、
  params_given（地方参数是否已写在题面里）、trap（命中的易错点）
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from laborcheck.cities import CityPolicy, all_cities
from laborcheck.core import Finding, yuan
from laborcheck.rules import (
    leave,
    noncompete,
    overtime,
    payroll,
    penalty,
    probation,
    severance,
    tax,
    wage,
)
from laborcheck.rules.overtime import OvertimeKind
from laborcheck.rules.penalty import PenaltyKind
from laborcheck.rules.probation import TermKind

VERSION = "1.0.2"
HOLDOUT_SHARE = 0.3

Item = dict[str, Any]


# 金额误差容忍（元）：精确计算题 1 元；全年累计、各项分别取整的题 20 元
TOLERANCE = {"take_home": Decimal("20"), "offer_compare": Decimal("20")}


def _item(
    topic: str,
    qtype: str,
    question: str,
    finding: Finding,
    *,
    city: CityPolicy | None = None,
    verdict: bool | None = None,
    amount: Decimal | int | None = None,
    exact: bool = False,
    constraint: dict[str, Any] | None = None,
    needs_local: bool = False,
    params_given: bool = False,
    trap: str = "",
    unit: str = "元",
) -> Item:
    fmt = {"legality": "verdict", "calc": "amount", "rewrite": "rewrite"}[qtype]
    expected: dict[str, Any] = {"basis": finding.citations(), "explanation": finding.message}
    if verdict is not None:
        expected["verdict"] = "合法" if verdict else "违法"
    if amount is not None:
        a = Decimal(amount)
        expected["amount"] = float(a)
        expected["unit"] = unit
        expected["tolerance"] = 0.0 if exact else float(TOLERANCE.get(topic, Decimal("1")))
    if constraint is not None:
        expected["constraint"] = constraint
    return {
        "input": {"question": question, "format": fmt, "city": city.code if city else None},
        "expected": expected,
        "tags": {
            "type": qtype,
            "topic": topic,
            "city": city.code if city else "national",
            "needs_local_data": needs_local,
            "params_given": params_given,
            "trap": trap,
        },
    }


COMPANIES = [
    "星河科技",
    "远帆信息",
    "青禾数据",
    "北辰智能",
    "云栈网络",
    "澄明软件",
    "松果互娱",
    "知行教育",
]
ROLES = [
    "算法工程师",
    "后端开发工程师",
    "产品经理",
    "数据分析师",
    "测试工程师",
    "运营专员",
    "UI 设计师",
    "销售代表",
]


def _company(rng: random.Random) -> str:
    return f"{rng.choice(COMPANIES)}有限公司"


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, d.day)


def _cn_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


# ---------------------------------------------------------------- 试用期期限
def gen_probation_term(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    cases: list[tuple[TermKind, int | None, int, str]] = []
    for term in (2, 3, 6, 11, 12, 18, 24, 35, 36, 48, 60):
        cap, _ = probation.max_probation_months(TermKind.FIXED, term)
        options = {cap, cap + 1}
        if cap >= 2:
            options.add(cap - 1)
        if term in (12, 36):
            options.add(cap + 1)
        for p in sorted(options):
            if p == 0 and cap == 0:
                continue
            trap = "boundary" if term in (3, 12, 36) else ""
            if term in (11, 35):
                trap = "just_below_boundary"
            cases.append((TermKind.FIXED, term, p, trap))
    cases += [
        (TermKind.OPEN_ENDED, None, 6, ""),
        (TermKind.OPEN_ENDED, None, 7, ""),
        (TermKind.TASK, None, 1, "task_contract"),
        (TermKind.TASK, None, 2, "task_contract"),
    ]
    for kind, term, p, trap in cases:
        company = _company(rng)
        role = rng.choice(ROLES)
        if kind is TermKind.FIXED:
            assert term is not None
            start = date(2026, rng.choice([1, 3, 4, 7, 9]), 1)
            end = _add_months(start, term) - timedelta(days=1)
            if rng.random() < 0.5 or term in (12, 36):
                term_text = f"自{_cn_date(start)}起至{_cn_date(end)}止"
                trap = trap or "date_range"
            else:
                term_text = f"为{term}个月"
        elif kind is TermKind.OPEN_ENDED:
            term_text = "为无固定期限"
        else:
            term_text = "以完成某数据迁移项目为期限，项目验收完成时合同终止"
        clause = f"甲方（{company}）聘用乙方担任{role}。本合同期限{term_text}，其中试用期{p}个月。"
        f = probation.check_probation(kind, term, p)
        q = f"以下是一份劳动合同中的条款：\n「{clause}」\n请判断其中试用期的约定是否合法。"
        yield _item("probation_term", "legality", q, f, verdict=f.ok, trap=trap)
        if not f.ok:
            qr = (
                f"以下是一份劳动合同中的条款：\n「{clause}」\n"
                "请判断试用期约定是否合法；如不合法，请在合同期限不变的前提下改写该条款使其合法，"
                "并在 key_value 中给出改写后的试用期月数。"
            )
            yield _item(
                "probation_term",
                "rewrite",
                qr,
                f,
                verdict=False,
                constraint={
                    "field": "key_value",
                    "op": "<=",
                    "value": f.values["max_months"],
                    "min": 0,
                },
                trap=trap,
            )
    # 重复约定、仅约定试用期
    for _ in range(3):
        company = _company(rng)
        f = probation.check_probation(TermKind.FIXED, 36, 3, previous_probation=True)
        q = (
            f"小王在{company}工作，第一份两年期合同（含 2 个月试用期）到期后续签了三年期合同，"
            "续签合同中又约定了 3 个月试用期。请判断续签合同中的试用期约定是否合法。"
        )
        yield _item("probation_term", "legality", q, f, verdict=False, trap="second_probation")
    for months in (3, 6):
        f = probation.check_probation(TermKind.FIXED, months, months, probation_only=True)
        q = (
            f"{_company(rng)}与新员工签订的书面合同名为《试用期合同》，只约定了 {months} 个月试用期，"
            "没有约定其他劳动合同期限。请判断这种约定是否合法有效。"
        )
        yield _item("probation_term", "legality", q, f, verdict=False, trap="probation_only")


# ---------------------------------------------------------------- 试用期工资
def gen_probation_wage(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for city in cities:
        mw = city.need("min_wage_monthly")
        low_agreed = [int(mw / Decimal("0.8")) - d for d in (300, 150)]  # 80% 低于最低工资
        high_agreed = [8000, 12000, 15000, 22000]
        for agreed in low_agreed + rng.sample(high_agreed, 2):
            f = probation.min_probation_wage(agreed, min_wage=mw)
            binding = f.values["binding"] == "min_wage"
            q = (
                f"某公司在{city.name}招聘员工，劳动合同约定转正后月工资 {agreed} 元。"
                f"按{city.name}现行月最低工资标准，试用期月工资最低不得低于多少元？"
                "（直接比较应发工资即可，不考虑个人社保公积金扣除。）"
            )
            yield _item(
                "probation_wage",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["floor"],
                needs_local=binding,
                trap="min_wage_binding" if binding else "",
            )
            if binding:
                qp = (
                    f"某公司在{city.name}招聘员工，劳动合同约定转正后月工资 {agreed} 元，"
                    f"当地月最低工资标准为 {yuan(mw)} 元。试用期月工资最低不得低于多少元？"
                    "（直接比较应发工资即可，不考虑个人社保公积金扣除。）"
                )
                yield _item(
                    "probation_wage",
                    "calc",
                    qp,
                    f,
                    city=city,
                    amount=f.values["floor"],
                    params_given=True,
                    trap="min_wage_binding",
                )
        # 合法性和改写
        for agreed, ratio in (
            (10000, Decimal("0.8")),
            (10000, Decimal("0.7")),
            (18000, Decimal("0.75")),
            (int(mw / Decimal("0.8")) - 200, Decimal("0.8")),
            (rng.choice([9000, 13000, 16000]), Decimal("0.85")),
            (int(mw / Decimal("0.8")) - 100, Decimal("0.82")),
        ):
            pw = yuan(Decimal(agreed) * ratio)
            f = probation.check_probation_wage(agreed, pw, min_wage=mw)
            local = f.values["binding"] == "min_wage"
            clause = f"乙方转正后月工资为 {agreed} 元，试用期月工资为 {pw} 元。"
            q = (
                f"以下是{city.name}某公司劳动合同中的条款：\n「{clause}」\n"
                "请判断试用期工资约定是否合法（直接比较应发工资即可）。"
            )
            yield _item(
                "probation_wage",
                "legality",
                q,
                f,
                city=city,
                verdict=f.ok,
                needs_local=local,
                trap="min_wage_binding" if local else "",
            )
            if not f.ok:
                qr = (
                    f"以下是{city.name}某公司劳动合同中的条款：\n「{clause}」\n"
                    "请判断试用期工资约定是否合法；如不合法，请改写该条款，"
                    "并在 key_value 中给出改写后的试用期月工资（元）。"
                )
                yield _item(
                    "probation_wage",
                    "rewrite",
                    qr,
                    f,
                    city=city,
                    verdict=False,
                    constraint={
                        "field": "key_value",
                        "op": ">=",
                        "value": float(f.values["floor"]),
                        "max": agreed,
                    },
                    needs_local=local,
                )


# ---------------------------------------------------------------- 违约金
PENALTY_CLAUSES: dict[PenaltyKind, list[str]] = {
    PenaltyKind.EARLY_RESIGNATION: [
        "乙方在合同期内提前解除劳动合同的，应向甲方支付违约金人民币{amt}元。",
        "乙方承诺服务满三年，未满三年主动离职的，按每提前一个月 1000 元的标准向甲方支付违约金，最高{amt}元。",
    ],
    PenaltyKind.PROBATION_RESIGNATION: [
        "乙方在试用期内离职的，须向甲方支付违约金{amt}元，用于弥补招聘成本。",
    ],
    PenaltyKind.NO_NOTICE: [
        "乙方离职未提前三十日书面通知甲方的，应支付违约金{amt}元。",
    ],
    PenaltyKind.ORDINARY_TRAINING: [
        "甲方为乙方提供为期两周的新员工入职培训，乙方入职未满两年离职的，应向甲方支付培训违约金{amt}元。",
    ],
    PenaltyKind.NON_COMPETE: [
        "乙方离职后违反竞业限制约定的，应向甲方支付违约金{amt}元（乙方为高级技术人员，竞业限制期限 12 个月，甲方按月支付补偿）。",
    ],
}


def gen_penalty(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for kind, templates in PENALTY_CLAUSES.items():
        for tpl in templates:
            for _ in range(2):
                amt = rng.choice([5000, 10000, 20000, 30000, 50000])
                clause = tpl.format(amt=amt)
                f = penalty.check_penalty_kind(kind)
                q = f"以下是一份劳动合同中的条款：\n「{clause}」\n请判断该违约金约定是否合法。"
                yield _item("penalty_kind", "legality", q, f, verdict=f.ok)
                if not f.ok:
                    qr = (
                        f"以下是一份劳动合同中的条款：\n「{clause}」\n"
                        "请判断该违约金约定是否合法；如不合法，请改写该条款，"
                        "并在 key_value 中给出改写后由劳动者承担的违约金金额（元）。"
                    )
                    yield _item(
                        "penalty_kind",
                        "rewrite",
                        qr,
                        f,
                        verdict=False,
                        constraint={"field": "key_value", "op": "<=", "value": 0, "min": 0},
                    )
    # 服务期：约定金额超过培训费
    for _ in range(4):
        cost = rng.choice([12000, 20000, 30000, 48000])
        months = rng.choice([24, 36])
        amt = cost * rng.choice([2, 3])
        clause = (
            f"甲方出资 {cost} 元送乙方参加专业技术培训（有支付凭证），双方约定服务期 {months} 个月。"
            f"乙方违反服务期约定的，应向甲方支付违约金 {amt} 元。"
        )
        f = penalty.check_service_penalty(cost, months, 0, agreed=amt)
        q = f"以下是一份培训协议中的条款：\n「{clause}」\n请判断该违约金约定是否合法。"
        yield _item(
            "service_penalty", "legality", q, f, verdict=False, trap="exceeds_training_cost"
        )
        qr = (
            f"以下是一份培训协议中的条款：\n「{clause}」\n"
            "请判断该违约金约定是否合法；如不合法，请改写，"
            "并在 key_value 中给出改写后约定的违约金上限金额（元）。"
        )
        yield _item(
            "service_penalty",
            "rewrite",
            qr,
            f,
            verdict=False,
            constraint={"field": "key_value", "op": "<=", "value": cost, "min": 1},
        )
    # 服务期：按未履行比例计算上限
    for _ in range(10):
        cost = rng.choice([18000, 24000, 30000, 36000, 50000, 60000])
        months = rng.choice([24, 36, 48, 60])
        served = rng.randrange(1, months)
        f = penalty.service_period_penalty_cap(cost, months, served)
        q = (
            f"公司出资 {cost} 元为员工提供专业技术培训，约定服务期 {months} 个月。"
            f"员工服务了 {served} 个月后辞职。公司最多可以要求员工支付多少违约金？"
        )
        yield _item("service_penalty", "calc", q, f, amount=f.values["cap"], trap="prorate")


# ---------------------------------------------------------------- 竞业限制
NC_ROLES = {
    "senior_tech": ["首席算法科学家", "芯片架构负责人"],
    "senior_manager": ["分管销售的副总经理", "财务总监"],
    "confidential": ["接触核心源代码的研发工程师", "掌握客户报价的销售经理"],
    "ordinary": ["前台接待", "保洁员", "食堂厨师"],
}


def gen_noncompete(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for months in (6, 12, 18, 24, 30, 36):
        f = noncompete.check_duration(months)
        clause = f"乙方（高级技术人员）离职后 {months} 个月内不得到与甲方有竞争关系的单位任职，甲方按月支付补偿。"
        q = f"以下是一份竞业限制协议的条款：\n「{clause}」\n请判断竞业限制期限的约定是否合法。"
        yield _item(
            "noncompete", "legality", q, f, verdict=f.ok, trap="boundary" if months == 24 else ""
        )
        if not f.ok:
            qr = q.replace(
                "请判断竞业限制期限的约定是否合法。",
                "请判断竞业限制期限是否合法；如不合法，请改写，并在 key_value 中给出改写后的期限（月）。",
            )
            yield _item(
                "noncompete",
                "rewrite",
                qr,
                f,
                verdict=False,
                constraint={"field": "key_value", "op": "<=", "value": 24, "min": 1},
            )
    for person, roles in NC_ROLES.items():
        for role in roles:
            f = noncompete.check_person(person)
            secret = "，不接触公司商业秘密和技术秘密" if person == "ordinary" else ""
            q = (
                f"某公司要求岗位为「{role}」的员工{secret}签订竞业限制协议，离职后一年内不得到同行业公司工作，"
                "公司按月支付补偿。请判断对该员工约定竞业限制是否合法。"
            )
            yield _item("noncompete", "legality", q, f, verdict=f.ok)
    for city in cities:
        mw = city.need("min_wage_monthly")
        local = city.noncompete_local()
        who = "掌握公司技术秘密的研发人员" if local else "员工"
        for avg in (rng.choice([5500, 6000, 7000]), rng.choice([15000, 20000, 30000])):
            f = noncompete.default_monthly_compensation(avg, min_wage=mw, **local)
            binding = f.values["monthly"] == f.values["min_wage"]
            q = (
                f"{who}在{city.name}工作，离职前十二个月月平均工资 {avg} 元。竞业限制协议约定了 1 年竞业限制，"
                "但没有约定经济补偿。员工离职后履行了竞业限制义务，每月可以要求多少经济补偿？"
            )
            trap = "local_rule" if local else ("min_wage_binding" if binding else "no_agreed_comp")
            yield _item(
                "noncompete",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["monthly"],
                needs_local=binding or bool(local),
                trap=trap,
            )
        if local:
            for avg, agreed in ((20000, 6000), (16000, 4800), (12000, 7000), (30000, 9000)):
                f = noncompete.check_agreed_compensation(avg, agreed=agreed, **local)
                q = (
                    f"{city.name}某公司与掌握公司技术秘密的离职研发人员约定竞业限制 1 年，员工离职前十二个月"
                    f"月平均工资 {avg} 元，协议约定每月补偿 {agreed} 元。请判断该补偿约定是否符合{city.name}的规定。"
                )
                yield _item(
                    "noncompete",
                    "legality",
                    q,
                    f,
                    city=city,
                    verdict=f.ok,
                    needs_local=True,
                    trap="local_rule",
                )
                if not f.ok:
                    qr = (
                        q
                        + "如不符合，请改写补偿条款，并在 key_value 中给出改写后的每月补偿金额（元）。"
                    )
                    yield _item(
                        "noncompete",
                        "rewrite",
                        qr,
                        f,
                        city=city,
                        verdict=False,
                        constraint={
                            "field": "key_value",
                            "op": ">=",
                            "value": float(f.values["floor"]),
                        },
                        needs_local=True,
                        trap="local_rule",
                    )
    for comp in (3000, 4500, 6000, 9000):
        f = noncompete.release_extra_compensation(comp)
        q = (
            f"竞业限制期限为 2 年，公司每月支付补偿 {comp} 元。履行半年后，公司单方面通知解除竞业限制协议。"
            "员工可以要求公司额外支付多少竞业限制经济补偿？"
        )
        yield _item("noncompete", "calc", q, f, amount=f.values["amount"])


# ---------------------------------------------------------------- 加班
def gen_overtime(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for _ in range(20):
        base = rng.choice([8700, 10875, 13050, 15000, 18000, 21750, 26100, 30000])
        kind = rng.choice(list(OvertimeKind))
        hours = (
            rng.choice([4, 8, 12, 16, 20])
            if kind is not OvertimeKind.WORKDAY
            else rng.choice([10, 18, 25, 36])
        )
        f = overtime.overtime_pay(base, kind, hours)
        desc = {
            OvertimeKind.WORKDAY: f"本月工作日晚上累计延长工作 {hours} 小时",
            OvertimeKind.REST_DAY: f"本月周末加班 {hours} 小时，公司没有安排补休",
            OvertimeKind.HOLIDAY: f"国庆节法定节假日当天加班 {hours} 小时",
        }[kind]
        q = (
            f"员工加班费计算基数为月工资 {base} 元，{desc}。按法定标准，这部分加班费应为多少元？"
            "（月计薪天数按 21.75 天，每天 8 小时。）"
        )
        yield _item("overtime", "calc", q, f, amount=f.values["amount"])
    for _ in range(3):
        f = overtime.overtime_pay(15000, OvertimeKind.REST_DAY, 8, comp_leave=True)
        f.ok = True
        q = "公司规定：员工周六加班 8 小时的，在下周安排同等时长的补休，不再支付加班费。请判断该规定是否合法。"
        yield _item("overtime", "legality", q, f, verdict=True, trap="rest_day_comp_leave")
        f2 = Finding(
            "overtime.holiday_comp",
            False,
            "法定休假日安排劳动者工作的，应支付不低于工资 300% 的工资报酬，不能以补休代替。",
            overtime.overtime_pay(15000, OvertimeKind.HOLIDAY, 8).basis,
        )
        q2 = "公司规定：员工在春节法定节假日加班的，一律在节后安排调休，不再支付加班费。请判断该规定是否合法。"
        yield _item("overtime", "legality", q2, f2, verdict=False, trap="holiday_comp_leave")
        break


# ---------------------------------------------------------------- 经济补偿
def gen_severance(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for city in cities:
        if city.avg_monthly_wage is None:
            # 封顶基数没有官方来源：题面给出假设的社平工资，只考计算
            for assumed, w, months in (
                (12000, 45000, 170),
                (15000, 52000, 100),
                (12000, 20000, 77),
            ):
                hypo = replace(city, avg_monthly_wage=Decimal(assumed))
                f = severance.economic_compensation(w, months, hypo)
                yrs, rem = divmod(months, 12)
                q = (
                    f"员工在{city.name}工作 {yrs} 年 {rem} 个月后，公司依法提前三十日通知解除劳动合同并应支付经济补偿。"
                    f"员工离职前十二个月平均工资 {w} 元。假设当地上年度职工月平均工资为 {assumed} 元"
                    "（仅为本题设定）。公司应支付多少经济补偿？"
                )
                yield _item(
                    "severance",
                    "calc",
                    q,
                    f,
                    city=city,
                    amount=f.values["amount"],
                    params_given=True,
                    trap="high_wage_cap" if f.values["capped"] else "no_12_year_cap",
                )
            continue
        avg = city.avg_monthly_wage
        cap = avg * 3
        cases = [
            (rng.choice([9000, 12000, 16000]), rng.choice([7, 17, 29, 43, 66]), False),
            (rng.choice([18000, 25000]), rng.choice([5, 6, 30]), False),
            (int(cap) + rng.choice([5000, 15000]), rng.choice([60, 90, 170]), True),
            (int(cap) + 8000, rng.choice([20, 40]), True),
        ]
        for w, months, local in cases:
            f = severance.economic_compensation(w, months, city)
            yrs, rem = divmod(months, 12)
            dur = f"{yrs} 年 {rem} 个月" if yrs else f"{rem} 个月"
            q = (
                f"员工在{city.name}工作 {dur} 后，公司以客观情况发生重大变化为由与其解除劳动合同"
                f"（已提前三十日书面通知）。员工离职前十二个月平均工资 {w} 元。"
                f"按{city.name}的标准，公司应支付多少经济补偿？"
            )
            trap = "high_wage_cap" if f.values["capped"] else ("half_month" if 0 < rem < 6 else "")
            yield _item(
                "severance",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["amount"],
                needs_local=f.values["capped"] or local,
                trap=trap,
            )
            if f.values["capped"]:
                qp = q.replace(
                    f"按{city.name}的标准，",
                    f"已知当地上年度职工月平均工资为 {yuan(avg)} 元。",
                )
                yield _item(
                    "severance",
                    "calc",
                    qp,
                    f,
                    city=city,
                    amount=f.values["amount"],
                    params_given=True,
                    trap="high_wage_cap",
                )
    for city in cities:
        if city.avg_monthly_wage is None:
            continue
        w, months = rng.choice([10000, 14000, 20000]), rng.choice([15, 27, 40])
        f = severance.illegal_termination_damages(w, months, city)
        yrs, rem = divmod(months, 12)
        q = (
            f"员工在{city.name}工作 {yrs} 年 {rem} 个月，月平均工资 {w} 元，公司无正当理由将其辞退，"
            "经仲裁认定为违法解除，员工不要求恢复劳动关系。公司应支付多少赔偿金？"
        )
        yield _item(
            "severance", "calc", q, f, city=city, amount=f.values["amount"], trap="illegal_double"
        )
    for months in (3, 5, 8, 12, 15):
        w = rng.choice([8000, 10000, 12000])
        f = wage.no_contract_double_wage(w, months)
        q = (
            f"员工入职后公司一直没有签书面劳动合同，月工资 {w} 元，已工作整 {months} 个月（期间劳动关系持续）。"
            "按法律规定，员工可以主张未签劳动合同的二倍工资差额共多少元？"
        )
        yield _item(
            "double_wage",
            "calc",
            q,
            f,
            amount=f.values["amount"],
            trap="cap_11_months" if months > 12 else "",
        )


# ---------------------------------------------------------------- 年休假
def gen_leave(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for years, given in ((3, 5), (10, 5), (12, 10), (20, 10), (25, 15), (9, 5)):
        f = leave.annual_leave_days(years)
        ok = given >= f.values["days"]
        f.ok = ok
        q = f"员工累计工作已满 {years} 年（在现单位已工作 2 年），公司每年只给他 {given} 天带薪年休假。请判断是否合法。"
        yield _item(
            "annual_leave",
            "legality",
            q,
            f,
            verdict=ok,
            trap="boundary" if years in (10, 20) else "",
        )
    for start, yrs in (
        (date(2025, 7, 1), 3),
        (date(2025, 10, 15), 12),
        (date(2025, 3, 20), 1),
        (date(2025, 9, 1), 25),
    ):
        f = leave.new_hire_leave_days(start, yrs)
        q = (
            f"员工累计工作已满 {yrs} 年，于{_cn_date(start)}跳槽入职新公司。"
            f"按规定，他在新公司 {start.year} 年度可以休多少天带薪年休假？"
        )
        yield _item(
            "annual_leave",
            "calc",
            q,
            f,
            amount=f.values["days"],
            exact=True,
            unit="天",
            trap="prorate_floor",
        )
    for w, d in ((10875, 2), (21750, 3), (15000, 5), (26100, 4)):
        f = leave.unused_leave_pay(w, d)
        q = (
            f"员工月工资 {w} 元，本年度有 {d} 天年休假，公司因工作需要没有安排休假，员工也未书面放弃。"
            "除正常工资外，公司还应额外支付多少未休年休假工资？（月计薪天数 21.75 天。）"
        )
        yield _item("annual_leave", "calc", q, f, amount=f.values["amount"], trap="extra_200")


# ---------------------------------------------------------------- 社保公积金
def _si_rates_text(city: CityPolicy) -> str:
    parts = []
    for it in city.si_items:
        fixed = f"另加每月 {it.employee_fixed} 元" if it.employee_fixed else ""
        parts.append(
            f"{it.name}个人比例 {it.employee_rate:%}{fixed}，缴费基数上下限 {it.base_lower}—{it.base_upper} 元"
        )
    return "；".join(parts)


def _si_scope(city: CityPolicy) -> str:
    """题面中写明计入哪些险种的个人部分（各城市数据覆盖的险种不同）。"""
    return "、".join(it.name.split("（")[0].removesuffix("保险") for it in city.si_items) + "保险"


def gen_payroll(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for city in cities:
        upper = max(it.base_upper for it in city.si_items)
        lower = min(it.base_lower for it in city.si_items)
        grosses = [
            int(lower) - rng.choice([200, 800]),
            rng.choice([12000, 15000, 18000]),
            int(upper) + rng.choice([3000, 10000]),
        ]
        for g in grosses:
            f = payroll.social_insurance(g, city)
            trap = "base_floor" if g < lower else ("base_cap" if g > upper else "")
            q = (
                f"在{city.name}工作，月工资 {g} 元，社保缴费基数按月工资申报。按{city.name}{city.period}的标准，"
                f"个人每月需要缴纳的{_si_scope(city)}合计多少元？（只算这几项。）"
            )
            yield _item(
                "social_insurance",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["employee_total"],
                needs_local=True,
                trap=trap,
            )
            qp = (
                f"月工资 {g} 元，社保缴费基数按月工资申报。已知：{_si_rates_text(city)}。"
                "个人每月需要缴纳的社保合计多少元？"
            )
            yield _item(
                "social_insurance",
                "calc",
                qp,
                f,
                city=city,
                amount=f.values["employee_total"],
                params_given=True,
                trap=trap,
            )
        top = city.need("hf_rate_max")
        for g, rate in (
            (rng.choice([10000, 16000]), top),
            (int(city.need("hf_base_upper")) + 8000, top),
            (int(city.need("hf_base_lower")) - 300, city.need("hf_rate_min")),
        ):
            f = payroll.housing_fund(g, city, rate)
            lo, hi = city.hf_base_lower, city.hf_base_upper
            trap = "base_cap" if g > hi else ("base_floor" if g < lo else "")
            q = (
                f"应届生新入职{city.name}某公司，月工资 {g} 元，公积金基数按月工资申报，单位和个人缴存比例均为 {rate:%}。"
                f"按{city.name}现行缴存基数上下限，个人每月缴存公积金多少元？"
            )
            yield _item(
                "housing_fund",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["employee"],
                needs_local=trap != "",
                trap=trap,
            )


# ---------------------------------------------------------------- 个税与到手收入
def gen_tax(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for bonus in (36000, 36001, 144000, 144001, 20000, 60000, 300000, 420001):
        f = tax.bonus_tax_separate(bonus)
        trap = "bonus_cliff" if bonus % 1000 == 1 else ""
        q = f"年终奖 {bonus} 元，选择全年一次性奖金单独计税，应缴纳个人所得税多少元？"
        yield _item("bonus_tax", "calc", q, f, amount=f.values["tax"], trap=trap)
    for _ in range(10):
        salary = rng.choice([180000, 240000, 300000, 400000, 600000])
        bonus = rng.choice([30000, 60000, 100000, 150000])
        sihf = rng.choice([24000, 36000, 48000])
        special = rng.choice([0, 12000, 36000, 60000])
        f = tax.compare_bonus_methods(
            annual_salary=salary, bonus=bonus, annual_si_hf=sihf, annual_special=special
        )
        q = (
            f"全年工资薪金 {salary} 元（不含年终奖），年终奖 {bonus} 元，全年个人缴纳社保公积金 {sihf} 元，"
            f"专项附加扣除全年 {special} 元，无其他扣除。在年终奖单独计税和并入综合所得两种方式中选更省税的一种，"
            "全年个税合计最少是多少元？"
        )
        best = min(f.values["separate_total"], f.values["combined_total"])
        yield _item("bonus_tax", "calc", q, f, amount=best, trap="method_choice")
    for _ in range(14):
        g = rng.choice([15000, 20000, 25000, 30000, 40000, 50000])
        sihf = rng.choice([2000, 3000, 4500])
        sp = rng.choice([0, 1500, 3000])
        k = rng.choice([1, 3, 4, 7, 10, 12])
        f = tax.monthly_withholding(monthly_gross=g, monthly_si_hf=sihf, monthly_special=sp)
        amt = f.values["per_month"][k - 1]
        q = (
            f"员工 1 月入职，每月工资 {g} 元，每月个人缴纳社保公积金 {sihf} 元，每月专项附加扣除 {sp} 元，"
            f"无其他收入。按累计预扣法，第 {k} 个月应预扣预缴个人所得税多少元？"
        )
        yield _item("withholding", "calc", q, f, amount=amt, trap="cumulative_bracket")


def gen_take_home(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    for city in cities:
        for _ in range(5):
            g = rng.choice([12000, 18000, 25000, 35000, 45000])
            bonus = rng.choice([0, 2, 3]) * g
            top = city.need("hf_rate_max")
            f = payroll.annual_take_home(
                g, city, hf_rate=top, annual_bonus=bonus, bonus_method="separate"
            )
            btxt = f"，年终奖 {bonus} 元（单独计税）" if bonus else "，没有年终奖"
            q = (
                f"应届生拿到{city.name}的 offer：月薪 {g} 元，12 个月{btxt}；社保和公积金都按月薪作为基数，"
                f"公积金按{city.name}允许的最高比例 {top:%} 缴存，社保按{city.name}{city.period}标准（只算{_si_scope(city)}的个人部分），"
                "没有专项附加扣除。全年税后到手的现金收入是多少元？（税前工资和年终奖，扣除个人缴纳的社保、个人缴纳的公积金和个税；公积金账户里的钱不算作到手收入。）"
            )
            yield _item(
                "take_home",
                "calc",
                q,
                f,
                city=city,
                amount=f.values["annual_net"],
                needs_local=True,
                trap="base_cap" if g > 30000 else "",
            )
    pairs = [(cities[i], cities[j]) for i in range(len(cities)) for j in range(i + 1, len(cities))]
    for a, b in pairs:
        ga, gb = rng.choice([20000, 25000, 30000]), rng.choice([20000, 25000, 30000, 38000])
        fa = payroll.annual_take_home(ga, a, hf_rate=a.need("hf_rate_max"))
        fb = payroll.annual_take_home(gb, b, hf_rate=b.need("hf_rate_max"))
        diff = yuan(fa.values["annual_net"] - fb.values["annual_net"])
        f = Finding(
            "offer.compare",
            None,
            f"{fa.message}\n{fb.message}\n差额 {diff} 元。",
            fa.basis,
            {"diff": diff},
        )
        q = (
            f"应届生有两个 offer：{a.name}月薪 {ga} 元，{b.name}月薪 {gb} 元，都是 12 薪、没有年终奖，"
            f"社保公积金都按月薪作为基数、公积金按各自城市允许的最高比例缴存，社保按各自城市现行标准（{a.name}只算{_si_scope(a)}，{b.name}只算{_si_scope(b)}，均为个人部分），"
            f"没有专项附加扣除。{a.name} offer 的全年税后到手现金收入（扣除个人社保、个人公积金和个税，公积金账户里的钱不算）比{b.name}多多少元？（少则为负数）"
        )
        yield _item("offer_compare", "calc", q, f, city=a, amount=diff, needs_local=True)


SI_WAIVER_CLAUSES = [
    (
        "乙方自愿放弃缴纳社会保险，甲方每月额外支付社保补贴 {amt} 元，乙方承诺不因此追究甲方责任。",
        False,
    ),
    ("双方约定试用期内不缴纳社会保险，转正后再由甲方依法缴纳。", False),
    ("乙方签署《自愿不缴纳社保承诺书》，甲方将单位应缴部分折算为现金随工资发放。", False),
    ("甲方依法为乙方缴纳社会保险，个人应缴部分由甲方从乙方工资中代扣代缴。", True),
]


def gen_misc(rng: random.Random, cities: list[CityPolicy]) -> Iterator[Item]:
    from laborcheck.core import Basis

    for tpl, ok in SI_WAIVER_CLAUSES:
        clause = tpl.format(amt=rng.choice([800, 1000, 1500]))
        basis = (
            [Basis("labor_law", "第七十二条")] if ok else [Basis("spc_labor_interp_2", "第十九条")]
        )
        msg = (
            "用人单位和劳动者必须依法参加社会保险，单位依法代扣代缴个人部分，合法。"
            if ok
            else "约定或承诺无需缴纳社会保险费的，该约定或承诺无效；劳动者可以据此解除劳动合同并要求经济补偿。"
        )
        q = f"以下是一份劳动合同（或入职材料）中的条款：\n「{clause}」\n请判断该约定是否合法有效。"
        yield _item(
            "si_waiver",
            "legality",
            q,
            Finding("si.waiver", ok, msg, basis),
            verdict=ok,
            trap="" if ok else "waiver_invalid",
        )
    cases = [
        (
            "某公司要求所有员工（包括不接触任何商业秘密的仓库搬运工）签订竞业限制协议，离职后一年内不得到同行业工作，"
            "公司按月支付补偿。请判断该协议对仓库搬运工是否有效。",
            False,
            [Basis("labor_contract_law", "第二十四条"), Basis("spc_labor_interp_2", "第十三条")],
            "劳动者未知悉、接触商业秘密和保密事项的，竞业限制条款不生效。",
        ),
        (
            "某公司与高级技术人员约定：在职期间不得在与公司有竞争关系的其他单位兼职或任职，在职期间不另行支付竞业限制补偿。"
            "请判断该在职期间竞业限制约定是否有效。",
            True,
            [Basis("spc_labor_interp_2", "第十四条")],
            "与高级技术人员等约定在职期间竞业限制，以未支付经济补偿为由主张无效的，不予支持。",
        ),
    ]
    for q, ok, basis, msg in cases:
        yield _item(
            "noncompete",
            "legality",
            q,
            Finding("noncompete.misc", ok, msg, basis),
            verdict=ok,
            trap="interp_2",
        )


GENERATORS: list[Callable[[random.Random, list[CityPolicy]], Iterator[Item]]] = [
    gen_probation_term,
    gen_probation_wage,
    gen_penalty,
    gen_noncompete,
    gen_overtime,
    gen_severance,
    gen_leave,
    gen_payroll,
    gen_tax,
    gen_take_home,
    gen_misc,
]


def generate(seed: int = 20260, cities: list[CityPolicy] | None = None) -> list[Item]:
    rng = random.Random(seed)
    cities = cities or all_cities()
    items: list[Item] = []
    for gen in GENERATORS:
        items.extend(gen(rng, cities))
    # 去重（同一题面只保留一次）
    seen: set[str] = set()
    uniq = []
    for it in items:
        q = it["input"]["question"]
        if q in seen:
            continue
        seen.add(q)
        uniq.append(it)
    # 按 (topic, type) 分层划分留出集
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in uniq:
        groups[(it["tags"]["topic"], it["tags"]["type"])].append(it)
    split_rng = random.Random(seed + 1)
    for group in groups.values():
        idx = list(range(len(group)))
        split_rng.shuffle(idx)
        n_hold = round(len(group) * HOLDOUT_SHARE)
        hold = set(idx[:n_hold])
        for i, it in enumerate(group):
            it["split"] = "holdout" if i in hold else "dev"
    for n, it in enumerate(uniq, 1):
        it["id"] = f"lc-{n:04d}"
    return [
        {"id": it["id"], **{k: it[k] for k in ("input", "expected", "tags", "split")}}
        for it in uniq
    ]


def write(items: list[Item], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    meta = {
        "name": "laborcheck",
        "version": VERSION,
        "count": len(items),
        "splits": {s: sum(1 for i in items if i["split"] == s) for s in ("dev", "holdout")},
    }
    path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
