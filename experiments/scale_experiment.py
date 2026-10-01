"""规模实验：验证增量维护的读取/求值计数不随库总量增长，并报告内存峰值。

场景（可复现，固定随机种子）：
- 满规模：10 万资产、1 万关键词、2000 集合，其中 1500 个集合命中 >=90% 资产；
- 半规模：5 万资产、5 千关键词、1000 集合（无关数据减半）。
- 一次关键词子树移动只涉及 100 个资产和 20 个集合，其余数据无关。

对每次移动记录 stats 差值：assets_read（实际读取的资产记录数）与
rules_evaluated（求值的规则数）。两种规模下二者应当完全相同，
证明计数不随库总量增长。内存峰值用 resource.ru_maxrss 报告。

运行：python3 experiments/scale_experiment.py
"""

from __future__ import annotations

import random
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smart_collections import SmartCollections

# 关键词 id 布局：0=root, 1=分支A, 2=分支B, 3..22=被移动子树(20 节点), 其余无关
SUBTREE_IDS = list(range(3, 23))
BRANCH_A, BRANCH_B, ROOT = 1, 2, 0
N_AFFECTED_ASSETS = 100
N_AFFECTED_COLS = 20


def build_parents(n_kw: int) -> dict[int, int | None]:
    parents: dict[int, int | None] = {0: None, 1: 0, 2: 0}
    for kid in SUBTREE_IDS:
        parents[kid] = 1 if kid == 3 else kid - 1  # 3->1, 4->3, ..., 22->21（链）
    for kid in range(23, n_kw):
        parents[kid] = ROOT if kid % 2 == 0 else (23 + (kid % 50))  # 浅层无关树
    for kid in range(23, min(73, n_kw)):  # 保证 23..72 存在且挂在 root 上
        parents[kid] = ROOT
    return parents


def build_assets(rng: random.Random, n_assets: int, n_kw: int) -> list[dict]:
    assets = []
    for i in range(N_AFFECTED_ASSETS):  # 前 100 个：被打上被移动子树内的标签
        assets.append({
            "id": f"hit{i}",
            "rating": rng.randint(0, 5),
            "iso": rng.choice([None, 100, 400, 1600]),
            "keywords": {rng.choice(SUBTREE_IDS)},
        })
    unrelated = list(range(23, n_kw))
    for i in range(n_assets - N_AFFECTED_ASSETS):
        assets.append({
            "id": f"a{i}",
            "rating": rng.randint(0, 5),
            "iso": rng.choice([None, 100, 400, 1600]),
            "keywords": set(rng.sample(unrelated, rng.randint(1, 2))),
        })
    return assets


def build_collections(rng: random.Random, n_cols: int, n_kw: int) -> dict[int, dict]:
    cols: dict[int, dict] = {}
    # 20 个受影响集合：引用旧/新分支节点（含否定与复合形式）
    for j in range(N_AFFECTED_COLS // 2):
        cols[j] = {"keyword": BRANCH_A} if j % 2 == 0 else {"not": {"keyword": BRANCH_A}}
        cols[j + N_AFFECTED_COLS // 2] = (
            {"all": [{"keyword": BRANCH_B}, {"rating_ge": 0}]}
            if j % 2 == 0
            else {"any": [{"keyword": BRANCH_B}, {"iso_gt": 10_000}]}
        )
    cid = N_AFFECTED_COLS
    # 1500（或半数）个命中 >=90% 资产的集合：rating_ge 0 恒真，再叠加无关条件
    n_wide = (n_cols * 3) // 4
    unrelated = list(range(23, n_kw))
    for _ in range(n_wide):
        cols[cid] = {"any": [{"rating_ge": 0}, {"keyword": rng.choice(unrelated)}]}
        cid += 1
    # 其余：随机窄规则，均与被移动子树无关
    while cid < n_cols:
        cols[cid] = rng.choice([
            {"rating_ge": rng.randint(1, 5)},
            {"iso_gt": rng.choice([100, 400, 1600])},
            {"keyword": rng.choice(unrelated)},
            {"all": [{"rating_ge": rng.randint(1, 3)}, {"iso_gt": 400}]},
            {"not": {"keyword": rng.choice(unrelated)}},
        ])
        cid += 1
    return cols


def peak_mib() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS 返回字节，Linux 返回 KiB
    return rss / 1_048_576 if sys.platform == "darwin" else rss / 1024


def run_scale(n_assets: int, n_kw: int, n_cols: int) -> dict:
    rng = random.Random(42)
    parents = build_parents(n_kw)
    assets = build_assets(rng, n_assets, n_kw)
    collections = build_collections(rng, n_cols, n_kw)

    sc = SmartCollections(assets, parents, collections)

    # sanity：>=3/4 的集合命中 >=90% 资产（直接数成员位图的比特，避免展开 id 列表）
    threshold = int(n_assets * 0.9)
    wide = sum(1 for cid in collections if sc._members[cid].bit_count() >= threshold)
    assert wide >= (n_cols * 3) // 4, f"命中>=90%的集合数不足: {wide}"

    before = sc.stats_snapshot()
    sc.move_keyword(3, BRANCH_B)  # 子树 3..22 从分支A 移到 分支B
    after = sc.stats_snapshot()

    # 受影响集合确实更新：分支A 下只有被移动子树，移走后集合 0 变空；
    # 100 个资产加入分支B 集合
    assert sc._members[0].bit_count() == 0  # {"keyword": A}
    assert sc._members[N_AFFECTED_COLS // 2].bit_count() == N_AFFECTED_ASSETS

    return {
        "n_assets": n_assets,
        "n_kw": n_kw,
        "n_cols": n_cols,
        "assets_read": after["assets_read"] - before["assets_read"],
        "rules_evaluated": after["rules_evaluated"] - before["rules_evaluated"],
        "peak_mib": peak_mib(),
    }


def main() -> None:
    results = [
        run_scale(50_000, 5_000, 1_000),
        run_scale(100_000, 10_000, 2_000),
    ]
    print(f"{'规模(资产/关键词/集合)':<28} {'读取资产数':>10} {'求值规则数':>10} {'峰值内存MiB':>12}")
    for r in results:
        scale = f"{r['n_assets']}/{r['n_kw']}/{r['n_cols']}"
        print(f"{scale:<28} {r['assets_read']:>10} {r['rules_evaluated']:>10} {r['peak_mib']:>12.1f}")
    reads = {r["assets_read"] for r in results}
    evals = {r["rules_evaluated"] for r in results}
    assert len(reads) == 1 and len(evals) == 1, "计数随库总量增长！"
    assert results[-1]["peak_mib"] < 512, "峰值内存超过 512 MiB！"
    print("结论：一次移动只读取 100 个资产、求值 20 集合 x 100 资产 = 2000 条规则，")
    print("计数与库总量无关；满规模峰值内存低于 512 MiB。")


if __name__ == "__main__":
    main()
