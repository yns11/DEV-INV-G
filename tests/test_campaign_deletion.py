"""Supprimer une campagne, ou un lot : qui le peut, et ce qui reste après.

Une campagne est le travail de quelqu'un. Elle disparaissait donc de deux
manières impossibles à défendre : pas du tout — la liste accumulait les essais,
les doublons et les campagnes créées par erreur —, ou physiquement, ce qui
emporterait avec elle les comptages, les journaux et la piste d'audit d'un
inventaire que quelqu'un a signé.

D'où deux règles, et ces tests ne vérifient rien d'autre : seul l'auteur
supprime, et la suppression est logique.

Le lot en ajoute une troisième, et elle n'existe que parce qu'il y a un lot :
**tout ou rien**. Une suppression à moitié appliquée est le pire des trois
résultats possibles — on a coché huit campagnes, cinq ont disparu, et il faut
relire la liste pour savoir lesquelles.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign
from inventory.errors import NotFoundError, PermissionDeniedError, ValidationError
from inventory.services.campaign_service import MAX_BULK_DELETE, CampaignService


def campaign(*, created_by: str = "alice@usine") -> Campaign:
    return Campaign(
        id="camp-1",
        code="INV-2026-06",
        label="Inventaire général",
        count_date="2026-06-13",
        status=CampaignStatus.ANALYSIS,
        created_by=created_by,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


def service(
    stored: Campaign, *, actor: str
) -> tuple[CampaignService, list[str], list[dict[str, Any]]]:
    """Le service, la liste des suppressions posées et celle des événements."""
    deleted: list[str] = []
    events: list[dict[str, Any]] = []

    @contextmanager
    def transaction():
        yield None

    ctx = SimpleNamespace(
        actor=actor,
        db=SimpleNamespace(transaction=transaction),
        campaigns=SimpleNamespace(
            get=lambda cid: stored,
            soft_delete=lambda cid, *, actor, conn=None: deleted.append(cid),
        ),
        record=lambda **kw: events.append(kw) or "evt",
    )
    with_access(ctx)
    return CampaignService(cast(Any, ctx)), deleted, events


class TestOnlyTheAuthorDeletes:
    def test_the_author_can(self):
        svc, deleted, _ = service(campaign(), actor="alice@usine")
        svc.delete("camp-1")
        assert deleted == ["camp-1"]

    def test_somebody_else_cannot(self):
        """403, et non un silence : une action refusée doit se voir."""
        svc, deleted, _ = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete("camp-1")
        assert deleted == []
        assert caught.value.status_code == 403

    def test_the_refusal_names_the_owner(self):
        """« Vous n'avez pas le droit » n'aide personne à savoir à qui demander."""
        svc, _, _ = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete("camp-1")
        assert "alice@usine" in str(caught.value)

    def test_an_ownerless_campaign_is_not_up_for_grabs(self):
        """Champ vide en base : cela ne fait de personne son auteur."""
        svc, deleted, _ = service(campaign(created_by=""), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete("camp-1")
        assert deleted == []


class TestItLeavesATrace:
    def test_the_deletion_is_recorded_before_it_happens(self):
        """Écrit dans la même transaction : l'un sans l'autre serait un trou."""
        svc, _, events = service(campaign(), actor="alice@usine")
        svc.delete("camp-1")
        assert len(events) == 1
        assert events[0]["entity_type"] == "campaign"
        assert events[0]["before"]["code"] == "INV-2026-06"

    def test_a_refused_deletion_records_nothing(self):
        svc, _, events = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete("camp-1")
        assert events == []


# --------------------------------------------------------------------------- #
# Le lot
# --------------------------------------------------------------------------- #

def batch(
    *campaigns: Campaign, actor: str
) -> tuple[CampaignService, list[str], list[dict[str, Any]], list[str]]:
    """Le service sur plusieurs campagnes, et les trois traces qu'on relit.

    ``opened`` compte les transactions : un lot doit en ouvrir **une**, sans
    quoi la moitié d'une suppression peut être validée pendant que l'autre
    échoue — précisément ce que la règle du tout-ou-rien interdit.
    """
    stored = {c.id: c for c in campaigns}
    deleted: list[str] = []
    events: list[dict[str, Any]] = []
    opened: list[str] = []

    @contextmanager
    def transaction():
        opened.append("txn")
        yield None

    def get(campaign_id: str) -> Campaign:
        found = stored.get(campaign_id)
        if found is None:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)
        return found

    ctx = SimpleNamespace(
        actor=actor,
        db=SimpleNamespace(transaction=transaction),
        campaigns=SimpleNamespace(
            get=get,
            soft_delete=lambda cid, *, actor, conn=None: deleted.append(cid),
        ),
        record=lambda **kw: events.append(kw) or "evt",
    )
    with_access(ctx)
    return CampaignService(cast(Any, ctx)), deleted, events, opened


def some(number: int, *, created_by: str = "alice@usine") -> Campaign:
    return Campaign(
        id=f"camp-{number}",
        code=f"INV-2026-{number:02d}",
        label=f"Inventaire {number}",
        count_date="2026-06-13",
        status=CampaignStatus.PREPARATION,
        created_by=created_by,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


class TestLeLotSupprimeCeQuOnLuiDonne:
    def test_toutes_dun_coup(self):
        svc, deleted, _, _ = batch(some(1), some(2), some(3), actor="alice@usine")
        codes = svc.delete_many(["camp-1", "camp-2", "camp-3"])
        assert deleted == ["camp-1", "camp-2", "camp-3"]
        assert codes == ["INV-2026-01", "INV-2026-02", "INV-2026-03"]

    def test_dans_une_seule_transaction(self):
        """C'est ce qui rend le tout-ou-rien vrai jusqu'en base.

        Une transaction par campagne laisserait, sur une panne au milieu, une
        partie des suppressions validées — l'état exact que la règle refuse.
        """
        svc, _, _, opened = batch(some(1), some(2), some(3), actor="alice@usine")
        svc.delete_many(["camp-1", "camp-2", "camp-3"])
        assert len(opened) == 1

    def test_une_campagne_cochee_deux_fois_ne_part_quune_fois(self):
        """Deux chemins de sélection, une seule trace d'audit."""
        svc, deleted, events, _ = batch(some(1), actor="alice@usine")
        assert svc.delete_many(["camp-1", "camp-1"]) == ["INV-2026-01"]
        assert deleted == ["camp-1"]
        assert len(events) == 1

    def test_chaque_campagne_a_sa_trace(self):
        """L'audit se relit campagne par campagne.

        Une entrée unique portant dix codes serait invisible depuis neuf
        d'entre elles — c'est-à-dire depuis l'endroit où on la cherche.
        """
        svc, _, events, _ = batch(some(1), some(2), actor="alice@usine")
        svc.delete_many(["camp-1", "camp-2"])
        assert [e["campaign_id"] for e in events] == ["camp-1", "camp-2"]
        assert all(e["entity_type"] == "campaign" for e in events)
        assert all(e["before"]["code"] for e in events)


class TestUnSeulRefusArreteLeLot:
    def test_une_campagne_dun_collegue_bloque_tout(self):
        svc, deleted, events, opened = batch(
            some(1), some(2, created_by="bob@usine"), some(3), actor="alice@usine"
        )
        with pytest.raises(PermissionDeniedError):
            svc.delete_many(["camp-1", "camp-2", "camp-3"])
        assert deleted == []
        assert events == []
        assert opened == []

    def test_le_refus_nomme_les_fautives(self):
        """Sans les noms, il faut décocher au hasard jusqu'à ce que ça passe."""
        svc, _, _, _ = batch(
            some(1), some(2, created_by="bob@usine"),
            some(3, created_by="bob@usine"), actor="alice@usine",
        )
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete_many(["camp-1", "camp-2", "camp-3"])
        assert "INV-2026-02" in str(caught.value)
        assert "INV-2026-03" in str(caught.value)

    def test_les_refus_sont_rassembles_et_non_rendus_un_par_un(self):
        """Rendre la main sur la première ferait recommencer autant de fois."""
        svc, _, _, _ = batch(
            *[some(n, created_by="bob@usine") for n in (1, 2, 3, 4)],
            actor="alice@usine",
        )
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete_many([f"camp-{n}" for n in (1, 2, 3, 4)])
        assert caught.value.details["codes"] == [
            f"INV-2026-0{n}" for n in (1, 2, 3, 4)
        ]

    def test_une_campagne_deja_supprimee_arrete_le_lot(self):
        """Deux onglets ouverts, l'un supprime, l'autre coche : il faut le dire.

        Passer outre laisserait croire que le lot demandé est parti en entier.
        """
        svc, deleted, _, _ = batch(some(1), actor="alice@usine")
        with pytest.raises(NotFoundError) as caught:
            svc.delete_many(["camp-1", "camp-inconnue"])
        assert deleted == []
        assert caught.value.details["campaignIds"] == ["camp-inconnue"]

    def test_le_refus_de_propriete_passe_avant_celui_dexistence(self):
        """« Elle n'est pas à vous » se corrige en décochant ; l'autre en
        rafraîchissant. La première est celle qu'on veut lire d'abord."""
        svc, _, _, _ = batch(some(2, created_by="bob@usine"), actor="alice@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete_many(["camp-2", "camp-inconnue"])


class TestLeLotEstBorne:
    def test_un_lot_vide_est_refuse(self):
        svc, deleted, _, _ = batch(some(1), actor="alice@usine")
        with pytest.raises(ValidationError):
            svc.delete_many([])
        assert deleted == []

    def test_au_dela_du_plafond_rien_ne_part(self):
        """Une sélection oubliée sur dix mille lignes doit être refusée vite."""
        svc, deleted, _, opened = batch(some(1), actor="alice@usine")
        with pytest.raises(ValidationError) as caught:
            svc.delete_many([f"camp-{n}" for n in range(MAX_BULK_DELETE + 1)])
        assert deleted == []
        assert opened == []
        assert caught.value.details["maximum"] == MAX_BULK_DELETE

    def test_le_plafond_lui_meme_passe(self):
        """Une borne qu'on ne peut pas atteindre est une borne mal placée."""
        campaigns = [some(n) for n in range(MAX_BULK_DELETE)]
        svc, deleted, _, _ = batch(*campaigns, actor="alice@usine")
        svc.delete_many([c.id for c in campaigns])
        assert len(deleted) == MAX_BULK_DELETE


# --------------------------------------------------------------------------- #
# La route, puis l'écran : un service que rien n'appelle n'existe pas
# --------------------------------------------------------------------------- #

class TestLaRouteAtteintLeService:
    """La classe de défaut de ce dépôt : écrit, testé, et jamais atteint.

    Ces contrôles montent l'application et posent la requête, avec un service
    en doublure — ce qui est vérifié est le câblage et le contrat rendu au
    navigateur, pas la façon dont on joint la base.
    """

    def client(self, monkeypatch, *, codes=None, error=None):
        from fastapi.testclient import TestClient

        from inventory.api import app as module
        from inventory.api.deps import campaign_service
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ENV", "local")
        get_settings.cache_clear()
        seen: dict[str, Any] = {}

        def delete_many(ids):
            seen["ids"] = list(ids)
            if error is not None:
                raise error
            return list(codes or [])

        application = module.create_app()
        application.dependency_overrides[campaign_service] = lambda: SimpleNamespace(
            delete_many=delete_many
        )
        return TestClient(application), seen

    def test_le_lot_arrive_au_service(self, monkeypatch):
        client, seen = self.client(monkeypatch, codes=["INV-A", "INV-B"])
        with client:
            body = client.post(
                "/api/campaigns/bulk-delete", json={"ids": ["c1", "c2"]}
            ).json()
        assert seen["ids"] == ["c1", "c2"]
        assert body == {"deleted": 2, "codes": ["INV-A", "INV-B"]}

    def test_les_codes_reviennent_pour_que_le_message_dise_quoi(self, monkeypatch):
        """« 12 supprimées » ne permet pas de reconnaître la treizième absente."""
        client, _ = self.client(monkeypatch, codes=["INV-A"])
        with client:
            body = client.post(
                "/api/campaigns/bulk-delete", json={"ids": ["c1"]}
            ).json()
        assert body["codes"] == ["INV-A"]

    def test_un_lot_vide_est_refuse_par_le_contrat(self, monkeypatch):
        """Refusé à la porte : le service n'a même pas à être appelé."""
        client, seen = self.client(monkeypatch)
        with client:
            assert client.post(
                "/api/campaigns/bulk-delete", json={"ids": []}
            ).status_code == 422
        assert "ids" not in seen

    def test_un_lot_demesure_est_refuse_avant_detre_lu(self, monkeypatch):
        client, seen = self.client(monkeypatch)
        with client:
            response = client.post(
                "/api/campaigns/bulk-delete",
                json={"ids": [f"c{n}" for n in range(MAX_BULK_DELETE + 1)]},
            )
        assert response.status_code == 422
        assert "ids" not in seen

    def test_un_refus_du_service_devient_un_403(self, monkeypatch):
        client, _ = self.client(
            monkeypatch,
            error=PermissionDeniedError("INV-B ne vous appartient pas.", codes=["INV-B"]),
        )
        with client:
            response = client.post(
                "/api/campaigns/bulk-delete", json={"ids": ["c1", "c2"]}
            )
        assert response.status_code == 403
        assert "INV-B" in response.json()["message"]

    def test_la_route_unitaire_reste(self, monkeypatch):
        """Supprimer une campagne depuis sa ligne ne doit pas passer par un lot."""
        from inventory.api import app as module
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ENV", "local")
        get_settings.cache_clear()
        paths = {
            (route.path, method)
            for route in module.create_app().routes
            for method in getattr(route, "methods", ())
        }
        assert ("/api/campaigns/{campaign_id}", "DELETE") in paths
        assert ("/api/campaigns/bulk-delete", "POST") in paths


class TestLEcranSaitLeDemander:
    """Le geste doit être atteignable, et depuis les deux affichages."""

    def source(self, relative: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (root / "frontend" / "src" / relative).read_text()

    def test_le_client_connait_la_route(self):
        api = self.source("lib/api.ts")
        assert "deleteCampaigns" in api
        assert "/campaigns/bulk-delete" in api

    def test_la_grille_se_coche(self):
        screen = self.source("features/Campaigns.tsx")
        assert "selectable" in screen
        assert "onSelectedChange={onSelectionChange}" in screen

    def test_les_vignettes_aussi(self):
        """La sélection ne doit pas exister à moitié selon l'affichage choisi."""
        screen = self.source("features/Campaigns.tsx")
        assert "function SelectBox" in screen
        assert "<SelectBox" in screen

    def test_une_barre_dit_ce_qui_partira(self):
        screen = self.source("features/Campaigns.tsx")
        assert "function SelectionBar" in screen
        assert "Supprimer la sélection" in screen

    def test_elle_compte_ce_qui_partira_et_non_ce_qui_est_coche(self):
        """Les deux chiffres diffèrent dès qu'une campagne d'un collègue est
        dans le lot, et c'est le second qu'on croit avoir demandé."""
        screen = self.source("features/Campaigns.tsx")
        bar = screen[screen.index("function SelectionBar"):]
        assert "deletionBlocker(c, actor) === null" in bar[:1200]

    def test_une_campagne_filtree_ne_part_pas_avec_le_lot(self):
        """Elle n'est plus à l'écran : personne ne la relit avant de confirmer."""
        screen = self.source("features/Campaigns.tsx")
        assert "shown.filter((campaign) => selection.has(campaign.id))" in screen

    def test_la_fenetre_nomme_chaque_campagne(self):
        """« Douze campagnes seront supprimées » ne se vérifie pas."""
        screen = self.source("features/Campaigns.tsx")
        modal = screen[screen.index("function DeleteCampaignsModal"):]
        assert "deletable.map((campaign)" in modal[:4000]
        assert "campaign.code" in modal[:4000]

    def test_elle_montre_a_part_ce_qui_ne_partira_pas(self):
        screen = self.source("features/Campaigns.tsx")
        modal = screen[screen.index("function DeleteCampaignsModal"):]
        assert "resteront en place" in modal[:5000]
