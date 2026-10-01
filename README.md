# 摄影图库智能集合增量维护组件

元数据已提取的前提下，在内存中维护“智能集合”成员：每次资产字段修改或
关键词子树移动返回时，所有集合成员都等于在当前关键词树上对全部资产
直接解释规则的结果。不考虑并发与崩溃恢复。

## 规则表示

嵌套 dict，最多 16 个叶条件、深度不超过 8：

- `{"all": [子规则...]}` / `{"any": [子规则...]}` / `{"not": 子规则}`
- 叶条件：
  - `{"rating_ge": k}`：rating >= k（rating 为 0..5 整数）
  - `{"iso_gt": t}`：ISO > t；ISO 缺失（None）时该比较为假
  - `{"keyword": kid}`：具有关键词 kid 或其后代（含 kid 自身）

## 快速上手

```python
from smart_collections import SmartCollections

assets = [
    {"id": "IMG_001", "rating": 5, "iso": 400, "keywords": {10, 21}},
    {"id": "IMG_002", "rating": 3, "iso": None, "keywords": {22}},
]
keyword_parents = {0: None, 1: 0, 10: 1, 21: 10, 22: 1}   # id -> 父 id，根为 None
collections = {
    "fav":   {"all": [{"rating_ge": 4}, {"not": {"iso_gt": 800}}]},
    "nature": {"keyword": 10},                              # 10 及后代（含 21）
}

sc = SmartCollections(assets, keyword_parents, collections)

sc.update_assets({"IMG_002": {"rating": 5, "iso": 100}})   # 未给字段保持不变
sc.move_keyword(22, 10)          # 关键词 22 连同子树挂到 10 下，资产标签不变
sc.members("fav")                # -> 当前成员 id 列表（顺序自定）
sc.stats_snapshot()              # -> {"assets_read": ..., "rules_evaluated": ...}
```

约束：初始化后不增删资产 / 关键词 / 集合，只修改资产字段与关键词父节点；
`move_keyword` 的输入需保证移动后仍为树（根不可移动）。未知资产 id 抛
`KeyError`，非法规则 / 未知关键词 id 抛 `ValueError`。

## 实现原理

- 集合成员与关键词子树覆盖都用 Python int 位图（第 i 位 = 内部资产下标），
  10 万资产时每位图约 12.5 KiB。
- 初始化全量计算：叶条件转位图（rating 按 6 桶一次扫描、ISO 排序扫描、
  关键词用子树位图），再按规则树做位图代数（all=AND、any=OR、
  not=全掩码 XOR），与逐资产解释严格等价。
- 增量更新只按索引定位受影响的 (集合, 资产) 对，再逐对重算该集合规则：
  - rating / ISO 变化 → 阈值索引（rating 桶、ISO 排序阈值 + 二分）；
  - 关键词替换 / 子树移动 → 资产关键词祖先链（path_set）前后差集，
    再经“关键词 → 集合”反向索引定位。
- 不扫描全部资产、不遍历整棵关键词树、不逐条执行全部集合规则；
  不把重算推迟到 `members`。

## 测试与实验

```bash
python3 -m pytest tests/ -q          # 随机差分测试 + 定向场景（23 个用例）
python3 experiments/scale_experiment.py   # 规模 / 计数 / 内存实验
```

- `tests/test_random_diff.py`：与独立全量解释器 `tests/brute_force.py`
  随机交错对比（字段修改、关键词替换、子树移动，每步校验全部集合）。
- `tests/test_scenarios.py`：照片同时带被移动子树中两个标签、另带旧祖先下
  其他标签的跨分支移动场景，验证否定与复合规则不错误移出、不重复加入。
- `experiments/scale_experiment.py`：10 万资产 / 1 万关键词 / 2000 集合
  （1500 个集合命中 >=90% 资产），一次移动只涉及 100 资产、20 集合；
  实测两种规模下读取资产数与求值规则数完全相同（不随库总量增长），
  满规模峰值内存约 320 MiB（< 512 MiB）。
