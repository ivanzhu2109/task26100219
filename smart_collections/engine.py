"""Incremental smart-collection maintenance engine.

Design notes
------------
* Collection memberships are stored as big-int bitmaps (bit i = asset
  index i), so 2000 collections x 100k assets cost ~25 MiB and full
  initialization combines leaf bitmaps with C-speed &, |, ^ operators.
* After init, mutations never scan all assets / the whole keyword tree /
  all rules:
  - rating / ISO changes consult sorted threshold indexes to find only
    the collections whose leaf result actually flips;
  - keyword-set changes walk the ancestor chain of the changed keywords
    and look up collections indexed per keyword;
  - MoveKeyword touches only the moved subtree (assets tagged in it) and
    the symmetric difference of the old/new ancestor chains (collections
    with keyword leaves there).
* Affected (asset, collection) pairs are re-evaluated immediately by
  interpreting the collection rule for that single asset, so `not` and
  overlapping multi-label support are handled by plain boolean logic.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .model import (
    All,
    Any,
    Asset,
    CollectionDef,
    HasKeyword,
    IsoGreaterThan,
    Not,
    RatingAtLeast,
    Rule,
    validate_rule,
)

_MISSING_ISO = -1


class SmartCollections:
    def __init__(
        self,
        assets: Iterable[Asset],
        keyword_parents: Dict[int, Optional[int]],
        collections: Iterable[CollectionDef],
    ) -> None:
        self._stats = {"assets_read": 0, "rules_evaluated": 0}

        # ---- keywords ----
        self._kw_ids: List[int] = list(keyword_parents.keys())
        self._kidx: Dict[int, int] = {kid: i for i, kid in enumerate(self._kw_ids)}
        n_kw = len(self._kw_ids)
        self._parent: List[int] = [-1] * n_kw
        self._children: List[List[int]] = [[] for _ in range(n_kw)]
        roots = 0
        for kid, par in keyword_parents.items():
            ki = self._kidx[kid]
            if par is None:
                roots += 1
            else:
                if par not in self._kidx:
                    raise ValueError(f"unknown parent keyword {par!r}")
                pi = self._kidx[par]
                self._parent[ki] = pi
                self._children[pi].append(ki)
        if roots != 1:
            raise ValueError(f"keyword forest must have exactly one root, got {roots}")
        # cycle / connectivity check: BFS from the root must reach all nodes
        root = next(i for i, p in enumerate(self._parent) if p == -1)
        seen = 0
        stack = [root]
        while stack:
            node = stack.pop()
            seen += 1
            stack.extend(self._children[node])
        if seen != n_kw:
            raise ValueError("keyword parents do not form a single tree")

        # ---- assets ----
        self._asset_ids: List[int] = []
        self._aidx: Dict[int, int] = {}
        self._rating = array("b")
        self._iso = array("i")
        self._akw: List[frozenset] = []
        self._kw_assets: List[Set[int]] = [set() for _ in range(n_kw)]
        for asset in assets:
            if asset.asset_id in self._aidx:
                raise ValueError(f"duplicate asset id {asset.asset_id!r}")
            if not 0 <= asset.rating <= 5:
                raise ValueError(f"rating out of range: {asset.rating}")
            if asset.iso is not None and asset.iso < 0:
                raise ValueError(f"iso must be >= 0 or None: {asset.iso}")
            ai = len(self._asset_ids)
            self._aidx[asset.asset_id] = ai
            self._asset_ids.append(asset.asset_id)
            self._rating.append(asset.rating)
            self._iso.append(_MISSING_ISO if asset.iso is None else asset.iso)
            kw_idx = set()
            for kid in asset.keywords:
                if kid not in self._kidx:
                    raise ValueError(f"unknown keyword {kid!r}")
                kw_idx.add(self._kidx[kid])
            self._akw.append(frozenset(kw_idx))
            for ki in kw_idx:
                self._kw_assets[ki].add(ai)
        n_assets = len(self._asset_ids)
        self._all_mask = (1 << n_assets) - 1
        self._nbytes = (n_assets + 7) // 8

        # ---- collections ----
        self._col_ids: List[int] = []
        self._cidx: Dict[int, int] = {}
        self._rules: List[Rule] = []  # HasKeyword carries keyword *indices*
        self._has_kw_leaf: List[bool] = []
        rating_thresh: List[Tuple[int, int]] = []
        iso_thresh: List[Tuple[int, int]] = []
        self._kw_cols: List[List[int]] = [[] for _ in range(n_kw)]
        for col in collections:
            if col.collection_id in self._cidx:
                raise ValueError(f"duplicate collection id {col.collection_id!r}")
            validate_rule(col.rule)
            ci = len(self._col_ids)
            self._cidx[col.collection_id] = ci
            self._col_ids.append(col.collection_id)
            has_kw = False

            def visit(node: Rule) -> Rule:
                nonlocal has_kw
                if isinstance(node, RatingAtLeast):
                    rating_thresh.append((node.value, ci))
                    return node
                if isinstance(node, IsoGreaterThan):
                    iso_thresh.append((node.value, ci))
                    return node
                if isinstance(node, HasKeyword):
                    if node.keyword_id not in self._kidx:
                        raise ValueError(f"unknown keyword {node.keyword_id!r}")
                    ki = self._kidx[node.keyword_id]
                    self._kw_cols[ki].append(ci)
                    has_kw = True
                    return HasKeyword(ki)
                if isinstance(node, All):
                    return All(tuple(visit(c) for c in node.children))
                if isinstance(node, Any):
                    return Any(tuple(visit(c) for c in node.children))
                if isinstance(node, Not):
                    return Not(visit(node.child))
                raise TypeError(f"unknown rule node: {node!r}")

            self._rules.append(visit(col.rule))
            self._has_kw_leaf.append(has_kw)

        rating_thresh.sort()
        iso_thresh.sort()
        self._rating_thresh_vals = [t for t, _ in rating_thresh]
        self._rating_thresh_cols = [c for _, c in rating_thresh]
        self._iso_thresh_vals = [t for t, _ in iso_thresh]
        self._iso_thresh_cols = [c for _, c in iso_thresh]

        # ---- full initial computation via leaf bitmaps ----
        self._rating_bm_cache: Dict[int, int] = {}
        self._iso_bm_cache: Dict[int, int] = {}
        self._kw_bm_cache: Dict[int, int] = {}
        self._build_keyword_bitmaps()
        self._member: List[int] = [self._combine(rule) for rule in self._rules]

    # ------------------------------------------------------------------
    # bitmap helpers (bit i of a bitmap == asset index i)
    # ------------------------------------------------------------------
    def _bitmap_from_indices(self, indices: Iterable[int]) -> int:
        ba = bytearray(self._nbytes)
        for ai in indices:
            ba[ai >> 3] |= 1 << (ai & 7)
        return int.from_bytes(ba, "little")

    def _rating_bitmap(self, value: int) -> int:
        bm = self._rating_bm_cache.get(value)
        if bm is None:
            bm = self._bitmap_from_indices(
                ai for ai, r in enumerate(self._rating) if r >= value
            )
            self._rating_bm_cache[value] = bm
        return bm

    def _iso_bitmap(self, value: int) -> int:
        bm = self._iso_bm_cache.get(value)
        if bm is None:
            bm = self._bitmap_from_indices(
                ai for ai, v in enumerate(self._iso) if v > value
            )
            self._iso_bm_cache[value] = bm
        return bm

    def _build_keyword_bitmaps(self) -> None:
        """One post-order pass over the tree; keeps bitmaps only for
        keywords referenced by HasKeyword leaves."""
        needed = {ki for ki, cols in enumerate(self._kw_cols) if cols}
        if not needed:
            return
        root = next(i for i, p in enumerate(self._parent) if p == -1)
        order = [root]
        for node in order:  # BFS/DFS preorder; children appended
            order.extend(self._children[node])
        pending: Dict[int, int] = {}
        for node in reversed(order):
            bm = self._bitmap_from_indices(self._kw_assets[node])
            for child in self._children[node]:
                bm |= pending.pop(child)
            if node in needed:
                self._kw_bm_cache[node] = bm
            pending[node] = bm

    def _keyword_bitmap(self, ki: int) -> int:
        return self._kw_bm_cache[ki]

    def _combine(self, rule: Rule) -> int:
        if isinstance(rule, RatingAtLeast):
            return self._rating_bitmap(rule.value)
        if isinstance(rule, IsoGreaterThan):
            return self._iso_bitmap(rule.value)
        if isinstance(rule, HasKeyword):
            return self._keyword_bitmap(rule.keyword_id)
        if isinstance(rule, All):
            bm = self._all_mask
            for child in rule.children:
                bm &= self._combine(child)
            return bm
        if isinstance(rule, Any):
            bm = 0
            for child in rule.children:
                bm |= self._combine(child)
            return bm
        if isinstance(rule, Not):
            return self._all_mask ^ self._combine(rule.child)
        raise TypeError(f"unknown rule node: {rule!r}")

    # ------------------------------------------------------------------
    # per-asset rule evaluation (incremental path)
    # ------------------------------------------------------------------
    def _keyword_closure(self, ai: int) -> Set[int]:
        closure: Set[int] = set()
        for kw in self._akw[ai]:
            node = kw
            while node != -1 and node not in closure:
                closure.add(node)
                node = self._parent[node]
        return closure

    def _eval(self, rule: Rule, ai: int, closure: Optional[Set[int]]) -> bool:
        if isinstance(rule, RatingAtLeast):
            return self._rating[ai] >= rule.value
        if isinstance(rule, IsoGreaterThan):
            iso = self._iso[ai]
            return iso != _MISSING_ISO and iso > rule.value
        if isinstance(rule, HasKeyword):
            return rule.keyword_id in closure
        if isinstance(rule, All):
            return all(self._eval(c, ai, closure) for c in rule.children)
        if isinstance(rule, Any):
            return any(self._eval(c, ai, closure) for c in rule.children)
        if isinstance(rule, Not):
            return not self._eval(rule.child, ai, closure)
        raise TypeError(f"unknown rule node: {rule!r}")

    def _reeval(self, ai: int, ci: int) -> None:
        self._stats["rules_evaluated"] += 1
        closure = self._keyword_closure(ai) if self._has_kw_leaf[ci] else None
        bit = 1 << ai
        if self._eval(self._rules[ci], ai, closure):
            self._member[ci] |= bit
        else:
            self._member[ci] &= ~bit

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------
    def update_assets(self, patches: Iterable[dict]) -> None:
        for patch in patches:
            ai = self._aidx[patch["asset_id"]]
            affected: Set[int] = set()

            if "rating" in patch:
                new_rating = patch["rating"]
                if not 0 <= new_rating <= 5:
                    raise ValueError(f"rating out of range: {new_rating}")
                old_rating = self._rating[ai]
                if new_rating != old_rating:
                    lo, hi = min(old_rating, new_rating), max(old_rating, new_rating)
                    left = bisect_right(self._rating_thresh_vals, lo)
                    right = bisect_right(self._rating_thresh_vals, hi)
                    affected.update(self._rating_thresh_cols[left:right])
                    self._rating[ai] = new_rating

            if "iso" in patch:
                new_iso = patch["iso"]
                if new_iso is not None and new_iso < 0:
                    raise ValueError(f"iso must be >= 0 or None: {new_iso}")
                new_eff = _MISSING_ISO if new_iso is None else new_iso
                old_eff = self._iso[ai]
                if new_eff != old_eff:
                    lo, hi = min(old_eff, new_eff), max(old_eff, new_eff)
                    left = bisect_left(self._iso_thresh_vals, lo)
                    right = bisect_left(self._iso_thresh_vals, hi)
                    affected.update(self._iso_thresh_cols[left:right])
                    self._iso[ai] = new_eff

            if "keywords" in patch:
                new_kws = set()
                for kid in patch["keywords"]:
                    if kid not in self._kidx:
                        raise ValueError(f"unknown keyword {kid!r}")
                    new_kws.add(self._kidx[kid])
                old_kws = self._akw[ai]
                if new_kws != old_kws:
                    changed = (new_kws - old_kws) | (old_kws - new_kws)
                    for ki in new_kws - old_kws:
                        self._kw_assets[ki].add(ai)
                    for ki in old_kws - new_kws:
                        self._kw_assets[ki].discard(ai)
                    self._akw[ai] = frozenset(new_kws)
                    for ki in changed:
                        node = ki
                        while node != -1:
                            affected.update(self._kw_cols[node])
                            node = self._parent[node]

            if affected:
                self._stats["assets_read"] += 1
                for ci in affected:
                    self._reeval(ai, ci)

    def move_keyword(self, keyword_id: int, new_parent: int) -> None:
        if keyword_id not in self._kidx:
            raise ValueError(f"unknown keyword {keyword_id!r}")
        if new_parent not in self._kidx:
            raise ValueError(f"unknown keyword {new_parent!r}")
        k = self._kidx[keyword_id]
        np = self._kidx[new_parent]
        old_parent = self._parent[k]
        if old_parent == -1:
            raise ValueError("cannot move the root keyword")
        if old_parent == np:
            return

        old_chain = self._ancestors_or_self(old_parent)
        new_chain = self._ancestors_or_self(np)
        affected_kws = old_chain ^ new_chain
        affected_cols: Set[int] = set()
        for ki in affected_kws:
            affected_cols.update(self._kw_cols[ki])

        affected_assets: Set[int] = set()
        stack = [k]
        while stack:
            node = stack.pop()
            affected_assets.update(self._kw_assets[node])
            stack.extend(self._children[node])

        self._children[old_parent].remove(k)
        self._parent[k] = np
        self._children[np].append(k)

        for ai in affected_assets:
            if affected_cols:
                self._stats["assets_read"] += 1
            for ci in affected_cols:
                self._reeval(ai, ci)

    def _ancestors_or_self(self, ki: int) -> Set[int]:
        chain: Set[int] = set()
        node = ki
        while node != -1:
            chain.add(node)
            node = self._parent[node]
        return chain

    # ------------------------------------------------------------------
    # queries / instrumentation
    # ------------------------------------------------------------------
    def members(self, collection_id: int) -> List[int]:
        ci = self._cidx[collection_id]
        bm = self._member[ci]
        out: List[int] = []
        while bm:
            lsb = bm & -bm
            out.append(self._asset_ids[lsb.bit_length() - 1])
            bm ^= lsb
        return out

    def reset_stats(self) -> None:
        self._stats = {"assets_read": 0, "rules_evaluated": 0}

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)
