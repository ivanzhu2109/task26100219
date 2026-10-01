"""Scale experiment: incremental cost must not depend on library size.

Builds a library of N assets / 10k keywords / 2000 collections (>=1000 of
them matching >=90% of assets). One MoveKeyword touches exactly 100
assets and 20 collections; everything else is unrelated filler. We record
how many asset records were read and how many rule evaluations were
performed, then repeat with different N to show both counters stay flat.
Peak RSS is reported and asserted < 512 MiB.

Run:  python3 experiments/scale_experiment.py            (all sizes)
      python3 experiments/scale_experiment.py --run N    (single size)
"""

import json
import os
import random
import resource
import subprocess
import sys
import time

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
from smart_collections.interpreter import collection_members

SEED = 20261001
N_KEYWORDS = 10_000
N_COLLECTIONS = 2_000
N_WIDE = 1_000          # collections matching >= 90% of assets
N_MOVED_ASSETS = 100    # assets tagged inside the moved subtree
N_MOVE_COLS = 20        # collections referencing old/new ancestor chains
ISO_POOL = [50, 100, 200, 400, 800, 1600, 3200]

# keyword id layout
ROOT = 0
OLD_A, OLD_P = 1, 2                 # old ancestor chain: ROOT > OLD_A > OLD_P
M = 3                               # moved subtree root
SUBTREE = set(range(M, M + 10))     # nodes 3..12 move together
NEW_A, NEW_P = 13, 14               # new ancestor chain: ROOT > NEW_A > NEW_P
FREE_START = 15
CHAIN_KWS = {ROOT, OLD_A, OLD_P, NEW_A, NEW_P}


def build_keyword_parents(rng):
    parents = {ROOT: None, OLD_A: ROOT, OLD_P: OLD_A, NEW_A: ROOT, NEW_P: NEW_A}
    for kw in SUBTREE:
        parents[kw] = M if kw == M else kw - 1
    parents[M] = OLD_P
    for kw in range(FREE_START, N_KEYWORDS):
        # free keywords attach under ROOT or other free keywords only, so
        # the moved subtree stays exactly SUBTREE and the ancestor chains
        # of OLD_P / NEW_P stay exactly {OLD_A, OLD_P, ROOT} / {NEW_A, NEW_P, ROOT}
        parents[kw] = ROOT if kw == FREE_START else rng.choice(
            [ROOT] + list(range(FREE_START, kw))
        )
    return parents


def build_assets(rng, n_assets):
    moved_kws = sorted(SUBTREE)
    assets = []
    for aid in range(n_assets):
        rating = 0 if rng.random() < 0.05 else rng.randrange(1, 6)  # 95% >= 1
        iso = None if rng.random() < 0.2 else rng.choice(ISO_POOL)
        if aid < N_MOVED_ASSETS:
            kws = {rng.choice(moved_kws) for _ in range(2)}
        else:
            kws = set()
            for _ in range(rng.randrange(0, 3)):
                kw = rng.randrange(FREE_START, N_KEYWORDS)
                kws.add(kw)
        assets.append(Asset(aid, rating, iso, frozenset(kws)))
    return assets


def build_collections(rng):
    cols = []
    # 1000 wide collections, each matching >= 90% of assets
    for cid in range(N_WIDE):
        if cid % 2 == 0:
            rule = RatingAtLeast(1)  # 95% of assets
        else:
            rule = Any((RatingAtLeast(2), IsoGreaterThan(50)))
        cols.append(CollectionDef(cid, rule))
    # 20 collections on the old/new ancestor chains of the moved subtree
    chain = [OLD_A, OLD_P, NEW_A, NEW_P]
    for i in range(N_MOVE_COLS):
        kw = chain[i % len(chain)]
        if i % 2 == 0:
            rule = All((HasKeyword(kw), RatingAtLeast(0)))
        else:
            rule = Any((HasKeyword(kw), IsoGreaterThan(10_000_000)))
        cols.append(CollectionDef(N_WIDE + i, rule))
    # remaining filler collections, keyword leaves away from the chains
    while len(cols) < N_COLLECTIONS:
        cid = len(cols)
        kw1 = rng.randrange(FREE_START, N_KEYWORDS)
        kw2 = rng.randrange(M, M + 10)  # inside moved subtree: unaffected
        rule = All(
            (
                Any((HasKeyword(kw1), RatingAtLeast(rng.randrange(0, 6)))),
                Not(All((HasKeyword(kw2), IsoGreaterThan(rng.choice(ISO_POOL))))),
            )
        )
        cols.append(CollectionDef(cid, rule))
    return cols


def peak_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def run_single(n_assets):
    rng = random.Random(SEED)
    t0 = time.perf_counter()
    parents = build_keyword_parents(rng)
    assets = build_assets(rng, n_assets)
    cols = build_collections(rng)
    eng = SmartCollections(assets, parents, cols)
    t_init = time.perf_counter() - t0

    # sanity: wide collections really match >= 90%
    wide0 = len(eng.members(0))
    assert wide0 >= 0.9 * n_assets, wide0

    # one move: 100 assets in the subtree, 20 collections on the chains
    eng.reset_stats()
    t0 = time.perf_counter()
    eng.move_keyword(M, NEW_P)
    t_move = time.perf_counter() - t0
    stats = eng.stats

    # correctness spot-check of the 20 affected collections vs interpreter
    parents[M] = NEW_P
    for i in range(N_MOVE_COLS):
        cid = N_WIDE + i
        expect = collection_members(assets, parents, cols[cid])
        assert set(eng.members(cid)) == expect, cid

    result = {
        "assets": n_assets,
        "init_s": round(t_init, 2),
        "move_ms": round(t_move * 1000, 2),
        "assets_read": stats["assets_read"],
        "rules_evaluated": stats["rules_evaluated"],
        "peak_mib": round(peak_mib(), 1),
    }
    print(json.dumps(result))
    return result


def main():
    if "--run" in sys.argv:
        run_single(int(sys.argv[sys.argv.index("--run") + 1]))
        return
    sizes = [50_000, 100_000, 200_000]
    results = []
    for n in sizes:
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--run", str(n)],
            capture_output=True, text=True, check=True,
        )
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
    print(f"{'assets':>8} {'init_s':>7} {'move_ms':>8} "
          f"{'assets_read':>11} {'rules_eval':>10} {'peak_MiB':>9}")
    for r in results:
        print(f"{r['assets']:>8} {r['init_s']:>7} {r['move_ms']:>8} "
              f"{r['assets_read']:>11} {r['rules_evaluated']:>10} {r['peak_mib']:>9}")
    counts = {(r["assets_read"], r["rules_evaluated"]) for r in results}
    assert len(counts) == 1, f"counters grew with library size: {counts}"
    assert counts.pop() == (N_MOVED_ASSETS, N_MOVED_ASSETS * N_MOVE_COLS)
    assert all(r["peak_mib"] < 512 for r in results), "memory budget exceeded"
    print("SCALE_EXPERIMENT_OK")


if __name__ == "__main__":
    main()
