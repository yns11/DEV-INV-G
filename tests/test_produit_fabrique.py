"""Le produit fabriqué, et ce qu'il fait voir que rien d'autre ne montrait.

Le cas
------
Deux pièces d'un même assemblage se ressemblent, voisinent sur la même étagère,
et l'une est comptée à la place de l'autre. L'inventaire rend alors un excédent
franc sur la première et un manque du même ordre sur la seconde — deux anomalies
à investiguer, deux allers-retours en magasin, pour **une** erreur qui se corrige
sans bouger de son bureau.

Rien ne les rapprochait. La catégorie est trop large ; le programme dit pour quel
marché la pièce est produite, pas de quel assemblage elle fait partie ; et
l'emplacement les sépare aussi mal que la référence, puisque les deux sont
justement au même endroit.

Ce que ces contrôles tiennent
----------------------------
**Le rattachement se charge comme une grille**, par fichier, collage ou saisie, y
compris sur une campagne dont le référentiel articles est déjà gelé — ce qui est
la raison d'être de la table à part. Une référence appartient à un produit : la
recharger la déplace.

**La compensation est une soustraction, pas une intuition.** Elle se calcule dans
le domaine, sans base et sans modèle, et c'est ce qui permet de la vérifier sur
des chiffres écrits à la main. Trois conditions la délimitent, et chacune retire
un faux positif que la précédente laissait passer.

**Le produit atteint la ligne d'écart.** Sans cela, ni le filtre de la vue
Écarts, ni la colonne de l'export, ni le dossier envoyé au modèle n'auraient de
quoi le lire — la fonctionnalité existerait en base et nulle part ailleurs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import BookStockLine, Campaign, Item, ItemProduct
from inventory.errors import FrozenError

pytestmark = pytest.mark.postgres

WH, LOC = "B06", "A-01"

#: Deux références du même assemblage, et une troisième d'un autre. La troisième
#: est ce qui distingue « le rapprochement marche » de « le rapprochement
#: rapproche tout ».
STATOR, ROTOR, VIS = "P-STATOR", "P-ROTOR", "P-VIS"
MOTEUR, AUTRE = "MOTEUR M3 GEN2", "MOTEUR M5"
TOUTES = (STATOR, ROTOR, VIS)


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_produit") as database:
        yield database


def _campaign(campaign_id: str, status: CampaignStatus) -> Campaign:
    return Campaign(
        id=campaign_id,
        code=f"PR-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 9, 12),
        status=status,
        created_by="test",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"PR-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status='ANALYSIS' WHERE id=%s", (campaign_id,)
        )
    return _campaign(campaign_id, CampaignStatus.ANALYSIS)


@pytest.fixture
def ctx(db, campaign, monkeypatch):
    """Le contexte, ses trois articles, et l'archive sur la base.

    Le chargement dépose sa pièce justificative comme tout import, et le dos
    « volume Unity Catalog » demande un espace de travail Databricks que ces
    contrôles n'ont pas.
    """
    from inventory.config import Settings, get_settings
    from inventory.services.context import ServiceContext

    monkeypatch.setenv("INV_EVIDENCE_STORE", "lakebase")
    get_settings.cache_clear()
    monkeypatch.setattr(
        Settings, "evidence_configured", property(lambda self: True), raising=False
    )
    context = ServiceContext(actor="test", db=db, settings=get_settings())
    context.referentials.upsert_items(
        [
            Item(
                campaign_id=campaign.id,
                item_number=number,
                name=f"Article {number}",
                std_price=Decimal(10),
            )
            for number in TOUTES
        ],
        actor="test",
    )
    return context


@pytest.fixture
def service(ctx):
    from inventory.services import ImportService

    return ImportService(ctx)


def _rattacher(ctx, campaign: Campaign, *pairs: tuple[str, str]) -> None:
    ctx.products.upsert(
        campaign.id,
        [
            ItemProduct(campaign_id=campaign.id, item_number=number, product=product)
            for number, product in pairs
        ],
        actor="test",
    )


def _couples(ctx, campaign: Campaign) -> set[tuple[str, str]]:
    return {(r.item_number, r.product) for r in ctx.products.list(campaign.id)}


# --------------------------------------------------------------------------- #
# 1. Le rattachement
# --------------------------------------------------------------------------- #


class TestUneReferenceAppartientAUnProduit:
    def test_un_rattachement_se_pose(self, ctx, campaign):
        _rattacher(ctx, campaign, (STATOR, MOTEUR))

        assert _couples(ctx, campaign) == {(STATOR, MOTEUR)}

    def test_recharger_une_reference_la_deplace(self, ctx, campaign):
        """Elle n'appartient qu'à un produit : la recharger ne l'ajoute pas à un
        second, sans quoi le même écart compterait dans deux assemblages."""
        _rattacher(ctx, campaign, (STATOR, MOTEUR))
        _rattacher(ctx, campaign, (STATOR, AUTRE))

        assert _couples(ctx, campaign) == {(STATOR, AUTRE)}

    def test_un_second_chargement_ne_detache_pas_les_autres(self, ctx, campaign):
        """Un fichier de trente références ne dit rien des quatre cent cinquante
        autres."""
        _rattacher(ctx, campaign, (STATOR, MOTEUR))
        _rattacher(ctx, campaign, (ROTOR, MOTEUR))

        assert _couples(ctx, campaign) == {(STATOR, MOTEUR), (ROTOR, MOTEUR)}

    def test_un_produit_vide_detache(self, ctx, campaign):
        _rattacher(ctx, campaign, (STATOR, MOTEUR), (ROTOR, MOTEUR))
        _rattacher(ctx, campaign, (STATOR, ""))

        assert _couples(ctx, campaign) == {(ROTOR, MOTEUR)}

    def test_le_produit_est_normalise_comme_une_cle(self, ctx, campaign):
        """« M3 GEN2 » et « m3  gen2 » désignent le même produit : deux graphies
        en feraient deux valeurs à cocher dans une liste qui n'en attend
        qu'une."""
        _rattacher(ctx, campaign, (STATOR, "  moteur m3   gen2 "))

        assert [r.product for r in ctx.products.list(campaign.id)] == ["MOTEUR M3 GEN2"]

    def test_tout_detacher_est_un_geste_a_part(self, ctx, campaign):
        from inventory.services.product_service import clear_products

        _rattacher(ctx, campaign, (STATOR, MOTEUR), (ROTOR, MOTEUR))

        assert clear_products(ctx, campaign) == 2
        assert ctx.products.list(campaign.id) == []


class TestLeChargementPasseParLePipelineDesGrilles:
    def test_un_collage_charge_le_tableau(self, service, ctx, campaign):
        outcome = service.import_products(
            campaign, mode="paste",
            text=f"Article\tProduit fabriqué\n{STATOR}\t{MOTEUR}\n{ROTOR}\t{MOTEUR}\n",
        )

        assert outcome.rows_accepted == 2
        assert outcome.details["products"] == 1
        assert _couples(ctx, campaign) == {(STATOR, MOTEUR), (ROTOR, MOTEUR)}

    def test_le_contrat_est_declare_comme_les_autres(self):
        from inventory.ingest.contracts import CONTRACTS

        assert "products" in CONTRACTS
        assert CONTRACTS["products"].natural_key == ("item_number",)

    def test_une_reference_hors_referentiel_est_acceptee_et_signalee(
        self, service, ctx, campaign
    ):
        outcome = service.import_products(
            campaign, mode="paste",
            text=f"Article\tProduit fabriqué\n{STATOR}\t{MOTEUR}\nP-999\t{MOTEUR}\n",
        )

        assert outcome.details["unknownItems"] == 1
        assert outcome.details["unknownItemNumbers"] == ["P-999"]
        assert (("P-999", MOTEUR)) in _couples(ctx, campaign)

    def test_le_chargement_est_rejouable(self):
        from inventory.services.import_replay import TARGET_METHODS

        assert TARGET_METHODS["products"] == "import_products"


class TestIlSeChargeSurUneCampagneDejaGelee:
    """La raison d'être de la table à part.

    Le référentiel articles gèle à l'entrée en comptage. Une colonne d'`item`
    serait donc arrivée trop tard pour les campagnes qu'on analyse aujourd'hui —
    c'est-à-dire précisément celles où ce rattachement sert.
    """

    @pytest.mark.parametrize(
        "status",
        [CampaignStatus.PREPARATION, CampaignStatus.COUNTING, CampaignStatus.ANALYSIS],
    )
    def test_il_reste_ouvert_jusqu_a_la_cloture(self, service, campaign, status):
        outcome = service.import_products(
            _campaign(campaign.id, status), mode="paste",
            text=f"Article\tProduit fabriqué\n{STATOR}\t{MOTEUR}\n",
        )

        assert outcome.rows_accepted == 1

    def test_le_referentiel_articles_lui_est_bien_ferme_au_comptage(
        self, service, campaign
    ):
        """Le contraste qui donne son sens au contrôle précédent : au même
        statut, le référentiel articles refuse ce que celui-ci accepte."""
        with pytest.raises(FrozenError):
            service.import_items(
                _campaign(campaign.id, CampaignStatus.COUNTING), mode="paste",
                text="Article\tDésignation\nP-NEUF\tQuelque chose\n",
            )

    def test_mais_il_se_ferme_a_la_cloture(self, service, campaign):
        with pytest.raises(FrozenError):
            service.import_products(
                _campaign(campaign.id, CampaignStatus.CLOSED), mode="paste",
                text=f"Article\tProduit fabriqué\n{STATOR}\t{MOTEUR}\n",
            )


# --------------------------------------------------------------------------- #
# 2. Le produit atteint la ligne d'écart
# --------------------------------------------------------------------------- #


def _stock(ctx, campaign: Campaign, quantities: dict[str, int]) -> None:
    ctx.book_stock.replace(
        campaign.id,
        [
            BookStockLine(
                campaign_id=campaign.id,
                item_number=number,
                warehouse_id=WH,
                location_id=LOC,
                qty=Decimal(qty),
                unit_cost=Decimal(10),
                reference_date=dt.date(2026, 9, 12),
            )
            for number, qty in quantities.items()
        ],
        batch_id=None,
    )


class TestLeProduitAtteintLaLigneDEcart:
    def test_la_ligne_le_porte(self, ctx, campaign):
        """Sans cela, le filtre de la vue Écarts, la colonne de l'export et le
        dossier envoyé au modèle n'auraient rien à lire : la fonctionnalité
        existerait en base et nulle part ailleurs."""
        from inventory.services import AnalysisService

        _stock(ctx, campaign, {STATOR: 10, VIS: 5})
        _rattacher(ctx, campaign, (STATOR, MOTEUR))

        rows = {
            r["itemNumber"]: r["manufacturedProduct"]
            for r in AnalysisService(ctx).top_variances(campaign)
        }
        assert rows[STATOR] == MOTEUR

    def test_une_reference_sans_produit_porte_une_chaine_vide(self, ctx, campaign):
        """Et non l'absence de clé : l'écran lit la même colonne sur toutes les
        lignes, et une clé manquante y serait un cas de plus à traiter."""
        from inventory.services import AnalysisService

        _stock(ctx, campaign, {VIS: 5})

        rows = AnalysisService(ctx).top_variances(campaign)
        assert rows[0]["manufacturedProduct"] == ""

    def test_l_export_excel_porte_sa_colonne(self, ctx, campaign):
        import io

        from openpyxl import load_workbook

        from inventory.services import ReportService

        _stock(ctx, campaign, {STATOR: 10})
        _rattacher(ctx, campaign, (STATOR, MOTEUR))

        payload, _ = ReportService(ctx).variance_export(campaign)
        classeur = load_workbook(io.BytesIO(payload))
        texte = "\n".join(
            str(cell.value)
            for sheet in classeur.worksheets
            for row in sheet.iter_rows()
            for cell in row
            if cell.value is not None
        )

        assert "Produit fabriqué" in texte
        assert MOTEUR in texte
