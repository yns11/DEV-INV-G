"""Bill-of-materials index and explosion of work-in-progress assemblies.

Why this is not a one-line join
-------------------------------
The legacy Power Query step ``ECLATEE`` did a single-level *inner* join between
the counted WIP assemblies and ``BOMFINALE``. That is right most of the time and
wrong in three ways that each cost money:

1. **Silent drops.** An inner join makes an assembly with no BOM row vanish.
   The quantity counted on the shop floor simply disappeared from the journal —
   no warning, no line, no trace. Here it is reported as an exception
   (:attr:`ExplosionResult.unknown_parents`) and blocks nothing but is visible.
2. **Phantom levels.** When a BOM level is a *phantom* (a structural grouping
   that carries no ERP stock of its own), stopping at level 1 credits stock to
   an item that has no stock account. Those quantities never matched anything in
   the book stock and ended up as permanent, unexplainable variances.
3. **Cycles.** A self-referencing structure made the workbook recalculate
   forever. Here it raises :class:`BomCycleError` with the offending path.

How deep should an explosion go?
--------------------------------
This is a business question, not a technical one. When a MEL is counted as
"WIP / waiting for decision", its stator has **not** been backflushed yet, so
the ERP still carries the stator as stock in its own right. Crediting the
stator's raw components instead of the stator would double-count.

The rule implemented here is therefore: **stop at the first stock-carrying
item**. A child is expanded further only when it is a phantom — i.e. it appears
as a BOM parent but is not a stock-carrying article. With the default
configuration (every referential article is stock-carrying) this reduces exactly
to the historical single-level behaviour, so results stay comparable with past
campaigns, while phantom chains are now handled correctly.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .models import BomLink, WipBreakdown
from .quantities import ZERO, quantize_qty

__all__ = ["BomCycleError", "BomIndex", "ExplosionResult"]


class BomCycleError(ValueError):
    """The bill of materials contains a cycle.

    A cycle makes "quantity of component per assembly" mathematically undefined,
    so it is surfaced as a data defect rather than worked around.
    """

    def __init__(self, cycle: Sequence[str]) -> None:
        self.cycle = list(cycle)
        super().__init__("Cycle de nomenclature : " + " → ".join(self.cycle))


@dataclass(slots=True)
class ExplosionResult:
    """Outcome of exploding a set of assemblies.

    :ivar components: cumulated quantity per stock-carrying component.
    :ivar breakdown: one row per (parent, child) contribution — the data behind
        the "what is this WIP made of?" drill-down required by the spec.
    :ivar unknown_parents: assemblies referenced by a count but absent from the
        BOM referential. They contribute nothing and must be arbitrated.
    :ivar truncated_parents: assemblies whose phantom chain hit ``max_depth``.
    """

    components: dict[str, Decimal] = field(default_factory=dict)
    breakdown: list[WipBreakdown] = field(default_factory=list)
    unknown_parents: set[str] = field(default_factory=set)
    truncated_parents: set[str] = field(default_factory=set)

    def add(self, item: str, qty: Decimal) -> None:
        self.components[item] = quantize_qty(self.components.get(item, ZERO) + qty)


#: Sentinel pushed on the traversal stack to mark "leaving this node".
_POP = "\x00POP"


class BomIndex:
    """Immutable, query-optimised view of a campaign's bill of materials.

    Built once per consolidation run and reused for every zone, turning the
    explosion from ``O(zones × links)`` joins into indexed traversals with a
    per-parent memo.

    :param links: the frozen BOM edges of the campaign.
    :param excluded_children: items flagged ``BOM`` or ``ALL`` in the exclusion
        referential; they are dropped from every parent's structure.
    :param is_phantom: predicate telling whether a child should be expanded
        further instead of being credited as stock. Defaults to "nothing is a
        phantom", i.e. single-level explosion (ERP-consistent).
    :param max_depth: safety valve on phantom-chain depth.
    """

    __slots__ = (
        "_cache",
        "_children",
        "_excluded",
        "_is_phantom",
        "_max_depth",
        "_truncated",
    )

    def __init__(
        self,
        links: Iterable[BomLink],
        *,
        excluded_children: Iterable[str] = (),
        is_phantom: Callable[[str], bool] | None = None,
        max_depth: int = 10,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        self._excluded = frozenset(excluded_children)
        self._max_depth = max_depth
        self._is_phantom = is_phantom or (lambda _item: False)

        # Merge duplicate (parent, child) edges rather than double-counting.
        # The ERP export repeats a pair when several BOM versions are effective.
        merged: dict[tuple[str, str], Decimal] = {}
        for link in links:
            if link.child_item in self._excluded or link.parent_item in self._excluded:
                continue
            key = (link.parent_item, link.child_item)
            merged[key] = merged.get(key, ZERO) + link.qty_per

        children: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
        for (parent, child), qty in merged.items():
            children[parent].append((child, quantize_qty(qty)))
        for kids in children.values():
            kids.sort()  # deterministic traversal → reproducible breakdowns

        self._children: dict[str, list[tuple[str, Decimal]]] = dict(children)
        #: memoised expansion of one unit of a parent
        self._cache: dict[str, dict[str, Decimal]] = {}
        #: parents whose phantom chain was cut short by ``max_depth``
        self._truncated: set[str] = set()

    # ------------------------------------------------------------ introspection

    def __len__(self) -> int:
        """Number of distinct parent → child edges."""
        return sum(len(v) for v in self._children.values())

    @property
    def parents(self) -> frozenset[str]:
        """Items that have at least one child."""
        return frozenset(self._children)

    def has_bom(self, item: str) -> bool:
        return item in self._children

    def direct_children(self, item: str) -> list[tuple[str, Decimal]]:
        """Level-1 structure of *item* as ``(child, qty_per)`` pairs."""
        return list(self._children.get(item, ()))

    def orphan_parents(self, referenced: Iterable[str]) -> set[str]:
        """Referenced assemblies that have no BOM and cannot be exploded."""
        return {item for item in referenced if item not in self._children}

    def find_cycles(self) -> list[list[str]]:
        """Every distinct cycle in the graph, as item paths.

        Iterative DFS with an explicit stack: a deep or wide structure cannot
        exhaust the Python recursion limit inside the 6 GB app container.
        Traversal follows *every* edge, not only phantom ones, so a cycle is
        reported even if the current phantom configuration would never walk it.
        """
        cycles: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        colour: dict[str, int] = {}  # 0 unvisited, 1 on-stack, 2 finished

        for root in sorted(self._children):
            if colour.get(root, 0) != 0:
                continue
            colour[root] = 1
            path: list[str] = [root]
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                node, idx = stack[-1]
                kids = self._children.get(node, ())
                if idx >= len(kids):
                    colour[node] = 2
                    stack.pop()
                    path.pop()
                    continue
                stack[-1] = (node, idx + 1)
                child = kids[idx][0]
                state = colour.get(child, 0)
                if state == 1:
                    cycle = [*path[path.index(child):], child]
                    sig = tuple(cycle)
                    if sig not in seen:
                        seen.add(sig)
                        cycles.append(cycle)
                elif state == 0 and child in self._children:
                    colour[child] = 1
                    path.append(child)
                    stack.append((child, 0))
                else:
                    colour.setdefault(child, 2)
        return cycles

    # ---------------------------------------------------------------- explosion

    def unit_explosion(self, parent: str) -> dict[str, Decimal]:
        """Stock-carrying quantities consumed by **one** unit of *parent*.

        Memoised, so a component shared by fifty assemblies is expanded once.

        :raises BomCycleError: if a cycle is reachable from *parent* through
            phantom levels.
        """
        cached = self._cache.get(parent)
        if cached is None:
            cached = self._expand(parent)
            self._cache[parent] = cached
        return cached

    def _expand(self, root: str) -> dict[str, Decimal]:
        """Iterative pre-order expansion with path-based cycle detection."""
        totals: dict[str, Decimal] = defaultdict(Decimal)
        truncated = False
        # LIFO of (item, multiplier, depth); _POP marks the end of a subtree.
        stack: deque[tuple[str, Decimal, int]] = deque([(root, Decimal(1), 0)])
        ancestors: list[str] = []

        while stack:
            item, multiplier, depth = stack.pop()

            if item == _POP:
                ancestors.pop()
                continue

            kids = self._children.get(item)
            is_expandable = bool(kids) and (item == root or self._is_phantom(item))

            if not is_expandable:
                # Stock-carrying item (or a leaf): credit it and stop here.
                totals[item] += multiplier
                continue

            if depth >= self._max_depth:
                # Stop descending but keep the quantity: losing it would be a
                # silent stock loss, the exact failure this module removes.
                truncated = True
                totals[item] += multiplier
                continue

            if item in ancestors:
                raise BomCycleError([*ancestors[ancestors.index(item):], item])

            ancestors.append(item)
            stack.append((_POP, ZERO, depth))
            for child, qty_per in kids or ():
                stack.append((child, multiplier * qty_per, depth + 1))

        if truncated:
            self._truncated.add(root)
        return {k: quantize_qty(v) for k, v in totals.items()}

    def explode(
        self,
        assemblies: Mapping[str, Decimal],
        *,
        zone_code: str = "",
        keep_breakdown: bool = True,
    ) -> ExplosionResult:
        """Explode counted *assemblies* (item → quantity) into components.

        :param zone_code: stamped on breakdown rows so the WIP drill-down can be
            filtered by zone.
        :param keep_breakdown: pass ``False`` for large batches where only the
            aggregated component quantities are needed.
        """
        result = ExplosionResult()
        for parent, parent_qty in assemblies.items():
            if parent_qty == 0:
                continue
            if not self.has_bom(parent):
                result.unknown_parents.add(parent)
                continue
            unit = self.unit_explosion(parent)
            if parent in self._truncated:
                result.truncated_parents.add(parent)
            for child, qty_per in unit.items():
                child_qty = quantize_qty(qty_per * parent_qty)
                result.add(child, child_qty)
                if keep_breakdown:
                    result.breakdown.append(
                        WipBreakdown(
                            parent_item=parent,
                            parent_qty=parent_qty,
                            child_item=child,
                            qty_per_parent=qty_per,
                            child_qty=child_qty,
                            depth=1,
                            zone_code=zone_code,
                        )
                    )
        return result
