"""用假的 SDK 客户端测试工具调用循环，不发真实请求。"""

import asyncio
import json
from types import SimpleNamespace as NS

from laborcheck.bench.providers import Client, ModelSpec


class FakeOpenAI:
    def __init__(self):
        self.calls = []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            call = NS(id="c1", function=NS(name="bonus_tax", arguments='{"bonus": 36001}'))
            msg = NS(content="", tool_calls=[call])
        else:
            assert kw["messages"][-1]["role"] == "tool"
            msg = NS(content='{"amount": 3390.10}', tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=100, completion_tokens=20))


def test_openai_tool_loop():
    spec = ModelSpec("x", "x", "offline", "m", input_price=1.0, output_price=2.0)
    c = Client(spec)
    c.spec = ModelSpec("x", "x", "openai", "m", input_price=1.0, output_price=2.0)
    c._c = FakeOpenAI()
    r = asyncio.run(c.ask("s", "u", with_tools=True))
    assert r.error is None
    assert json.loads(r.text)["amount"] == 3390.10
    assert r.trace[0]["tool"] == "bonus_tax"
    assert json.loads(r.trace[0]["result"])["values"]["tax"] == 3390.10
    assert r.tokens_in == 200 and r.tokens_out == 40
    assert abs(r.cost_usd - (200 * 1 + 40 * 2) / 1e6) < 1e-12


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self.msg


class FakeAnthropic:
    def __init__(self, refuse=False):
        self.n = 0
        self.refuse = refuse
        self.messages = NS(stream=self.stream)

    def stream(self, **kw):
        self.n += 1
        usage = NS(
            input_tokens=50,
            output_tokens=10,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        if self.refuse:
            return FakeStream(NS(stop_reason="refusal", content=[], usage=usage))
        if self.n == 1:
            assert "temperature" not in kw
            block = NS(
                type="tool_use",
                id="t1",
                name="check_probation",
                input={"term_kind": "fixed", "term_months": 12, "probation_months": 3},
            )
            return FakeStream(NS(stop_reason="tool_use", content=[block], usage=usage))
        assert kw["messages"][-1]["content"][0]["type"] == "tool_result"
        text = NS(type="text", text='{"verdict": "违法"}')
        return FakeStream(NS(stop_reason="end_turn", content=[text], usage=usage))


def _anthropic_client(fake):
    c = Client(ModelSpec("x", "x", "offline", "m"))
    c.spec = ModelSpec("x", "x", "anthropic", "claude-opus-5-5", effort="medium")
    c._c = fake
    return c


def test_anthropic_tool_loop():
    r = asyncio.run(_anthropic_client(FakeAnthropic()).ask("s", "u", with_tools=True))
    assert json.loads(r.text)["verdict"] == "违法"
    assert json.loads(r.trace[0]["result"])["ok"] is False


def test_anthropic_refusal_recorded():
    r = asyncio.run(_anthropic_client(FakeAnthropic(refuse=True)).ask("s", "u", with_tools=False))
    assert r.text == "" and r.trace == [{"stop_reason": "refusal"}]
