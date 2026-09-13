"""Recharger l'export ERP rafraîchit une ligne ; il n'en ajoute pas une seconde.

Ce qui s'est passé sur une campagne terrain
-------------------------------------------
Le journal GENERIQUE est rempli par la consolidation — une valeur **manuelle**
pour chacun de ses articles — puis exporté, collé dans l'ERP et posté.
L'extraction suivante le rapporte, l'application le recharge, et ses 245
articles se retrouvent comptés deux fois : 6 448 049 unités lues pour
3 224 025 comptées.

Le mécanisme. Le rechargement supprimait les lignes sans valeur manuelle, puis
réinsérait **toutes** celles du fichier avec un identifiant neuf, sans jamais
retrouver celle qui portait déjà cet article. La ligne à valeur manuelle
survivait au ménage — c'est ce qui protège une correction humaine, et c'est
juste — et l'import lui en ajoutait une seconde à côté. Les deux vivaient, et
les quantités comptées les additionnaient.

Le commentaire du dépôt promettait « remplace `qty_imported` mais préserve
`qty_manual` ». La préservation marchait ; le remplacement, non — et aucun
contrôle ne couvrait la combinaison des deux. C'est ce module.

Ce qui reste permis
-------------------
Deux lignes manuelles pour un même article : l'écran permet de les créer, ce
sont deux relevés distincts au même endroit, et elles s'additionnent. Aucun
index unique ne l'interdit. Ce qui ne doit pas coexister, c'est une ligne
importée **à côté** d'une ligne qui porte déjà une valeur : les deux décrivent
la même mesure.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.db import new_id
from inventory.domain.enums import CampaignStatus, DataSource, JournalKind
from inventory.domain.models import Campaign, CountJournalLine, Item, LocationKey

pytestmark = pytest.mark.postgres

WH, LOC = "B06VRAC", "GENERIQUE"
KEY = LocationKey(warehouse_id=WH, location_id=LOC)
ITEM = "P-00005775"


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_reimport") as database:
        yield database


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"RE-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute("UPDATE campaign SET status='COUNTING' WHERE id=%s", (campaign_id,))
    return Campaign(
        id=campaign_id, code=f"RE-{campaign_id[:8]}", label="",
        count_date=dt.date(2026, 9, 12), status=CampaignStatus.COUNTING,
        created_by="test", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def ctx(db, campaign, monkeypatch):
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    context = ServiceContext(actor="test", db=db, settings=get_settings())
    monkeypatch.setattr(context, "guard", lambda c, w: None, raising=False)
    context.referentials.upsert_items(
        [Item(campaign_id=campaign.id, item_number=ITEM, name="X",
              std_price=Decimal(3))],
        actor="test",
    )
    context.journals.ensure_journals(
        campaign.id, [KEY], kinds={KEY: JournalKind.INVV}, actor="test"
    )
    return context


@pytest.fixture
def journal_id(ctx, campaign):
    """Un journal en cours : `counted_quantities` ignore ceux qui dorment.

    C'est voulu — un journal que personne n'a ouvert n'apporte aucun comptage —
    et il faut donc le réveiller ici, sans quoi ces contrôles liraient zéro
    partout et passeraient pour de mauvaises raisons.
    """
    from inventory.domain.enums import JournalStatus

    journal = next(j for j in ctx.journals.list(campaign.id) if j.key == KEY)
    ctx.journals.set_status(
        campaign.id, [journal.id], JournalStatus.IN_PROGRESS, actor="test"
    )
    return journal.id


def _importee(campaign, journal_id, qty, *, item=ITEM) -> CountJournalLine:
    """Ce que l'import fabrique : une ligne portée par `qty_imported`."""
    return CountJournalLine(
        id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
        item_number=item, qty_imported=Decimal(qty), unit="PCE",
        source=DataSource.ERP_IMPORT, updated_by="import",
        qty_on_hand=Decimal(100), erp_journal_number="NPEM-VRAC", label_count=1,
    )


def _valeur_de_lapplication(ctx, campaign, journal_id, qty, *, source, item=ITEM):
    """Ce que la consolidation ou une correction pose : une valeur manuelle."""
    ctx.journals.replace_lines_for_journal(
        journal_id, campaign.id,
        [CountJournalLine(
            id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
            item_number=item, qty_manual=Decimal(qty), unit="PCE", source=source,
        )],
        actor="test",
    )


def _lignes(ctx, campaign, journal_id) -> list[CountJournalLine]:
    return ctx.journals.lines_by_journal(campaign.id).get(journal_id, [])


def _deux_saisies(ctx, campaign, journal_id) -> list[str]:
    """Deux relevés manuels du même article, comme l'écran permet d'en créer."""
    ids = []
    for qty in (5, 7):
        ligne = CountJournalLine(
            id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
            item_number=ITEM, qty_manual=Decimal(qty), unit="PCE",
            source=DataSource.MANUAL,
        )
        ctx.journals.upsert_line(ligne, actor="test")
        ids.append(ligne.id)
    return ids


def _compte(ctx, campaign, item=ITEM) -> Decimal | None:
    for row in ctx.journals.counted_quantities(campaign.id):
        if row["item_number"] == item:
            return row["qty"]
    return None


class TestLaConsolidationNEstPasDoubleeParSonRetour:
    """Le cas réel, au grain d'un article."""

    def test_le_journal_ne_porte_quune_ligne(self, ctx, campaign, journal_id):
        _valeur_de_lapplication(ctx, campaign, journal_id, 40,
                                source=DataSource.CONSOLIDATION)
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 40)]
        )
        assert len(_lignes(ctx, campaign, journal_id)) == 1

    def test_et_la_quantite_comptee_reste_celle_du_comptage(
        self, ctx, campaign, journal_id
    ):
        """Le chiffre que l'exploitant a vu doubler."""
        _valeur_de_lapplication(ctx, campaign, journal_id, 40,
                                source=DataSource.CONSOLIDATION)
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 40)]
        )
        assert _compte(ctx, campaign) == Decimal(40)

    def test_la_ligne_porte_desormais_les_deux_valeurs(
        self, ctx, campaign, journal_id
    ):
        """Ce que l'écho de l'ERP apportait n'est pas jeté, il est reporté."""
        _valeur_de_lapplication(ctx, campaign, journal_id, 40,
                                source=DataSource.CONSOLIDATION)
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 40)]
        )
        ligne = _lignes(ctx, campaign, journal_id)[0]
        assert ligne.qty_manual == Decimal(40)
        assert ligne.qty_imported == Decimal(40)

    def test_rien_ne_saccumule_aux_rechargements_suivants(
        self, ctx, campaign, journal_id
    ):
        """Le jour J, l'extraction revient toutes les quinze minutes."""
        _valeur_de_lapplication(ctx, campaign, journal_id, 40,
                                source=DataSource.CONSOLIDATION)
        for _ in range(4):
            ctx.journals.replace_imported_lines(
                campaign.id, [journal_id], [_importee(campaign, journal_id, 40)]
            )
        assert len(_lignes(ctx, campaign, journal_id)) == 1
        assert _compte(ctx, campaign) == Decimal(40)


class TestUneCorrectionManuelleNEstPasDoubleeNonPlus:
    """Le même mécanisme, sur un journal ordinaire."""

    def test_la_correction_survit_et_reste_seule(self, ctx, campaign, journal_id):
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 30)]
        )
        ligne = _lignes(ctx, campaign, journal_id)[0]
        ctx.journals.upsert_line(
            ligne.model_copy(update={"qty_manual": Decimal(35)}), actor="test"
        )

        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 30)]
        )

        assert len(_lignes(ctx, campaign, journal_id)) == 1
        assert _compte(ctx, campaign) == Decimal(35)

    def test_et_la_valeur_de_lerp_se_rafraichit_sous_elle(
        self, ctx, campaign, journal_id
    ):
        """C'est la promesse d'origine du dépôt, et elle doit tenir aussi."""
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 30)]
        )
        ligne = _lignes(ctx, campaign, journal_id)[0]
        ctx.journals.upsert_line(
            ligne.model_copy(update={"qty_manual": Decimal(35)}), actor="test"
        )

        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 31)]
        )

        refreshed = _lignes(ctx, campaign, journal_id)[0]
        assert refreshed.qty_manual == Decimal(35), "la correction ne s'efface pas"
        assert refreshed.qty_imported == Decimal(31), "l'ERP, lui, se rafraîchit"


class TestCeQuiResteLegitime:
    """Élargir la garde ne doit pas retirer un geste que le métier utilise."""

    def test_deux_saisies_manuelles_du_meme_article_survivent(
        self, ctx, campaign, journal_id
    ):
        """Deux relevés distincts au même endroit s'additionnent.

        C'est pourquoi aucun index unique ne porte (journal, article) : il
        interdirait ce que l'écran permet de créer.
        """
        for qty in (5, 7):
            ctx.journals.upsert_line(
                CountJournalLine(
                    id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
                    item_number=ITEM, qty_manual=Decimal(qty), unit="PCE",
                    source=DataSource.MANUAL,
                ),
                actor="test",
            )

        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 12)]
        )

        assert len(_lignes(ctx, campaign, journal_id)) == 2
        assert _compte(ctx, campaign) == Decimal(12), "5 + 7, et non 24"

    def test_lecho_de_lerp_se_pose_sur_la_plus_ancienne_et_sur_elle_seule(
        self, ctx, campaign, journal_id
    ):
        """Le poser sur les deux ne changerait rien au total, mais mentirait :
        l'ERP n'a rapporté qu'un chiffre pour cet article.

        Laquelle des deux importe peu ; ce qui compte est que ce soit **toujours
        la même**.
        """
        _deux_saisies(ctx, campaign, journal_id)
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 12)]
        )

        portees = [l.qty_imported for l in _lignes(ctx, campaign, journal_id)]
        assert sorted(portees, key=lambda v: v is None) == [Decimal(12), None]

    def test_et_elle_ne_se_deplace_pas_aux_rechargements_suivants(
        self, ctx, campaign, journal_id
    ):
        """Le contrôle qui a trouvé un vrai défaut, et la raison du tri sur `id`.

        Le choix se faisait d'abord sur `updated_at` — « la plus ancienne » —
        mais le rechargement met justement cette colonne à jour. La ligne
        choisie devenait la plus récente, l'import suivant portait l'écho sur
        l'autre, et au bout de deux passages les deux annonçaient chacune le
        chiffre de l'ERP. Le jour J, avec une extraction tous les quarts
        d'heure, il aurait fait des allers-retours toute la journée.
        """
        _deux_saisies(ctx, campaign, journal_id)
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 12)]
        )
        choisie = next(
            l.id for l in _lignes(ctx, campaign, journal_id)
            if l.qty_imported is not None
        )

        for _ in range(3):
            ctx.journals.replace_imported_lines(
                campaign.id, [journal_id], [_importee(campaign, journal_id, 12)]
            )

        portees = {l.id: l.qty_imported for l in _lignes(ctx, campaign, journal_id)}
        assert portees[choisie] == Decimal(12), "la même à chaque fois"
        assert sum(1 for v in portees.values() if v is not None) == 1

    def test_un_article_absent_du_fichier_precedent_entre_normalement(
        self, ctx, campaign, journal_id
    ):
        """Le chemin d'insertion existe toujours : sans lui, plus rien n'entre."""
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 9)]
        )
        assert _compte(ctx, campaign) == Decimal(9)

    def test_une_ligne_importee_disparue_du_fichier_sen_va(
        self, ctx, campaign, journal_id
    ):
        """L'autre moitié de « remplacer » : ce qui n'est plus là ne reste pas."""
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 9)]
        )
        ctx.journals.replace_imported_lines(campaign.id, [journal_id], [])
        assert _lignes(ctx, campaign, journal_id) == []


class TestLaMigrationRangeCeQuiAvaitDejaEteEcrit:
    """La 032, sur les quatre formes de lignes qui existent en base.

    Le code ne crée plus le doublon ; restent celles déjà écrites. Ce sont
    elles que la migration fusionne, et elle ne doit toucher que celles-là.
    """

    def _replay(self, db) -> None:
        from inventory.db.migrations import MIGRATIONS_DIR

        sql = (MIGRATIONS_DIR / "032_doublons_de_comptage_fusionnes.sql").read_text(
            encoding="utf-8"
        )
        with db.transaction() as conn, conn.cursor() as cur:
            cur.execute(sql)

    def _doublon(self, db, ctx, campaign, journal_id, *, item, manual, imported,
                 source=DataSource.MANUAL) -> None:
        """L'état exact que l'ancien import laissait : deux lignes vivantes."""
        for line in (
            CountJournalLine(
                id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
                item_number=item, qty_manual=Decimal(manual), unit="PCE",
                source=source,
            ),
            CountJournalLine(
                id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
                item_number=item, qty_imported=Decimal(imported), unit="PCE",
                source=DataSource.ERP_IMPORT,
            ),
        ):
            ctx.journals.upsert_line(line, actor="test")

    def test_la_consolidation_doublee_revient_a_son_chiffre(
        self, db, ctx, campaign, journal_id
    ):
        self._doublon(db, ctx, campaign, journal_id, item=ITEM, manual=40,
                      imported=40, source=DataSource.CONSOLIDATION)
        assert _compte(ctx, campaign) == Decimal(80), "l'état d'avant, à réparer"

        self._replay(db)

        assert _compte(ctx, campaign) == Decimal(40)
        assert len(_lignes(ctx, campaign, journal_id)) == 1

    def test_la_valeur_de_lerp_est_reportee_avant_le_retrait(
        self, db, ctx, campaign, journal_id
    ):
        """Rien n'est jeté : la ligne qui survit porte les deux."""
        self._doublon(db, ctx, campaign, journal_id, item=ITEM, manual=35,
                      imported=30)
        self._replay(db)

        ligne = _lignes(ctx, campaign, journal_id)[0]
        assert ligne.qty_manual == Decimal(35)
        assert ligne.qty_imported == Decimal(30)

    def test_la_redondante_est_retiree_logiquement_et_signee(
        self, db, ctx, campaign, journal_id
    ):
        """Une ligne disparue sans auteur est une ligne inexplicable six mois
        plus tard. Et elle reste relisible : `deleted_at`, pas `DELETE`."""
        self._doublon(db, ctx, campaign, journal_id, item=ITEM, manual=35,
                      imported=30)
        self._replay(db)

        with db.connection() as conn:
            retirees = conn.execute(
                "SELECT qty_imported, updated_by FROM count_journal_line "
                "WHERE journal_id = %s AND deleted_at IS NOT NULL",
                (journal_id,),
            ).fetchall()
        assert len(retirees) == 1
        assert retirees[0]["updated_by"] == "migration-032"
        assert retirees[0]["qty_imported"] == Decimal(30), "elle garde son contenu"

    def test_deux_saisies_manuelles_ne_sont_pas_fusionnees(
        self, db, ctx, campaign, journal_id
    ):
        """Ce que l'écran permet de créer, la migration ne le défait pas."""
        for qty in (5, 7):
            ctx.journals.upsert_line(
                CountJournalLine(
                    id=new_id(), journal_id=journal_id, campaign_id=campaign.id,
                    item_number=ITEM, qty_manual=Decimal(qty), unit="PCE",
                    source=DataSource.MANUAL,
                ),
                actor="test",
            )
        self._replay(db)

        assert len(_lignes(ctx, campaign, journal_id)) == 2
        assert _compte(ctx, campaign) == Decimal(12)

    def test_elle_choisit_la_meme_ligne_daccueil_que_limport(
        self, db, ctx, campaign, journal_id
    ):
        """La plus ancienne, des deux côtés.

        Si les deux règles divergeaient, une base fusionnée puis rechargée
        finirait avec l'écho de l'ERP sur deux lignes — et le doublon
        reviendrait par la porte de derrière.
        """
        saisies = _deux_saisies(ctx, campaign, journal_id)
        ctx.journals.upsert_line(_importee(campaign, journal_id, 12), actor="test")
        self._replay(db)
        accueil = next(
            l.id for l in _lignes(ctx, campaign, journal_id)
            if l.qty_imported is not None
        )

        # Ce que l'import aurait choisi sur les mêmes lignes.
        assert accueil == min(saisies), "l'identifiant tranche, des deux côtés"

        # Et un rechargement après la fusion ne réveille pas un second porteur.
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 12)]
        )
        portees = [l.qty_imported for l in _lignes(ctx, campaign, journal_id)]
        assert sum(1 for v in portees if v is not None) == 1

    def test_une_ligne_importee_seule_nest_pas_touchee(
        self, db, ctx, campaign, journal_id
    ):
        """Le cas de l'immense majorité des journaux : rien à fusionner."""
        ctx.journals.replace_imported_lines(
            campaign.id, [journal_id], [_importee(campaign, journal_id, 9)]
        )
        self._replay(db)

        assert len(_lignes(ctx, campaign, journal_id)) == 1
        assert _compte(ctx, campaign) == Decimal(9)

    def test_elle_se_rejoue_sans_rien_changer(self, db, ctx, campaign, journal_id):
        self._doublon(db, ctx, campaign, journal_id, item=ITEM, manual=40,
                      imported=40, source=DataSource.CONSOLIDATION)
        self._replay(db)
        apres_un = _compte(ctx, campaign)
        self._replay(db)
        self._replay(db)

        assert _compte(ctx, campaign) == apres_un == Decimal(40)
