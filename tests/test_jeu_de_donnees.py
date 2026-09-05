"""Le jeu de données de contrôle, chargé dans l'application et confronté.

`fixtures/jeu-de-donnees/` porte une campagne complète en CSV et, à côté,
`oracle.py` : un calcul du résultat attendu **écrit sans l'application**, à
partir des règles telles que la documentation les énonce.

Ce fichier fait la troisième chose, celle qui donne son sens aux deux autres :
il charge les CSV par les vrais importeurs, déroule le vrai processus — déclarer
et sceller les précomptages, charger le stock ERP, importer les journaux du jour
J, consolider GENERIQUE — puis compare les chiffres de l'application à
`attendu.json`, ligne à ligne.

Deux implémentations indépendantes qui tombent sur les mêmes chiffres se
confirment l'une l'autre. Une seule ne confirme rien : c'est la différence entre
« le calcul est reproductible » et « le calcul est celui qu'on a écrit ».

Quand ce contrôle échoue, l'une des deux a tort — et le message dit laquelle des
grandeurs diverge, ce qui suffit presque toujours à savoir laquelle.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus, JournalStatus, SheetPass
from inventory.domain.models import Campaign, LocationKey, Thresholds

pytestmark = pytest.mark.postgres

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "jeu-de-donnees"


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def _text(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope="module")
def attendu() -> dict:
    """Le résultat théorique, relu du fichier que l'oracle a écrit.

    Il est régénéré ici plutôt que lu tel quel : un `attendu.json` oublié
    derrière une modification du jeu de données ferait passer ce contrôle sur
    des chiffres qui ne décrivent plus rien.
    """
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(FIXTURES / "oracle.py")], check=True, capture_output=True
    )
    return json.loads((FIXTURES / "attendu.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_jeu_donnees") as database:
        yield database


@pytest.fixture(scope="module")
def charge(db):
    """Dérouler le processus complet, dans l'ordre où il a lieu."""
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext
    from inventory.services.early_count_service import EarlyCountService
    from inventory.services.import_service import ImportService

    campaign_id = make_campaign(db, f"INV-TEST-{uuid.uuid4().hex[:6]}")
    campaign = Campaign(
        id=campaign_id,
        code="INV-TEST-01",
        label="Jeu de données de contrôle",
        count_date=dt.date(2026, 6, 13),
        status=CampaignStatus.PREPARATION,
        # `make_campaign` crée la campagne au nom de « test » : l'acteur doit
        # être le même, sinon la barrière d'identité refuse toute écriture —
        # et c'est très bien qu'elle le fasse.
        created_by="test",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        # Les seuils que l'oracle applique, écrits ici pour que les deux
        # comptent les mêmes lignes matérielles.
        thresholds=[
            Thresholds(item_type=t, value_abs_eur="100", qty_relative="0.02")
            for t in ("COMPONENT", "SEMI_FINISHED", "FINISHED", "PACKAGING", "UNKNOWN")
        ],
    )
    ctx = ServiceContext(actor="test", db=db, settings=get_settings())
    imports = ImportService(ctx)
    imports.batches.archive = lambda *a, **k: None

    def charger(importeur, fichier: str) -> None:
        outcome = importeur(campaign, mode="file",
                            payload=_text(fichier), filename=fichier)
        assert not outcome.errors, f"{fichier} : {outcome.errors[:3]}"

    # --- Préparation : référentiels, zones, feuilles -----------------------
    # Les référentiels gèlent à l'entrée en comptage : ils se chargent avant,
    # comme dans le processus réel.
    charger(imports.import_items, "01-articles.csv")
    charger(imports.import_boms, "02-nomenclatures.csv")
    charger(imports.import_locations, "03-emplacements.csv")
    ctx.forget_progress(campaign_id)

    from inventory.services.generic_service import GenericService

    generic = GenericService(ctx)
    for r in _rows("07-zones-generique.csv"):
        generic.create_zone(
            campaign, code=r["Code zone"], label=r["Libellé"],
            sector=r["Secteur"], display_order=int(r["Ordre"]),
        )
    charger(imports.import_count_sheets, "08-feuilles-generique.csv")

    # --- Passage en comptage ----------------------------------------------
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'COUNTING' WHERE id = %s", (campaign_id,)
        )
    campaign = campaign.model_copy(update={"status": CampaignStatus.COUNTING})
    ctx.forget_progress(campaign_id)

    # --- Précomptages : importer, puis déclarer (ce qui scelle) -----------
    charger(imports.import_journal_lines, "04-journaux-precomptage.csv")
    early = EarlyCountService(ctx)
    perimetres = {
        "NPEM-A": [LocationKey(warehouse_id="ATP", location_id="SOL"),
                   LocationKey(warehouse_id="ATP", location_id="SE2")],
        "NPEM-B": [LocationKey(warehouse_id="B06", location_id="PAL01")],
    }
    for numero, keys in perimetres.items():
        journal = ctx.erp_journals.get_by_number(campaign_id, numero)
        early.declare_scope(campaign, journal.id, keys)

    # --- Jour J : stock ERP général, puis les journaux du jour ------------
    charger(imports.import_book_stock, "05-stock-erp-jour-j.csv")
    charger(imports.import_journal_lines, "06-journaux-jour-j.csv")

    # --- L'emplacement inventorié ailleurs --------------------------------
    forced = next(
        j for j in ctx.journals.list(campaign_id)
        if j.key == LocationKey(warehouse_id="B06", location_id="FORCE")
    )
    ctx.journals.set_status(
        campaign_id, [forced.id], JournalStatus.BOOK_ENFORCED, actor=ctx.actor
    )

    # --- GENERIQUE : les deux passages, puis l'arbitrage ------------------
    _saisir_generique(ctx, campaign, generic)

    # --- Analyse : les mouvements postés après le comptage ------------------
    # Un ajustement est un mouvement réel enregistré pendant l'analyse ; il
    # s'ajoute au comptage pour donner le stock physique.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status = 'ANALYSIS' WHERE id = %s", (campaign_id,)
        )
    campaign = campaign.model_copy(update={"status": CampaignStatus.ANALYSIS})
    charger(imports.import_adjustments, "10-ajustements.csv")
    # L'écart backflush se lit sur une période — des lundis ISO, fin exclue.
    outcome = imports.import_backflush(
        campaign, mode="file", payload=_text("11-backflush.csv"),
        filename="11-backflush.csv",
        period_start=dt.date(2026, 5, 18), period_end=dt.date(2026, 6, 15),
    )
    assert not outcome.errors, outcome.errors[:3]

    ctx.forget_progress(campaign_id)
    return ctx, campaign


def _saisir_generique(ctx, campaign, generic) -> None:
    """Reporter les quantités des deux passages, puis l'arbitrage décidé."""
    zones = {z.code: z for z in ctx.sheets.list_zones(campaign.id)}
    sheets = ctx.sheets.list_sheets(campaign.id)
    lines = ctx.sheets.lines_by_sheet(campaign.id)

    # Le CSV porte les libellés que l'écran affiche ; le modèle porte les codes.
    sections = {
        "Bord de ligne": "LINE_SIDE",
        "WIP (à éclater)": "WIP",
        "WIP assemblé": "WIP_OK",
    }
    par_passage = {
        (r["Feuille"], int(r["Passage"]), r["Numéro d'article"],
         sections[r["Section"]]): Decimal(r["Quantité comptée"].replace(",", "."))
        for r in _rows("09-comptages-generique.csv")
    }
    for sheet in sheets:
        zone = next(z for z in zones.values() if z.id == sheet.zone_id)
        passage = 1 if sheet.pass_no is SheetPass.PASS_1 else 2
        rows = []
        for line in lines.get(sheet.id, []):
            qty = par_passage.get(
                (zone.code, passage, line.item_number, str(line.section))
            )
            if qty is not None:
                rows.append({
                    "id": line.id,
                    "item_number": line.item_number,
                    "section": str(line.section),
                    "unit": line.unit,
                    "qty": str(qty),
                })
        if rows:
            generic.upsert_sheet_lines(campaign, sheet.id, rows)
    for zone in zones.values():
        generic.refresh_arbitrations(campaign, zone.id)

    for r in _rows("09b-arbitrages-generique.csv"):
        if not r["Décidée"].strip().lower().startswith("o"):
            continue
        article = r["Numéro d'article"]
        zone = zones[r["Feuille"]]
        pending = [
            a for a in ctx.sheets.list_arbitrations(campaign.id)
            if a.zone_id == zone.id and a.item_number == article
        ]
        assert pending, f"aucun arbitrage ouvert pour {article}"
        generic.decide_arbitration(
            campaign, pending[0].id,
            Decimal(r["Quantité arbitrée"].replace(",", ".")),
            comment="jeu de données de contrôle",
        )


def _kpis(charge) -> dict:
    from inventory.services.analysis_service import AnalysisService

    ctx, campaign = charge
    return AnalysisService(ctx).kpis(campaign).as_dict()


class TestLeProcessusSeDeroule:
    """Ce qui doit être vrai avant même de comparer un chiffre."""

    def test_les_precomptages_sont_scelles(self, charge, attendu):
        ctx, campaign = charge
        scelles = {f"{w} / {l}" for w, l in ctx.journals.sealed_keys(campaign.id)}
        assert scelles == set(attendu["emplacementsScelles"])

    def test_le_chargement_general_preserve_leur_reference(self, charge, attendu):
        """Le snapshot du jour J vise ces emplacements ; il ne doit pas gagner."""
        ctx, campaign = charge
        reference = {
            f"{b.item_number} @ {b.warehouse_id} / {b.location_id}": b.qty
            for b in ctx.book_stock.list(campaign.id)
        }
        for cle, attendue in attendu["referenceScellee"].items():
            assert reference[cle] == Decimal(attendue), cle

    def test_la_consolidation_donne_les_quantites_attendues(self, charge, attendu):
        from inventory.services.consolidation_service import ConsolidationService

        ctx, campaign = charge
        result = ConsolidationService(ctx).consolidate(
            campaign, preview=True, provisional=True
        )
        obtenu = {l.item_number: l.qty for l in result.lines}
        assert obtenu == {
            ref: Decimal(qte)
            for ref, qte in attendu["consolidationGenerique"].items()
        }


class TestLesKpisTombentSurLeCalculTheorique:
    """Le carrousel, grandeur par grandeur."""

    @pytest.mark.parametrize("app_key,oracle_key", [
        ("bookQty", "stockErpQte"),
        ("bookValue", "stockErpValeur"),
        ("countedQty", "compteQte"),
        ("physicalQty", "physiqueQte"),
        ("physicalValue", "physiqueValeur"),
        ("netVarianceQty", "ecartNetQte"),
        ("netVarianceValue", "ecartNetValeur"),
        ("grossVarianceQty", "ecartBrutQte"),
        ("grossVarianceValue", "ecartBrutValeur"),
    ])
    def test_cette_grandeur(self, charge, attendu, app_key, oracle_key):
        obtenu = Decimal(str(_kpis(charge)[app_key]))
        theorique = Decimal(attendu["kpi"][oracle_key])
        assert obtenu == theorique, (
            f"{app_key} : l'application dit {obtenu}, le calcul théorique "
            f"{theorique}"
        )

    @pytest.mark.parametrize("app_key,oracle_key", [
        ("lineCount", "nbLignes"),
        ("accurateLineCount", "nbLignesExactes"),
        ("materialLineCount", "nbLignesMaterielles"),
        ("countedOnlyCount", "nbCompteSeul"),
        ("bookOnlyCount", "nbErpSeul"),
    ])
    def test_ce_compte(self, charge, attendu, app_key, oracle_key):
        assert _kpis(charge)[app_key] == attendu["kpi"][oracle_key], app_key

    def test_l_ira(self, charge, attendu):
        obtenu = Decimal(str(_kpis(charge)["ira"]))
        theorique = Decimal(attendu["kpi"]["ira"])
        assert abs(obtenu - theorique) < Decimal("0.000001")

    @pytest.mark.parametrize("app_key,oracle_key", [
        ("netReliabilityValue", "fiabiliteNetteValeur"),
        ("grossReliabilityValue", "fiabiliteBruteValeur"),
        ("grossReliabilityQty", "fiabiliteBruteQte"),
    ])
    def test_cette_fiabilite(self, charge, attendu, app_key, oracle_key):
        obtenu = Decimal(str(_kpis(charge)[app_key]))
        theorique = Decimal(attendu["kpi"][oracle_key])
        assert abs(obtenu - theorique) < Decimal("0.000001"), app_key


class TestChaqueArticleTombeJuste:
    """Le total peut être juste par compensation : les lignes, non."""

    def _par_article(self, charge) -> dict[str, dict]:
        from inventory.services.analysis_service import AnalysisService

        ctx, campaign = charge
        return {
            l.item_number: l
            for l in AnalysisService(ctx).variances(campaign, granularity="item")
        }

    def test_les_memes_articles_produisent_une_ligne(self, charge, attendu):
        obtenu = set(self._par_article(charge))
        theorique = {l["article"] for l in attendu["ecartsParArticle"]}
        assert obtenu == theorique

    def test_les_quantites_et_les_valeurs(self, charge, attendu):
        lignes = self._par_article(charge)
        for theorique in attendu["ecartsParArticle"]:
            ligne = lignes[theorique["article"]]
            assert ligne.book_qty == Decimal(theorique["stockErpQte"]), (
                f"{theorique['article']} : stock ERP"
            )
            assert ligne.counted_qty == Decimal(theorique["compteQte"]), (
                f"{theorique['article']} : compté"
            )
            assert ligne.physical_qty == Decimal(theorique["physiqueQte"]), (
                f"{theorique['article']} : physique"
            )
            assert ligne.variance_qty == Decimal(theorique["ecartQte"]), (
                f"{theorique['article']} : écart en quantité"
            )
            assert ligne.variance_value == Decimal(theorique["ecartValeur"]), (
                f"{theorique['article']} : écart en valeur"
            )
            assert ligne.book_value == Decimal(theorique["stockErpValeur"]), (
                f"{theorique['article']} : valeur du stock ERP"
            )
