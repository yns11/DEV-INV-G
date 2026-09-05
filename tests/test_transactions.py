"""Une commande métier écrit tout, ou rien.

Plusieurs commandes touchaient deux ou trois tables à la suite, sans transaction
autour. Ce n'est pas une faute visible tant que rien ne casse : le geste réussit,
les écritures s'enchaînent, et l'écran affiche ce qu'il faut. Elle se voit le
jour où la connexion tombe entre la deuxième et la troisième — et ce jour-là,
elle laisse derrière elle un état que rien dans l'application ne sait décrire :

- une zone créée dont les feuilles de comptage n'existent pas, que l'écran
  présente comme prête à compter alors qu'il n'y a rien à ouvrir ;
- une quantité saisie sans la trace d'audit qui dit qui l'a saisie, dans une
  application dont c'est précisément la raison d'être ;
- une trace annonçant « suppression de 40 lignes » quand 12 seulement sont
  parties ;
- une feuille dont les lignes lues par le modèle sont écrites mais dont le
  chemin de la pièce justificative manque : des chiffres que plus rien ne
  rattache au papier ;
- une consolidation « courante » dont le journal GENERIQUE est resté vide.

Ces contrôles n'ouvrent aucune base. Ils observent la seule chose qui compte
ici : **au moment de chaque écriture, une transaction était-elle ouverte ?**
La doublure :class:`conftest.FakeTransactions` porte cette profondeur, les
dépôts factices la relèvent à l'appel, et le test la relit après coup.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.domain.enums import (
    CampaignStatus,
    DataSource,
    JournalStatus,
    SheetPass,
)
from inventory.domain.models import CountSheetLine, Zone
from inventory.services.consolidation_service import ConsolidationService
from inventory.services.counting_service import CountingService
from inventory.services.generic_service import GenericService

CAMPAIGN_ID = "camp-1"


def campaign(status: CampaignStatus = CampaignStatus.COUNTING) -> Any:
    """Une campagne minimale, en phase Comptage sauf mention contraire."""
    return cast(
        Any,
        SimpleNamespace(
            id=CAMPAIGN_ID,
            code="INV-2026",
            status=status,
            created_by="chef@usine",
            config=SimpleNamespace(generic_passes=2),
        ),
    )


# --------------------------------------------------------------------------- #
# Saisie d'une quantité : la ligne et sa trace
# --------------------------------------------------------------------------- #

def counting_service() -> tuple[CountingService, Any, Any]:
    ctx = cast(Any, SimpleNamespace(actor="chef@usine"))
    ledger = with_transactions(ctx)

    journal = SimpleNamespace(
        id="j-1", campaign_id=CAMPAIGN_ID, status=JournalStatus.IN_PROGRESS,
        key=SimpleNamespace(warehouse_id="B06", location_id="VRAC"),
    )

    def upsert_line(line, *, actor, expected_version=None, conn=None):
        ledger.note("ligne")
        return line

    def delete_line(campaign_id, line_id, *, actor, conn=None):
        ledger.note("ligne")

    def set_status(campaign_id, journal_ids, status, *, actor, posted_at=None, conn=None):
        ledger.note("statut")
        return len(journal_ids)

    ctx.journals = SimpleNamespace(
        get=lambda jid: journal,
        list_lines=lambda jid: [],
        upsert_line=upsert_line,
        delete_line=delete_line,
        set_status=set_status,
    )
    ctx.record = lambda **kw: ledger.note("audit") or "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
    )
    with_access(ctx)
    return CountingService(ctx), ledger, ctx


class TestUneSaisieEtSaTrace:
    """Une quantité saisie sans qui l'a saisie n'est pas une quantité auditable."""

    def test_la_ligne_et_son_audit_partagent_la_transaction(self):
        service, ledger, _ = counting_service()
        service.upsert_line(
            campaign(), "j-1", line_id=None, item_number="P-1", qty=Decimal("12")
        )
        assert list(ledger.writes) == ["ligne", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes

    def test_la_suppression_aussi(self):
        service, ledger, _ = counting_service()
        service.delete_line(campaign(), "l-1")
        assert list(ledger.writes) == ["ligne", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes

    def test_un_changement_de_statut_aussi(self):
        service, ledger, _ = counting_service()
        service.set_status(campaign(), ["j-1"], JournalStatus.IN_PROGRESS)
        assert list(ledger.writes) == ["statut", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes


# --------------------------------------------------------------------------- #
# Zones et feuilles
# --------------------------------------------------------------------------- #

def generic_service() -> tuple[GenericService, Any, Any]:
    ctx = cast(Any, SimpleNamespace(actor="chef@usine"))
    ledger = with_transactions(ctx)

    zone = Zone(
        id="z-1", campaign_id=CAMPAIGN_ID, code="Z1", passes=2, free_entry=True,
    )
    sheet = SimpleNamespace(
        id="s-1", campaign_id=CAMPAIGN_ID, zone_id="z-1", pass_no=SheetPass.PASS_1,
    )

    def create_zone(z, *, actor, conn=None):
        ledger.note("zone")
        return z

    def ensure_sheets(cid, zid, passes, *, actor, conn=None):
        ledger.note("feuilles")
        return len(list(passes))

    def set_zone_closed(cid, zid, *, closed, actor, conn=None):
        ledger.note("clôture")
        return 1

    def delete_sheet_line(cid, line_id, *, actor, conn=None):
        ledger.note(f"ligne:{line_id}")

    def replace_sheet_lines(sid, lines, *, actor, conn=None):
        ledger.note("lignes")
        return len(lines)

    def upsert_sheet_lines(lines, *, actor, conn=None):
        ledger.note("lignes")
        return len(lines)

    existing = CountSheetLine(
        id="l-1", sheet_id="s-1", campaign_id=CAMPAIGN_ID, item_number="P-1",
        source=DataSource.MANUAL,
    )
    ctx.sheets = SimpleNamespace(
        list_zones=lambda cid: [zone],
        create_zone=create_zone,
        ensure_sheets=ensure_sheets,
        set_zone_closed=set_zone_closed,
        list_arbitrations=lambda cid: [],
        get_sheet=lambda sid: sheet,
        # Le service relit les feuilles pour savoir de quelles zones les lignes
        # supprimées viennent : le document se décide sur le passage 1 et vaut
        # pour les deux.
        list_sheets=lambda cid, **kw: [sheet],
        list_sheet_lines=lambda sid, **kw: [],
        lines_by_sheet=lambda cid: {"s-1": [existing]},
        delete_sheet_line=delete_sheet_line,
        replace_sheet_lines=replace_sheet_lines,
        upsert_sheet_lines=upsert_sheet_lines,
    )
    ctx.record = lambda **kw: ledger.note("audit") or "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
    )
    with_access(ctx)
    service = GenericService(ctx)
    service.refresh_arbitrations = lambda campaign, zone_id=None: 0  # type: ignore[method-assign]
    return service, ledger, ctx


class TestUneZoneEtSesFeuilles:
    """Une zone sans feuilles est une zone que rien ne permet de compter."""

    def test_zone_feuilles_et_audit_partagent_la_transaction(self):
        service, ledger, _ = generic_service()
        service.create_zone(campaign(CampaignStatus.PREPARATION), code="Z9")
        assert list(ledger.writes) == ["zone", "feuilles", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes

    def test_la_cloture_et_sa_trace_aussi(self):
        service, ledger, _ = generic_service()
        service.set_zone_closed(campaign(), "z-1", closed=True)
        assert list(ledger.writes) == ["clôture", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes


class TestUnLotDeSuppressions:
    """La trace annonce un nombre : elle ne doit pas survivre à un lot coupé."""

    def test_toutes_les_lignes_et_la_trace_partagent_la_transaction(self):
        service, ledger, _ = generic_service()
        service.delete_sheet_lines(campaign(CampaignStatus.PREPARATION), ["l-1"])
        assert list(ledger.writes) == ["ligne:l-1", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes


class TestLEnregistrementDUneFeuille:
    def test_les_lignes_et_la_trace_partagent_la_transaction(self):
        service, ledger, _ = generic_service()
        service.upsert_sheet_lines(
            campaign(CampaignStatus.PREPARATION),
            "s-1",
            [{"item_number": "P-1", "section": "LINE_SIDE"}],
        )
        assert list(ledger.writes) == ["lignes", "audit"]
        assert ledger.all_writes_inside_one_transaction(), ledger.writes


# --------------------------------------------------------------------------- #
# Consolidation
# --------------------------------------------------------------------------- #

class TestLaConsolidationEtSonJournal:
    """Un calcul « courant » dont le journal est vide ferait passer pour
    consolidée une campagne qui ne l'est pas.

    Les deux écritures étaient séparées, et pire : le refus « aucun journal
    GENERIQUE n'existe » se déclenchait *après* l'enregistrement du calcul. Une
    campagne mal configurée repartait donc avec une consolidation courante et
    rien pour la porter.
    """

    def build(self):
        from inventory.domain.consolidation import ConsolidationResult
        from inventory.domain.models import ConsolidatedLine

        ctx = cast(Any, SimpleNamespace(actor="chef@usine"))
        db = with_transactions(ctx)

        key = SimpleNamespace(warehouse_id="B06", location_id="GENERIQUE")
        journal = SimpleNamespace(
            id="j-gen", campaign_id=CAMPAIGN_ID, key=key,
            status=JournalStatus.PENDING,
        )

        def save_run(**kw):
            db.note("calcul")
            return "run-1"

        def replace_lines_for_journal(jid, cid, lines, *, actor, conn=None):
            db.note("journal")
            return len(lines)

        def set_status(cid, jids, status, *, actor, posted_at=None, conn=None):
            db.note("statut")
            return len(jids)

        ctx.consolidation = SimpleNamespace(save_run=save_run)
        ctx.journals = SimpleNamespace(
            list=lambda cid: [journal],
            replace_lines_for_journal=replace_lines_for_journal,
            set_status=set_status,
        )
        ctx.record = lambda **kw: db.note("audit") or "evt"
        ctx.forget_progress = lambda cid=None: None
        ctx.progress = lambda c: SimpleNamespace(
            items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
        )
        with_access(ctx)

        camp = campaign()
        camp.config = SimpleNamespace(generic_passes=2, generic_key=key)

        service = ConsolidationService(ctx)
        service.consolidate = lambda campaign, preview=False: ConsolidationResult(  # type: ignore[method-assign]
            campaign_id=CAMPAIGN_ID,
            lines=[
                ConsolidatedLine(
                    campaign_id=CAMPAIGN_ID, item_number="P-1", qty=Decimal("7")
                )
            ],
            zones_included=["Z1"],
        )
        return service, db, camp

    def test_le_calcul_le_journal_et_la_trace_partagent_la_transaction(self):
        service, db, camp = self.build()
        service.consolidate_and_save(camp)
        assert list(db.writes) == ["calcul", "journal", "statut", "audit"]
        assert db.all_writes_inside_one_transaction(), db.writes
        assert db.opened == 1

    def test_un_journal_manquant_nenregistre_aucun_calcul(self):
        """Le refus doit tomber avant la première écriture, pas après."""
        from inventory.errors import ValidationError

        service, db, camp = self.build()
        service.ctx.journals.list = lambda cid: []
        with pytest.raises(ValidationError):
            service.consolidate_and_save(camp)
        assert db.writes == {}, "un calcul a été enregistré avant le refus"


# --------------------------------------------------------------------------- #
# Le garde-fou de la doublure elle-même
# --------------------------------------------------------------------------- #

class TestLaDoublureNeMentPas:
    """Sans cela, tous les contrôles ci-dessus passeraient sur du vide.

    ``all_inside_one_transaction`` sur un registre vide doit être faux, sinon
    une commande qui n'écrirait plus rien passerait pour irréprochable.
    """

    def test_hors_transaction_la_profondeur_est_nulle(self):
        db = with_transactions(cast(Any, SimpleNamespace()))
        db.note("écriture nue")
        assert db.writes == {"écriture nue": 0}
        assert not db.all_writes_inside_one_transaction()

    def test_un_registre_vide_ne_prouve_rien(self):
        db = with_transactions(cast(Any, SimpleNamespace()))
        assert not db.all_writes_inside_one_transaction()

    def test_la_transaction_se_referme(self):
        db = with_transactions(cast(Any, SimpleNamespace()))
        with db.transaction() as conn:
            assert db.depth == 1
            assert conn == db.connection
        assert db.depth == 0
        assert db.opened == 1

    def test_elle_se_referme_meme_sur_incident(self):
        """C'est tout l'intérêt : l'échec au milieu doit défaire, pas figer."""
        db = with_transactions(cast(Any, SimpleNamespace()))
        with pytest.raises(RuntimeError), db.transaction():
            raise RuntimeError("la connexion tombe")
        assert db.depth == 0
