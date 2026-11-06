"""打分：解析模型输出末尾的 JSON，与标准答案比较。"""

from __future__ import annotations

import json
import re
from typing import Any

_ARTICLE = re.compile(r"第[一二三四五六七八九十百零〇]+条")


def extract_json(text: str) -> dict[str, Any] | None:
    """取输出中最后一个能解析的 JSON 对象。"""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    ends = [i for i, ch in enumerate(text) if ch == "}"]
    for end in reversed(ends):
        depth = 0
        for start in range(end, -1, -1):
            ch = text[start]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
    return None


def to_number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    s = str(v).replace(",", "").replace("，", "").replace("元", "").replace("天", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def norm_verdict(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    if "不合法" in s or "违法" in s or "无效" in s:
        return "违法"
    if "合法" in s or "有效" in s:
        return "合法"
    return None


def _constraint_ok(value: float | None, c: dict[str, Any]) -> bool:
    if value is None:
        return False
    target = float(c["value"])
    ok = value <= target + 1e-9 if c["op"] == "<=" else value >= target - 1e-9
    if "min" in c and value < float(c["min"]) - 1e-9:
        ok = False
    if "max" in c and value > float(c["max"]) + 1e-9:
        ok = False
    return ok


def basis_hit(pred: dict[str, Any] | None, expected: dict[str, Any]) -> bool | None:
    """依据命中：标准依据中出现的条号，模型给出的依据里至少命中一条。没有条号的题返回 None。"""
    want = set()
    for cite in expected.get("basis", []):
        want.update(_ARTICLE.findall(cite))
    if not want:
        return None
    if not pred:
        return False
    got = pred.get("basis") or []
    text = " ".join(map(str, got)) if isinstance(got, list) else str(got)
    return any(a in text for a in want)


def score(item: dict[str, Any], output: str) -> dict[str, Any]:
    exp = item["expected"]
    fmt = item["input"]["format"]
    pred = extract_json(output)
    detail: dict[str, Any] = {"parsed": pred is not None}
    if pred is None:
        return {
            "passed": False,
            "detail": {**detail, "reason": "no_json"},
            "basis_hit": basis_hit(None, exp),
        }
    if fmt == "verdict":
        v = norm_verdict(pred.get("verdict"))
        passed = v == exp["verdict"]
        detail.update(pred_verdict=v)
    elif fmt == "amount":
        a = to_number(pred.get("amount"))
        passed = a is not None and abs(a - exp["amount"]) <= exp["tolerance"] + 1e-9
        detail.update(pred_amount=a, error=None if a is None else round(a - exp["amount"], 2))
    else:
        v = norm_verdict(pred.get("verdict"))
        kv = to_number(pred.get("key_value"))
        verdict_ok = v == exp["verdict"]
        value_ok = _constraint_ok(kv, exp["constraint"])
        has_text = bool(str(pred.get("rewrite") or "").strip())
        passed = verdict_ok and value_ok and has_text
        detail.update(
            pred_verdict=v,
            pred_key_value=kv,
            verdict_ok=verdict_ok,
            value_ok=value_ok,
            has_rewrite=has_text,
        )
    return {"passed": passed, "detail": detail, "basis_hit": basis_hit(pred, exp)}
