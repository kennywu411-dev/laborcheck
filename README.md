# laborcheck

中国劳动合同与 Offer 的规则引擎，以及一个用来测量大模型在这类问题上准确率的公开评测集。

劳动合同和 offer 里有很多确定性的法条规则和本地计算：试用期上限、违约金能不能约定、竞业限制补偿、加班费、经济补偿封顶、各城市社保公积金基数、年终奖计税……这些问题有唯一正确的答案，但答案依赖具体条文和当年的地方数据。本项目要回答两个问题：

1. 主流大模型凭自身知识回答这类问题，准确率有多高、错在哪里？
2. 让模型调用一个带依据的确定性规则引擎，能提升多少？

> **声明**：本项目仅用于研究和学习，不构成法律意见。法律法规和地方政策会调整，具体问题请以官方最新文件为准并咨询专业人士。

## 主要结果

在 339 题评测集上（详见 [docs/results.md](docs/results.md) 和 [技术报告草稿](docs/technical-report.md)）：

| 模型 | 仅模型 | 模型 + 规则引擎工具 | 需要地方数据的 80 题（仅模型 → 加工具） |
|---|---|---|---|
| Claude Opus 5.5 | 87.6% | 98.8% | 47.5% → 98.8% |
| Claude Sonnet 5.5 | 85.3% | 100% | 43.8% → 100% |
| Claude Haiku 5.5 | 82.6% | 99.7% | 35.0% → 100% |

只依赖全国性法律的题目，强模型已经很少出错；错误集中在当年的地方标准上（最低工资、社保公积金基数），模型普遍还在用往年的数字。把同类参数写进题面后，三档全部答对。DeepSeek-V4-Pro 和通义 Qwen3.8-Max 只做了 26 题小样本试跑，呈现相同模式。

## 内容

| 部分 | 位置 | 说明 |
|---|---|---|
| 规则引擎 | `src/laborcheck/rules/` | Python，确定性计算，每个结论附法条或政策文件依据 |
| 依据登记 | `src/laborcheck/data/sources.toml` | 全国性法律法规、司法解释、税收政策，均对照官方原文核对 |
| 城市数据 | `src/laborcheck/data/cities/` | 北京、上海、深圳、杭州的社保、公积金、最低工资等，每个数字附官方链接、文号、适用期间和原文摘录 |
| 评测集 | `data/laborcheck-v1.jsonl` | 合同和 offer 均为构造；标准答案由引擎计算并经人工抽检；分开发集和留出集 |
| 测评工具 | `src/laborcheck/bench/` | 统一提示、打分、工具调用、花费上限、逐题记录 |
| 文档 | `docs/` | 数据来源、评测集说明、抽检记录、测评结果、技术报告 |

## 规则覆盖

- 试用期：期限上限与合同期限的关系、只能约定一次、仅约定试用期不成立、试用期工资下限、违法约定的赔偿金
- 违约金：只能在服务期和竞业限制两种情形约定；服务期违约金按未履行期间分摊培训费
- 竞业限制：人员范围、期限不超过二年、未约定补偿时的法定标准（含深圳地方标准）、单位提前解除时的额外三个月补偿
- 加班费：工作日、休息日、法定节假日三档倍数；月计薪天数 21.75
- 经济补偿：N 的计算、社平工资三倍封顶与十二年上限、最低工资保底、违法解除二倍赔偿、代通知金
- 未签书面劳动合同的二倍工资
- 年休假：天数、新入职折算、未休年假工资
- 社保公积金：个人和单位缴费，基数上下限截取
- 个税：综合所得、累计预扣法、全年一次性奖金单独计税与并入比较、税后到手收入、多城市 offer 对比

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run pytest -q
```

查看某个城市的数据和来源：

```bash
uv run laborcheck city shanghai
```

在代码里使用引擎：

```python
from decimal import Decimal
from laborcheck.cities import load_city
from laborcheck.rules import payroll, probation
from laborcheck.rules.probation import TermKind

f = probation.check_probation(TermKind.FIXED, 12, 3)
print(f.ok, f.message, f.citations())
# False 12 个月的固定期限劳动合同试用期最长 2 个月，约定 3 个月超出 1 个月，违法。 ['《中华人民共和国劳动合同法》第十九条']

sh = load_city("shanghai")
print(payroll.annual_take_home(20000, sh, hf_rate=Decimal("0.07")).message)
```

## 运行测评

1. 复制 `.env.example` 为 `.env`，填入你有的模型 API key（`.env` 已被 git 忽略）。
2. 核对 `configs/models.toml` 里的模型名和价格。
3. 先小样本试跑，再估算全量花费：

```bash
uv run laborcheck models
uv run laborcheck run --model deepseek --per-topic 1 --budget 0.5
uv run laborcheck estimate --run runs/<运行ID>
uv run laborcheck run --model deepseek --split all --budget 3
uv run laborcheck run --model deepseek --split all --tools --budget 3
uv run laborcheck report runs/<运行ID1> runs/<运行ID2>
```

每次运行在 `runs/<运行ID>/` 下保存配置快照和逐题结果，可以断点续跑（`--run-id` 指定同一个 ID）。

## 评测数据格式

每行一个 JSON：

```json
{"id": "lc-0001", "input": {"question": "...", "format": "verdict|amount|rewrite", "city": "shanghai"},
 "expected": {"verdict": "违法", "amount": 1080.0, "tolerance": 1.0, "constraint": {...}, "basis": ["《...》第十九条"]},
 "tags": {"type": "legality|calc|rewrite", "topic": "probation_term", "city": "...",
          "needs_local_data": false, "params_given": false, "trap": "boundary"},
 "split": "dev|holdout"}
```

## 许可

代码采用 MIT 许可。评测题目为构造内容，可自由用于研究。
