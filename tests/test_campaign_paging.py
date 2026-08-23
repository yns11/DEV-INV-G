"""Aucune campagne ne disparaît de la liste.

La liste était bornée à cent — sans le dire. Après quelques années
d'inventaires trimestriels, les plus anciennes cessaient simplement
d'apparaître : aucun message, aucun bouton, rien qui distingue « il n'y en a
pas d'autres » de « il y en a d'autres, hors de portée ». Elles étaient
pourtant toujours en base, et une campagne close est justement ce qu'on vient
rechercher des mois plus tard.

La réponse porte désormais le total. Ce n'est pas une commodité d'affichage :
c'est la seule façon pour l'interface de savoir qu'elle ne montre pas tout, et
donc de proposer la suite.

**Décalage plutôt que curseur**, contrairement à ce que suggérait l'audit. Un
curseur évite qu'une ligne insérée entre deux pages en décale une autre ; cela
suppose des écritures concurrentes fréquentes. Cette liste s'allonge de
quelques lignes par an et se trie sur une date de comptage stable. Le curseur
coûterait un encodage que personne ne lit, pour un défaut que personne ne
rencontrera.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.db.repositories import CampaignRepository
from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign


def campaign(n: int) -> Campaign:
    return Campaign(
        id=f"camp-{n}", code=f"INV-{n:04d}", label="Inventaire",
        count_date="2026-09-01", status=CampaignStatus.CLOSED,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


# --------------------------------------------------------------------------- #
# La requête
# --------------------------------------------------------------------------- #

class TestTheQuery:
    def spy(self, *, rows, total):
        repo = CampaignRepository.__new__(CampaignRepository)
        seen: list[tuple[str, tuple[Any, ...]]] = []

        def fetch_all(query, params=(), *, conn=None):
            seen.append((" ".join(query.split()), tuple(params or ())))
            return rows

        def fetch_one(query, params=(), *, conn=None):
            seen.append((" ".join(query.split()), tuple(params or ())))
            return {"n": total}

        repo._fetch_all = fetch_all  # type: ignore[method-assign]
        repo._fetch_one = fetch_one  # type: ignore[method-assign]
        repo._to_model = lambda row: row  # type: ignore[method-assign]
        return repo, seen

    def test_it_asks_for_the_total(self):
        """Sans lui, l'interface ne peut pas savoir qu'elle tronque."""
        repo, seen = self.spy(rows=[], total=137)
        _, total = repo.list()
        assert total == 137
        assert any("count(*)" in q for q, _ in seen)

    def test_the_page_carries_a_limit_and_an_offset(self):
        repo, seen = self.spy(rows=[], total=0)
        repo.list(limit=25, offset=50)
        page = next(q for q, _ in seen if "ORDER BY" in q)
        assert "LIMIT %s OFFSET %s" in page
        params = next(p for q, p in seen if "ORDER BY" in q)
        assert params == (25, 50)

    def test_the_total_counts_the_same_set_as_the_page(self):
        """Un total qui inclurait les clôturées alors que la page les exclut
        annoncerait des campagnes que « charger la suite » ne ramènerait jamais."""
        repo, seen = self.spy(rows=[], total=0)
        repo.list(include_closed=False)
        queries = [q for q, _ in seen]
        assert all("status <> 'CLOSED'" in q for q in queries), queries

    def test_deleted_campaigns_are_in_neither(self):
        repo, seen = self.spy(rows=[], total=0)
        repo.list()
        assert all("deleted_at IS NULL" in q for q, _ in seen)


# --------------------------------------------------------------------------- #
# Le service
# --------------------------------------------------------------------------- #

def service(*, rows, total):
    from inventory.services.campaign_service import CampaignService

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    seen: dict[str, Any] = {}

    def listing(*, include_closed, limit, offset):
        seen.update(include_closed=include_closed, limit=limit, offset=offset)
        return rows, total

    ctx.campaigns = SimpleNamespace(list=listing)
    with_access(ctx)
    return CampaignService(ctx), seen


class TestTheServiceBoundsWhatItIsGiven:
    def test_a_default_page_is_asked_for(self):
        svc, seen = service(rows=[], total=0)
        svc.list()
        assert seen["limit"] == svc.PAGE
        assert seen["offset"] == 0

    @pytest.mark.parametrize(
        "asked,applied", [(1, 1), (250, 250), (5000, 500), (0, 1), (-3, 1)]
    )
    def test_the_limit_is_clamped(self, asked, applied):
        """Une page de cinq mille lignes n'aide personne et immobilise la base."""
        svc, seen = service(rows=[], total=0)
        svc.list(limit=asked)
        assert seen["limit"] == applied

    def test_a_negative_offset_starts_at_the_beginning(self):
        svc, seen = service(rows=[], total=0)
        svc.list(offset=-10)
        assert seen["offset"] == 0

    def test_the_total_travels_through(self):
        svc, _ = service(rows=[campaign(1)], total=137)
        page, total = svc.list()
        assert len(page) == 1
        assert total == 137


# --------------------------------------------------------------------------- #
# Le contrat rendu au navigateur
# --------------------------------------------------------------------------- #

class TestTheResponseSaysHowManyExist:
    def client(self, monkeypatch, *, rows, total):
        """L'application, avec un service de campagnes en doublure.

        Surcharge de dépendance plutôt que patch de méthode : le service réel
        est construit par une dépendance qui exige Lakebase, et elle échoue
        avant d'atteindre la méthode. Ce qui est vérifié ici est le contrat
        rendu au navigateur, pas la façon dont on joint la base.
        """
        from fastapi.testclient import TestClient

        from inventory.api import app as module
        from inventory.api.deps import campaign_service
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ENV", "local")
        get_settings.cache_clear()
        application = module.create_app()
        application.dependency_overrides[campaign_service] = lambda: SimpleNamespace(
            list=lambda **kwargs: (rows, total)
        )
        return TestClient(application)

    def test_the_payload_carries_the_items_and_the_total(self, monkeypatch):
        with self.client(monkeypatch, rows=[campaign(1)], total=137) as client:
            body = client.get("/api/campaigns").json()
        assert body["total"] == 137
        assert len(body["items"]) == 1
        assert body["items"][0]["code"] == "INV-0001"

    def test_an_empty_list_is_not_a_missing_total(self, monkeypatch):
        with self.client(monkeypatch, rows=[], total=0) as client:
            body = client.get("/api/campaigns").json()
        assert body == {"items": [], "total": 0, "offset": 0}

    def test_the_offset_comes_back_so_the_client_knows_where_it_is(
        self, monkeypatch
    ):
        with self.client(monkeypatch, rows=[], total=137) as client:
            body = client.get("/api/campaigns?offset=100").json()
        assert body["offset"] == 100

    @pytest.mark.parametrize("bad", ["limit=0", "limit=501", "offset=-1"])
    def test_an_impossible_page_is_refused_rather_than_clamped_silently(
        self, monkeypatch, bad
    ):
        """Le service borne ce qu'il reçoit ; l'API refuse ce qui n'a pas de sens."""
        with self.client(monkeypatch, rows=[], total=0) as client:
            assert client.get(f"/api/campaigns?{bad}").status_code == 422


class TestTheBrowserKnowsThereIsMore:
    """Le total n'a de valeur que si l'écran s'en sert."""

    def source(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (root / "frontend" / "src" / "features" / "Campaigns.tsx").read_text()

    def test_the_view_computes_what_it_is_not_showing(self):
        assert "const hidden = Math.max(0, known - loaded.length)" in self.source()

    def test_it_offers_to_load_them(self):
        source = self.source()
        assert "Charger les plus anciennes" in source
        assert "setLimit((current) => current + PAGE)" in source

    def test_it_says_how_many_are_missing(self):
        """« 100 sur 137 » plutôt qu'un silence."""
        assert "plus ancienne(s) non chargée(s)" in self.source()

    def test_the_client_asks_for_the_page_it_wants(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        api = (root / "frontend" / "src" / "lib" / "api.ts").read_text()
        assert "listCampaigns: (includeClosed = true, limit?: number)" in api
