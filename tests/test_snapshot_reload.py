"""Recharger le stock ERP remplace le snapshot — journaux compris.

Le stock ERP est une *photographie*. En recharger une nouvelle ne complète pas
l'ancienne, elle la remplace. Les journaux, eux, ne suivaient pas : ceux du
nouveau snapshot s'ajoutaient à ceux de l'ancien, si bien que la liste des
emplacements à compter grossissait à chaque chargement et que la couverture
comptait des emplacements qui n'existaient plus.

La règle retenue tient en deux phrases. Un emplacement né d'un snapshot et
absent du suivant s'en va, avec son journal. Sauf si ce journal porte déjà du
travail — une ligne saisie, un statut avancé — auquel cas il reste et l'import
le dit : un emplacement compté sous un snapshot qui ne le liste plus est
exactement ce qu'il faut regarder, pas ce qu'il faut nettoyer en silence.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from inventory.domain.enums import DataSource, LocationStatus, LocationType
from inventory.domain.models import Location, LocationKey
from inventory.services.import_service import ImportOutcome, ImportService

GENERIC = LocationKey(warehouse_id="B06VRAC", location_id="GENERIQUE")
CAMPAIGN = cast(Any, SimpleNamespace(
    id="camp-1", config=SimpleNamespace(generic_key=GENERIC)
))


def key(warehouse: str, location: str) -> LocationKey:
    return LocationKey(warehouse_id=warehouse, location_id=location)


def location(warehouse: str, place: str, *, source=DataSource.SYSTEM) -> Location:
    return Location(
        campaign_id="camp-1",
        warehouse_id=warehouse,
        location_id=place,
        type=LocationType.LABEL,
        status=LocationStatus.ACTIVE,
        source=source,
    )


def service(
    *,
    untouched: set[tuple[str, str]],
    journals: set[tuple[str, str]],
    counted_sheet_lines: int = 0,
):
    """A context whose journals answer what has been worked on."""
    calls: dict[str, Any] = {"deleted": [], "disabled": []}

    def delete(campaign_id, keys, *, conn=None):
        calls["deleted"] = list(keys)
        return len(keys)

    def disable(campaign_id, keys, status, *, actor, conn=None):
        calls["disabled"] = [(k, status) for k in keys]
        return len(keys)

    ctx = SimpleNamespace(
        actor="testeur",
        journals=SimpleNamespace(
            untouched_journal_keys=lambda cid, keys, conn=None: {
                k for k in untouched
                if k in {(x.warehouse_id, x.location_id) for x in keys}
            },
            journal_keys=lambda cid, keys, conn=None: {
                k for k in journals
                if k in {(x.warehouse_id, x.location_id) for x in keys}
            },
            delete_journals_for_locations=delete,
        ),
        referentials=SimpleNamespace(set_location_status=disable),
        sheets=SimpleNamespace(
            count_counted_lines=lambda cid, conn=None: counted_sheet_lines
        ),
    )
    return ImportService(cast(Any, ctx)), calls


class TestALocationTheNewSnapshotDroppedAndNobodyCounted:
    def test_its_journal_goes(self):
        generic, calls = service(
            untouched={("B06", "ALLEE-B")}, journals={("B06", "ALLEE-B")}
        )
        outcome = ImportOutcome(target="book_stock")
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [key("B06", "ALLEE-B")], outcome=outcome, conn=None
        )
        assert removed == 1
        assert kept == set()
        assert calls["deleted"] == [key("B06", "ALLEE-B")]

    def test_the_location_is_disabled_with_it(self):
        """Un emplacement actif sans stock recréerait son journal au prochain
        chargement : le retirer à moitié ne retire rien."""
        generic, calls = service(
            untouched={("B06", "ALLEE-B")}, journals={("B06", "ALLEE-B")}
        )
        generic._retire_stale_locations(
            CAMPAIGN, [key("B06", "ALLEE-B")],
            outcome=ImportOutcome(target="book_stock"), conn=None,
        )
        assert calls["disabled"] == [(key("B06", "ALLEE-B"), LocationStatus.DISABLED)]

    def test_the_import_says_how_many(self):
        generic, _ = service(
            untouched={("B06", "ALLEE-B")}, journals={("B06", "ALLEE-B")}
        )
        outcome = ImportOutcome(target="book_stock")
        generic._retire_stale_locations(
            CAMPAIGN, [key("B06", "ALLEE-B")], outcome=outcome, conn=None
        )
        assert outcome.details["journalsRemoved"] == 1
        assert outcome.details["locationsRetired"] == 1


class TestALocationSomebodyHasAlreadyCounted:
    """Recharger un snapshot n'est pas une décision de jeter du travail."""

    def test_its_journal_is_kept(self):
        generic, calls = service(untouched=set(), journals={("QUAL", "LABO")})
        outcome = ImportOutcome(target="book_stock")
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [key("QUAL", "LABO")], outcome=outcome, conn=None
        )
        assert removed == 0
        assert kept == {key("QUAL", "LABO")}
        assert calls["deleted"] == []

    def test_its_location_stays_active(self):
        """Sinon le comptage ouvert disparaîtrait des écrans qui le montrent."""
        generic, calls = service(untouched=set(), journals={("QUAL", "LABO")})
        generic._retire_stale_locations(
            CAMPAIGN, [key("QUAL", "LABO")],
            outcome=ImportOutcome(target="book_stock"), conn=None,
        )
        assert calls["disabled"] == []

    def test_the_import_warns_and_names_it(self):
        generic, _ = service(untouched=set(), journals={("QUAL", "LABO")})
        outcome = ImportOutcome(target="book_stock")
        generic._retire_stale_locations(
            CAMPAIGN, [key("QUAL", "LABO")], outcome=outcome, conn=None
        )
        assert outcome.details["locationsKept"] == ["QUAL / LABO"]
        assert any("comptage" in w.message for w in outcome.warnings)


class TestAMixedReload:
    def test_the_empty_one_goes_and_the_counted_one_stays(self):
        generic, calls = service(
            untouched={("B06", "ALLEE-B")},
            journals={("B06", "ALLEE-B"), ("QUAL", "LABO")},
        )
        outcome = ImportOutcome(target="book_stock")
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [key("B06", "ALLEE-B"), key("QUAL", "LABO")],
            outcome=outcome, conn=None,
        )
        assert (removed, kept) == (1, {key("QUAL", "LABO")})
        assert calls["deleted"] == [key("B06", "ALLEE-B")]


class TestNothingToDo:
    def test_an_unchanged_snapshot_touches_nothing(self):
        generic, calls = service(untouched=set(), journals=set())
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [], outcome=ImportOutcome(target="book_stock"), conn=None
        )
        assert (removed, kept) == (0, set())
        assert calls["deleted"] == [] and calls["disabled"] == []

    def test_a_stale_location_that_never_had_a_journal_is_still_closed(self):
        """Rien à supprimer, mais l'emplacement n'a plus lieu d'être actif."""
        generic, calls = service(untouched=set(), journals=set())
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [key("B06", "VIDE")],
            outcome=ImportOutcome(target="book_stock"), conn=None,
        )
        assert (removed, kept) == (0, set())
        assert calls["disabled"] == [(key("B06", "VIDE"), LocationStatus.DISABLED)]


class TestWhichLocationsAreEvenCandidates:
    """Seuls ceux nés d'un snapshot. Un emplacement déclaré à la main reste."""

    def test_a_hand_declared_location_is_not_stale(self):
        keys = {key("B06", "ALLEE-A"): location("B06", "ALLEE-A")}
        declared = location("B06", "MANUEL", source=DataSource.FILE_IMPORT)
        keys[key("B06", "MANUEL")] = declared
        snapshot = {key("B06", "ALLEE-A")}

        stale = [
            k for k, loc in keys.items()
            if k not in snapshot
            and loc.source is DataSource.SYSTEM
            and loc.status is LocationStatus.ACTIVE
        ]
        assert stale == []

    def test_a_snapshot_born_location_is(self):
        keys = {
            key("B06", "ALLEE-A"): location("B06", "ALLEE-A"),
            key("B06", "ALLEE-B"): location("B06", "ALLEE-B"),
        }
        snapshot = {key("B06", "ALLEE-A")}
        stale = [
            k for k, loc in keys.items()
            if k not in snapshot
            and loc.source is DataSource.SYSTEM
            and loc.status is LocationStatus.ACTIVE
        ]
        assert stale == [key("B06", "ALLEE-B")]


class TestTheGeneriqueLocationWhoseCountingLivesInSheets:
    """Son journal ne porte aucune ligne : le juger dessus le dit vierge.

    Une zone entière peut avoir été comptée en feuilles sans qu'une seule ligne
    de journal existe. La règle « journal vide donc jetable » emporterait alors
    tout le comptage B06VRAC au premier rechargement de snapshot, et sans le
    dire.
    """

    def stale_generic(self, *, counted: int):
        generic, calls = service(
            untouched={("B06VRAC", "GENERIQUE")},
            journals={("B06VRAC", "GENERIQUE")},
            counted_sheet_lines=counted,
        )
        outcome = ImportOutcome(target="book_stock")
        removed, kept = generic._retire_stale_locations(
            CAMPAIGN, [GENERIC], outcome=outcome, conn=None
        )
        return removed, kept, calls, outcome

    def test_a_counted_sheet_keeps_it(self):
        removed, kept, calls, _ = self.stale_generic(counted=12)
        assert removed == 0
        assert kept == {GENERIC}
        assert calls["deleted"] == []

    def test_and_the_location_stays_active(self):
        _, _, calls, _ = self.stale_generic(counted=12)
        assert calls["disabled"] == []

    def test_the_import_says_so_rather_than_keeping_it_quietly(self):
        _, _, _, outcome = self.stale_generic(counted=12)
        assert outcome.warnings
        assert "B06VRAC / GENERIQUE" in outcome.details["locationsKept"]

    def test_without_a_single_counted_line_it_goes_like_any_other(self):
        removed, kept, calls, _ = self.stale_generic(counted=0)
        assert (removed, kept) == (1, set())
        assert calls["deleted"] == [GENERIC]
