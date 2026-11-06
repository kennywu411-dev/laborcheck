"""命令行入口。

laborcheck gen                         生成评测集
laborcheck city beijing                查看城市数据及来源
laborcheck models                      列出模型及 .env 中是否有 key
laborcheck run --model deepseek ...    运行测评
laborcheck estimate --run runs/<id>    用试跑结果估算全量花费
laborcheck report runs/<id> ...        汇总结果
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from laborcheck.cities import CITY_CODES, load_city

DATASET = Path("data/laborcheck-v1.jsonl")


def cmd_gen(a: argparse.Namespace) -> None:
    from laborcheck.evalset.generate import generate, write

    items = generate(seed=a.seed)
    write(items, Path(a.out))
    from collections import Counter

    c = Counter((it["tags"]["type"], it["split"]) for it in items)
    print(f"共 {len(items)} 题 -> {a.out}")
    for (t, s), n in sorted(c.items()):
        print(f"  {t:9s} {s:8s} {n}")


def cmd_city(a: argparse.Namespace) -> None:
    c = load_city(a.code)
    print(f"{c.name}  {c.period}")
    for k, v in c.sources.items():
        print(f"  {k}: {v.get('quote') or ''}  [{v.get('doc_name', '')}] {v.get('doc_url', '')}")
    for n in c.notes:
        print(f"  注：{n}")


def cmd_sources(a: argparse.Namespace) -> None:
    """输出数据来源文档（Markdown）。"""
    from laborcheck.cities import all_cities
    from laborcheck.core import sources

    out = [
        "# 数据来源",
        "",
        "本文件由 `uv run laborcheck sources` 根据数据文件生成，请勿手工修改。",
        "",
        "## 全国性法律法规与政策",
        "",
        "| 文件 | 版本 | 核对状态 |",
        "|---|---|---|",
    ]
    for s in sources().values():
        status = "已核对" if s["status"] == "verified" else "待核实"
        out.append(f"| [{s['name']}]({s['url']}) | {s.get('version', '')} | {status} |")
    labels = {
        "min_wage_monthly": "月最低工资",
        "min_wage_hourly": "小时最低工资",
        "avg_monthly_wage": "经济补偿封顶用月平均工资",
        "hf_base_lower": "公积金基数下限",
        "hf_base_upper": "公积金基数上限",
        "hf_rate_min": "公积金比例下限",
        "hf_rate_max": "公积金比例上限",
        "noncompete_min_ratio": "竞业限制补偿最低比例",
    }
    for c in all_cities():
        out += [
            "",
            f"## {c.name}",
            "",
            f"适用期间：{c.period}",
            "",
            "| 项目 | 数值 | 适用期间 | 来源 | 原文摘录 |",
            "|---|---|---|---|---|",
        ]
        for key, label in labels.items():
            v = getattr(c, key)
            if v is None:
                out.append(f"| {label} | 待核实（未找到官方来源，引擎不提供） | | | |")
                continue
            src = c.source_of(key)
            mark = "（推算值）" if src.get("derived") == "True" else ""
            out.append(
                f"| {label} | {v}{mark} | {src.get('period', '')} | "
                f"[{src.get('doc_name', '')}]({src.get('doc_url', '')}) | {src.get('quote', '')} |"
            )
        out += [
            "",
            "| 社保险种 | 基数下限 | 基数上限 | 个人比例 | 单位比例 | 说明 | 来源 |",
            "|---|---|---|---|---|---|---|",
        ]
        for it in c.si_items:
            src = c.sources[f"si.{it.code}"]
            fixed = f" + {it.employee_fixed} 元/月" if it.employee_fixed else ""
            out.append(
                f"| {it.name} | {it.base_lower} | {it.base_upper} | {it.employee_rate:%}{fixed} | "
                f"{it.employer_rate:%} | {src.get('period', '')} | "
                f"[{src.get('doc_name', '')}]({src.get('doc_url', '')}) |"
            )
        if c.notes:
            out += ["", "说明：", ""] + [f"- {n}" for n in c.notes]
        docs = {}
        for v in c.sources.values():
            if v.get("doc_url"):
                docs[v["doc_url"]] = v.get("doc_name", "")
    text = "\n".join(out) + "\n"
    if a.out:
        Path(a.out).write_text(text, "utf-8")
    else:
        print(text)


def cmd_models(a: argparse.Namespace) -> None:
    from laborcheck.bench.providers import load_env, load_models

    load_env()
    for name, spec in load_models().items():
        mark = "有 key" if spec.has_key() else f"缺 {spec.key_env}"
        print(f"  {name:13s} {spec.display:28s} {mark}")


def cmd_run(a: argparse.Namespace) -> None:
    from laborcheck.bench.providers import load_env, load_models
    from laborcheck.bench.runner import dataset_hash, load_dataset, new_run_id, run, select

    load_env()
    spec = load_models()[a.model]
    if not spec.has_key():
        sys.exit(f"缺少 {spec.key_env}，请写入 .env")
    data_path = Path(a.dataset)
    items = select(
        load_dataset(data_path), split=a.split, per_topic=a.per_topic, limit=a.limit, seed=a.seed
    )
    mode = "tools" if a.tools else "closed"
    run_dir = Path(a.runs) / (a.run_id or new_run_id(a.model, mode))
    meta = {
        "dataset": str(data_path),
        "dataset_sha256_12": dataset_hash(data_path),
        "split": a.split,
        "per_topic": a.per_topic,
        "limit": a.limit,
        "seed": a.seed,
    }
    print(f"{spec.display}（{mode}）{len(items)} 题，花费上限 ${a.budget} -> {run_dir}")
    res = asyncio.run(
        run(
            spec,
            items,
            run_dir=run_dir,
            with_tools=a.tools,
            budget_usd=a.budget,
            concurrency=a.concurrency,
            meta=meta,
        )
    )
    print(json.dumps(res, ensure_ascii=False))


def cmd_estimate(a: argparse.Namespace) -> None:
    from laborcheck.bench.runner import latest_records, load_dataset, select

    recs = latest_records(Path(a.run))
    ok = [r for r in recs if r["status"] == "ok"]
    if not ok:
        sys.exit("试跑没有成功的题")
    per = sum(r["cost_usd"] for r in ok) / len(ok)
    n = len(select(load_dataset(Path(a.dataset)), split=a.split))
    print(f"试跑 {len(ok)} 题，平均每题 ${per:.5f}；{a.split} 共 {n} 题，预估 ${per * n:.2f}")


def cmd_report(a: argparse.Namespace) -> None:
    from laborcheck.bench.report import breakdown_table, markdown_table, summarize

    sums = [summarize(Path(p)) for p in a.runs]
    out = [
        markdown_table(sums),
        "",
        breakdown_table(sums, "data"),
        "",
        breakdown_table(sums, "topic"),
    ]
    text = "\n".join(out)
    if a.json:
        Path(a.json).write_text(json.dumps(sums, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(text)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="laborcheck", description="劳动合同与 Offer 规则引擎和评测")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="生成评测集")
    g.add_argument("--seed", type=int, default=20260)
    g.add_argument("--out", default=str(DATASET))
    g.set_defaults(fn=cmd_gen)

    c = sub.add_parser("city", help="查看城市数据及来源")
    c.add_argument("code", choices=CITY_CODES)
    c.set_defaults(fn=cmd_city)

    so = sub.add_parser("sources", help="输出数据来源文档")
    so.add_argument("--out", default=None)
    so.set_defaults(fn=cmd_sources)

    m = sub.add_parser("models", help="列出模型")
    m.set_defaults(fn=cmd_models)

    r = sub.add_parser("run", help="运行测评")
    r.add_argument("--model", required=True)
    r.add_argument("--tools", action="store_true", help="允许模型调用规则引擎工具")
    r.add_argument("--dataset", default=str(DATASET))
    r.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    r.add_argument("--per-topic", type=int, default=None, help="每个 (主题, 题型) 抽几题，用于试跑")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--budget", type=float, required=True, help="本次运行花费上限（美元）")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--runs", default="runs")
    r.add_argument("--run-id", default=None, help="指定运行 ID（用于续跑）")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("estimate", help="用试跑结果估算全量花费")
    e.add_argument("--run", required=True)
    e.add_argument("--dataset", default=str(DATASET))
    e.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    e.set_defaults(fn=cmd_estimate)

    rp = sub.add_parser("report", help="汇总结果")
    rp.add_argument("runs", nargs="+")
    rp.add_argument("--json", default=None)
    rp.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
