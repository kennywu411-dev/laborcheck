"""汇总运行结果：准确率（Wilson 95% 区间）、按题型、主题和划分拆分、花费、典型错误；
两次运行的配对比较（McNemar）、回归门禁和看板。统计口径由评测平台 evalkit 统一提供。"""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from evalkit import store
from evalkit.compare import compare as ek_compare
from evalkit.stats import wilson

from laborcheck.bench.runner import SCORER, latest_records

TYPE_LABEL = {"legality": "合法性判断", "calc": "金额计算", "rewrite": "条款改写"}
TOPIC_LABEL = {
    "probation_term": "试用期期限",
    "probation_wage": "试用期工资",
    "penalty_kind": "违约金情形",
    "service_penalty": "服务期违约金",
    "noncompete": "竞业限制",
    "overtime": "加班费",
    "severance": "经济补偿",
    "double_wage": "二倍工资",
    "annual_leave": "年休假",
    "social_insurance": "社保",
    "housing_fund": "公积金",
    "bonus_tax": "年终奖个税",
    "withholding": "累计预扣",
    "take_home": "税后到手",
    "offer_compare": "多 offer 对比",
    "si_waiver": "放弃社保约定",
}


SPLIT_LABEL = {"dev": "开发集", "holdout": "留出集"}


def _acc(recs: list[dict]) -> dict[str, Any]:
    n = len(recs)
    k = sum(1 for r in recs if r["passed"])
    lo, hi = wilson(k, n)
    return {"n": n, "correct": k, "acc": k / n if n else 0.0, "ci": [lo, hi]}


def summarize(run_dir: Path) -> dict[str, Any]:
    cfg = json.loads((run_dir / "config.json").read_text("utf-8"))
    recs = latest_records(run_dir)
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in recs:
        t = r["tags"]
        by["type"][t["type"]].append(r)
        by["topic"][t["topic"]].append(r)
        by["split"][r["split"]].append(r)
        if t["params_given"]:
            by["data"]["题面给出地方参数"].append(r)
        elif t["needs_local_data"]:
            by["data"]["需要模型自己知道地方数据"].append(r)
        else:
            by["data"]["不依赖地方数据"].append(r)
    basis = [r["basis_hit"] for r in recs if r["basis_hit"] is not None]
    return {
        "run_id": cfg["run_id"],
        "model": cfg["model"]["display"],
        "mode": cfg["mode"],
        "overall": _acc(recs),
        "by": {dim: {k: _acc(v) for k, v in sorted(g.items())} for dim, g in by.items()},
        "basis_hit_rate": sum(basis) / len(basis) if basis else None,
        "errors": sum(1 for r in recs if r["status"] == "error"),
        "no_json": sum(1 for r in recs if r["detail"].get("reason") == "no_json"),
        "cost_usd": round(sum(r["cost_usd"] for r in recs), 4),
        "tokens_in": sum(r["tokens_in"] for r in recs),
        "tokens_out": sum(r["tokens_out"] for r in recs),
        "avg_latency_s": round(sum(r["latency_s"] for r in recs) / len(recs), 2) if recs else 0,
        "tool_calls": sum(len([x for x in r["trace"] if "tool" in x]) for r in recs),
    }


def _pct(x: dict[str, Any]) -> str:
    return f"{x['acc'] * 100:.1f}%（{x['correct']}/{x['n']}）"


def _cell(t: dict[str, Any], k: str) -> str:
    return _pct(t[k]) if k in t else "—"


def markdown_table(summaries: list[dict[str, Any]]) -> str:
    lines = [
        "| 运行 ID | 模型 | 模式 | 总准确率 | 95% 区间 | 合法性判断 | 金额计算 | 条款改写 | 依据命中 | 花费 (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        t = s["by"]["type"]
        lo, hi = s["overall"]["ci"]
        bh = f"{s['basis_hit_rate'] * 100:.0f}%" if s["basis_hit_rate"] is not None else "—"
        mode = "模型 + 规则引擎工具" if s["mode"] == "tools" else "仅模型"
        lines.append(
            f"| `{s['run_id']}` | {s['model']} | {mode} | {_pct(s['overall'])} | "
            f"{lo * 100:.0f}%–{hi * 100:.0f}% | {_cell(t, 'legality')} | {_cell(t, 'calc')} | "
            f"{_cell(t, 'rewrite')} | {bh} | {s['cost_usd']:.4f} |"
        )
    return "\n".join(lines)


def breakdown_table(summaries: list[dict[str, Any]], dim: str) -> str:
    keys = sorted({k for s in summaries for k in s["by"].get(dim, {})})
    labels = {"topic": TOPIC_LABEL, "split": SPLIT_LABEL}.get(dim, TYPE_LABEL)
    label = lambda k: labels.get(k, k)  # noqa: E731
    head = (
        "| "
        + {"topic": "主题", "data": "地方数据", "type": "题型", "split": "划分"}[dim]
        + " | "
        + " | ".join(
            f"{s['model']}（{'工具' if s['mode'] == 'tools' else '仅模型'}）" for s in summaries
        )
        + " |"
    )
    lines = [head, "|---" * (len(summaries) + 1) + "|"]
    for k in keys:
        cells = [(_pct(s["by"][dim][k]) if k in s["by"].get(dim, {}) else "—") for s in summaries]
        lines.append(f"| {label(k)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def error_cases(run_dir: Path, dataset: dict[str, dict], limit: int = 8) -> list[dict[str, Any]]:
    """挑典型错误：每个主题最多一条，优先依赖地方数据和带易错点标签的题。"""
    recs = [r for r in latest_records(run_dir) if not r["passed"] and r["status"] == "ok"]
    recs.sort(
        key=lambda r: (not r["tags"]["trap"], not r["tags"]["needs_local_data"], r["item_id"])
    )
    seen: set[str] = set()
    out = []
    for r in recs:
        topic = r["tags"]["topic"]
        if topic in seen:
            continue
        seen.add(topic)
        item = dataset[r["item_id"]]
        out.append(
            {
                "item_id": r["item_id"],
                "topic": topic,
                "trap": r["tags"]["trap"],
                "question": item["input"]["question"],
                "expected": item["expected"],
                "detail": r["detail"],
                "output_tail": r["output"][-600:],
            }
        )
        if len(out) >= limit:
            break
    return out


def evalkit_rows(run_dir: Path) -> list[dict[str, Any]]:
    """任意一次运行的 evalkit 结果行：新运行本身就带 evalkit 字段，接入前的运行现场补上。"""
    cfg = json.loads((run_dir / "config.json").read_text("utf-8"))
    variant = f"{cfg['model']['name']}-{cfg['mode']}"
    out = []
    for r in latest_records(run_dir):
        if "variant" not in r:
            r = r | {
                "variant": variant,
                "repeat": 0,
                "scores": {
                    SCORER: {
                        "value": float(r["passed"]),
                        "passed": r["passed"],
                        "detail": r["detail"],
                    }
                },
            }
        out.append(r)
    return out


def compare_runs(run_a: Path, run_b: Path, slices: tuple[str, ...] = ()) -> dict[str, Any]:
    """同一批题上 A → B 的配对比较：a_only = 只有 A 答对的题，b_only = 只有 B 答对的题。"""
    return ek_compare(evalkit_rows(run_a), evalkit_rows(run_b), slices=slices)


def mcnemar_table(pairs: list[tuple[Path, Path]]) -> str:
    """两种模式的配对比较表（docs/测评结果.md 的格式）：每对是 (仅模型, 加工具)。"""
    lines = [
        "| 模型 | 仅加工具后答对 | 仅不加工具时答对 | p 值 |",
        "|---|---|---|---|",
    ]
    for closed, tools in pairs:
        c = compare_runs(closed, tools)
        model = json.loads((closed / "config.json").read_text("utf-8"))["model"]["display"]
        lines.append(f"| {model} | {len(c['b_only'])} | {len(c['a_only'])} | {c['p_value']:.1e} |")
    return "\n".join(lines)


def dashboard(run_dirs: list[Path], output: Path) -> Path:
    """evalkit 静态看板；接入前的运行在临时目录里转换，原文件不改。"""
    from evalkit.dashboard import build

    tmp = Path(tempfile.mkdtemp(prefix="laborcheck-dashboard-")) / "laborcheck"
    dirs = []
    for rd in run_dirs:
        d = tmp / rd.name
        store.write_rows(d, evalkit_rows(rd))
        cfg = store.load_config(rd)
        if not isinstance(cfg.get("dataset"), dict):  # 接入前的运行只记了数据集路径
            cfg["dataset"] = {"name": "laborcheck", "hash": cfg.get("dataset_sha256_12")}
        store.write_json(d / store.CONFIG, cfg)
        dirs.append(d)
    return build(dirs, output, title="laborcheck 评测看板")
