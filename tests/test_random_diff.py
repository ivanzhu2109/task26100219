"""随机交错差分测试：组件 vs 独立全量解释器。

每回合随机执行字段修改、关键词替换或子树移动，之后比较所有集合成员。
规则含嵌套 all/any/not、缺失 ISO、关键词子树条件。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brute_force import BruteForce
from smart_collections import SmartCollections, validate_rule


def random_leaf(rng: random.Random, kw_ids: list[int]) -> dict:
    kind = rng.choice(["rating_ge", "iso_gt", "keyword"])
    if kind == "rating_ge":
        return {"rating_ge": rng.randint(0, 5)}
    if kind == "iso_gt":
        return {"iso_gt": rng.choice([50, 100, 200, 400, 800, 1600, 3200])}
    return {"keyword": rng.choice(kw_ids)}


def _random_rule(rng: random.Random, kw_ids: list[int], depth: int) -> dict:
    if depth >= 4 or rng.random() < 0.45:
        return random_leaf(rng, kw_ids)
    kind = rng.choice(["all", "any", "not"])
    if kind == "not":
        return {"not": _random_rule(rng, kw_ids, depth + 1)}
    return {kind: [_random_rule(rng, kw_ids, depth + 1)
                   for _ in range(rng.randint(2, 3))]}


def random_rule(rng: random.Random, kw_ids: list[int]) -> dict:
    """生成合法规则（不超过 16 叶、深度 8），不合法就重摇。"""
    while True:
        rule = _random_rule(rng, kw_ids, 0)
        try:
            validate_rule(rule)
            return rule
        except ValueError:
            continue


def build_case(rng: random.Random):
    n_assets, n_kw, n_cols = 60, 25, 15
    # 随机生成单根树（0 为根）
    parents = {0: None}
    for kid in range(1, n_kw):
        parents[kid] = rng.randrange(0, kid)
    kw_ids = list(parents)
    assets = []
    for i in range(n_assets):
        assets.append({
            "id": f"a{i}",
            "rating": rng.randint(0, 5),
            "iso": rng.choice([None, 50, 100, 200, 400, 800, 1600, 3200, 6400]),
            "keywords": set(rng.sample(kw_ids, rng.randint(0, 3))),
        })
    collections = {cid: random_rule(rng, kw_ids) for cid in range(n_cols)}
    return assets, parents, collections, kw_ids


def subtree_nodes(parents: dict[int, int | None], root: int) -> set[int]:
    result = {root}
    grew = True
    while grew:
        grew = False
        for kid, parent in parents.items():
            if parent in result and kid not in result:
                result.add(kid)
                grew = True
    return result


@pytest.mark.parametrize("seed", range(20))
def test_random_interleaved(seed: int):
    rng = random.Random(seed)
    assets, parents, collections, kw_ids = build_case(rng)
    sc = SmartCollections(assets, parents, collections)
    bf = BruteForce(assets, parents, collections)
    asset_ids = [a["id"] for a in assets]

    def check_all():
        for cid in collections:
            assert set(sc.members(cid)) == bf.members(cid), f"cid={cid}"

    check_all()
    for step in range(300):
        op = rng.random()
        if op < 0.45:
            # 字段修改：rating / ISO（含置缺失）/ 关键词替换
            patches = {}
            for aid in rng.sample(asset_ids, rng.randint(1, 5)):
                patch = {}
                if rng.random() < 0.5:
                    patch["rating"] = rng.randint(0, 5)
                if rng.random() < 0.5:
                    patch["iso"] = rng.choice([None, 50, 100, 200, 400, 800, 1600, 3200])
                if rng.random() < 0.4:
                    patch["keywords"] = set(rng.sample(kw_ids, rng.randint(0, 3)))
                if patch:
                    patches[aid] = patch
            if patches:
                sc.update_assets(patches)
                bf.update_assets(patches)
        else:
            # 子树移动：选非根节点，新父节点不在其子树内
            kid = rng.choice(kw_ids[1:])
            sub = subtree_nodes(parents, kid)
            candidates = [k for k in kw_ids if k not in sub]
            new_parent = rng.choice(candidates)
            sc.move_keyword(kid, new_parent)
            bf.move_keyword(kid, new_parent)
            parents[kid] = new_parent
        check_all(), f"step={step}"
