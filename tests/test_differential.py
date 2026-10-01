"""Randomized differential test: incremental engine vs naive interpreter.

Interleaves asset field updates, keyword-set replacements and subtree
moves; after every single mutation all collections are compared against
a from-scratch interpretation on the current keyword tree.
"""

import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smart_collections import (
    All,
    Any,
    Asset,
    CollectionDef,
    HasKeyword,
    IsoGreaterThan,
    Not,
    RatingAtLeast,
    SmartCollections,
)
from smart_collections.interpreter import all_members

ISO_POOL = [50, 100, 200, 400, 800, 1600, 3200, 6400]


def random_tree(rng, n):
    """Random single-root tree over keyword ids 0..n-1; 0 is the root."""
    parents = {0: None}
    for kid in range(1, n):
        parents[kid] = rng.randrange(0, kid)
    return parents


def random_rule(rng, n_kw, depth=0, max_depth=6):
    if depth >= max_depth or rng.random() < 0.45:
        kind = rng.randrange(3)
        if kind == 0:
            return RatingAtLeast(rng.randrange(0, 6))
        if kind == 1:
            return IsoGreaterThan(rng.choice(ISO_POOL))
        return HasKeyword(rng.randrange(n_kw))
    kind = rng.randrange(3)
    if kind == 0:
        return Not(random_rule(rng, n_kw, depth + 1, max_depth))
    n = rng.randrange(1, 4)
    children = tuple(random_rule(rng, n_kw, depth + 1, max_depth) for _ in range(n))
    return All(children) if kind == 1 else Any(children)


def random_assets(rng, n_assets, n_kw):
    assets = []
    for aid in range(n_assets):
        iso = rng.choice(ISO_POOL + [None, None])
        kws = {rng.randrange(n_kw) for _ in range(rng.randrange(0, 4))}
        assets.append(Asset(aid, rng.randrange(0, 6), iso, frozenset(kws)))
    return assets


def subtree_of(parents, root):
    out = {root}
    grew = True
    while grew:
        grew = False
        for kid, par in parents.items():
            if par in out and kid not in out:
                out.add(kid)
                grew = True
    return out


def run_seed(seed):
    rng = random.Random(seed)
    n_assets, n_kw, n_cols = 150, 40, 25
    parents = random_tree(rng, n_kw)
    assets = random_assets(rng, n_assets, n_kw)
    cols = [CollectionDef(cid, random_rule(rng, n_kw)) for cid in range(n_cols)]

    eng = SmartCollections(assets, parents, cols)

    def check_all(step):
        ref = all_members(assets, parents, cols)
        for cid, expect in ref.items():
            got = set(eng.members(cid))
            assert got == expect, f"seed={seed} step={step} col={cid}"

    check_all("init")

    for step in range(300):
        op = rng.randrange(4)
        if op == 0:  # rating change
            aid = rng.randrange(n_assets)
            new_rating = rng.randrange(0, 6)
            eng.update_assets([{"asset_id": aid, "rating": new_rating}])
            a = assets[aid]
            assets[aid] = Asset(aid, new_rating, a.iso, a.keywords)
        elif op == 1:  # iso change (sometimes removed)
            aid = rng.randrange(n_assets)
            new_iso = rng.choice(ISO_POOL + [None])
            eng.update_assets([{"asset_id": aid, "iso": new_iso}])
            a = assets[aid]
            assets[aid] = Asset(aid, a.rating, new_iso, a.keywords)
        elif op == 2:  # keyword set replacement
            aid = rng.randrange(n_assets)
            new_kws = {rng.randrange(n_kw) for _ in range(rng.randrange(0, 4))}
            eng.update_assets([{"asset_id": aid, "keywords": new_kws}])
            a = assets[aid]
            assets[aid] = Asset(aid, a.rating, a.iso, frozenset(new_kws))
        else:  # subtree move
            k = rng.randrange(1, n_kw)
            forbidden = subtree_of(parents, k)
            candidates = [x for x in range(n_kw) if x not in forbidden]
            new_parent = rng.choice(candidates)
            eng.move_keyword(k, new_parent)
            parents[k] = new_parent
        check_all(step)


def test_differential():
    for seed in range(8):
        run_seed(seed)


if __name__ == "__main__":
    test_differential()
    print("DIFFERENTIAL_OK")
