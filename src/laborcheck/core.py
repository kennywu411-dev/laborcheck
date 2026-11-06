"""引擎公共类型：依据、结论、金额。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from importlib import resources
from typing import Any

CENT = Decimal("0.01")


def dec(x: Any) -> Decimal:
    """把 int / str / Decimal 转成 Decimal；float 先转 str，避免二进制误差。"""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(str(x))
    return Decimal(x)


def yuan(x: Any) -> Decimal:
    """四舍五入到分。"""
    return dec(x).quantize(CENT, rounding=ROUND_HALF_UP)


@cache
def sources() -> dict[str, dict[str, str]]:
    """依据文件登记表：id -> {name, url, ...}。"""
    text = resources.files("laborcheck.data").joinpath("sources.toml").read_text("utf-8")
    return tomllib.loads(text)


@dataclass(frozen=True)
class Basis:
    """一条依据：某个文件的某一条（或某一项）。"""

    source: str
    article: str = ""

    def __post_init__(self) -> None:
        if self.source not in sources():
            raise KeyError(f"未登记的依据文件：{self.source}")

    @property
    def name(self) -> str:
        return sources()[self.source]["name"]

    @property
    def url(self) -> str:
        return sources()[self.source].get("url", "")

    def cite(self) -> str:
        return f"《{self.name}》{self.article}" if self.article else f"《{self.name}》"


@dataclass
class Finding:
    """一条结论。

    ok 为 True 表示合法 / 未发现问题，False 表示违法或有风险；计算类结论 ok 为 None。
    """

    rule: str
    ok: bool | None
    message: str
    basis: list[Basis] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)

    def citations(self) -> list[str]:
        return [b.cite() for b in self.basis]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "ok": self.ok,
            "message": self.message,
            "basis": self.citations(),
            "values": {k: _jsonable(v) for k, v in self.values.items()},
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v
