"""模型调用：OpenAI 兼容接口和 Anthropic 接口，支持工具调用循环。"""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from laborcheck.bench.tools import call_tool, tool_specs

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 16000


@dataclass(frozen=True)
class ModelSpec:
    name: str
    display: str
    api: str
    model: str
    key_env: str = ""
    base_url: str = ""
    input_price: float = 0.0
    output_price: float = 0.0
    effort: str = ""
    cache_read_price: float = 0.0  # 美元 / 百万 token；缓存写入按输入价的 1.25 倍计

    def cost(
        self, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0
    ) -> float:
        return (
            tokens_in * self.input_price
            + tokens_out * self.output_price
            + cache_read * self.cache_read_price
            + cache_write * self.input_price * 1.25
        ) / 1_000_000

    def has_key(self) -> bool:
        return self.api == "offline" or bool(os.environ.get(self.key_env))


@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def load_env(path: Path = Path(".env")) -> None:
    """读取 .env（只设置尚未存在的环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v


def load_models(path: Path = Path("configs/models.toml")) -> dict[str, ModelSpec]:
    raw = tomllib.loads(path.read_text("utf-8"))
    specs = {name: ModelSpec(name=name, **cfg) for name, cfg in raw.items()}
    specs["oracle"] = ModelSpec("oracle", "标准答案（流程自检）", "offline", "oracle")
    return specs


class Client:
    """对单个模型的调用封装。"""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        if spec.api == "openai":
            import openai

            self._c = openai.AsyncOpenAI(
                api_key=os.environ[spec.key_env], base_url=spec.base_url, max_retries=4, timeout=300
            )
        elif spec.api == "anthropic":
            import anthropic

            self._c = anthropic.AsyncAnthropic(
                api_key=os.environ[spec.key_env], max_retries=4, timeout=600
            )
        else:
            self._c = None

    async def ask(
        self, system: str, user: str, *, with_tools: bool, item: dict | None = None
    ) -> Reply:
        t0 = time.perf_counter()
        try:
            if self.spec.api == "offline":
                reply = _oracle(item)
            elif self.spec.api == "openai":
                reply = await self._ask_openai(system, user, with_tools)
            else:
                reply = await self._ask_anthropic(system, user, with_tools)
        except Exception as e:  # 记录失败，不中断整轮测评
            reply = Reply(text="", error=f"{type(e).__name__}: {e}")
        reply.latency_s = round(time.perf_counter() - t0, 2)
        reply.cost_usd = self.spec.cost(
            reply.tokens_in, reply.tokens_out, reply.cache_read, reply.cache_write
        )
        return reply

    async def _ask_openai(self, system: str, user: str, with_tools: bool) -> Reply:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        tools = [{"type": "function", "function": t} for t in tool_specs()] if with_tools else None
        reply = Reply(text="")
        for _ in range(MAX_TOOL_ROUNDS + 1):
            kwargs: dict[str, Any] = {"model": self.spec.model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            resp = await self._c.chat.completions.create(**kwargs)
            if resp.usage:
                reply.tokens_in += resp.usage.prompt_tokens or 0
                reply.tokens_out += resp.usage.completion_tokens or 0
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                reply.text = msg.content or ""
                return reply
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = call_tool(c.function.name, args)
                reply.trace.append({"tool": c.function.name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
        reply.error = "tool_rounds_exceeded"
        return reply

    async def _ask_anthropic(self, system: str, user: str, with_tools: bool) -> Reply:
        # 测评要记录被测模型自己的回答，所以不启用拒答时自动切换到其他模型的 fallbacks；
        # stop_reason 为 refusal 的题按答错处理并在 trace 中标出。
        messages: list[Any] = [{"role": "user", "content": user}]
        tools = (
            [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tool_specs()
            ]
            if with_tools
            else None
        )
        reply = Reply(text="")
        for _ in range(MAX_TOOL_ROUNDS + 1):
            kwargs: dict[str, Any] = {
                "model": self.spec.model,
                "max_tokens": MAX_TOKENS,
                # 渲染顺序为 tools -> system：在系统提示上打缓存标记，工具定义和系统提示一起缓存
                "system": [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ],
                "messages": messages,
            }
            if self.spec.effort:
                kwargs["output_config"] = {"effort": self.spec.effort}
            if tools:
                kwargs["tools"] = tools
            async with self._c.messages.stream(**kwargs) as stream:
                resp = await stream.get_final_message()
            reply.tokens_in += resp.usage.input_tokens
            reply.tokens_out += resp.usage.output_tokens
            reply.cache_read += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            reply.cache_write += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
            if resp.stop_reason == "refusal":
                reply.trace.append({"stop_reason": "refusal"})
                reply.text = ""
                return reply
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                reply.text = "".join(b.text for b in resp.content if b.type == "text")
                return reply
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in uses:
                result = call_tool(b.name, dict(b.input))
                reply.trace.append({"tool": b.name, "args": dict(b.input), "result": result})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": result})
            messages.append({"role": "user", "content": results})
        reply.error = "tool_rounds_exceeded"
        return reply


def _oracle(item: dict | None) -> Reply:
    """离线自检：直接输出标准答案，用来验证打分和记录流程。"""
    assert item is not None
    exp = item["expected"]
    out = {
        "verdict": exp.get("verdict"),
        "amount": exp.get("amount"),
        "key_value": exp.get("constraint", {}).get("value"),
        "rewrite": "（标准答案自检）" if "constraint" in exp else None,
        "basis": exp["basis"],
    }
    return Reply(text=json.dumps(out, ensure_ascii=False))
