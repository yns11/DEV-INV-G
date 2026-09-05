"""Les deux passages d'une zone portent le même document.

C'est la définition du double comptage : deux équipes à qui l'on pose la **même
question**, et dont on compare les réponses. Deux documents différents rendent
la comparaison sans objet — un article présent d'un côté et absent de l'autre
remonte en arbitrage comme un désaccord, alors que personne n'a jamais été
invité à le compter.

La copie ne descendait qu'à la création de la seconde feuille, et seulement pour
y **ajouter** ce qui manquait. Une référence retirée, un intertitre renommé,
deux lignes échangées : rien de tout cela ne passait, et les deux feuilles
divergeaient dès la première correction — silencieusement, jusqu'à l'arbitrage.

Ce qui ne se recopie pas, ce sont les **quantités** : ce serait cesser de
compter deux fois. Elles sont retrouvées ligne à ligne sur la clé, parce que les
perdre à chaque correction de la feuille serait pire que de les recopier.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.config import get_settings
from inventory.db import new_id
from inventory.db.repositories import SheetRepository
from inventory.domain.enums import (
    CountLineKind,
    CountSection,
    DataSource,
    SheetPass,
)
from inventory.domain.models import CountSheetLine, Item, Zone
from inventory.services.context import ServiceContext
from inventory.services.generic_service import GenericService

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_document_partage") as database:
        yield database


@pytest.fixture
def bench(db):
    """Une zone à deux comptages, son service, et ses deux feuilles."""
    sheets = SheetRepository(db)
    campaign_id = make_campaign(db, f"DOC-{uuid.uuid4().hex[:8]}")
    ctx = ServiceContext(actor="test", db=db, settings=get_settings())
    ctx.referentials.upsert_items(
        [
            Item(campaign_id=campaign_id, item_number=n, name=n)
            for n in ("P-1", "P-2", "P-3")
        ],
        actor="test",
    )
    zone = sheets.create_zone(
        Zone(id=new_id(), campaign_id=campaign_id, code="ZONE-1", passes=2),
        actor="test",
    )
    sheets.ensure_sheets(
        campaign_id, zone.id, [SheetPass.PASS_1, SheetPass.PASS_2], actor="test"
    )
    by_pass = {s.pass_no: s for s in sheets.list_sheets(campaign_id, zone_id=zone.id)}
    return (
        GenericService(ctx),
        ctx.campaigns.get(campaign_id),
        zone,
        sheets,
        by_pass[SheetPass.PASS_1],
        by_pass[SheetPass.PASS_2],
    )


def rows(*specs) -> list[dict]:
    """Le document tel que l'écran l'envoie."""
    return [dict(spec) for spec in specs]


def article(number: str, **kwargs) -> dict:
    return {"item_number": number, "section": "LINE_SIDE", **kwargs}


def start_counting(db, campaign_id: str) -> None:
    """Ouvrir la saisie des quantités sans rejouer toute la préparation.

    Le raccourci est assumé : ce qui est vérifié ici est le partage du document
    entre les deux passages, pas le séquencement des phases — qui a ses propres
    contrôles, et qui refuserait la saisie tant que le stock ERP n'est pas gelé.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'COUNTING', "
            "book_stock_frozen_at = now() WHERE id = %s",
            (campaign_id,),
        )
        conn.execute(
            "INSERT INTO book_stock (id, campaign_id, item_number, warehouse_id, "
            "location_id, qty, unit, unit_cost) VALUES "
            "(gen_random_uuid(), %s, 'P-1', 'B06', 'PAL 01', 1, 'PCE', 1) "
            "ON CONFLICT DO NOTHING",
            (campaign_id,),
        )


class TestLeDocumentDescendSurLeSecondPassage:
    def test_une_reference_ajoutee_arrive_sur_les_deux_feuilles(self, bench):
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1"), article("P-2")), replace=True
        )

        assert [l.item_number for l in sheets.list_sheet_lines(second.id)] == [
            "P-1", "P-2"
        ]

    def test_une_reference_retiree_part_des_deux(self, bench):
        """Le défaut : elle restait au passage 2, et remontait en désaccord."""
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1"), article("P-2")), replace=True
        )
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1")), replace=True
        )

        assert [l.item_number for l in sheets.list_sheet_lines(second.id)] == ["P-1"]

    def test_un_intertitre_suit_aussi(self, bench):
        """C'est lui qui dit au compteur *où aller* : le second doit l'avoir."""
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(campaign, first.id, rows(
            {"line_kind": "SUBSECTION", "label": "Stock physique B15"},
            article("P-1"),
        ), replace=True)

        mirrored = sheets.list_sheet_lines(second.id)
        assert [(l.line_kind, l.label) for l in mirrored] == [
            (CountLineKind.SUBSECTION, "Stock physique B15"),
            (CountLineKind.ARTICLE, ""),
        ]

    def test_l_ordre_est_le_meme_des_deux_cotes(self, bench):
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(campaign, first.id, rows(
            article("P-3"), article("P-1"), article("P-2"),
        ), replace=True)

        assert [l.item_number for l in sheets.list_sheet_lines(second.id)] == [
            "P-3", "P-1", "P-2"
        ]

    def test_une_ligne_supprimee_part_des_deux_feuilles(self, bench):
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1"), article("P-2")), replace=True
        )
        doomed = next(
            l for l in sheets.list_sheet_lines(first.id) if l.item_number == "P-2"
        )
        service.delete_sheet_lines(campaign, [doomed.id])

        assert [l.item_number for l in sheets.list_sheet_lines(second.id)] == ["P-1"]


class TestMaisPasLesQuantites:
    """Recopier les quantités, ce serait cesser de compter deux fois."""

    def test_la_recopie_ne_pose_aucune_quantite(self, bench, db):
        service, campaign, _zone, sheets, first, second = bench
        start_counting(db, campaign.id)
        campaign = service.ctx.campaigns.get(campaign.id)
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1", qty=42)), replace=True
        )

        [mirrored] = sheets.list_sheet_lines(second.id)
        assert mirrored.has_entry is False

    def test_une_quantite_deja_relevee_au_second_passage_survit(self, bench, db):
        """Sinon corriger la feuille effacerait le travail du second compteur.

        C'est le cas courant : le passage 2 est en cours quand quelqu'un
        s'aperçoit qu'une référence manque sur la liste.
        """
        service, campaign, _zone, sheets, first, second = bench
        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1")), replace=True
        )
        counted = sheets.list_sheet_lines(second.id)[0]
        sheets.upsert_sheet_lines(
            [counted.model_copy(update={"qty_manual": Decimal(17)})],
            actor="compteur",
        )

        service.upsert_sheet_lines(
            campaign, first.id, rows(article("P-1"), article("P-2")), replace=True
        )

        kept = {l.item_number: l for l in sheets.list_sheet_lines(second.id)}
        assert kept["P-1"].qty == 17
        assert kept["P-2"].has_entry is False


class TestLaProvenanceAppartientALaLigne:
    """Une correction à la main ne réécrit pas la provenance de la feuille.

    L'écran renvoie les cent lignes qu'il affiche, y compris les
    quatre-vingt-dix-neuf que personne n'a touchées. Les marquer toutes « saisie
    manuelle » effaçait la trace de la lecture IA sur toute la feuille dès
    qu'une seule cellule était corrigée : on ne pouvait plus dire quelle valeur
    avait été relue par un humain — ce qui est exactement ce que la colonne
    existe pour dire.
    """

    @pytest.fixture
    def read_by_ai(self, bench, db):
        service, campaign, _zone, sheets, first, _second = bench
        start_counting(db, campaign.id)
        campaign = service.ctx.campaigns.get(campaign.id)
        sheets.replace_sheet_lines(first.id, [
            CountSheetLine(
                id=new_id(), sheet_id=first.id, campaign_id=campaign.id,
                item_number=number, qty_imported=Decimal(qty),
                source=DataSource.SCAN_AI, confidence=0.9, display_order=order,
            )
            for order, (number, qty) in enumerate((("P-1", 10), ("P-2", 20)))
        ], actor="ia")
        return service, campaign, sheets, first

    def sent(self, sheets, sheet_id, **changes):
        """La feuille telle que l'écran la renvoie : toutes les lignes."""
        return [
            {
                "id": l.id, "item_number": l.item_number, "section": str(l.section),
                "line_kind": str(l.line_kind), "label": l.label, "unit": l.unit,
                "comment": l.comment, "display_order": l.display_order,
                "qty": float(l.qty),
                **(changes if l.item_number == changes.get("item_number") else {}),
            }
            for l in sheets.list_sheet_lines(sheet_id)
        ]

    def test_la_ligne_corrigee_devient_une_saisie(self, read_by_ai):
        service, campaign, sheets, first = read_by_ai
        service.upsert_sheet_lines(
            campaign, first.id,
            self.sent(sheets, first.id, item_number="P-1", qty=11),
            replace=True,
        )

        corrected = {l.item_number: l for l in sheets.list_sheet_lines(first.id)}
        assert corrected["P-1"].source is DataSource.MANUAL
        assert corrected["P-1"].qty == 11

    def test_les_autres_gardent_la_leur(self, read_by_ai):
        """Le défaut, dit tel quel : toute la feuille passait en « saisie »."""
        service, campaign, sheets, first = read_by_ai
        service.upsert_sheet_lines(
            campaign, first.id,
            self.sent(sheets, first.id, item_number="P-1", qty=11),
            replace=True,
        )

        untouched = {l.item_number: l for l in sheets.list_sheet_lines(first.id)}
        assert untouched["P-2"].source is DataSource.SCAN_AI
        assert untouched["P-2"].qty == 20

    def test_et_la_lecture_du_modele_reste_lisible_a_cote(self, read_by_ai):
        """La correction se pose à côté de la lecture, jamais dessus : c'est ce
        qui permet de voir *ce que le modèle avait lu*."""
        service, campaign, sheets, first = read_by_ai
        service.upsert_sheet_lines(
            campaign, first.id,
            self.sent(sheets, first.id, item_number="P-1", qty=11),
            replace=True,
        )

        corrected = {l.item_number: l for l in sheets.list_sheet_lines(first.id)}
        assert corrected["P-1"].qty_imported == 10
        assert corrected["P-1"].qty_manual == 11

    def test_un_enregistrement_sans_rien_changer_ne_change_rien(self, read_by_ai):
        """Ouvrir la feuille et cliquer « Enregistrer » n'est pas un comptage."""
        service, campaign, sheets, first = read_by_ai
        service.upsert_sheet_lines(
            campaign, first.id, self.sent(sheets, first.id), replace=True
        )

        assert all(
            l.source is DataSource.SCAN_AI
            for l in sheets.list_sheet_lines(first.id)
        )


class TestUnArbitrageNeSurvitPasAuChiffreQuIlTranche:
    """« Entre 12 et 15, je retiens 12 » ne veut plus rien dire à 12 contre 40.

    La décision restait pourtant : le comptage n°2 corrigé après coup, la
    consolidation postait toujours 12 — contre un comptage que plus personne
    n'avait regardé. C'est le pire des deux mondes : une quantité validée par
    quelqu'un, sur une comparaison qui n'existe plus.
    """

    from inventory.domain.consolidation import build_arbitration_lines as _build

    @staticmethod
    def zone_counts(zone, sheets_by_pass, lines, arbitrations):
        from inventory.domain.consolidation import ZoneCounts

        return ZoneCounts(
            zone=zone, sheets=list(sheets_by_pass), lines_by_sheet=lines,
            arbitrations=arbitrations,
        )

    def build(self, bench, *, p1: int, p2: int, prior=None):
        from decimal import Decimal

        _service, campaign, zone, _sheets, first, second = bench
        line = lambda sheet, qty: CountSheetLine(
            id=new_id(), sheet_id=sheet.id, campaign_id=campaign.id,
            item_number="P-1", qty_manual=Decimal(qty),
        )
        return type(self)._build(
            self.zone_counts(
                zone, [first, second],
                {first.id: [line(first, p1)], second.id: [line(second, p2)]},
                [prior] if prior else [],
            ),
            campaign_id=campaign.id, id_factory=new_id,
        )

    def decided(self, bench, *, p1: int, p2: int, retained: int):
        """Un arbitrage pris sur ces deux chiffres-là."""
        import datetime as dt
        from decimal import Decimal

        from inventory.domain.models import ArbitrationLine

        _service, campaign, zone, *_ = bench
        return ArbitrationLine(
            id=new_id(), campaign_id=campaign.id, zone_id=zone.id,
            item_number="P-1", section=CountSection.LINE_SIDE,
            qty_pass_1=Decimal(p1), qty_pass_2=Decimal(p2),
            qty_arbitrated=Decimal(retained), decided_by="chef",
            decided_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            comment="Le n°1 était mal éclairé.",
        )

    def test_une_decision_tient_tant_que_les_chiffres_ne_bougent_pas(self, bench):
        prior = self.decided(bench, p1=12, p2=15, retained=12)
        [line] = self.build(bench, p1=12, p2=15, prior=prior)
        assert line.is_resolved and line.qty_arbitrated == 12

    def test_elle_tombe_dès_qu_un_comptage_change(self, bench):
        prior = self.decided(bench, p1=12, p2=15, retained=12)
        [line] = self.build(bench, p1=12, p2=40, prior=prior)
        assert not line.is_resolved

    def test_la_quantite_reste_proposee_pour_ne_pas_la_faire_retaper(self, bench):
        prior = self.decided(bench, p1=12, p2=15, retained=12)
        [line] = self.build(bench, p1=12, p2=40, prior=prior)
        assert line.qty_arbitrated == 12

    def test_et_la_ligne_dit_pourquoi_elle_est_revenue(self, bench):
        prior = self.decided(bench, p1=12, p2=15, retained=12)
        [line] = self.build(bench, p1=12, p2=40, prior=prior)
        assert "changé" in line.comment


class TestArbitrerEnLot:
    """Quarante écarts, une règle, un geste.

    Ligne par ligne était la seule voie. Sur une zone dont on sait déjà lequel
    des deux comptages fait foi — la première équipe comptait sous la pluie —
    c'est quarante gestes qui n'ajoutent aucun jugement : donc quarante
    occasions de se tromper de champ, et une raison de ne pas arbitrer du tout.
    """

    @pytest.fixture
    def zone_arbitree(self, bench, db):
        from decimal import Decimal

        from inventory.domain.models import ArbitrationLine

        service, campaign, zone, sheets, *_ = bench
        start_counting(db, campaign.id)
        campaign = service.ctx.campaigns.get(campaign.id)
        sheets.upsert_arbitrations([
            ArbitrationLine(
                id=new_id(), campaign_id=campaign.id, zone_id=zone.id,
                item_number=number, section=CountSection.LINE_SIDE,
                qty_pass_1=Decimal(p1), qty_pass_2=Decimal(p2),
            )
            for number, p1, p2 in (("P-1", 12, 15), ("P-2", 30, 28))
        ])
        return service, campaign, zone, sheets

    def retained(self, sheets, campaign, zone):
        return {
            a.item_number: (a.qty_arbitrated, a.is_resolved)
            for a in sheets.list_arbitrations(campaign.id, zone_id=zone.id)
        }

    def test_tout_le_comptage_1(self, zone_arbitree):
        service, campaign, zone, sheets = zone_arbitree
        assert service.decide_arbitrations(
            campaign, zone.id, choice="PASS_1"
        )["decided"] == 2
        assert self.retained(sheets, campaign, zone) == {
            "P-1": (12, True), "P-2": (30, True),
        }

    def test_tout_le_comptage_2(self, zone_arbitree):
        service, campaign, zone, sheets = zone_arbitree
        service.decide_arbitrations(campaign, zone.id, choice="PASS_2")
        assert self.retained(sheets, campaign, zone) == {
            "P-1": (15, True), "P-2": (28, True),
        }

    def test_une_ligne_deja_tranchee_n_est_pas_retouchee(self, zone_arbitree):
        """Un lot ne défait pas un jugement pris une par une."""
        service, campaign, zone, sheets = zone_arbitree
        one = next(
            a for a in sheets.list_arbitrations(campaign.id, zone_id=zone.id)
            if a.item_number == "P-1"
        )
        service.decide_arbitration(campaign, one.id, __import__("decimal").Decimal(13))

        service.decide_arbitrations(campaign, zone.id, choice="PASS_2")
        assert self.retained(sheets, campaign, zone)["P-1"] == (13, True)

    def test_valider_tout_enterine_les_propositions(self, zone_arbitree):
        service, campaign, zone, sheets = zone_arbitree
        service.prefill_with_pass_2(campaign, zone.id)

        assert service.decide_arbitrations(
            campaign, zone.id, choice="PROPOSED"
        )["decided"] == 2
        assert all(
            resolved for _q, resolved in self.retained(sheets, campaign, zone).values()
        )

    def test_valider_tout_sans_proposition_ne_tranche_rien(self, zone_arbitree):
        """Et le dit : une zone laissée ouverte ne doit pas se lire comme finie."""
        service, campaign, zone, sheets = zone_arbitree
        result = service.decide_arbitrations(campaign, zone.id, choice="PROPOSED")
        assert result == {"decided": 0, "skipped": 2}
        assert not any(
            resolved for _q, resolved in self.retained(sheets, campaign, zone).values()
        )

    def test_un_choix_inconnu_est_refuse(self, zone_arbitree):
        from inventory.errors import ValidationError

        service, campaign, zone, _sheets = zone_arbitree
        with pytest.raises(ValidationError):
            service.decide_arbitrations(campaign, zone.id, choice="LE_PLUS_GRAND")
