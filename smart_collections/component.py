"""智能集合增量维护组件。

不变式：每次修改返回时，每个集合的成员位图都等于在当前关键词树上
对全部资产直接解释规则的结果。

实现要点：
- 集合成员与关键词子树覆盖都用 Python int 位图（第 i 位 = 内部资产下标 i），
  10 万资产时每位图约 12.5 KiB，2000 个集合仅约 25 MiB。
- 初始化允许全量计算：叶条件先转成位图（rating 用 6 个桶一次扫描；
  ISO 按排序后一次扫描；关键词直接用子树位图），再按规则树做位图代数
  （all=AND, any=OR, not=全掩码 XOR），与逐资产解释严格等价。
- 增量更新只按索引定位受影响的 (集合, 资产) 对：
  - rating/ISO 变化 → 阈值索引（rating 桶、ISO 排序阈值 + 二分）；
  - 关键词变化 / 子树移动 → 资产关键词祖先链 path_set 的前后差集，
    差集中的节点才有子树位图变化，再经 关键词→集合 反向索引定位。
- 定位后只对每个 (集合, 资产) 对重算该集合规则（≤16 叶），并翻转成员位。
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any, Iterable, Iterator

from .keywords import KeywordTree
from .rules import evaluate, iter_leaves, validate_rule


def _iter_bits(bitmap: int) -> Iterator[int]:
    while bitmap:
        lsb = bitmap & -bitmap
        yield lsb.bit_length() - 1
        bitmap ^= lsb


class SmartCollections:
    def __init__(
        self,
        assets: Iterable[dict[str, Any]],
        keyword_parents: dict[int, int | None],
        collections: dict[int, dict[str, Any]],
    ) -> None:
        """assets: [{"id", "rating", "iso", "keywords"}]；collections: {id: 规则}。"""
        assets = list(assets)
        self._ids: list[Any] = [a["id"] for a in assets]
        if len(set(self._ids)) != len(self._ids):
            raise ValueError("资产 id 必须唯一")
        self._idx_of = {aid: i for i, aid in enumerate(self._ids)}
        self._rating: list[int] = []
        self._iso: list[int | None] = []
        self._keywords: list[frozenset[int]] = []
        for a in assets:
            rating = a["rating"]
            if not isinstance(rating, int) or not 0 <= rating <= 5:
                raise ValueError(f"rating 必须是 0..5 的整数: {rating!r}")
            iso = a.get("iso")
            if iso is not None and not isinstance(iso, int):
                raise ValueError(f"iso 必须是整数或 None: {iso!r}")
            self._rating.append(rating)
            self._iso.append(iso)
            self._keywords.append(frozenset(a.get("keywords", ())))
        n = len(self._ids)
        self._all_mask = (1 << n) - 1

        self._tree = KeywordTree(keyword_parents)
        self._init_keyword_bitmaps()

        self._rules: dict[int, dict] = {}
        self._rating_conds: dict[int, list[int]] = {}
        self._iso_conds: dict[int, list[int]] = {}
        self._iso_thresholds: list[int] = []
        self._kw_conds: dict[int, list[int]] = {}
        for cid, rule in collections.items():
            validate_rule(rule)
            self._rules[cid] = rule
            for leaf in iter_leaves(rule):
                (key, value), = leaf.items()
                if key == "rating_ge":
                    self._rating_conds.setdefault(value, []).append(cid)
                elif key == "iso_gt":
                    self._iso_conds.setdefault(value, []).append(cid)
                else:
                    if value not in self._tree:
                        raise ValueError(f"集合 {cid} 引用了未知关键词 {value}")
                    self._kw_conds.setdefault(value, []).append(cid)
        self._iso_thresholds = sorted(self._iso_conds)

        self._assets_read = 0
        self._rules_evaluated = 0
        self._members: dict[int, int] = {}
        self._init_memberships()

    # ---------- 初始化（允许全量计算） ----------

    def _bitmap_from_indices(self, indices: Iterable[int]) -> int:
        # 先写 bytearray 再一次性转 int，避免逐位置位导致大 int 反复重分配
        buf = bytearray((len(self._ids) + 7) // 8)
        for idx in indices:
            buf[idx >> 3] |= 1 << (idx & 7)
        return int.from_bytes(buf, "little")

    def _init_keyword_bitmaps(self) -> None:
        node_assets: dict[int, list[int]] = {}
        for idx, kws in enumerate(self._keywords):
            for node in self._tree.path_set(kws):
                node_assets.setdefault(node, []).append(idx)
        for node, indices in node_assets.items():
            self._tree.set_bitmap(node, self._bitmap_from_indices(indices))

    def _init_memberships(self) -> None:
        leaf_cache: dict[tuple[str, int], int] = {}

        def leaf_bitmap(key: str, value: int) -> int:
            cache_key = (key, value)
            bitmap = leaf_cache.get(cache_key)
            if bitmap is None:
                if key == "rating_ge":
                    bitmap = self._rating_ge_bitmap(value)
                elif key == "iso_gt":
                    bitmap = self._iso_gt_bitmap(value)
                else:
                    bitmap = self._tree.subtree_bitmap(value)
                leaf_cache[cache_key] = bitmap
            return bitmap

        def combine(rule: dict) -> int:
            (key, value), = rule.items()
            if key == "all":
                result = self._all_mask
                for child in value:
                    result &= combine(child)
                return result
            if key == "any":
                result = 0
                for child in value:
                    result |= combine(child)
                return result
            if key == "not":
                return self._all_mask ^ combine(value)
            return leaf_bitmap(key, value)

        for cid, rule in self._rules.items():
            self._members[cid] = combine(rule)

    def _rating_ge_bitmap(self, k: int) -> int:
        # rating 只有 0..5：先按桶一次扫描建 6 个位图，再按需合并
        buckets = getattr(self, "_rating_buckets", None)
        if buckets is None:
            bufs = [bytearray((len(self._ids) + 7) // 8) for _ in range(6)]
            for idx, rating in enumerate(self._rating):
                bufs[rating][idx >> 3] |= 1 << (idx & 7)
            buckets = [int.from_bytes(buf, "little") for buf in bufs]
            self._rating_buckets = buckets
        bitmap = 0
        for rating in range(max(k, 0), 6):
            bitmap |= buckets[rating]
        return bitmap

    def _iso_gt_bitmap(self, t: int) -> int:
        return self._bitmap_from_indices(
            idx for idx, iso in enumerate(self._iso)
            if iso is not None and iso > t
        )

    # ---------- 增量维护 ----------

    def _reevaluate(self, pairs: set[tuple[int, int]]) -> None:
        """对每个 (集合, 资产下标) 对重算规则并同步成员位。"""
        for cid, idx in pairs:
            self._rules_evaluated += 1
            hit = evaluate(
                self._rules[cid],
                self._rating[idx],
                self._iso[idx],
                lambda kid, i=idx: self._tree.has_bit(kid, i),
            )
            bit = 1 << idx
            if hit:
                self._members[cid] |= bit
            else:
                self._members[cid] &= ~bit

    def update_assets(self, patches: dict[Any, dict[str, Any]]) -> None:
        """批量替换指定资产的 rating / iso / keywords；未给字段保持不变。"""
        pairs: set[tuple[int, int]] = set()
        for aid, patch in patches.items():
            idx = self._idx_of[aid]  # 未知 id -> KeyError
            self._assets_read += 1

            if "rating" in patch:
                new_rating = patch["rating"]
                old_rating = self._rating[idx]
                if new_rating != old_rating:
                    for k, cids in self._rating_conds.items():
                        if (old_rating >= k) != (new_rating >= k):
                            for cid in cids:
                                pairs.add((cid, idx))
                    self._rating[idx] = new_rating

            if "iso" in patch:
                new_iso = patch["iso"]
                old_iso = self._iso[idx]
                if new_iso != old_iso:
                    # 受影响阈值 t: (old > t) != (new > t)，即 min <= t < max
                    # （None 视为 -inf，缺失 ISO 的比较恒为假）
                    eff = [x if x is not None else -(1 << 62) for x in (old_iso, new_iso)]
                    lo, hi = min(eff), max(eff)
                    left = bisect_left(self._iso_thresholds, lo)
                    right = bisect_left(self._iso_thresholds, hi)
                    for t in self._iso_thresholds[left:right]:
                        for cid in self._iso_conds[t]:
                            pairs.add((cid, idx))
                    self._iso[idx] = new_iso

            if "keywords" in patch:
                new_kws = frozenset(patch["keywords"])
                old_kws = self._keywords[idx]
                if new_kws != old_kws:
                    before = self._tree.path_set(old_kws)
                    after = self._tree.path_set(new_kws)
                    for node in before ^ after:
                        if node in after:
                            self._tree.set_bit(node, idx)
                        else:
                            self._tree.clear_bit(node, idx)
                        for cid in self._kw_conds.get(node, ()):
                            pairs.add((cid, idx))
                    self._keywords[idx] = new_kws

        self._reevaluate(pairs)

    def move_keyword(self, kid: int, new_parent: int) -> None:
        """把非根关键词连同子树换父节点；资产上的关键词 id 不变。"""
        if self._tree.parent_of(kid) == new_parent:
            return
        # 只有被移动子树内关键词标注过的资产，其祖先链才可能变化
        affected_assets = list(_iter_bits(self._tree.subtree_bitmap(kid)))
        before_paths = []
        for idx in affected_assets:
            self._assets_read += 1
            before_paths.append(self._tree.path_set(self._keywords[idx]))

        self._tree.move(kid, new_parent)

        pairs: set[tuple[int, int]] = set()
        for idx, before in zip(affected_assets, before_paths):
            after = self._tree.path_set(self._keywords[idx])
            for node in before ^ after:
                if node in after:
                    self._tree.set_bit(node, idx)
                else:
                    self._tree.clear_bit(node, idx)
                for cid in self._kw_conds.get(node, ()):
                    pairs.add((cid, idx))
        self._reevaluate(pairs)

    # ---------- 查询与统计 ----------

    def members(self, collection_id: int) -> list[Any]:
        """返回当前集合成员资产 id，顺序自定。"""
        bitmap = self._members[collection_id]
        return [self._ids[idx] for idx in _iter_bits(bitmap)]

    def stats_snapshot(self) -> dict[str, int]:
        """计数器快照：assets_read（读取的资产记录数）、rules_evaluated（求值的规则数）。

        统计的是初始化之后的增量维护开销；对某次操作取前后差值即可。
        """
        return {
            "assets_read": self._assets_read,
            "rules_evaluated": self._rules_evaluated,
        }
