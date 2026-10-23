"""Descubrimiento de rutas (ciclos) en el grafo de pares.

Como harjus: cada par BASE/QUOTE es una arista QUOTE→BASE (comprar) y otra BASE→QUOTE
(vender). Buscamos ciclos que empiecen y acaben en un activo base configurado, de 3 tramos
(triangular) o hasta `max_len`. El índice símbolo → rutas (idea de bmino) permite recalcular
solo las rutas afectadas cuando llega una actualización.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .models import BUY, SELL, Leg, Path, SymbolInfo


def build_paths(
    symbols: dict[str, SymbolInfo],
    bases: list[str],
    assets: set[str] | None,
    max_len: int,
    fee_for: Callable[[str], float],
) -> list[Path]:
    allowed = None if not assets else set(assets) | set(bases)
    adj: dict[str, list[tuple[str, Leg]]] = defaultdict(list)
    for s in symbols.values():
        if allowed is not None and (s.base not in allowed or s.quote not in allowed):
            continue
        fee = fee_for(s.symbol)
        adj[s.quote].append((s.base, Leg(s, BUY, fee)))
        adj[s.base].append((s.quote, Leg(s, SELL, fee)))

    found: list[list[Leg]] = []
    for start in bases:

        def dfs(asset: str, legs: list[Leg], visited: frozenset[str]) -> None:
            for nxt, leg in adj.get(asset, ()):
                if nxt == start:
                    if len(legs) + 1 >= 3:
                        found.append(legs + [leg])
                elif nxt not in visited and len(legs) + 1 < max_len:
                    dfs(nxt, legs + [leg], visited | {nxt})

        dfs(start, [], frozenset({start}))

    paths = []
    for i, legs in enumerate(found):
        start = legs[0].asset_in
        mult = 1.0
        for leg in legs:
            mult *= 1.0 - leg.fee
        name = " → ".join([start] + [leg.asset_out for leg in legs])
        paths.append(Path(i, start, tuple(legs), mult, name, tuple(leg.symbol.symbol for leg in legs)))
    return paths


def build_index(paths: list[Path]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for p in paths:
        for sym in p.symbols:
            index[sym].append(p.id)
    return dict(index)
