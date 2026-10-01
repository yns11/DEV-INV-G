"""Rejouer un chargement depuis le fichier qu'il a laissé.

Il n'y a pas d'annulation d'import, et il n'y en aura probablement jamais : une
ligne mise à jour en place ne garde pas son image d'avant. Ce qui existe, c'est
le fichier d'origine — chaque chargement de fichier est archivé tel qu'il a été
reçu — et comme l'import remplace, le rejouer remet les quantités qu'il portait.

Le rejeu ne fait donc rien de neuf. Il retire les quatre gestes qui séparaient
l'exploitant d'un retour en arrière que l'application savait déjà faire :
retrouver le lot, télécharger la pièce, revenir à l'écran d'import, la
reprendre.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign, Item
from inventory.errors import NotFoundError, ValidationError

pytestmark = pytest.mark.postgres

ITEM = "MASS-1"


def _classeur(lignes: list[tuple[str, str, str, float]]) -> bytes:
    """Un vrai `.xlsx` de stock ERP, comme celui qu'un exploitant dépose."""
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Numéro d'article", "Entrepôt", "Emplacement", "Quantité",
                  "Coût unitaire"])
    for article, entrepot, emplacement, qty in lignes:
        sheet.append([article, entrepot, emplacement, qty, 2.0])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_rejeu") as database:
        yield database


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"RJ-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute("UPDATE campaign SET status='COUNTING' WHERE id=%s", (campaign_id,))
    return Campaign(
        id=campaign_id, code=f"RJ-{campaign_id[:8]}", label="",
        count_date=dt.date(2026, 9, 13), status=CampaignStatus.COUNTING,
        created_by="test", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def ctx(db, campaign, monkeypatch):
    """Un contexte qui archive **dans la base**.

    L'archive a deux dos : un volume Unity Catalog, ou la table `evidence_blob`
    de la migration 022. Le rejeu ne dépend d'aucun des deux — il relit par le
    même magasin que le dépôt — mais ces contrôles ont besoin d'un magasin qui
    marche sans espace de travail Databricks.
    """
    from inventory.config import Settings, get_settings
    from inventory.services.context import ServiceContext

    monkeypatch.setenv("INV_EVIDENCE_STORE", "lakebase")
    get_settings.cache_clear()
    monkeypatch.setattr(
        Settings, "evidence_configured", property(lambda self: True), raising=False
    )
    context = ServiceContext(actor="test", db=db, settings=get_settings())
    monkeypatch.setattr(context, "guard", lambda c, w: None, raising=False)
    context.referentials.upsert_items(
        [Item(campaign_id=campaign.id, item_number=ITEM, name="X",
              std_price=Decimal(2))],
        actor="test",
    )
    return context


@pytest.fixture
def service(ctx):
    from inventory.services.import_service import ImportService

    return ImportService(ctx)


def _charger(service, campaign, qty: float) -> str:
    """Un chargement de stock ERP, et l'identifiant du lot qu'il laisse."""
    outcome = service.import_book_stock(
        campaign, mode="file", payload=_classeur([(ITEM, "ATP", "SOL", qty)]),
        filename=f"stock-{qty:.0f}.xlsx",
    )
    assert outcome.batch_id, "sans lot enregistré, il n'y a rien à rejouer"
    return outcome.batch_id


def _stock(ctx, campaign) -> Decimal | None:
    for line in ctx.book_stock.list(campaign.id):
        if line.item_number == ITEM:
            return line.qty
    return None


class TestRejouerRemetCeQueLeFichierPortait:
    def test_le_chiffre_revient_a_celui_du_lot_rejoue(
        self, ctx, service, campaign
    ):
        """Le geste que l'exploitant veut : défaire un chargement de trop."""
        premier = _charger(service, campaign, 100)
        _charger(service, campaign, 250)
        assert _stock(ctx, campaign) == Decimal(250)

        service.replay(campaign, premier)

        assert _stock(ctx, campaign) == Decimal(100)

    def test_le_rejeu_laisse_sa_propre_trace(self, ctx, service, campaign):
        """Un retour en arrière est une écriture : l'historique doit le porter.

        Sans quoi l'audit décrirait une campagne dont les chiffres ont changé
        sans que rien ne l'explique.
        """
        premier = _charger(service, campaign, 100)
        _charger(service, campaign, 250)
        avant = len(ctx.imports.list(campaign.id))

        service.replay(campaign, premier)

        assert len(ctx.imports.list(campaign.id)) == avant + 1

    def test_il_rend_le_meme_compte_rendu_quun_import(self, service, campaign):
        """C'est le même import : l'écran qui l'affiche n'a rien à apprendre."""
        premier = _charger(service, campaign, 100)
        outcome = service.replay(campaign, premier)

        assert outcome.rows_accepted == 1
        assert outcome.target == "book_stock"

    def test_et_se_rejoue_autant_de_fois_quon_veut(self, ctx, service, campaign):
        premier = _charger(service, campaign, 100)
        _charger(service, campaign, 250)

        for _ in range(3):
            service.replay(campaign, premier)

        assert _stock(ctx, campaign) == Decimal(100)


class TestCeQuiNaPasDeFichierNeSeRejouePas:
    """Un collage et une lecture ERP n'archivent rien : le refus le dit."""

    def test_un_collage_est_refuse_en_nommant_la_raison(self, service, campaign):
        outcome = service.import_book_stock(
            campaign, mode="paste",
            text=(
                "Numéro d'article\tEntrepôt\tEmplacement\tQuantité\tCoût unitaire\n"
                f"{ITEM}\tATP\tSOL\t7\t2"
            ),
        )
        with pytest.raises(NotFoundError) as refus:
            service.replay(campaign, outcome.batch_id or "")
        assert "collages" in str(refus.value)

    def test_un_lot_inconnu_aussi(self, service, campaign):
        with pytest.raises(NotFoundError):
            service.replay(campaign, str(uuid.uuid4()))

    def test_le_lot_dune_autre_campagne_reste_hors_de_portee(
        self, db, service, campaign
    ):
        """L'identifiant vient de l'URL ; le filtre campagne n'est pas une
        ceinture de plus mais la seule chose qui empêche de rejouer ailleurs."""
        from inventory.config import get_settings
        from inventory.services.context import ServiceContext
        from inventory.services.import_service import ImportService

        autre_id = make_campaign(db, f"RJ-{uuid.uuid4().hex[:8]}")
        with db.transaction() as conn:
            conn.execute("UPDATE campaign SET status='COUNTING' WHERE id=%s",
                         (autre_id,))
        autre_ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        autre_ctx.guard = lambda c, w: None  # type: ignore[method-assign]
        autre = Campaign(
            id=autre_id, code=f"RJ-{autre_id[:8]}", label="",
            count_date=dt.date(2026, 9, 13), status=CampaignStatus.COUNTING,
            created_by="test", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        )
        autre_ctx.referentials.upsert_items(
            [Item(campaign_id=autre_id, item_number=ITEM, name="X",
                  std_price=Decimal(2))],
            actor="test",
        )
        ailleurs = _charger(ImportService(autre_ctx), autre, 400)

        with pytest.raises(NotFoundError):
            service.replay(campaign, ailleurs)


class TestLaPeriodeDeLEcartBackflushEstRetrouvee:
    """Sans ses bornes, le rejeu répondrait à une autre question.

    L'écart backflush est qualifié par une période, et la table des lots ne la
    range nulle part. Elle est dans le **rapport** du lot, où l'import l'a
    écrite. La lire là est ce qui rend le rejeu fidèle plutôt que plausible.
    """

    def test_un_lot_sans_bornes_est_refuse_plutot_que_recalcule(
        self, db, service, campaign
    ):
        """Refuser en disant quoi faire vaut mieux qu'un chiffre faux et muet."""
        from inventory.db import new_id

        batch_id = new_id()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO import_batch (id, campaign_id, target, filename, "
                "storage_path, report, imported_by) "
                "VALUES (%s, %s, 'backflush', 'b.xlsx', 'un/chemin', '{}', 'test')",
                (batch_id, campaign.id),
            )
        with pytest.raises(ValidationError) as refus:
            service.replay(campaign, batch_id)
        assert "période" in str(refus.value)

    def test_les_bornes_du_rapport_sont_celles_du_rejeu(self, db, service, campaign):
        from psycopg.types.json import Jsonb

        from inventory.db import new_id
        from inventory.services.import_replay import _period_of

        batch_id = new_id()
        rapport = {"details": {"periodStart": "2026-06-01", "periodEnd": "2026-06-30"}}
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO import_batch (id, campaign_id, target, filename, "
                "storage_path, report, imported_by) "
                "VALUES (%s, %s, 'backflush', 'b.xlsx', 'un/chemin', %s, 'test')",
                (batch_id, campaign.id, Jsonb(rapport)),
            )
        row = service.ctx.imports.replayable(campaign.id, batch_id)

        assert _period_of("backflush", row["report"]) == {
            "period_start": dt.date(2026, 6, 1),
            "period_end": dt.date(2026, 6, 30),
        }

    def test_une_grille_sans_periode_nen_demande_aucune(self):
        from inventory.services.import_replay import _period_of

        assert _period_of("book_stock", {}) == {}


class TestUneSeuleTableDAiguillage:
    """La route et le rejeu désignent la même méthode pour une cible.

    Deux copies auraient fini par diverger sur ce qu'une cible veut dire — et la
    divergence ne se serait vue que le jour où l'une des deux gagne.
    """

    def test_la_route_importe_la_table_du_service(self):
        from pathlib import Path

        import inventory

        source = (
            Path(inventory.__file__).parent / "api" / "routers" / "data.py"
        ).read_text(encoding="utf-8")
        assert "from ...services.import_replay import" in source
        assert '"items": "import_items"' not in source, "la table a été recopiée"

    def test_une_cible_inconnue_nomme_celles_qui_existent(self):
        from inventory.services.import_replay import resolve_target

        with pytest.raises(ValidationError) as refus:
            resolve_target("inexistant")
        assert "book_stock" in str(refus.value.details)
