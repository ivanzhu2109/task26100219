"""集合规则的表示、校验与求值。

规则用嵌套 dict 表示：
- {"all": [子规则...]}   全部子规则为真
- {"any": [子规则...]}   任一子规则为真
- {"not": 子规则}        布尔取反
- 叶条件：
  - {"rating_ge": int}   rating >= 整数
  - {"iso_gt": int}      ISO > 整数（ISO 缺失时为假）
  - {"keyword": int}     具有某关键词或其后代
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

MAX_LEAVES = 16
MAX_DEPTH = 8

Rule = dict[str, Any]

_LEAF_KEYS = ("rating_ge", "iso_gt", "keyword")


def validate_rule(rule: Rule) -> None:
    """校验规则结构，不合法时抛 ValueError。"""
    leaves = 0

    def walk(node: Rule, depth: int) -> None:
        nonlocal leaves
        if depth > MAX_DEPTH:
            raise ValueError(f"规则深度超过 {MAX_DEPTH}")
        if not isinstance(node, dict) or len(node) != 1:
            raise ValueError(f"规则节点必须是单键 dict: {node!r}")
        (key, value), = node.items()
        if key in ("all", "any"):
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError(f"{key} 需要非空子规则列表")
            for child in value:
                walk(child, depth + 1)
        elif key == "not":
            walk(value, depth + 1)
        elif key in ("rating_ge", "iso_gt"):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{key} 需要整数阈值: {value!r}")
            leaves += 1
        elif key == "keyword":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"keyword 需要整数关键词 id: {value!r}")
            leaves += 1
        else:
            raise ValueError(f"未知规则节点: {key!r}")

    walk(rule, 1)
    if leaves > MAX_LEAVES:
        raise ValueError(f"叶条件数量 {leaves} 超过 {MAX_LEAVES}")


def iter_leaves(rule: Rule) -> Iterator[Rule]:
    """遍历规则中的所有叶条件。"""
    (key, value), = rule.items()
    if key in ("all", "any"):
        for child in value:
            yield from iter_leaves(child)
    elif key == "not":
        yield from iter_leaves(value)
    else:
        yield rule


def evaluate(
    rule: Rule,
    rating: int,
    iso: int | None,
    kw_match: Callable[[int], bool],
) -> bool:
    """对单个资产直接解释规则。kw_match(kid) 表示资产是否命中关键词子树。"""
    (key, value), = rule.items()
    if key == "all":
        return all(evaluate(c, rating, iso, kw_match) for c in value)
    if key == "any":
        return any(evaluate(c, rating, iso, kw_match) for c in value)
    if key == "not":
        return not evaluate(value, rating, iso, kw_match)
    if key == "rating_ge":
        return rating >= value
    if key == "iso_gt":
        return iso is not None and iso > value
    if key == "keyword":
        return kw_match(value)
    raise ValueError(f"未知规则节点: {key!r}")
