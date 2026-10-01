"""定向场景：跨分支移动子树时的重叠支持问题。

关键词树：
        root(0)
       /      \\
     A(1)      B(2)
    /   \\       \\
  S(3)  T(4)    U(5)
  / \\
x(6) y(7)

照片 P 同时带有待移动子树 S 中的两个标签 x、y，另带旧祖先 A 下的
其他标签 T。把 S 从 A 移到 B 后：
- P 对旧祖先 A 的匹配仍成立（由 T 支撑），不得被错误移出；
- P 对新祖先 B 的匹配新增；
- 含否定与复合的规则必须同步翻转，且成员不得重复。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brute_force import BruteForce
from smart_collections import SmartCollections

PARENTS = {0: None, 1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3, 7: 3}

ASSETS = [
    {"id": "P", "rating": 4, "iso": 200, "keywords": {6, 7, 4}},  # x, y, T
    {"id": "Q", "rating": 2, "iso": None, "keywords": {6}},        # 仅 x
    {"id": "R", "rating": 5, "iso": 800, "keywords": {4}},         # 仅 T，与移动无关
    {"id": "Z", "rating": 0, "iso": None, "keywords": set()},      # 无关键词
]

COLLECTIONS = {
    "old_anc": {"keyword": 1},                       # A 及后代
    "new_anc": {"keyword": 2},                       # B 及后代
    "sub": {"keyword": 3},                           # S 及后代（子树整体移动）
    "not_old": {"not": {"keyword": 1}},
    "not_new": {"not": {"keyword": 2}},
    "compound": {"all": [{"keyword": 1}, {"not": {"keyword": 2}}]},
    "compound_any": {"any": [{"keyword": 4}, {"keyword": 6}]},
    "compound_neg_any": {"not": {"any": [{"keyword": 3}, {"iso_gt": 400}]}},
    "mixed": {"all": [{"rating_ge": 2}, {"any": [{"keyword": 5}, {"keyword": 7}]}]},
}


def make():
    sc = SmartCollections(ASSETS, PARENTS, COLLECTIONS)
    bf = BruteForce(ASSETS, PARENTS, COLLECTIONS)
    return sc, bf


def check_all(sc, bf):
    for cid in COLLECTIONS:
        got = sc.members(cid)
        assert len(got) == len(set(got)), f"{cid}: 成员重复 {got}"
        assert set(got) == bf.members(cid), f"{cid}: {got} != {bf.members(cid)}"


def test_cross_branch_move_with_overlapping_support():
    sc, bf = make()
    check_all(sc, bf)

    # 移动前：P、Q 在 old_anc；P 由 x/y/T 三重支撑
    assert set(sc.members("old_anc")) == {"P", "Q", "R"}
    assert set(sc.members("new_anc")) == set()
    assert set(sc.members("compound")) == {"P", "Q", "R"}

    # 跨分支移动：S(3) 从 A(1) 移到 B(2)
    sc.move_keyword(3, 2)
    bf.move_keyword(3, 2)
    check_all(sc, bf)

    # P 仍命中旧祖先 A（T 支撑），Q 被移出；R 不受影响
    assert set(sc.members("old_anc")) == {"P", "R"}
    # P、Q 新增命中新祖先 B
    assert set(sc.members("new_anc")) == {"P", "Q"}
    # 子树条件不受移动影响
    assert set(sc.members("sub")) == {"P", "Q"}
    # 否定规则同步翻转
    assert set(sc.members("not_old")) == {"Q", "Z"}
    assert set(sc.members("not_new")) == {"R", "Z"}
    # 复合规则：P 命中 B 后不再满足 all[A, not B]
    assert set(sc.members("compound")) == {"R"}
    assert set(sc.members("compound_any")) == {"P", "Q", "R"}
    # R 的 ISO 800 > 400 使 any 为真、not 为假，故只剩 Z
    assert set(sc.members("compound_neg_any")) == {"Z"}
    # Q 只有 x(6)，不满足 any[keyword 5, keyword 7]
    assert set(sc.members("mixed")) == {"P"}

    # 移回去，状态必须完全恢复
    sc.move_keyword(3, 1)
    bf.move_keyword(3, 1)
    check_all(sc, bf)
    assert set(sc.members("old_anc")) == {"P", "Q", "R"}
    assert set(sc.members("new_anc")) == set()
    assert set(sc.members("compound")) == {"P", "Q", "R"}


def test_keyword_replacement_overlapping_support():
    """关键词替换：资产同时持有新旧子树中的标签时不得错误移出。"""
    sc, bf = make()
    # P 已有 {x(6), y(7), T(4)}；把 x 替换为 U(5)，y 仍支撑 S 的匹配
    sc.update_assets({"P": {"keywords": {5, 7, 4}}})
    bf.update_assets({"P": {"keywords": {5, 7, 4}}})
    check_all(sc, bf)
    assert "P" in sc.members("sub")       # y 仍支撑
    assert "P" in sc.members("new_anc")   # U 在 B 下
    assert "P" in sc.members("old_anc")   # T 仍支撑

    # 再移除 y 和 T，只留 U：P 应同时退出 sub 与 old_anc
    sc.update_assets({"P": {"keywords": {5}}})
    bf.update_assets({"P": {"keywords": {5}}})
    check_all(sc, bf)
    assert set(sc.members("sub")) == {"Q"}
    # Q 的 x(6) 仍在 A 的子树 S 下，故 Q 仍命中 old_anc
    assert set(sc.members("old_anc")) == {"Q", "R"}
    assert set(sc.members("new_anc")) == {"P"}


def test_iso_and_rating_edges():
    """缺失 ISO 比较为假；not 对缺失 ISO 取反为真；rating 边界。"""
    sc, bf = make()
    # Q 的 ISO 缺失：iso_gt 为假
    sc.update_assets({"Q": {"iso": 1600}, "R": {"iso": None}, "Z": {"rating": 5}})
    bf.update_assets({"Q": {"iso": 1600}, "R": {"iso": None}, "Z": {"rating": 5}})
    check_all(sc, bf)
    # R 的 ISO 变缺失后，compound_neg_any 中 not(any[...]) 只看关键词 3
    assert set(sc.members("compound_neg_any")) == {"R", "Z"}
    # Z 的 rating 提到 5 但无关键词，mixed 仍不命中
    assert "Z" not in sc.members("mixed")
