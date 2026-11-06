import asyncio
import json

import pytest

from laborcheck.bench import scoring
from laborcheck.bench.providers import Client, ModelSpec
from laborcheck.bench.runner import latest_records, run
from laborcheck.bench.tools import call_tool, tool_specs

VERDICT_ITEM = {
    "input": {"format": "verdict"},
    "expected": {"verdict": "违法", "basis": ["《中华人民共和国劳动合同法》第十九条"]},
}
AMOUNT_ITEM = {
    "input": {"format": "amount"},
    "expected": {"amount": 1080.0, "tolerance": 1.0, "basis": []},
}
REWRITE_ITEM = {
    "input": {"format": "rewrite"},
    "expected": {
        "verdict": "违法",
        "constraint": {"field": "key_value", "op": "<=", "value": 2, "min": 0},
        "basis": ["《中华人民共和国劳动合同法》第十九条"],
    },
}


def test_extract_last_json():
    text = '分析……{"a": 1}\n最终：\n```json\n{"verdict": "违法", "amount": null}\n```'
    assert scoring.extract_json(text) == {"verdict": "违法", "amount": None}


def test_extract_json_with_nested_braces():
    text = '{"verdict": "合法", "basis": ["{x}"], "amount": null}'
    assert scoring.extract_json(text)["verdict"] == "合法"


def test_no_json():
    r = scoring.score(VERDICT_ITEM, "我认为违法")
    assert r["passed"] is False and r["detail"]["reason"] == "no_json"


@pytest.mark.parametrize(("v", "ok"), [("违法", True), ("不合法", True), ("合法", False)])
def test_verdict(v, ok):
    out = json.dumps({"verdict": v, "basis": ["劳动合同法第十九条"]}, ensure_ascii=False)
    r = scoring.score(VERDICT_ITEM, out)
    assert r["passed"] is ok
    assert r["basis_hit"] is True


@pytest.mark.parametrize(
    ("a", "ok"), [(1080, True), ("1,080.00元", True), (1081, True), (1081.5, False), (None, False)]
)
def test_amount(a, ok):
    assert scoring.score(AMOUNT_ITEM, json.dumps({"amount": a}))["passed"] is ok


def test_rewrite_requires_all_parts():
    good = {"verdict": "违法", "key_value": 2, "rewrite": "试用期为2个月。"}
    assert scoring.score(REWRITE_ITEM, json.dumps(good, ensure_ascii=False))["passed"]
    for bad in ({**good, "key_value": 3}, {**good, "rewrite": ""}, {**good, "verdict": "合法"}):
        assert not scoring.score(REWRITE_ITEM, json.dumps(bad, ensure_ascii=False))["passed"]


def test_tool_specs_valid():
    names = [t["name"] for t in tool_specs()]
    assert len(names) == len(set(names))
    for t in tool_specs():
        assert t["parameters"]["type"] == "object"


def test_call_tool_ok_and_error():
    out = json.loads(call_tool("bonus_tax", {"bonus": 36001}))
    assert out["values"]["tax"] == 3390.10
    assert "error" in json.loads(call_tool("bonus_tax", {}))
    assert "error" in json.loads(call_tool("nope", {}))
    out = json.loads(
        call_tool(
            "check_probation", {"term_kind": "fixed", "term_months": 12, "probation_months": 3}
        )
    )
    assert out["ok"] is False


def test_city_policy_tool():
    out = json.loads(call_tool("get_city_policy", {"city": "上海"}))
    assert out["housing_fund"]["rate_max"] == 0.07


def test_oracle_run(tmp_path):
    from laborcheck.cities import load_city
    from laborcheck.evalset.generate import generate

    items = generate(cities=[load_city("shanghai")])[:20]
    spec = ModelSpec("oracle", "oracle", "offline", "oracle")
    res = asyncio.run(run(spec, items, run_dir=tmp_path / "r", with_tools=False, budget_usd=1))
    assert res["done"] == 20
    recs = latest_records(tmp_path / "r")
    assert all(r["passed"] for r in recs)
    # 续跑不重复
    asyncio.run(run(spec, items, run_dir=tmp_path / "r", with_tools=False, budget_usd=1))
    assert len((tmp_path / "r" / "results.jsonl").read_text().splitlines()) == 20


def test_client_offline():
    reply = asyncio.run(
        Client(ModelSpec("o", "o", "offline", "o")).ask(
            "s", "u", with_tools=False, item={"expected": {"verdict": "合法", "basis": []}}
        )
    )
    assert json.loads(reply.text)["verdict"] == "合法"
