"""数据完整性：每个数字都有来源，依据文件都已核对。"""

import pytest

from laborcheck.cities import CITY_CODES, load_city
from laborcheck.core import sources


def test_all_national_sources_verified():
    for sid, s in sources().items():
        assert s["status"] == "verified", sid
        assert s["url"].startswith("https://"), sid


@pytest.mark.parametrize("code", CITY_CODES)
def test_city_values_have_sources(code):
    c = load_city(code)
    for field in (
        "min_wage_monthly",
        "hf_base_lower",
        "hf_base_upper",
        "hf_rate_min",
        "hf_rate_max",
    ):
        assert getattr(c, field) is not None, field
        src = c.source_of(field)
        assert src.get("doc_url", "").startswith(("https://", "http://")), field
        assert src.get("quote"), field
    assert c.si_items
    for it in c.si_items:
        assert c.sources[f"si.{it.code}"]["doc_url"].startswith(("https://", "http://"))
        assert it.base_lower < it.base_upper
    if c.avg_monthly_wage is not None:
        assert c.source_of("avg_monthly_wage")["doc_url"].startswith(("https://", "http://"))


def test_all_rule_basis_registered():
    """规则模块里引用的依据都在登记表中（Basis 构造时会校验，这里跑一遍生成器覆盖所有规则）。"""
    from laborcheck.evalset.generate import generate

    items = generate()
    assert len(items) > 200
    assert {i["split"] for i in items} == {"dev", "holdout"}
    assert len({i["id"] for i in items}) == len(items)
