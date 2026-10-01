"""Independent naive interpreter used as the test baseline.

Computes collection members by directly evaluating rules over every
asset on the current keyword tree. Deliberately shares no code with
the incremental engine.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

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
)


def _keyword_closure(keywords, keyword_parents) -> Set[int]:
    """All ancestors-or-self of the given keyword ids."""
    closure: Set[int] = set()
    for kw in keywords:
        node = kw
        while node is not None and node not in closure:
            closure.add(node)
            node = keyword_parents[node]
    return closure


def _eval_rule(rule: Rule, asset: Asset, closure: Set[int]) -> bool:
    if isinstance(rule, RatingAtLeast):
        return asset.rating >= rule.value
    if isinstance(rule, IsoGreaterThan):
        return asset.iso is not None and asset.iso > rule.value
    if isinstance(rule, HasKeyword):
        return rule.keyword_id in closure
    if isinstance(rule, All):
        return all(_eval_rule(c, asset, closure) for c in rule.children)
    if isinstance(rule, Any):
        return any(_eval_rule(c, asset, closure) for c in rule.children)
    if isinstance(rule, Not):
        return not _eval_rule(rule.child, asset, closure)
    raise TypeError(f"unknown rule node: {rule!r}")


def collection_members(
    assets: Iterable[Asset],
    keyword_parents: Dict[int, Optional[int]],
    collection: CollectionDef,
) -> Set[int]:
    result: Set[int] = set()
    for asset in assets:
        closure = _keyword_closure(asset.keywords, keyword_parents)
        if _eval_rule(collection.rule, asset, closure):
            result.add(asset.asset_id)
    return result


def all_members(
    assets: Iterable[Asset],
    keyword_parents: Dict[int, Optional[int]],
    collections: Iterable[CollectionDef],
) -> Dict[int, Set[int]]:
    asset_list = list(assets)
    return {
        c.collection_id: collection_members(asset_list, keyword_parents, c)
        for c in collections
    }
