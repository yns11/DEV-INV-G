"""Le stock ERP se charge sur un périmètre restreint.

Le fichier ERP couvre toute l'usine. La campagne, elle, choisit son périmètre :
un programme parti du site, une gamme après-vente comptée ailleurs, des
emballages que personne ne pèse — tout cela s'exclut sur la grille Articles, et
c'est une décision, prise avant le chargement.

Le mappeur en faisait une **erreur de ligne**. Or le stock ERP remplace
l'ensemble existant, et un chargement qui remplace refuse d'écrire dès qu'une
ligne est rejetée — à raison : il laisserait un ensemble amputé qui se présente
comme complet. Les deux règles se composaient en un piège :

    Le stock ERP remplace l'ensemble existant, et 1558 ligne(s) sur 1598 ont
    été refusées. L'écriture est annulée.

Plus le périmètre était restreint, moins le stock était chargeable. Un
inventaire portant sur quarante références sur mille six cents ne pouvait pas
démarrer.

Une ligne hors périmètre n'est pas une erreur de fichier : elle est écartée,
comptée et dite. C'est la règle que la lecture du backflush applique déjà.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.domain.enums import CampaignStatus, ExclusionScope
from inventory.domain.models import BookStockLine, Campaign, Item
from inventory.ingest.mappers import map_book_stock
from inventory.ingest.parser import ParseResult, RowError


def campaign() -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date="2026-09-01", status=CampaignStatus.COUNTING,
        created_by="chef@usine",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


def referential(*numbers: str, excluded: tuple[str, ...] = ()) -> dict[str, Item]:
    return {
        n: Item(
            campaign_id="camp-1", item_number=n, std_price="10",
            exclusions={ExclusionScope.ALL} if n in excluded else set(),
        )
        for n in numbers
    }


def stock_rows(*numbers: str) -> list[dict[str, Any]]:
    return [
        {"item_number": n, "warehouse_id": "B06", "location_id": "VRAC", "qty": "10"}
        for n in numbers
    ]


# --------------------------------------------------------------------------- #
# Le mappeur
# --------------------------------------------------------------------------- #

class TestTheMapperTellsADecisionFromAMistake:
    def test_l_article_hors_perimetre_est_ecarte(self):
        lines, errors, skipped = map_book_stock(
            "camp-1", stock_rows("P-1", "P-2"),
            items=referential("P-1", "P-2", excluded=("P-2",)),
        )

        assert [line.item_number for line in lines] == ["P-1"]
        assert errors == []
        assert len(skipped) == 1

    def test_l_article_inconnu_reste_une_erreur(self):
        """Sans article, la ligne n'a ni désignation, ni prix, ni matérialité.

        C'est le contraire de l'exclusion : un manque de données, pas un choix.
        """
        lines, errors, skipped = map_book_stock(
            "camp-1", stock_rows("INCONNU"), items=referential("P-1"),
        )

        assert lines == []
        assert len(errors) == 1
        assert skipped == []

    def test_les_deux_causes_ne_se_confondent_pas(self):
        _, errors, skipped = map_book_stock(
            "camp-1", stock_rows("P-1", "P-2", "INCONNU"),
            items=referential("P-1", "P-2", excluded=("P-2",)),
        )

        assert [e.value for e in errors] == ["INCONNU"]
        assert [w.value for w in skipped] == ["P-2"]

    def test_un_perimetre_presque_entierement_exclu_charge_quand_meme(self):
        """Le cas rencontré : quarante lignes gardées sur mille six cents."""
        gardees = [f"P-{n}" for n in range(40)]
        exclues = [f"X-{n}" for n in range(1558)]
        items = referential(*gardees, *exclues, excluded=tuple(exclues))

        lines, errors, skipped = map_book_stock(
            "camp-1", stock_rows(*gardees, *exclues), items=items
        )

        assert len(lines) == 40
        assert errors == [], "aucun refus : rien n'annule l'écriture"
        assert len(skipped) == 1558

    def test_l_ecart_ne_reste_pas_a_charge_de_l_article_exclu(self):
        """Son stock n'entre pas : l'inventaire ne le compte pas, et un stock
        ERP sans comptage produirait un écart égal à la totalité du stock."""
        lines, _, _ = map_book_stock(
            "camp-1", stock_rows("P-2"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert lines == []

    def test_le_message_dit_le_geste_qui_l_inclurait(self):
        _, _, skipped = map_book_stock(
            "camp-1", stock_rows("P-2"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert "hors du périmètre" in skipped[0].message
        assert "grille Articles" in skipped[0].message

    def test_la_ligne_ecartee_porte_sa_reference(self):
        """« 1558 lignes écartées » sans savoir lesquelles n'aide personne."""
        _, _, skipped = map_book_stock(
            "camp-1", stock_rows("P-2"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert skipped[0].value == "P-2"
        assert skipped[0].column == "item_number"


# --------------------------------------------------------------------------- #
# Le service
# --------------------------------------------------------------------------- #

def import_service(monkeypatch, *, rows, items):
    """Un service dont la lecture est dictée, et le mappage bien réel."""
    from inventory.services import import_service as module

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    written: list[int] = []
    ctx.book_stock = SimpleNamespace(
        replace=lambda cid, lines, **k: written.append(len(lines)) or len(lines)
    )
    ctx.referentials = SimpleNamespace(
        items_by_number=lambda cid: items,
        locations_by_key=lambda cid: {},
        upsert_warehouses=lambda w, *, actor, conn=None: len(list(w)),
        upsert_locations=lambda l, *, actor, conn=None: len(list(l)),
    )
    ctx.journals = SimpleNamespace(
        ensure_journals=lambda cid, keys, *, kinds, actor, conn=None: len(keys),
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
    monkeypatch.setattr(
        service.parser, "parse",
        lambda t, **kw: (
            None,
            ParseResult(contract_key=t, rows=rows, rows_received=len(rows)),
        ),
    )
    return service, written


class TestTheSnapshotLoadsAndSaysWhatItLeftOut:
    def run(self, monkeypatch, *, kept, excluded):
        items = referential(*kept, *excluded, excluded=tuple(excluded))
        service, written = import_service(
            monkeypatch, rows=stock_rows(*kept, *excluded), items=items
        )
        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="stock.csv"
        )
        return outcome, written

    def test_le_stock_du_perimetre_est_ecrit(self, monkeypatch):
        _, written = self.run(monkeypatch, kept=["P-1"], excluded=["X-1", "X-2"])

        assert written == [1], "l'écriture est annulée alors qu'elle ne doit pas l'être"

    def test_rien_n_est_compte_comme_refuse(self, monkeypatch):
        """C'est le décompte des refus qui déclenchait l'annulation."""
        outcome, _ = self.run(monkeypatch, kept=["P-1"], excluded=["X-1", "X-2"])

        assert outcome.rows_rejected == 0
        assert outcome.ok

    def test_les_lignes_ecartees_sont_dites(self, monkeypatch):
        """Écarter en silence serait la troncature muette que ce projet refuse."""
        outcome, _ = self.run(monkeypatch, kept=["P-1"], excluded=["X-1", "X-2"])

        assert outcome.details["outOfScopeLines"] == 2
        assert len(outcome.warnings) == 2

    def test_les_articles_sont_comptes_a_part_des_lignes(self, monkeypatch):
        """Un article hors périmètre présent sur dix emplacements fait dix
        lignes et un article : les deux chiffres ne disent pas la même chose."""
        items = referential("P-1", "X-1", excluded=("X-1",))
        rows = stock_rows("P-1") + [
            {"item_number": "X-1", "warehouse_id": "B06",
             "location_id": f"L{n}", "qty": "5"}
            for n in range(10)
        ]
        service, _ = import_service(monkeypatch, rows=rows, items=items)

        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="stock.csv"
        )

        assert outcome.details["outOfScopeLines"] == 10
        assert outcome.details["outOfScopeItems"] == 1

    def test_un_fichier_entierement_hors_perimetre_n_ecrit_rien(self, monkeypatch):
        """Et le dit, plutôt que de laisser croire à un chargement réussi."""
        outcome, written = self.run(monkeypatch, kept=[], excluded=["X-1"])

        assert written == []
        assert outcome.details["outOfScopeLines"] == 1

    def test_sans_exclusion_le_decompte_est_zero(self, monkeypatch):
        outcome, written = self.run(monkeypatch, kept=["P-1", "P-2"], excluded=[])

        assert written == [2]
        assert outcome.details["outOfScopeLines"] == 0
        assert outcome.warnings == []

    def test_un_article_inconnu_annule_toujours_l_ecriture(self, monkeypatch):
        """La garde du remplacement n'a pas bougé : elle vise les vraies
        erreurs, et une référence qu'aucun article ne décrit en est une."""
        from inventory.errors import ValidationError

        service, written = import_service(
            monkeypatch, rows=stock_rows("P-1", "INCONNU"),
            items=referential("P-1"),
        )

        with pytest.raises(ValidationError):
            service.import_book_stock(campaign(), payload=b"x", filename="s.csv")
        assert written == []


def test_la_ligne_ecartee_n_est_pas_une_erreur_de_ligne():
    """Le type est le même — `RowError` — mais la liste ne l'est pas.

    C'est cette distinction, et elle seule, qui empêche la garde du
    remplacement de se déclencher.
    """
    _, errors, skipped = map_book_stock(
        "camp-1", stock_rows("P-2"), items=referential("P-2", excluded=("P-2",))
    )

    assert isinstance(skipped[0], RowError)
    assert errors == []


def test_la_ligne_hors_perimetre_n_apparait_pas_dans_le_stock_ecrit(monkeypatch):
    """Le bout qui compte vraiment : ce qui atterrit en base."""
    items = referential("P-1", "X-1", excluded=("X-1",))
    service, _ = import_service(monkeypatch, rows=stock_rows("P-1", "X-1"), items=items)
    ecrit: list[BookStockLine] = []
    service.ctx.book_stock = SimpleNamespace(
        replace=lambda cid, lines, **k: ecrit.extend(lines) or len(lines)
    )

    service.import_book_stock(campaign(), payload=b"x", filename="stock.csv")

    assert [line.item_number for line in ecrit] == ["P-1"]
    assert all(line.qty == Decimal("10.000000") for line in ecrit)
