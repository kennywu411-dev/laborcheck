"""测评运行：逐题落盘、断点续跑、花费上限、配置快照。

运行目录 runs/<run_id>/：
- config.json   本次运行的模型配置、提示词、数据集版本、抽样方式
- results.jsonl 每题一行：输出、打分、token、花费、耗时、工具调用记录
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from laborcheck.bench import prompts
from laborcheck.bench.providers import Client, ModelSpec
from laborcheck.bench.scoring import score


def load_dataset(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def select(
    items: list[dict],
    *,
    split: str = "dev",
    per_topic: int | None = None,
    limit: int | None = None,
    seed: int = 0,
) -> list[dict]:
    """按 split 过滤；per_topic 给出时按 (topic, type) 分层抽样，用于小样本试跑。"""
    pool = [it for it in items if split == "all" or it["split"] == split]
    if per_topic:
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for it in pool:
            groups[(it["tags"]["topic"], it["tags"]["type"])].append(it)
        rng = random.Random(seed)
        pool = []
        for key in sorted(groups):
            g = groups[key]
            pool.extend(rng.sample(g, min(per_topic, len(g))))
        pool.sort(key=lambda it: it["id"])
    return pool[:limit] if limit else pool


def new_run_id(model: str, mode: str) -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{model}-{mode}"


def _done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text("utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("status") == "ok":
            out.add(rec["item_id"])
    return out


async def run(
    spec: ModelSpec,
    items: list[dict],
    *,
    run_dir: Path,
    with_tools: bool,
    budget_usd: float,
    concurrency: int = 4,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    cfg_path = run_dir / "config.json"
    system = prompts.system_prompt(with_tools)
    if not cfg_path.exists():
        cfg = {
            "run_id": run_dir.name,
            "model": asdict(spec),
            "mode": "tools" if with_tools else "closed",
            "system_prompt": system,
            "format_hints": prompts.FORMAT_HINT,
            "budget_usd": budget_usd,
            "item_ids": [it["id"] for it in items],
            **(meta or {}),
        }
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", "utf-8")

    done = _done_ids(results_path)
    todo = [it for it in items if it["id"] not in done]
    client = Client(spec)
    spent = _spent(results_path)
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    stopped = False

    async def one(item: dict) -> None:
        nonlocal spent, stopped
        async with sem:
            if spent >= budget_usd:
                stopped = True
                return
            reply = await client.ask(
                system, prompts.user_prompt(item), with_tools=with_tools, item=item
            )
            sc = (
                score(item, reply.text)
                if reply.error is None
                else {"passed": False, "detail": {"reason": "error"}, "basis_hit": None}
            )
            rec = {
                "item_id": item["id"],
                "split": item["split"],
                "tags": item["tags"],
                "status": "error" if reply.error else "ok",
                "error": reply.error,
                "passed": sc["passed"],
                "basis_hit": sc["basis_hit"],
                "detail": sc["detail"],
                "output": reply.text,
                "tokens_in": reply.tokens_in,
                "tokens_out": reply.tokens_out,
                "cache_read": reply.cache_read,
                "cache_write": reply.cache_write,
                "cost_usd": round(reply.cost_usd, 6),
                "latency_s": reply.latency_s,
                "trace": reply.trace,
            }
            async with lock:
                spent += reply.cost_usd
                with results_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(it) for it in todo))
    return {
        "run_dir": str(run_dir),
        "done": len(_done_ids(results_path)),
        "total": len(items),
        "spent_usd": round(spent, 4),
        "stopped_by_budget": stopped,
    }


def _spent(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(json.loads(line).get("cost_usd", 0) for line in path.read_text("utf-8").splitlines())


def latest_records(run_dir: Path) -> list[dict[str, Any]]:
    """每题取最后一条记录（续跑时出错的题会被重跑）。"""
    recs: dict[str, dict] = {}
    path = run_dir / "results.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{run_dir} 没有结果（可能被花费上限拦下，或尚未运行）")
    for line in (run_dir / "results.jsonl").read_text("utf-8").splitlines():
        rec = json.loads(line)
        recs[rec["item_id"]] = rec
    return [recs[k] for k in sorted(recs)]
