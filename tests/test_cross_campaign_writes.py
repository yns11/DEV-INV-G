"""Une écriture est portée par sa campagne, pas seulement par son identifiant.

La permission d'écriture est vérifiée sur la campagne de l'**URL** ; les
identifiants des objets, eux, arrivent dans le **corps** de la requête. Plusieurs
écritures recherchaient ensuite l'objet par son seul UUID. Un gestionnaire
habilité sur la campagne A pouvait donc poster un journal, supprimer une ligne
ou fermer une zone de la campagne B en connaissant son identifiant : la garde
avait bien vu A, et n'avait aucune raison de regarder ailleurs.

Ces contrôles portent sur la forme des requêtes émises — chaque écriture doit
citer sa campagne. La garantie structurelle, elle, est dans la migration 018 :
une clé étrangère composite empêche un enfant d'appartenir à la campagne d'un
autre, même si une requête future oublie le filtre.
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar

import pytest

from inventory.db import repositories as repo
from inventory.domain.enums import JournalStatus
from inventory.domain.models import CountJournalLine

CAMPAIGN = "camp-1"
INTRUDER = "camp-VOISINE"


class Spy:
    """Note les requêtes émises, sans base derrière."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _execute(self, query: str, params=(), *, conn=None) -> int:
        self.calls.append((" ".join(query.split()), tuple(params or ())))
        return 1

    def _execute_many(self, query: str, rows, *, conn=None) -> int:
        self.calls.append((" ".join(query.split()), tuple(rows)))
        return len(list(rows))

    @property
    def last(self) -> tuple[str, tuple[Any, ...]]:
        return self.calls[-1]


def spy_on(cls):
    """Une instance du dépôt dont les écritures sont observées."""
    instance = cls.__new__(cls)
    spy = Spy()
    instance._execute = spy._execute  # type: ignore[attr-defined]
    instance._execute_many = spy._execute_many  # type: ignore[attr-defined]
    return instance, spy


def scoped(query: str) -> bool:
    """La requête cite-t-elle la campagne dans son WHERE ?"""
    where = query.upper().split("WHERE", 1)
    return len(where) == 2 and "CAMPAIGN_ID" in where[1]


class TestJournals:
    def test_posting_a_batch_names_its_campaign(self):
        """Le cas exact du rapport : `WHERE id = ANY(...)`, sans campagne."""
        journals, spy = spy_on(repo.JournalRepository)
        journals.set_status(
            CAMPAIGN, ["j-1", "j-2"], JournalStatus.POSTED, actor="chef@usine"
        )
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params

    def test_an_empty_batch_writes_nothing(self):
        journals, spy = spy_on(repo.JournalRepository)
        assert journals.set_status(CAMPAIGN, [], JournalStatus.POSTED, actor="a") == 0
        assert spy.calls == []

    def test_deleting_a_line_names_its_campaign(self):
        journals, spy = spy_on(repo.JournalRepository)
        journals.delete_line(CAMPAIGN, "l-1", actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params

    def test_an_optimistic_update_names_its_campaign(self):
        """La version attendue protège d'une écriture concurrente, pas d'une
        écriture d'ailleurs : ce sont deux questions différentes."""
        journals, spy = spy_on(repo.JournalRepository)
        line = CountJournalLine(
            id="l-1", journal_id="j-1", campaign_id=CAMPAIGN, item_number="P-1",
        )
        journals.upsert_line(line, actor="chef@usine", expected_version=3)
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params


class TestZonesAndSheets:
    def test_closing_a_zone_names_its_campaign(self):
        sheets, spy = spy_on(repo.SheetRepository)
        sheets.set_zone_closed(CAMPAIGN, "z-1", closed=True, actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params

    def test_deleting_a_zone_names_its_campaign(self):
        sheets, spy = spy_on(repo.SheetRepository)
        sheets.delete_zone(CAMPAIGN, "z-1", actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params

    def test_updating_a_sheet_names_its_campaign(self):
        sheets, spy = spy_on(repo.SheetRepository)
        sheets.update_sheet(CAMPAIGN, "s-1", counter_name="Alice", actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params

    def test_deleting_a_sheet_line_names_its_campaign(self):
        sheets, spy = spy_on(repo.SheetRepository)
        sheets.delete_sheet_line(CAMPAIGN, "l-1", actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params


class TestAdjustments:
    def test_deleting_an_adjustment_names_its_campaign(self):
        adjustments, spy = spy_on(repo.AdjustmentRepository)
        adjustments.delete(CAMPAIGN, "a-1", actor="chef@usine")
        query, params = spy.last
        assert scoped(query), query
        assert CAMPAIGN in params


class TestTheCampaignIsWhatTheUrlSaid:
    """L'identifiant d'un objet ne doit jamais servir à choisir la campagne.

    C'est la moitié applicative du correctif : le service reçoit la campagne
    résolue et vérifiée par la garde, et la transmet telle quelle.
    """

    @pytest.mark.parametrize(
        "call",
        [
            lambda r: r.set_status(
                INTRUDER, ["j-1"], JournalStatus.POSTED, actor="a"
            ),
            lambda r: r.delete_line(INTRUDER, "l-1", actor="a"),
        ],
    )
    def test_the_campaign_travels_all_the_way_to_the_statement(self, call):
        journals, spy = spy_on(repo.JournalRepository)
        call(journals)
        assert INTRUDER in spy.last[1]


class TestTheSignatureItself:
    """La campagne est un paramètre obligatoire, pas une option de politesse.

    Un contrôle sur la forme des requêtes ne survit pas à une réécriture qui
    laisserait `campaign_id` par défaut à `None` : la requête citerait toujours
    la colonne, avec une valeur vide. Ces méthodes doivent donc *exiger* la
    campagne, en première position.
    """

    SCOPED: ClassVar[list] = [
        (repo.JournalRepository, "set_status"),
        (repo.JournalRepository, "delete_line"),
        (repo.SheetRepository, "set_zone_closed"),
        (repo.SheetRepository, "delete_zone"),
        (repo.SheetRepository, "update_sheet"),
        (repo.SheetRepository, "delete_sheet_line"),
        (repo.AdjustmentRepository, "delete"),
    ]

    @pytest.mark.parametrize("cls,name", SCOPED, ids=lambda v: getattr(v, "__name__", v))
    def test_the_campaign_comes_first_and_has_no_default(self, cls, name):
        params = list(inspect.signature(getattr(cls, name)).parameters.values())
        first = params[1]  # après self
        assert first.name == "campaign_id", (
            f"{cls.__name__}.{name} n'attend plus la campagne en premier"
        )
        assert first.default is inspect.Parameter.empty, (
            f"{cls.__name__}.{name} accepte une campagne implicite"
        )
