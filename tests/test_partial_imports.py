"""Un chargement qui remplace n'écrit pas un ensemble amputé.

Un import produit des lignes acceptées et des lignes rejetées. Écrire les
premières malgré les secondes est anodin pour un chargement qui **complète** :
trois lignes refusées sur quatre mille sont trois lignes manquantes, visibles
dans le rapport, que la prochaine version du fichier apportera.

C'est tout autre chose pour un chargement qui **remplace**. Le snapshot de stock
ERP, l'écart backflush et une nomenclature chargée en mode remplacement effacent
l'ensemble précédent avant d'écrire le nouveau. Les trois lignes refusées
deviennent alors trois lignes *supprimées* : la nomenclature passe de 4 000
liens à 3 997, plus rien ne dit lesquels ont disparu, l'éclatement du WIP se
fait contre une nomenclature incomplète, et l'écart d'inventaire qui en sort
porte sur des articles que personne ne reliera au fichier mal formé du matin.

C'est la faute de la troncature silencieuse, sous un autre nom : un ensemble
amputé qui se présente comme complet.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.domain.enums import CampaignStatus
from inventory.domain.imports import refuse_partial_write
from inventory.domain.models import BackflushLine, BomLink, BookStockLine, Campaign
from inventory.errors import ValidationError
from inventory.ingest.parser import ParseResult, RowError

MONDAY, NEXT_MONDAY = dt.date(2026, 8, 3), dt.date(2026, 8, 31)


def campaign(status: CampaignStatus = CampaignStatus.COUNTING) -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date="2026-09-01", status=status,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


# --------------------------------------------------------------------------- #
# La règle, seule
# --------------------------------------------------------------------------- #

class TestTheRuleItself:
    def test_a_completing_import_is_never_refused(self):
        """Il n'efface rien : les lignes refusées manquent, elles ne partent pas."""
        assert refuse_partial_write(
            wholesale=False, rejected=3, accepted=3997
        ) is None

    def test_a_wholesale_import_with_a_rejection_is_refused(self):
        assert refuse_partial_write(
            wholesale=True, rejected=3, accepted=3997
        ) is not None

    def test_a_clean_wholesale_import_goes_through(self):
        assert refuse_partial_write(
            wholesale=True, rejected=0, accepted=4000
        ) is None

    def test_the_derogation_lifts_the_refusal(self):
        assert refuse_partial_write(
            wholesale=True, rejected=3, accepted=3997, allow_partial=True
        ) is None

    def test_the_refusal_counts_both_sides(self):
        refusal = refuse_partial_write(wholesale=True, rejected=3, accepted=3997)
        assert refusal is not None
        assert refusal.rejected == 3
        assert refusal.accepted == 3997
        assert "3 ligne(s) sur 4000" in refusal.message

    def test_the_refusal_names_what_is_being_replaced(self):
        refusal = refuse_partial_write(
            wholesale=True, rejected=1, accepted=9, what="Le stock ERP"
        )
        assert refusal is not None
        assert refusal.message.startswith("Le stock ERP")

    def test_the_refusal_carries_the_first_reasons(self):
        """Pour que le fichier soit corrigeable sans ouvrir le rapport complet."""
        refusal = refuse_partial_write(
            wholesale=True, rejected=2, accepted=8,
            reasons=("quantité illisible", "article inconnu"),
        )
        assert refusal is not None
        assert "quantité illisible" in refusal.message
        assert "article inconnu" in refusal.message

    def test_it_stops_at_three_reasons_and_says_how_many_remain(self):
        """Cinquante motifs dans une phrase, personne ne les lit."""
        refusal = refuse_partial_write(
            wholesale=True, rejected=5, accepted=5,
            reasons=tuple(f"motif {n}" for n in range(5)),
        )
        assert refusal is not None
        assert "motif 3" not in refusal.message
        assert "et 2 autre(s)" in refusal.message

    def test_it_says_what_to_do_next(self):
        refusal = refuse_partial_write(wholesale=True, rejected=1, accepted=1)
        assert refusal is not None
        assert "Corrigez le fichier" in refusal.message


# --------------------------------------------------------------------------- #
# Les trois chargements qui remplacent
# --------------------------------------------------------------------------- #

def import_service(monkeypatch, *, rows, mapped, errors, target):
    """Un service dont la lecture et le mappage sont dictés par le test."""
    from inventory.services import import_service as module

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    written: list[str] = []

    def note(name):
        def _(*a, **k):
            written.append(name)
            return len(mapped)
        return _

    ctx.book_stock = SimpleNamespace(replace=note("stock"))
    ctx.backflush = SimpleNamespace(replace=note("backflush"))
    ctx.referentials = SimpleNamespace(
        items_by_number=lambda cid: {},
        items_in_scope=lambda cid: {},
        locations_by_key=lambda cid: {},
        upsert_warehouses=lambda w, *, actor, conn=None: len(list(w)),
        upsert_locations=lambda l, *, actor, conn=None: len(list(l)),
        upsert_bom_links=note("bom"),
        clear_bom=lambda cid, *, actor, conn=None: written.append("clear") or 0,
        list_bom_links=lambda cid: [],
    )
    ctx.journals = SimpleNamespace(
        ensure_journals=lambda cid, keys, *, kinds, actor, conn=None: len(keys),
        # Le chargement général confronte les emplacements scellés à ce que
        # l'ERP en dit le jour J ; ce contrôle-ci n'en a aucun.
        sealed_keys=lambda cid: set(),
    )
    ctx.imports = SimpleNamespace(create=lambda **k: k.get("batch_id") or "lot")
    ctx.record = lambda **kw: "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=0, book_stock_frozen=False
    )
    with_access(ctx)

    service = module.ImportService(ctx)
    service.batches.archive = lambda *a, **k: None  # type: ignore[method-assign]
    # La lecture est doublée là où elle se fait désormais : sur le lecteur que
    # le service compose, pas sur la façade d'une ligne qu'il expose.
    monkeypatch.setattr(
        service.parser, "parse",
        lambda t, **kw: (
            None,
            ParseResult(contract_key=t, rows=rows, rows_received=len(rows),
                        errors=list(errors)),
        ),
    )
    # `map_book_stock` rend quatre listes : les lignes retenues, les refus, et
    # deux familles d'écartées — hors périmètre, et référence inconnue. Ces
    # deux-là ne sont pas des refus, voir le mappeur ; la doublure doit rendre
    # la même forme, sans quoi le test dirait le contraire du code qu'il couvre.
    #
    # Ce qui est refusé ici vient donc du **fichier** (`ParseResult.errors`), et
    # c'est bien ce que ces contrôles visent : le remplacement amputé.
    monkeypatch.setattr(
        module, "map_book_stock", lambda *a, **k: (mapped, [], [], []),
        raising=False,
    )
    for name in ("map_bom_links", "map_backflush"):
        monkeypatch.setattr(
            module, name,
            (lambda *a, **k: (mapped, [])) if hasattr(module, name) else None,
            raising=False,
        )
    return service, written


ROWS = [{"item_number": f"P-{n}"} for n in range(4)]
REJECTED = [RowError(7, "qty", "abc", "quantité illisible")]


def stock_lines():
    return [
        BookStockLine(campaign_id="camp-1", item_number=f"P-{n}",
                      warehouse_id="B06", location_id="VRAC", qty=Decimal("10"))
        for n in range(3)
    ]


def bom_links():
    return [
        BomLink(campaign_id="camp-1", parent_item="MASS-1",
                child_item=f"P-{n}", qty_per=Decimal("1"))
        for n in range(3)
    ]


def backflush_lines():
    return [
        BackflushLine(campaign_id="camp-1", item_number=f"P-{n}",
                      period_start=MONDAY, period_end=NEXT_MONDAY)
        for n in range(3)
    ]


class TestTheErpStockSnapshot:
    """Le pire cas : chaque article manquant produit un écart de 100 %."""

    def run(self, monkeypatch, *, errors, **extra):
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=stock_lines(),
            errors=errors, target="book_stock",
        )
        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="stock.csv", **extra
        )
        return outcome, written

    def test_a_rejected_row_stops_the_snapshot(self, monkeypatch):
        with pytest.raises(ValidationError):
            self.run(monkeypatch, errors=REJECTED)

    def test_nothing_is_written_when_it_is_refused(self, monkeypatch):
        """Un refus qui écrirait quand même ne serait pas un refus."""
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=stock_lines(),
            errors=REJECTED, target="book_stock",
        )
        with pytest.raises(ValidationError):
            service.import_book_stock(campaign(), payload=b"x", filename="s.csv")
        assert written == []

    def test_a_clean_file_still_loads(self, monkeypatch):
        outcome, written = self.run(monkeypatch, errors=[])
        assert "stock" in written
        assert outcome.rows_accepted == 3

    def test_the_derogation_lets_it_through_and_says_so(self, monkeypatch):
        outcome, written = self.run(
            monkeypatch, errors=REJECTED, allow_partial=True
        )
        assert "stock" in written
        assert outcome.details["partialAccepted"] is True
        assert outcome.details["partialRejected"] == 1

    def test_a_clean_file_is_not_marked_partial(self, monkeypatch):
        """Le drapeau ne doit apparaître que quand il a servi."""
        outcome, _ = self.run(monkeypatch, errors=[], allow_partial=True)
        assert "partialAccepted" not in outcome.details


class TestTheBillOfMaterials:
    """Seul le mode remplacement est concerné : compléter n'efface rien."""

    def run(self, monkeypatch, *, errors, **extra):
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=bom_links(),
            errors=errors, target="boms",
        )
        outcome = service.import_boms(
            campaign(CampaignStatus.PREPARATION), payload=b"x",
            filename="bom.csv", **extra
        )
        return outcome, written

    def test_a_replacement_with_a_rejection_is_refused(self, monkeypatch):
        with pytest.raises(ValidationError):
            self.run(monkeypatch, errors=REJECTED, replace=True)

    def test_the_old_bill_is_not_cleared_when_it_is_refused(self, monkeypatch):
        """Le refus doit tomber avant `clear_bom`, sinon il détruit puis refuse."""
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=bom_links(),
            errors=REJECTED, target="boms",
        )
        with pytest.raises(ValidationError):
            service.import_boms(
                campaign(CampaignStatus.PREPARATION), replace=True,
                payload=b"x", filename="bom.csv",
            )
        assert written == []

    def test_completing_is_never_refused(self, monkeypatch):
        """Sans `replace`, les liens refusés manquent — ils ne disparaissent pas."""
        outcome, written = self.run(monkeypatch, errors=REJECTED)
        assert "bom" in written
        assert outcome.rows_rejected == 1

    def test_a_clean_replacement_goes_through(self, monkeypatch):
        _, written = self.run(monkeypatch, errors=[], replace=True)
        assert "clear" in written
        assert "bom" in written

    def test_the_derogation_applies_here_too(self, monkeypatch):
        outcome, written = self.run(
            monkeypatch, errors=REJECTED, replace=True, allow_partial=True
        )
        assert "clear" in written
        assert outcome.details["partialAccepted"] is True


class TestTheBackflushVariance:
    def run(self, monkeypatch, *, errors, **extra):
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=backflush_lines(),
            errors=errors, target="backflush",
        )
        outcome = service.import_backflush(
            campaign(), period_start=MONDAY, period_end=NEXT_MONDAY,
            payload=b"x", filename="bf.csv", **extra
        )
        return outcome, written

    def test_a_rejected_row_stops_it(self, monkeypatch):
        with pytest.raises(ValidationError):
            self.run(monkeypatch, errors=REJECTED)

    def test_nothing_is_written_when_it_is_refused(self, monkeypatch):
        service, written = import_service(
            monkeypatch, rows=ROWS, mapped=backflush_lines(),
            errors=REJECTED, target="backflush",
        )
        with pytest.raises(ValidationError):
            service.import_backflush(
                campaign(), period_start=MONDAY, period_end=NEXT_MONDAY,
                payload=b"x", filename="bf.csv",
            )
        assert written == []

    def test_a_clean_period_loads(self, monkeypatch):
        _, written = self.run(monkeypatch, errors=[])
        assert "backflush" in written


# --------------------------------------------------------------------------- #
# Ce que la dérogation traverse
# --------------------------------------------------------------------------- #

class TestTheDerogationReachesTheService:
    """Le drapeau ne sert que là où il veut dire quelque chose.

    Le transmettre à un chargement qui ne remplace rien serait accepté par
    Python et sans effet — pire qu'une erreur : l'interface l'afficherait sans
    que rien ne change jamais.
    """

    def options(self, target, *, replace=False, allow_partial=False):
        from inventory.api.routers.data import _write_options

        return _write_options(
            target, replace=replace, allow_partial=allow_partial
        )

    def test_a_wholesale_grid_receives_it(self):
        assert self.options("book_stock", allow_partial=True) == {
            "allow_partial": True
        }

    def test_the_backflush_too(self):
        assert self.options("backflush", allow_partial=True) == {
            "allow_partial": True
        }

    def test_the_bill_receives_it_only_when_replacing(self):
        assert self.options("boms", replace=True, allow_partial=True) == {
            "replace": True, "allow_partial": True
        }
        assert self.options("boms", replace=False, allow_partial=True) == {
            "replace": False
        }

    def test_a_completing_grid_never_receives_it(self):
        assert self.options("items", allow_partial=True) == {}
        assert self.options("count_sheets", allow_partial=True) == {}

    def test_the_refusal_is_a_four_twenty_two_not_a_five_hundred(self):
        """Le fichier est en cause, pas l'application."""
        assert ValidationError("x").status_code == 422
