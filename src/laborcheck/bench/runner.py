"""测评运行：由评测平台 evalkit 负责并发、逐题落盘、断点续跑和花费上限，本模块只接上模型调用和打分。

运行目录 runs/<run_id>/：
- config.json   本次运行的模型配置、提示词、数据集版本和内容哈希、抽样方式、每题内容哈希
- results.jsonl 每题一行：evalkit 字段（variant、scores 等）加上原有字段
                （输出、打分、token、花费、耗时、工具调用记录），原有的汇总代码照常读取
- summary.json  evalkit 的汇总

工具调用记录照旧放在结果行的 trace 里，不另写 trace 文件（运行目录纳入版本库）。
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalkit import store
from evalkit.dataset import Dataset, Item
from evalkit.runner import TaskResult, Variant
from evalkit.runner import arun as ek_arun
from evalkit.scorers import Score

from laborcheck.bench import prompts
from laborcheck.bench.providers import Client, ModelSpec
from laborcheck.bench.scoring import score

SCORER = "laborcheck"
# evalkit 自己写进 config.json 的字段；其余字段是本项目的配置快照，续跑时沿用第一次的
EVALKIT_CONFIG_KEYS = {
    "run_id",
    "tag",
    "created_at",
    "dataset",
    "variants",
    "scorers",
    "primary_metric",
    "repeats",
    "concurrency",
    "max_cost",
    "item_ids",
    "item_hashes",
    "code_version",
    "python",
}


def load_dataset(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def dataset_version(path: Path) -> str:
    meta = path.with_name(path.stem + ".meta.json")
    return json.loads(meta.read_text("utf-8")).get("version", "0.0.0") if meta.exists() else "0.0.0"


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


class LaborScorer:
    """evalkit 打分器：沿用 scoring.score。模型调用出错的题记 status=error，续跑时重跑。"""

    name = SCORER

    def __init__(self, items: dict[str, dict]):
        self.items = items

    def __call__(self, item: Item, output: Any) -> Score:
        text, err = (
            (output.get("text", ""), output.get("error"))
            if isinstance(output, dict)
            else (output, None)
        )
        if err is not None:
            detail = {"passed": False, "detail": {"reason": "error"}, "basis_hit": None}
            return Score(0.0, False, detail, status="error")
        sc = score(self.items[item.id], text or "")
        return Score(float(sc["passed"]), sc["passed"], sc)


def as_dataset(items: list[dict], version: str = "0.0.0") -> Dataset:
    return Dataset(
        "laborcheck",
        [Item(it["id"], it["input"], it["expected"], it["tags"], it["split"]) for it in items],
        version,
    )


def _legacy_fields(item: Item, row: dict) -> dict[str, Any]:
    """原有结果行字段（汇总、典型错误、估算都读这些）。"""
    sc = row["scores"].get(SCORER, {}).get("detail") or {}
    out = row["output"]
    return {
        # 任务本身抛异常时（不经过模型调用）evalkit 不带这些字段，补上默认值
        **{k: row.get(k, 0) for k in ("tokens_in", "tokens_out", "cache_read", "cache_write")},
        "trace": row.get("trace", []),
        "status": "error" if row.get("error") is not None else row["status"],
        "passed": bool(sc.get("passed", False)),
        "basis_hit": sc.get("basis_hit"),
        "detail": sc.get("detail", {"reason": "error"}),
        "output": out.get("text", "") if isinstance(out, dict) else out,
    }


async def run(
    spec: ModelSpec,
    items: list[dict],
    *,
    run_dir: Path,
    with_tools: bool,
    budget_usd: float,
    concurrency: int = 4,
    meta: dict[str, Any] | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """budget_usd 是这个运行目录的累计花费上限（续跑前已花的也算），出错的题也计花费。"""
    results = run_dir / store.RESULTS
    first = results.read_text("utf-8").split("\n", 1)[0] if results.exists() else ""
    if first.strip() and "variant" not in json.loads(first):
        raise SystemExit(f"{run_dir.name} 是接入 evalkit 之前的运行格式，不能续跑，请换一个运行 ID")
    system = prompts.system_prompt(with_tools)
    mode = "tools" if with_tools else "closed"
    prev = store.load_config(run_dir)
    snapshot = (
        {k: v for k, v in prev.items() if k not in EVALKIT_CONFIG_KEYS}
        if prev
        else {
            "model": asdict(spec),
            "mode": mode,
            "system_prompt": system,
            "format_hints": prompts.FORMAT_HINT,
            "budget_usd": budget_usd,
            **(meta or {}),
        }
    )
    # evalkit 续跑时只累计每题最后一条记录的花费；被重跑的出错记录也花了钱，补算进上限
    lines = results.read_text("utf-8").splitlines() if results.exists() else []
    superseded = sum(json.loads(x).get("cost_usd", 0) for x in lines if x.strip()) - sum(
        r.get("cost_usd", 0) for r in store.load_rows(run_dir)
    )
    by_id = {it["id"]: it for it in items}
    client = Client(spec)

    async def task(item: Item) -> TaskResult:
        raw = by_id[item.id]
        reply = await client.ask(system, prompts.user_prompt(raw), with_tools=with_tools, item=raw)
        usage = {
            "tokens_in": reply.tokens_in,
            "tokens_out": reply.tokens_out,
            "cache_read": reply.cache_read,
            "cache_write": reply.cache_write,
        }
        return TaskResult(
            {"text": reply.text, "error": reply.error},
            reply.cost_usd,
            usage,
            meta={
                "error": reply.error,
                **usage,
                "cost_usd": round(reply.cost_usd, 6),
                "latency_s": reply.latency_s,
                "trace": reply.trace,
            },
        )

    res = await ek_arun(
        as_dataset(items, (meta or {}).get("dataset_version", "0.0.0")),
        [Variant(f"{spec.name}-{mode}", task, {"model": spec.model, "mode": mode})],
        [LaborScorer(by_id)],
        runs_dir=run_dir.parent,
        tag=run_dir.name,
        resume=run_dir.name,
        concurrency=concurrency,
        max_cost=budget_usd - superseded,
        extra_config=snapshot,
        progress=progress,
        row_fields=_legacy_fields,
        save_traces=False,
    )
    ok = {r["item_id"] for r in res.rows if r["status"] == "ok"}
    return {
        "run_dir": str(run_dir),
        "done": len(ok),
        "total": len(items),
        "spent_usd": round(res.spent_usd + superseded, 4),
        "stopped_by_budget": res.skipped > 0,
    }


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
