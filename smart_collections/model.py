"""Data model: immutable definitions for assets, keyword tree and rules.

Rules are nested all/any/not combinations with three leaf types:
- RatingAtLeast(v): rating >= v
- IsoGreaterThan(v): ISO > v (false when ISO is missing)
- HasKeyword(k): asset carries keyword k or any descendant of k
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

MAX_LEAVES = 16
MAX_DEPTH = 8


@dataclass(frozen=True)
class RatingAtLeast:
    value: int


@dataclass(frozen=True)
class IsoGreaterThan:
    value: int


@dataclass(frozen=True)
class HasKeyword:
    keyword_id: int


Leaf = Union[RatingAtLeast, IsoGreaterThan, HasKeyword]


@dataclass(frozen=True)
class All:
    children: Tuple["Rule", ...]


@dataclass(frozen=True)
class Any:
    children: Tuple["Rule", ...]


@dataclass(frozen=True)
class Not:
    child: "Rule"


Rule = Union[Leaf, All, Any, Not]


@dataclass(frozen=True)
class CollectionDef:
    collection_id: int
    rule: Rule


@dataclass(frozen=True)
class Asset:
    asset_id: int
    rating: int  # 0..5
    iso: Optional[int]  # may be missing
    keywords: frozenset  # set of keyword ids


def count_leaves(rule: Rule) -> int:
    if isinstance(rule, (All, Any)):
        return sum(count_leaves(c) for c in rule.children)
    if isinstance(rule, Not):
        return count_leaves(rule.child)
    return 1


def rule_depth(rule: Rule) -> int:
    if isinstance(rule, (All, Any)):
        return 1 + max((rule_depth(c) for c in rule.children), default=0)
    if isinstance(rule, Not):
        return 1 + rule_depth(rule.child)
    return 1


def validate_rule(rule: Rule) -> None:
    """Raise ValueError unless leaves <= 16 and depth <= 8."""
    leaves = count_leaves(rule)
    if leaves > MAX_LEAVES:
        raise ValueError(f"rule has {leaves} leaves, max is {MAX_LEAVES}")
    depth = rule_depth(rule)
    if depth > MAX_DEPTH:
        raise ValueError(f"rule depth is {depth}, max is {MAX_DEPTH}")
