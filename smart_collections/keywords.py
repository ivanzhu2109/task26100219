"""关键词单根树：维护每个节点的“子树资产位图”。

位图用 Python int 表示，第 i 位对应内部资产下标 i。
子树位图 = 直接或间接以该子树内关键词标注的资产集合。
位图本身不在此处自动维护：调用方（组件）在资产关键词变化或子树移动时，
按 path_set 的差集逐位 set/clear，保证任何时刻位图与树结构一致。
"""

from __future__ import annotations

from typing import Iterable


class KeywordTree:
    def __init__(self, parents: dict[int, int | None]) -> None:
        """parents: 关键词 id -> 父 id，根的父为 None。必须恰好一个根。"""
        if not parents:
            raise ValueError("关键词树不能为空")
        self._parent: dict[int, int | None] = dict(parents)
        self._children: dict[int, list[int]] = {k: [] for k in parents}
        roots = []
        for kid, parent in parents.items():
            if parent is None:
                roots.append(kid)
            else:
                if parent not in parents:
                    raise ValueError(f"关键词 {kid} 的父节点 {parent} 不存在")
                self._children[parent].append(kid)
        if len(roots) != 1:
            raise ValueError(f"关键词树必须恰好一个根，实际 {len(roots)} 个")
        self._root = roots[0]
        self._check_acyclic()
        self._bitmap: dict[int, int] = {}

    def _check_acyclic(self) -> None:
        # 每个节点沿父链必须在有限步内到达根，否则存在环
        for start in self._parent:
            node: int | None = start
            steps = 0
            while node is not None:
                steps += 1
                if steps > len(self._parent):
                    raise ValueError("关键词树存在环")
                node = self._parent[node]

    @property
    def root(self) -> int:
        return self._root

    def parent_of(self, node: int) -> int | None:
        return self._parent[node]

    def __contains__(self, node: int) -> bool:
        return node in self._parent

    def is_descendant(self, node: int, ancestor: int) -> bool:
        """ancestor 是否为 node 的祖先（含二者相等）。"""
        cur: int | None = node
        while cur is not None:
            if cur == ancestor:
                return True
            cur = self._parent[cur]
        return False

    def path_set(self, keyword_ids: Iterable[int]) -> set[int]:
        """一组关键词的祖先链并集（含关键词自身）。"""
        result: set[int] = set()
        for kid in keyword_ids:
            if kid not in self._parent:
                raise ValueError(f"未知关键词 id: {kid}")
            cur: int | None = kid
            while cur is not None and cur not in result:
                result.add(cur)
                cur = self._parent[cur]
        return result

    def move(self, kid: int, new_parent: int) -> None:
        """仅修改树结构：把非根节点换父节点。输入保证移动后仍为树。"""
        if kid == self._root:
            raise ValueError("根节点不能移动")
        if kid not in self._parent or new_parent not in self._parent:
            raise ValueError("未知关键词 id")
        old_parent = self._parent[kid]
        if old_parent == new_parent:
            return
        self._children[old_parent].remove(kid)
        self._children[new_parent].append(kid)
        self._parent[kid] = new_parent

    def subtree_bitmap(self, node: int) -> int:
        """节点子树当前覆盖的资产位图。"""
        return self._bitmap.get(node, 0)

    def set_bitmap(self, node: int, bitmap: int) -> None:
        """整体替换节点的子树位图（初始化批量构建用）。"""
        self._bitmap[node] = bitmap

    def set_bit(self, node: int, asset_idx: int) -> None:
        self._bitmap[node] = self._bitmap.get(node, 0) | (1 << asset_idx)

    def clear_bit(self, node: int, asset_idx: int) -> None:
        self._bitmap[node] = self._bitmap.get(node, 0) & ~(1 << asset_idx)

    def has_bit(self, node: int, asset_idx: int) -> bool:
        return bool(self._bitmap.get(node, 0) & (1 << asset_idx))
