"""Vivacité et disponibilité sont deux questions, et deux réponses.

``/api/health`` répondait 200 dans tous les cas, ``ready`` portant la vérité
dans le corps. C'est le bon choix pour un humain qui vient lire l'état — une
page de diagnostic qui refuse de s'afficher quand ça va mal ne sert à rien — et
le mauvais pour un orchestrateur, qui ne regarde que le code de statut. Un
conteneur dont les migrations avaient échoué recevait donc du trafic comme les
autres, et le servait avec des erreurs SQL cinq couches plus bas.

Séparer les deux sondes n'est pas cosmétique, et le sens de chacune compte :

*Vivacité* — « ce processus est-il figé ? » — ne consulte aucune dépendance.
Une base indisponible ferait redémarrer en boucle des conteneurs parfaitement
sains ; le redémarrage ne répare pas la base, et la rafale de reconnexions
qu'il provoque l'empêche de revenir. C'est le mode de panne classique d'une
sonde de vivacité qui en sait trop.

*Disponibilité* — « ce conteneur peut-il servir ? » — les consulte toutes, et
répond 503 quand la réponse est non.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> Any:
    """Une application montée sans base derrière."""
    from inventory.api import app as module
    from inventory.config import get_settings

    monkeypatch.setenv("INV_ENV", "local")
    get_settings.cache_clear()
    application = module.create_app()
    with TestClient(application) as running:
        yield running
    get_settings.cache_clear()


def probe_paths(client) -> set[str]:
    return {route.path for route in client.app.routes if hasattr(route, "path")}


class TestTheThreeEndpointsExist:
    def test_the_two_probes_are_separate_paths(self, client):
        paths = probe_paths(client)
        assert "/api/health/live" in paths
        assert "/api/health/ready" in paths

    def test_the_diagnostic_page_is_still_there(self, client):
        """Elle répond à une autre question, et personne ne doit la perdre."""
        assert "/api/health" in probe_paths(client)


class TestLiveness:
    def test_it_answers_two_hundred(self, client):
        assert client.get("/api/health/live").status_code == 200

    def test_it_answers_even_with_no_database_at_all(self, client, monkeypatch):
        """C'est tout son intérêt : ne pas faire recycler un conteneur sain."""
        def explode(*a, **k):
            raise RuntimeError("Lakebase injoignable")

        monkeypatch.setattr("inventory.db.get_database", explode)
        assert client.get("/api/health/live").status_code == 200

    def test_it_says_nothing_about_the_database(self, client):
        """Une sonde de vivacité qui en sait trop devient une sonde couplée."""
        body = client.get("/api/health/live").json()
        assert "database" not in body
        assert "ready" not in body


class TestReadiness:
    """Chaque cause d'indisponibilité doit suffire, seule, à faire refuser.

    Le piège de ces contrôles : sans base configurée, *tout* est en panne à la
    fois, et un 503 ne prouve alors rien sur la cause qu'on croit tester. Chaque
    contrôle part donc d'un conteneur **sain** et n'y casse qu'une chose.
    """

    def healthy(self, client, monkeypatch, **broken: Any) -> Any:
        """Un conteneur en bon état, sauf ce que l'appelant y casse."""
        from inventory.api import app as module

        client.app.state.startup_error = broken.get("startup_error")
        monkeypatch.setattr(
            module, "_migration_state",
            lambda settings: {
                "applied": ["001_initial_schema"],
                "pending": broken.get("pending", []),
                "error": broken.get("migration_error"),
            },
        )
        monkeypatch.setattr(
            "inventory.config.Settings.lakebase_configured",
            property(lambda self: True),
        )
        pings = broken.get("database", True)
        monkeypatch.setattr(
            "inventory.db.get_database",
            lambda *a, **k: type("Db", (), {"ping": lambda self: pings})(),
        )
        return client.get("/api/health/ready")

    def test_a_healthy_container_takes_traffic(self, client, monkeypatch):
        """Le refus doit être levable, sinon il ne distingue rien."""
        response = self.healthy(client, monkeypatch)
        assert response.status_code == 200, response.json()
        assert response.json()["ready"] is True

    def test_an_unreachable_database_alone_refuses(self, client, monkeypatch):
        response = self.healthy(client, monkeypatch, database=False)
        assert response.status_code == 503
        assert response.json()["database"] is False

    def test_a_pending_migration_alone_refuses(self, client, monkeypatch):
        """Le schéma n'est pas celui que le code attend : mieux vaut refuser.

        Servir dans cet état produit des colonnes manquantes cinq couches plus
        bas, au moment où quelqu'un enregistre un comptage — pas un refus franc
        à la porte.
        """
        response = self.healthy(client, monkeypatch, pending=["019_x"])
        assert response.status_code == 503
        assert response.json()["pendingMigrations"] == ["019_x"]

    def test_an_unreadable_migration_state_alone_refuses(self, client, monkeypatch):
        """Ne pas savoir où en est le schéma n'est pas savoir qu'il va bien."""
        response = self.healthy(client, monkeypatch, migration_error="permission denied")
        assert response.status_code == 503

    def test_a_startup_error_alone_refuses(self, client, monkeypatch):
        """La base répond, mais l'initialisation a échoué : ce n'est pas prêt."""
        response = self.healthy(
            client, monkeypatch, startup_error="Lakebase initialisation failed"
        )
        assert response.status_code == 503
        assert response.json()["startupError"]

    def test_without_lakebase_at_all_it_refuses(self, client):
        """Le cas nominal d'un déploiement mal configuré."""
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False

    def test_the_refusal_says_why(self, client):
        """« 503 » sans raison oblige à aller lire les journaux du conteneur."""
        body = client.get("/api/health/ready").json()
        assert body["startupError"]
        assert "pendingMigrations" in body


class TestTheDiagnosticPageStaysForgiving:
    def test_it_answers_two_hundred_even_when_nothing_works(self, client):
        """Un humain vient y lire *pourquoi* ça ne marche pas."""
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["ready"] is False

    def test_it_still_carries_the_whole_diagnosis(self, client):
        body = client.get("/api/health")
        assert body.status_code == 200
        for key in ("version", "env", "lakebaseConfigured", "migrations",
                    "frontend", "evidenceConfigured"):
            assert key in body.json(), key
