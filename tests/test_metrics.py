"""Ce que l'exploitant peut mesurer.

Les journaux portaient une ligne par requête. Cela répond à « qu'est-il arrivé
à cette requête-là », jamais à « ce matin, qu'est-ce qui est lent ». Répondre à
la seconde demandait d'exporter des heures de journaux et de les agréger à la
main — ce qui se fait une fois, après coup, et jamais pendant l'inventaire.

Quatre choses manquaient, et chacune s'était déjà manifestée sous une forme
qu'on ne savait pas nommer. Un **pool épuisé** se voyait comme des requêtes qui
attendent quinze secondes puis échouent. Une **latence** se voyait comme « c'est
lent aujourd'hui ». Un **miroir ERP périmé** se voyait comme des écarts faux,
des jours plus tard. Un **contrat mal accordé** rejetant trois lignes par
fichier ne se voyait pas du tout.

Les compteurs HTTP vivent en mémoire du processus et le disent
(``uptimeSeconds``) ; ce qui doit survivre à un recyclage est déjà en base.
"""

from __future__ import annotations

import ast
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import forget_ambient_postgres

from inventory.metrics import MAX_ROUTES, OVERFLOW, WINDOW, Registry, _quantile

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def registry() -> Registry:
    return Registry()


# --------------------------------------------------------------------------- #
# Le registre
# --------------------------------------------------------------------------- #

class TestItCountsWhatHappened:
    def test_a_request_is_counted(self, registry):
        registry.observe("GET", "/api/campaigns", 200, 12.0)
        assert registry.snapshot()["requests"] == 1

    def test_two_calls_to_the_same_route_share_a_line(self, registry):
        registry.observe("GET", "/api/campaigns", 200, 12.0)
        registry.observe("GET", "/api/campaigns", 200, 20.0)
        assert len(registry.snapshot()["routes"]) == 1
        assert registry.snapshot()["routes"][0]["count"] == 2

    def test_the_method_separates_two_lines(self, registry):
        """POST /campaigns et GET /campaigns n'ont ni le coût ni le risque."""
        registry.observe("GET", "/api/campaigns", 200, 5.0)
        registry.observe("POST", "/api/campaigns", 201, 5.0)
        assert len(registry.snapshot()["routes"]) == 2

    def test_a_client_error_counts_as_an_error(self, registry):
        registry.observe("GET", "/api/campaigns", 404, 5.0)
        assert registry.snapshot()["errors"] == 1

    def test_a_client_error_is_not_a_server_error(self, registry):
        """Un 404 est un utilisateur qui se trompe ; un 500 est nous."""
        registry.observe("GET", "/api/campaigns", 404, 5.0)
        assert registry.snapshot()["serverErrors"] == 0

    def test_a_server_error_counts_as_both(self, registry):
        registry.observe("GET", "/api/campaigns", 500, 5.0)
        snapshot = registry.snapshot()
        assert (snapshot["errors"], snapshot["serverErrors"]) == (1, 1)

    def test_a_success_counts_as_neither(self, registry):
        registry.observe("GET", "/api/campaigns", 204, 5.0)
        assert registry.snapshot()["errors"] == 0


class TestItSaysHowLongThingsTake:
    def test_the_average_is_an_average(self, registry):
        for ms in (10.0, 20.0, 30.0):
            registry.observe("GET", "/r", 200, ms)
        assert registry.snapshot()["routes"][0]["avgMs"] == 20.0

    def test_the_maximum_is_kept(self, registry):
        """C'est la requête qui a fait dire « ça a planté »."""
        for ms in (10.0, 900.0, 30.0):
            registry.observe("GET", "/r", 200, ms)
        assert registry.snapshot()["routes"][0]["maxMs"] == 900.0

    def test_the_median_ignores_the_outlier(self, registry):
        for ms in (10.0, 10.0, 10.0, 10.0, 900.0):
            registry.observe("GET", "/r", 200, ms)
        assert registry.snapshot()["routes"][0]["p50Ms"] == 10.0

    def test_the_p95_does_not(self, registry):
        """Le p95 est ce qu'on lit : la lenteur qu'une personne sur dix subit
        doit y apparaître, là où la médiane la noie."""
        for ms in [10.0] * 90 + [900.0] * 10:
            registry.observe("GET", "/r", 200, ms)
        routes = registry.snapshot()["routes"]
        assert routes[0]["p95Ms"] == 900.0
        assert routes[0]["p50Ms"] == 10.0

    def test_the_quantile_never_invents_a_value(self):
        """Une interpolation donnerait une durée que rien n'a mesurée."""
        values = [1.0, 2.0, 100.0]
        assert _quantile(values, 0.5) in values
        assert _quantile(values, 0.9) in values

    def test_an_empty_route_has_no_quantile_rather_than_a_crash(self):
        assert _quantile([], 0.95) == 0.0

    def test_the_slowest_route_comes_first(self, registry):
        """La question posée est « qu'est-ce qui est lent » ; la réponse doit
        être la première ligne, pas la douzième."""
        registry.observe("GET", "/rapide", 200, 5.0)
        registry.observe("GET", "/lent", 200, 4000.0)
        assert registry.snapshot()["routes"][0]["route"] == "/lent"


class TestItStaysSmall:
    def test_only_the_recent_durations_are_kept(self, registry):
        """Garder toutes les durées d'une journée ferait grossir sans fin."""
        for _ in range(WINDOW * 3):
            registry.observe("GET", "/r", 200, 1.0)
        entry = registry._routes[("GET", "/r")]
        assert len(entry.recent) == WINDOW

    def test_the_cumulative_count_is_not_windowed(self, registry):
        """Le nombre de requêtes, lui, est celui depuis le démarrage."""
        for _ in range(WINDOW * 3):
            registry.observe("GET", "/r", 200, 1.0)
        assert registry.snapshot()["routes"][0]["count"] == WINDOW * 3

    def test_an_unbounded_stream_of_routes_does_not_grow_forever(self, registry):
        """Un scan de vulnérabilité essaie mille chemins inventés."""
        for i in range(MAX_ROUTES * 3):
            registry.observe("GET", f"/inconnue-{i}", 404, 1.0)
        assert len(registry._routes) <= MAX_ROUTES + 1

    def test_what_overflows_is_folded_rather_than_lost(self, registry):
        for i in range(MAX_ROUTES * 3):
            registry.observe("GET", f"/inconnue-{i}", 404, 1.0)
        assert registry.snapshot()["requests"] == MAX_ROUTES * 3
        assert any(r["route"] == OVERFLOW for r in registry.snapshot()["routes"])

    def test_the_window_size_is_declared(self, registry):
        """« p95 » sans dire sur quoi se lit comme une garantie sur la journée."""
        assert registry.snapshot()["windowSize"] == WINDOW


@pytest.fixture
def preemptive():
    """Faire commuter les fils sans arrêt, le temps d'un contrôle.

    Par défaut CPython ne rend la main qu'au bout de cinq millisecondes. Une
    section critique qui dure quelques microsecondes n'est donc presque jamais
    interrompue, et une absence de verrou passe inaperçue — sur cette machine,
    ce jour-là. C'est exactement le défaut qui attend la production, où les
    requêtes sont plus lentes et les commutations plus fréquentes : abaisser
    l'intervalle est ce qui rend le contrôle honnête.
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


@pytest.mark.usefixtures("preemptive")
class TestTwoRequestsAtOnce:
    def test_nothing_is_lost_when_ten_threads_count_together(self, registry):
        """Les routes synchrones de FastAPI tournent dans un pool de fils :
        deux requêtes incrémentent réellement le même compteur en même temps."""
        start = threading.Barrier(10)

        def hammer() -> None:
            start.wait()
            for _ in range(20_000):
                registry.observe("GET", "/r", 200, 1.0)

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert registry.snapshot()["requests"] == 200_000

    def test_the_series_ceiling_holds_when_ten_threads_open_new_routes(
        self, registry, monkeypatch
    ):
        """« Regarder puis insérer » n'est pas une seule opération.

        Sans verrou, dix fils lisent tous ``len(routes) < MAX`` avant qu'aucun
        n'ait inséré, et le plafond est franchi d'autant. Ce n'est pas
        théorique : le plafond existe précisément pour un scan qui ouvre des
        chemins inventés aussi vite qu'il peut, donc en parallèle.
        """
        monkeypatch.setattr("inventory.metrics.MAX_ROUTES", 50)
        start = threading.Barrier(10)

        def hammer(worker: int) -> None:
            start.wait()
            for i in range(500):
                registry.observe("GET", f"/w{worker}-{i}", 404, 1.0)

        threads = [threading.Thread(target=hammer, args=(w,)) for w in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(registry._routes) <= 51

    def test_a_snapshot_during_a_write_does_not_raise(self, registry, monkeypatch):
        """Itérer un dictionnaire pendant qu'un autre fil y insère lève
        « dictionary changed size during iteration » — sans le verrou.

        Le plafond de séries est relevé le temps du contrôle : sinon le
        dictionnaire cesse de grandir au bout de deux cents routes, et c'est
        justement sa croissance qui provoque la faute.
        """
        monkeypatch.setattr("inventory.metrics.MAX_ROUTES", 10_000)
        errors: list[BaseException] = []

        def write() -> None:
            for i in range(2000):
                registry.observe("GET", f"/r-{i}", 200, 1.0)
                # Rendre la main : sans cela le fil d'écriture insère ses deux
                # mille routes avant le premier instantané, et il n'y a plus
                # d'entrelacement à observer.
                time.sleep(0)

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            # Deux cents lectures suffisent : sans verrou, la faute tombe dès
            # les premières.
            for _ in range(200):
                if not writer.is_alive():
                    break
                try:
                    registry.snapshot()
                except BaseException as exc:
                    errors.append(exc)
        finally:
            writer.join()
        assert errors == []


# --------------------------------------------------------------------------- #
# La route sous laquelle on compte
# --------------------------------------------------------------------------- #

class TestTheSeriesIsTheRouteTemplate:
    """La faute classique de ce genre d'outil, et elle ne se voit qu'en usage."""

    def app_source(self) -> str:
        return (ROOT / "app" / "inventory" / "api" / "app.py").read_text()

    def test_the_middleware_does_not_count_the_raw_path(self):
        source = self.app_source()
        block = source[source.index("REGISTRY.observe(") :][:200]
        assert "request.url.path" not in block

    def test_it_asks_the_scope_for_the_matched_route(self):
        assert 'request.scope.get("route")' in self.app_source()

    def test_a_request_that_matched_nothing_has_one_name(self):
        """Sans repli, mille chemins inventés feraient mille séries."""
        from types import SimpleNamespace

        from inventory.api.app import _route_of

        unmatched = SimpleNamespace(scope={}, url=SimpleNamespace(path="/api/xyz"))
        assert _route_of(unmatched) == "(inconnue)"

    def test_a_matched_route_gives_its_template(self):
        from types import SimpleNamespace

        from inventory.api.app import _route_of

        matched = SimpleNamespace(
            scope={"route": SimpleNamespace(path="/api/campaigns/{campaign_id}/items")}
        )
        assert _route_of(matched) == "/api/campaigns/{campaign_id}/items"


# --------------------------------------------------------------------------- #
# Le point de terminaison
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _no_ambient_postgres(request, monkeypatch):
    """Les contrôles de contrat parlent d'une installation sans Lakebase.

    Sans cela, le shell du développeur — ou la partie ``postgres`` de cette
    suite — leur donne une base, et ils vérifient autre chose que ce qu'ils
    disent.
    """
    if request.node.get_closest_marker("postgres"):
        return
    forget_ambient_postgres(monkeypatch)


class TestTheEndpoint:
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from inventory.api import app as module
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ENV", "local")
        get_settings.cache_clear()
        return TestClient(module.create_app())

    def test_it_answers(self, monkeypatch):
        with self.client(monkeypatch) as client:
            assert client.get("/api/metrics").status_code == 200

    def test_it_answers_even_without_a_database(self, monkeypatch):
        """C'est la page qu'on vient lire *parce que* quelque chose ne va pas :
        elle ne doit pas être la deuxième chose qui ne marche pas."""
        with self.client(monkeypatch) as client:
            body = client.get("/api/metrics").json()
        assert body["erpMirror"] == {"error": "Lakebase non configuré."}
        assert body["pool"] == {"error": "Lakebase non configuré."}

    def test_it_carries_the_four_families(self, monkeypatch):
        with self.client(monkeypatch) as client:
            body = client.get("/api/metrics").json()
        for key in ("http", "pool", "erpMirror", "imports", "scanJobs"):
            assert key in body, key

    def test_the_call_counts_itself(self, monkeypatch):
        with self.client(monkeypatch) as client:
            client.get("/api/health")
            body = client.get("/api/metrics").json()
        assert body["http"]["requests"] >= 1
        assert any(r["route"] == "/api/health" for r in body["http"]["routes"])

    def test_the_window_is_a_parameter(self, monkeypatch):
        with self.client(monkeypatch) as client:
            assert client.get("/api/metrics?hours=48").status_code == 200

    @pytest.mark.parametrize("bad", ["hours=0", "hours=169", "hours=-1"])
    def test_an_impossible_window_is_refused(self, monkeypatch, bad):
        with self.client(monkeypatch) as client:
            assert client.get(f"/api/metrics?{bad}").status_code == 422

    def test_it_is_not_prometheus_text(self, monkeypatch):
        """Rien ne scrute ce conteneur : une seconde sérialisation serait un
        format de plus à tenir à jour, que personne n'analyserait."""
        with self.client(monkeypatch) as client:
            response = client.get("/api/metrics")
        assert response.headers["content-type"].startswith("application/json")

    def test_the_diagnostic_page_links_to_it(self):
        source = (ROOT / "app" / "inventory" / "api" / "app.py").read_text()
        assert '<a href="/api/metrics">/api/metrics</a>' in source


# --------------------------------------------------------------------------- #
# Les lectures d'exploitation
# --------------------------------------------------------------------------- #

class TestTheOperationsQueries:
    def repo(self, rows):
        from inventory.db.repositories import OperationsRepository

        repo = OperationsRepository.__new__(OperationsRepository)
        seen: list[str] = []

        def fetch_all(query, params=(), *, conn=None):
            seen.append(" ".join(query.split()))
            return rows

        def fetch_one(query, params=(), *, conn=None):
            seen.append(" ".join(query.split()))
            return rows[0] if rows else None

        repo._fetch_all = fetch_all  # type: ignore[method-assign]
        repo._fetch_one = fetch_one  # type: ignore[method-assign]
        return repo, seen

    def test_the_freshness_covers_every_mirror_table(self):
        from inventory.db.repositories import OperationsRepository

        repo, _ = self.repo([])
        got = repo.erp_freshness()
        assert len(got) == len(OperationsRepository.MIRRORS)
        assert {r["table"] for r in got} == {t for t, _ in OperationsRepository.MIRRORS}

    def test_an_unsynced_table_is_reported_rather_than_omitted(self):
        """Une table absente de la réponse et une table à zéro se confondent ;
        « jamais synchronisée » est justement l'information qu'on cherche."""
        repo, _ = self.repo([])
        assert all(
            r["syncedAt"] is None and r["rows"] == 0 for r in repo.erp_freshness()
        )

    def test_the_freshness_asks_for_the_last_sync_of_each(self):
        repo, seen = self.repo([])
        repo.erp_freshness()
        assert "max(synced_at)" in seen[0]

    def test_it_is_one_query_and_not_one_per_table(self):
        """Cinq allers-retours pour cinq nombres, sur une page de diagnostic."""
        repo, seen = self.repo([])
        repo.erp_freshness()
        assert len(seen) == 1

    def test_the_import_volumes_count_the_rejects_apart(self):
        repo, _ = self.repo([{"batches": 4, "accepted": 100, "rejected": 7,
                              "with_rejects": 2, "last_at": None}])
        got = repo.import_volumes()
        assert got["rowsRejected"] == 7
        assert got["batchesWithRejects"] == 2

    def test_a_period_with_no_import_is_zero_and_not_none(self):
        """`null` dans une case d'un tableau de bord se lit comme une panne."""
        repo, _ = self.repo([{"batches": 0, "accepted": None, "rejected": None,
                              "with_rejects": 0, "last_at": None}])
        got = repo.import_volumes()
        assert (got["rowsAccepted"], got["rowsRejected"]) == (0, 0)

    def test_the_window_reaches_the_query(self):
        repo, seen = self.repo([{}])
        repo.import_volumes(hours=72)
        assert "make_interval(hours => %s)" in seen[0]

    def test_the_scan_jobs_are_grouped_by_status(self):
        repo, _ = self.repo([{"status": "FAILED", "n": 3}, {"status": "RUNNING", "n": 1}])
        got = repo.scan_jobs()
        assert got["failed"] == 3
        assert got["running"] == 1

    def test_a_queued_job_counts_as_running(self):
        """L'attente et l'exécution sont la même chose pour qui attend."""
        repo, _ = self.repo([{"status": "QUEUED", "n": 2}])
        assert repo.scan_jobs()["running"] == 2


# --------------------------------------------------------------------------- #
# Contre une vraie base
# --------------------------------------------------------------------------- #

@pytest.mark.postgres
class TestAgainstARealDatabase:
    @pytest.fixture
    def repo(self):
        import os

        from inventory.config import Settings
        from inventory.db.engine import Database
        from inventory.db.repositories import OperationsRepository

        if not os.environ.get("PGHOST"):
            pytest.skip("PGHOST absent")
        return OperationsRepository(Database(Settings()))

    def test_every_mirror_table_exists(self, repo):
        """Le nom d'une table renommée par une migration ne se voit qu'ici."""
        rows = repo.erp_freshness()
        assert len(rows) == 5
        assert all(isinstance(r["rows"], int) for r in rows)

    def test_the_import_query_runs(self, repo):
        got = repo.import_volumes(hours=1)
        assert set(got) >= {"batches", "rowsAccepted", "rowsRejected"}

    def test_the_scan_query_runs(self, repo):
        assert "byStatus" in repo.scan_jobs(hours=1)

    def test_the_pool_exposes_its_stats(self, repo):
        stats = repo.db.stats
        assert "pool_size" in stats
        assert "requests_waiting" in stats


# --------------------------------------------------------------------------- #
# Ce qui n'a pas été fait, et pourquoi
# --------------------------------------------------------------------------- #

class TestTheCountersAreNotWrittenToTheDatabase:
    """Un aller-retour SQL par requête, pour une statistique, sur le chemin
    critique de toutes les requêtes. Ce qui doit survivre y est déjà."""

    def test_the_middleware_touches_no_repository(self):
        source = (ROOT / "app" / "inventory" / "api" / "app.py").read_text()
        tree = ast.parse(source)
        middleware = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "request_context"
        )
        body = ast.unparse(middleware)
        assert "Repository" not in body
        assert "get_database" not in body

    def test_the_registry_says_since_when_it_counts(self):
        """Sans `uptimeSeconds`, un compteur remis à zéro par un recyclage se
        lit comme une chute de trafic."""
        assert "uptimeSeconds" in Registry().snapshot()
