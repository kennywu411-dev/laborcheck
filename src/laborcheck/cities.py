"""城市政策数据：社保、公积金、最低工资、社平工资。

数据文件在 laborcheck/data/cities/<code>.toml，每个数字都登记了来源文件和适用期间。
未找到官方来源的数字不写进数据文件，对应字段为空，引擎遇到会报错而不是猜。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any

from laborcheck.core import dec


class MissingData(LookupError):
    """所需城市数据缺失（没有找到官方来源）。"""


@dataclass(frozen=True)
class SIItem:
    """一个社保险种。比例为小数，employee_fixed 为个人每月固定缴费（如北京大病医疗 3 元）。"""

    code: str
    name: str
    base_lower: Decimal
    base_upper: Decimal
    employee_rate: Decimal
    employer_rate: Decimal
    employee_fixed: Decimal = Decimal("0")


@dataclass(frozen=True)
class CityPolicy:
    code: str
    name: str
    period: str
    min_wage_monthly: Decimal | None = None
    min_wage_hourly: Decimal | None = None
    avg_monthly_wage: Decimal | None = None
    si_items: tuple[SIItem, ...] = ()
    hf_base_lower: Decimal | None = None
    hf_base_upper: Decimal | None = None
    hf_rate_min: Decimal | None = None
    hf_rate_max: Decimal | None = None
    # 地方对竞业限制补偿的最低比例（以离职前十二个月月平均工资为基数），无地方规定为 None
    noncompete_min_ratio: Decimal | None = None
    # 上述地方比例的依据：(sources.toml 中的文件 id, 条号)
    noncompete_basis: tuple[str, str] | None = None
    # 最低工资是否不含个人缴纳的社保公积金
    min_wage_excludes_personal_contrib: bool | None = None
    # 字段名 -> 来源说明（文件名、文号、URL、适用期间）
    sources: dict[str, dict[str, str]] = field(default_factory=dict, compare=False)
    notes: tuple[str, ...] = ()

    def need(self, name: str) -> Any:
        v = getattr(self, name)
        if v is None or v == ():
            raise MissingData(f"{self.name}缺少「{name}」数据（未找到官方来源）")
        return v

    def noncompete_local(self) -> dict[str, Any]:
        """传给竞业限制补偿函数的地方规定参数。"""
        if self.noncompete_min_ratio is None:
            return {}
        from laborcheck.core import Basis

        basis = Basis(*self.noncompete_basis) if self.noncompete_basis else None
        return {"local_min_ratio": self.noncompete_min_ratio, "local_basis": basis}

    def source_of(self, name: str) -> dict[str, str]:
        return self.sources.get(name, {})


CITY_CODES = ("beijing", "shanghai", "shenzhen", "hangzhou")


def _value(d: dict[str, Any], key: str) -> tuple[Decimal | None, dict[str, str] | None]:
    """数据文件中的数值字段写成表：{value, source, period, quote}。"""
    entry = d.get(key)
    if entry is None:
        return None, None
    if not isinstance(entry, dict):
        raise ValueError(f"{key} 必须写成带来源的表")
    if "source" not in entry:
        raise ValueError(f"{key} 没有登记来源")
    meta = {k: str(v) for k, v in entry.items() if k != "value"}
    return dec(str(entry["value"])), meta


def parse_city(raw: dict[str, Any]) -> CityPolicy:
    srcs: dict[str, dict[str, str]] = {}
    docs = raw.get("documents", {})

    def val(key: str) -> Decimal | None:
        v, meta = _value(raw, key)
        if meta is not None:
            doc = docs.get(meta["source"], {})
            srcs[key] = {**meta, **{f"doc_{k}": str(x) for k, x in doc.items()}}
        return v

    items = []
    for it in raw.get("social_insurance", []):
        if "source" not in it:
            raise ValueError(f"社保险种 {it.get('code')} 没有登记来源")
        items.append(
            SIItem(
                code=it["code"],
                name=it["name"],
                base_lower=dec(str(it["base_lower"])),
                base_upper=dec(str(it["base_upper"])),
                employee_rate=dec(str(it["employee_rate"])),
                employer_rate=dec(str(it["employer_rate"])),
                employee_fixed=dec(str(it.get("employee_fixed", 0))),
            )
        )
        doc = docs.get(it["source"], {})
        srcs[f"si.{it['code']}"] = {
            "source": it["source"],
            "period": str(it.get("period", "")),
            **{f"doc_{k}": str(x) for k, x in doc.items()},
        }

    return CityPolicy(
        code=raw["code"],
        name=raw["name"],
        period=raw["period"],
        min_wage_monthly=val("min_wage_monthly"),
        min_wage_hourly=val("min_wage_hourly"),
        avg_monthly_wage=val("avg_monthly_wage"),
        si_items=tuple(items),
        hf_base_lower=val("hf_base_lower"),
        hf_base_upper=val("hf_base_upper"),
        hf_rate_min=val("hf_rate_min"),
        hf_rate_max=val("hf_rate_max"),
        noncompete_min_ratio=val("noncompete_min_ratio"),
        noncompete_basis=tuple(raw["noncompete_basis"]) if "noncompete_basis" in raw else None,
        min_wage_excludes_personal_contrib=raw.get("min_wage_excludes_personal_contrib"),
        sources=srcs,
        notes=tuple(raw.get("notes", [])),
    )


@cache
def load_city(code: str) -> CityPolicy:
    if code not in CITY_CODES:
        raise KeyError(f"不支持的城市：{code}（支持 {', '.join(CITY_CODES)}）")
    text = resources.files("laborcheck.data.cities").joinpath(f"{code}.toml").read_text("utf-8")
    return parse_city(tomllib.loads(text))


def all_cities() -> list[CityPolicy]:
    return [load_city(c) for c in CITY_CODES]
