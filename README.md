# task26100219

摄影图库智能集合的增量维护组件（纯 Python、内存运行、无外部服务）。

## 组成

- `smart_collections/model.py` — 规则与资产的数据模型（`RatingAtLeast` / `IsoGreaterThan` / `HasKeyword` 叶条件，`All` / `Any` / `Not` 组合，叶 ≤16、深度 ≤8 校验）。
- `smart_collections/engine.py` — `SmartCollections` 增量引擎（库）。
- `smart_collections/interpreter.py` — 独立全量解释器，测试基准。
- `tests/` — 随机交错差分测试 + 跨分支移动定向场景测试。
- `experiments/scale_experiment.py` — 可复现规模实验（计数器与内存峰值）。

## 快速上手

```python
from smart_collections import (
    All, Any, Not, Asset, CollectionDef,
    RatingAtLeast, IsoGreaterThan, HasKeyword, SmartCollections,
)

parents = {0: None, 1: 0, 2: 1}                 # 关键词树：child -> parent
assets = [Asset("a1", rating=4, iso=200, keywords=frozenset({2})),
          Asset("a2", rating=2, iso=None, keywords=frozenset({1}))]
cols = [CollectionDef("best", All((RatingAtLeast(3), HasKeyword(1)))),
        CollectionDef("no-iso", Not(IsoGreaterThan(100)))]

eng = SmartCollections(assets, parents, cols)
eng.members("best")                              # ['a1']

eng.update_assets([{"asset_id": "a2", "rating": 5, "keywords": {2}}])
eng.members("best")                              # ['a1', 'a2']

eng.move_keyword(2, 0)                           # 子树换父节点，2 不再是 1 的后代
eng.members("best")                              # []
```

- `update_assets(patches)`：每个 patch 必含 `asset_id`，可选 `rating` / `iso` / `keywords`（整体替换）；未给字段保持不变，`iso=None` 表示缺失。
- `move_keyword(keyword_id, new_parent)`：非根节点连同子树换父，资产上的关键词 ID 不变。
- `members(collection_id)`：当前成员 ID 列表，顺序自定。
- `eng.reset_stats()` / `eng.stats`：读取资产记录数与规则求值次数的计数器。

## 设计要点

- 成员集用 Python 大整数位图存储（bit i = 资产 i），2000 集合 × 10 万资产仅约 25 MiB；初始化时叶条件位图用 C 速度的 `&` / `|` / `^` 组合成全量成员。
- 增量路径只触达受影响对象：
  - rating / ISO 变化查有序阈值索引，只找结果真正翻转的集合；
  - 关键词集合变化沿被改关键词的祖先链查按关键词索引的集合；
  - `MoveKeyword` 只遍历被移动子树（收集其中被标注的资产）和新旧祖先链的对称差（收集含这些关键词叶条件的集合）。
- 受影响的 (资产, 集合) 对立即按当前关键词树重新解释整条规则，因此 `not`、多标签重叠支持天然正确；变化既会加入新命中者也会移除失配者。

## 验证

```bash
python3 -m pytest tests/ -q          # 差分测试 + 跨分支移动场景
python3 experiments/scale_experiment.py
```

规模实验（种子固定，可复现）：5万 / 10万 / 20万资产三档，1万关键词、2000 集合（其中 1000 个各命中 ≥90% 资产）。一次 `MoveKeyword` 只涉及 100 个资产、20 个集合，实测：

| 资产数 | 读取资产数 | 规则求值数 | 峰值内存 |
|-------:|-----------:|-----------:|---------:|
| 50,000  | 100 | 2,000 | ~90 MiB |
| 100,000 | 100 | 2,000 | ~148 MiB |
| 200,000 | 100 | 2,000 | ~272 MiB |

两个计数不随库总量增长；10 万资产、1000 个宽集合时峰值内存远低于 512 MiB。
