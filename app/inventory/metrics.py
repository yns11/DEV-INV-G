"""Ce que le conteneur sait de lui-même, sans base de données.

Les journaux portaient déjà une ligne par requête — méthode, chemin, statut,
durée. Cela répond à « qu'est-il arrivé à cette requête-là », jamais à « le
jour d'inventaire, qu'est-ce qui est lent ». Trouver la réponse demandait
d'exporter des heures de journaux et de les agréger à la main, ce qui se fait
une fois, après coup, et jamais pendant.

Ce registre tient les mêmes chiffres en mémoire, agrégés au passage. Il coûte
un verrou et quelques centaines d'octets par route, et il répond en une lecture
à ce qu'il fallait sinon reconstituer.

**En mémoire, et assumé.** Un conteneur d'application Databricks est recyclé,
et ces compteurs repartent alors de zéro : ils décrivent *ce processus-ci
depuis son démarrage*, ce que ``uptimeSeconds`` dit explicitement. Les faire
survivre voudrait dire les écrire en base à chaque requête — un aller-retour
supplémentaire sur le chemin critique de toutes les requêtes, pour une
statistique. Ce qui doit survivre est déjà en base : les chargements, les
scans, la fraîcheur du miroir.

**Le gabarit de route, jamais le chemin.** ``/campaigns/{campaign_id}/items``
est une série ; ``/campaigns/abc-123/items`` en serait une par campagne, et la
mémoire du registre croîtrait avec l'usage. C'est la faute classique de ce
genre d'outil, et elle ne se voit qu'en production.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

#: Combien de durées récentes on garde par route pour en tirer des quantiles.
#: Assez pour que le p95 veuille dire quelque chose, assez peu pour que deux
#: cents routes tiennent dans quelques mégaoctets.
WINDOW = 512

#: Plafond de séries. Une route inconnue — un scan de vulnérabilité qui essaie
#: mille chemins — ne doit pas pouvoir faire grossir ce dictionnaire sans fin.
#: Au-delà, tout tombe dans une série commune plutôt que d'être perdu.
MAX_ROUTES = 200

#: Là où va ce qui dépasse le plafond.
OVERFLOW = "(autres)"


class _Route:
    """Les compteurs d'une route, et ses dernières durées."""

    __slots__ = ("count", "errors", "max_ms", "recent", "server_errors", "total_ms")

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.server_errors = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.recent: deque[float] = deque(maxlen=WINDOW)


def _quantile(sorted_values: list[float], q: float) -> float:
    """Le quantile d'une liste déjà triée, au plus proche rang.

    Pas d'interpolation : sur des durées de requêtes, elle inventerait une
    valeur qui n'a jamais été mesurée, et la lecture porte justement sur ce qui
    est arrivé.
    """
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, round(q * (len(sorted_values) - 1)))
    return sorted_values[index]


class Registry:
    """Les requêtes vues par ce processus, agrégées par route.

    Protégé par un verrou : les points de terminaison synchrones de FastAPI
    s'exécutent dans un pool de fils, donc deux requêtes incrémentent
    réellement les mêmes compteurs en même temps.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[tuple[str, str], _Route] = {}
        self._started = time.monotonic()

    def observe(self, method: str, route: str, status: int, duration_ms: float) -> None:
        key = (method, route)
        with self._lock:
            entry = self._routes.get(key)
            if entry is None:
                if len(self._routes) >= MAX_ROUTES:
                    key = (method, OVERFLOW)
                    entry = self._routes.get(key)
                if entry is None:
                    entry = self._routes[key] = _Route()
            entry.count += 1
            entry.total_ms += duration_ms
            entry.max_ms = max(entry.max_ms, duration_ms)
            entry.recent.append(duration_ms)
            if status >= 500:
                entry.server_errors += 1
                entry.errors += 1
            elif status >= 400:
                entry.errors += 1

    def snapshot(self) -> dict[str, Any]:
        """L'état courant, trié du plus lent au plus rapide.

        L'ordre n'est pas cosmétique : la question posée à ce tableau est
        « qu'est-ce qui est lent », et la réponse doit être la première ligne.
        """
        with self._lock:
            rows = [
                (method, route, entry.count, entry.errors, entry.server_errors,
                 entry.total_ms, entry.max_ms, sorted(entry.recent))
                for (method, route), entry in self._routes.items()
            ]
        routes = [
            {
                "method": method,
                "route": route,
                "count": count,
                "errors": errors,
                "serverErrors": server_errors,
                "avgMs": round(total_ms / count, 1) if count else 0.0,
                "p50Ms": round(_quantile(recent, 0.50), 1),
                "p95Ms": round(_quantile(recent, 0.95), 1),
                "maxMs": round(max_ms, 1),
            }
            for method, route, count, errors, server_errors, total_ms, max_ms, recent
            in rows
        ]
        routes.sort(key=lambda r: r["p95Ms"], reverse=True)
        return {
            "uptimeSeconds": round(time.monotonic() - self._started, 1),
            "requests": sum(r["count"] for r in routes),
            "errors": sum(r["errors"] for r in routes),
            "serverErrors": sum(r["serverErrors"] for r in routes),
            # Le p95 est calculé sur les dernières requêtes, pas depuis le
            # démarrage : le dire évite de lire « p95 » comme une garantie sur
            # la journée entière.
            "windowSize": WINDOW,
            "routes": routes,
        }

    def reset(self) -> None:
        """Repartir de zéro. Réservé aux tests — rien ne l'appelle en service."""
        with self._lock:
            self._routes.clear()
            self._started = time.monotonic()


#: Le registre du processus. Un seul, comme le processus.
REGISTRY = Registry()
