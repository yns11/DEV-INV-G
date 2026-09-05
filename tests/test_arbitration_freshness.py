"""La contradiction entre deux comptages se voit dès qu'elle existe.

Le défaut, tel qu'il se présentait
----------------------------------
La comparaison entre le premier et le second passage ne se calculait qu'à un
seul moment : la fermeture d'une zone. Entre la saisie et cette fermeture,
l'écran d'arbitrage affirmait

    « Aucun écart entre les deux comptages.
      Les deux équipes ont trouvé les mêmes quantités. »

sur une zone où deux équipes avaient relevé 95 et 90 sur la même référence. Le
compteur « Arbitrages en attente » de l'écran de comptage disait zéro pour la
même raison.

L'écart finissait par apparaître — au refus de la clôture de la zone,
c'est-à-dire au moment précis où l'on croyait avoir fini. Rien n'était perdu,
mais on l'apprenait le plus tard possible, et l'écran avait affirmé le
contraire entre-temps.

Trouvé en suivant le parcours complet dans un navigateur : aucun contrôle
n'échouait, parce que chacun regardait un maillon et pas la chaîne.

Le correctif
------------
La comparaison se recalcule là où les quantités changent — à l'enregistrement
d'une feuille — plutôt qu'à un moment choisi plus loin. Tous les lecteurs en
profitent d'un coup : l'onglet, le compteur, et la fermeture qui recalculait
déjà pour son propre compte.

Hors transaction, et journalisé plutôt que remonté : la saisie est enregistrée
quand le recalcul s'exécute, et annoncer un échec ferait ressaisir des
quantités qui sont en base. Un arbitrage manqué se rattrape de toute façon à la
fermeture, qui recalcule.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_transactions

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "app" / "inventory" / "services" / "generic_service.py"


def body_of(name: str) -> str:
    """Le corps d'une méthode de ``GenericService``, isolé.

    Chercher dans le module entier ferait passer un contrôle grâce à une autre
    méthode — et c'est précisément parce que l'appel existait *ailleurs* que le
    défaut est passé.
    """
    for node in ast.walk(ast.parse(SERVICE.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(ast.unparse(statement) for statement in node.body)
    raise AssertionError(f"{name} n'est pas défini dans {SERVICE.name}")


# --------------------------------------------------------------------------- #
# Le recalcul a lieu là où les quantités changent
# --------------------------------------------------------------------------- #

def service(*, passes: int, refreshed: list[str], monkeypatch):
    """Le service réel, avec le strict nécessaire autour.

    Le recalcul est remplacé par un témoin : ce qui est en question ici est
    **quand** il est appelé, pas ce qu'il calcule — cela, ses propres contrôles
    s'en chargent.

    Il est remplacé **dans le module qui l'appelle**, et pas sur l'instance :
    depuis qu'il a quitté ``GenericService`` pour devenir une fonction partagée
    par les cinq écritures de lignes, poser un attribut sur l'objet ne
    remplaçait plus rien — le service continuait d'appeler la vraie fonction,
    et le témoin restait vide sans que le contrôle sache dire pourquoi.
    """
    from inventory.services import generic_service as module
    from inventory.services.generic_service import GenericService

    monkeypatch.setattr(
        module,
        "refresh_zone_arbitrations",
        lambda ctx, campaign, zone_id: refreshed.append(zone_id),
    )

    sheet = SimpleNamespace(
        id="sheet-1", campaign_id="camp-1", zone_id="zone-1", version=1,
    )
    zone = SimpleNamespace(
        id="zone-1", code="Z1", allow_negative=False, passes=passes,
    )
    ctx = cast(Any, SimpleNamespace(
        actor="chef@usine",
        request_id="req-1",
        # La matrice de gel a ses propres contrôles ; ce qui est en question
        # ici est le moment du recalcul, et la faire tourner demanderait de
        # reconstruire un contexte complet pour rien.
        guard=lambda campaign, aspect: None,
        record=lambda **kw: None,
        sheets=SimpleNamespace(
            get_sheet=lambda sid: sheet,
            list_zones=lambda cid, **kw: [zone],
            list_sheets=lambda cid, **kw: [sheet],
            list_sheet_lines=lambda sid: [],
            upsert_sheet_lines=lambda lines, actor, conn=None: len(lines),
            replace_sheet_lines=lambda sid, lines, actor, conn=None: len(lines),
        ),
    ))
    with_transactions(ctx)
    instance = GenericService(ctx)
    # Une vraie campagne, pas une doublure : la barrière d'identité et la
    # matrice de gel en lisent une demi-douzaine de champs, et les ajouter un
    # par un à un `SimpleNamespace` revient à réécrire le modèle dans le
    # contrôle — jusqu'à ce qu'il diverge.
    campaign = Campaign(
        id="camp-1", code="ARB-1", label="Arbitrages",
        count_date=dt.date(2026, 6, 30), status=CampaignStatus.COUNTING,
        created_by="chef@usine",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )
    return instance, campaign


ROWS = [{"item_number": "P-00001", "section": "LINE_SIDE", "qty": "95", "unit": "PCE"}]


class TestTheComparisonIsRecomputedWhereQuantitiesChange:
    def test_saving_a_sheet_refreshes_the_zone(self, monkeypatch):
        """C'est le correctif : la contradiction n'attend plus la fermeture."""
        refreshed: list[str] = []
        instance, campaign = service(
            passes=2, refreshed=refreshed, monkeypatch=monkeypatch
        )
        instance.upsert_sheet_lines(campaign, "sheet-1", ROWS)
        assert refreshed == ["zone-1"]

    def test_a_single_pass_zone_has_nothing_to_compare(self, monkeypatch):
        """Un seul comptage : il n'y a pas de second passage à contredire.

        Recalculer y coûterait une lecture par enregistrement, sur les zones
        les plus nombreuses.
        """
        refreshed: list[str] = []
        instance, campaign = service(
            passes=1, refreshed=refreshed, monkeypatch=monkeypatch
        )
        instance.upsert_sheet_lines(campaign, "sheet-1", ROWS)
        assert refreshed == []

    def test_a_failed_refresh_does_not_lose_the_entry(self, monkeypatch):
        """La saisie est déjà enregistrée quand le recalcul s'exécute.

        Remonter l'échec ferait ressaisir des quantités qui sont en base — et
        elles ont été relevées à la main, sur le terrain.
        """
        from inventory.services import generic_service as module
        from inventory.services.generic_service import GenericService

        instance, campaign = service(
            passes=2, refreshed=[], monkeypatch=monkeypatch
        )

        def boom(ctx, campaign, zone_id):
            raise RuntimeError("base indisponible")

        monkeypatch.setattr(module, "refresh_zone_arbitrations", boom)
        assert isinstance(instance, GenericService)
        assert instance.upsert_sheet_lines(campaign, "sheet-1", ROWS) == 1

    def test_a_failed_refresh_is_logged(self, caplog, monkeypatch):
        """Journalisé, jamais tu : un recalcul qui échoue en silence
        reproduirait exactement le défaut qu'on corrige."""
        from inventory.services import generic_service as module

        instance, campaign = service(
            passes=2, refreshed=[], monkeypatch=monkeypatch
        )

        def boom(ctx, campaign, zone_id):
            raise RuntimeError("base indisponible")

        monkeypatch.setattr(module, "refresh_zone_arbitrations", boom)
        with caplog.at_level("ERROR"):
            instance.upsert_sheet_lines(campaign, "sheet-1", ROWS)
        assert any("arbitrages" in record.message.lower() for record in caplog.records)

    def test_the_zone_is_named_in_the_log(self):
        """Sans elle, le message n'apprend pas où regarder."""
        assert "zone.code" in body_of("upsert_sheet_lines")


# --------------------------------------------------------------------------- #
# Les deux autres moments n'ont pas disparu
# --------------------------------------------------------------------------- #

class TestTheOtherRecomputationsSurvive:
    def test_closing_a_zone_still_recomputes(self):
        """Elle recalculait déjà, et pour une bonne raison : refuser une
        clôture sur un écart créé entre-temps."""
        assert "refresh_zone_arbitrations" in body_of("set_zone_closed")

    def test_the_endpoint_stays_available(self):
        """Le recalcul explicite reste joignable pour un rattrapage."""
        router = ROOT / "app" / "inventory" / "api" / "routers" / "generic.py"
        assert "arbitrations/refresh" in router.read_text()


# --------------------------------------------------------------------------- #
# Sur une vraie base, de bout en bout
# --------------------------------------------------------------------------- #

@pytest.mark.postgres
class TestOnARealDatabase:
    """Le contrôle qui aurait attrapé le défaut.

    Les précédents portent sur l'appel ; celui-ci porte sur ce qu'un écran
    lirait. C'est la différence entre « la méthode est appelée » et « le
    compteur affiche un ».
    """

    def test_the_pending_count_rises_without_closing_the_zone(self):
        import os

        if not os.environ.get("PGHOST"):
            pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")

        import datetime as dt
        from decimal import Decimal

        from inventory.db import get_database
        from inventory.domain.models import Item
        from inventory.services import (
            CampaignService,
            GenericService,
            ImportService,
            ServiceContext,
        )

        get_database()
        ctx = ServiceContext(actor="local@dev")
        campaign = CampaignService(ctx).create(
            code=f"ARB-{dt.datetime.now(dt.UTC).strftime('%H%M%S%f')}",
            label="Fraîcheur des arbitrages",
            count_date=dt.date(2026, 6, 30),
        )
        ctx.referentials.upsert_items(
            [Item(campaign_id=campaign.id, item_number="P-00001", name="VIS")],
            actor=ctx.actor,
        )
        ImportService(ctx).import_count_sheets(
            campaign, mode="paste",
            text="Feuille\tArticle\tSection\tUnité\nZ-ARB\tP-00001\tBDL\tPCE\n",
        )

        from inventory.domain.enums import CampaignStatus

        campaign = CampaignService(ctx).transition(campaign.id, CampaignStatus.COUNTING)

        # Le séquencement l'exige, et il a raison : un comptage sans référence
        # à laquelle le comparer ne mesure rien.
        imports = ImportService(ctx)
        imports.import_book_stock(
            campaign, mode="paste",
            text=(
                "Numéro d'article\tEntrepôt\tEmplacement\tStock physique"
                "\tUnité\tCoût unitaire\n"
                "P-00001\tB06\tPAL 01\t100\tPCE\t2\n"
            ),
        )
        campaign = imports.freeze_book_stock(campaign)

        generic = GenericService(ctx)
        zone = next(z for z in generic.list_zones(campaign) if z["code"] == "Z-ARB")
        for pass_key, qty in (("PASS_1", Decimal(95)), ("PASS_2", Decimal(90))):
            sheet = next(s for s in zone["sheets"] if s["pass_no"] == pass_key)
            line = generic.get_sheet(campaign, sheet["id"])["lines"][0]
            generic.upsert_sheet_lines(campaign, sheet["id"], [{
                "id": line["id"], "item_number": "P-00001",
                "section": line["section"], "qty": qty, "unit": "PCE",
            }])

        # Aucune zone n'a été fermée : c'est tout l'objet du contrôle.
        pending = [
            a for a in ctx.sheets.list_arbitrations(campaign.id)
            if a.zone_id == zone["id"] and not a.is_resolved
            and a.qty_pass_1 != a.qty_pass_2
        ]
        assert len(pending) == 1, "l'écart 95/90 doit être visible sans fermeture"
        assert pending[0].item_number == "P-00001"
