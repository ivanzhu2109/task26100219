"""Targeted cross-branch move scenario.

Tree:
    root(0)
    |-- A(1)            old ancestor
    |   |-- M(2)        subtree to move
    |   |   |-- x(3)
    |   |   `-- y(4)
    |   `-- b(5)        other tag under the old ancestor
    `-- B(6)            new ancestor
        `-- c(7)

Photo P carries x and y (two tags inside the moved subtree) plus b
(another tag under the old ancestor A). After moving M under B:
* the old-ancestor match HasKeyword(A) must STILL hold for P (via b),
* the new-ancestor match HasKeyword(B) appears for P and Q,
* negated / compound rules must not drop P or double-count anything.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smart_collections import (
    All,
    Any,
    Asset,
    CollectionDef,
    HasKeyword,
    Not,
    RatingAtLeast,
    SmartCollections,
)
from smart_collections.interpreter import all_members

ROOT, A, M, X, Y, B_TAG, B, C = 0, 1, 2, 3, 4, 5, 6, 7

PARENTS = {ROOT: None, A: ROOT, M: A, X: M, Y: M, B_TAG: A, B: ROOT, C: B}

ASSETS = [
    Asset("P", 4, 400, frozenset({X, Y, B_TAG})),  # two moved tags + old-ancestor tag
    Asset("Q", 1, None, frozenset({X})),            # only a moved tag
    Asset("R", 5, 100, frozenset({C})),             # unrelated control
]

COLS = [
    CollectionDef("old_anc", HasKeyword(A)),
    CollectionDef("new_anc", HasKeyword(B)),
    CollectionDef("not_old", Not(HasKeyword(A))),
    CollectionDef("compound", All((HasKeyword(B), Not(HasKeyword(A))))),
    CollectionDef("mixed", Any((HasKeyword(M), All((RatingAtLeast(5), Not(HasKeyword(A))))))),
]


def members_map(eng):
    return {c.collection_id: set(eng.members(c.collection_id)) for c in COLS}


def test_cross_branch_move():
    eng = SmartCollections(ASSETS, dict(PARENTS), COLS)

    before = members_map(eng)
    assert before["old_anc"] == {"P", "Q"}
    assert before["new_anc"] == {"R"}
    assert before["not_old"] == {"R"}
    assert before["compound"] == {"R"}  # R is under B and not under A

    # move M from A to B (cross-branch)
    eng.move_keyword(M, B)
    parents = dict(PARENTS)
    parents[M] = B
    ref = all_members(ASSETS, parents, COLS)
    after = members_map(eng)

    for cid in ref:
        assert after[cid] == ref[cid], f"{cid}: {after[cid]} != {ref[cid]}"

    # P keeps the old-ancestor match via b, gains the new-ancestor match
    assert "P" in after["old_anc"]
    assert "P" in after["new_anc"]
    # Q loses the old-ancestor match, gains the new one
    assert after["old_anc"] == {"P"}
    assert after["new_anc"] == {"P", "Q", "R"}
    # negation: Q enters Not(A); P must NOT be wrongly removed from A-side
    assert after["not_old"] == {"Q", "R"}
    # compound: B and not A -> only Q (P still matches A via b)
    assert after["compound"] == {"Q", "R"}
    # no duplicates in any member list
    for c in COLS:
        ids = eng.members(c.collection_id)
        assert len(ids) == len(set(ids)), c.collection_id


def test_move_back_and_forth():
    eng = SmartCollections(ASSETS, dict(PARENTS), COLS)
    eng.move_keyword(M, B)
    eng.move_keyword(M, A)  # move back
    ref = all_members(ASSETS, dict(PARENTS), COLS)
    for cid, expect in ref.items():
        assert set(eng.members(cid)) == expect, cid


if __name__ == "__main__":
    test_cross_branch_move()
    test_move_back_and_forth()
    print("MOVE_SCENARIO_OK")
