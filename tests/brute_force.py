"""独立的全量解释器：测试基准用。

不做任何索引与增量维护，每次查询都在当前关键词树上对全部资产
直接解释规则。刻意与组件实现不共享任何代码。
"""

from __future__ import annotations

from typing import Any, Iterable


class BruteForce:
    def __init__(
        self,
        assets: Iterable[dict[str, Any]],
        keyword_parents: dict[int, int | None],
        collections: dict[int, dict[str, Any]],
    ) -> None:
        self.assets = {a["id"]: dict(a, keywords=set(a.get("keywords", ()))) for a in assets}
        self.parents = dict(keyword_parents)
        self.collections = dict(collections)

    def update_assets(self, patches: dict[Any, dict[str, Any]]) -> None:
        for aid, patch in patches.items():
            asset = self.assets[aid]
            if "rating" in patch:
                asset["rating"] = patch["rating"]
            if "iso" in patch:
                asset["iso"] = patch["iso"]
            if "keywords" in patch:
                asset["keywords"] = set(patch["keywords"])

    def move_keyword(self, kid: int, new_parent: int) -> None:
        self.parents[kid] = new_parent

    def _under(self, keyword_ids: set[int], ancestor: int) -> bool:
        """资产关键词集合中是否有任一关键词是 ancestor 的子孙（含自身）。"""
        for kid in keyword_ids:
            node = kid
            while node is not None:
                if node == ancestor:
                    return True
                node = self.parents[node]
        return False

    def _eval(self, rule: dict, asset: dict) -> bool:
        (key, value), = rule.items()
        if key == "all":
            return all(self._eval(c, asset) for c in value)
        if key == "any":
            return any(self._eval(c, asset) for c in value)
        if key == "not":
            return not self._eval(value, asset)
        if key == "rating_ge":
            return asset["rating"] >= value
        if key == "iso_gt":
            iso = asset.get("iso")
            return iso is not None and iso > value
        if key == "keyword":
            return self._under(asset["keywords"], value)
        raise ValueError(key)

    def members(self, collection_id: int) -> set[Any]:
        rule = self.collections[collection_id]
        return {aid for aid, asset in self.assets.items() if self._eval(rule, asset)}
