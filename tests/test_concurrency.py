"""Deux mains sur la même feuille ne s'effacent plus.

L'enregistrement d'une feuille de comptage **remplace** ses lignes. C'est la
bonne lecture du geste — la grille renvoie l'état complet de ce qu'on voit — et
c'est aussi ce qui rendait la course silencieuse.

Le jour d'inventaire, deux personnes travaillent sur la même feuille : une qui
saisit les quantités relevées, une qui vérifie une zone derrière elle. Chacune
ouvre l'écran, chacune enregistre. La seconde à cliquer écrivait l'ensemble
qu'elle avait sous les yeux, c'est-à-dire un ensemble qui ne contenait pas ce
que la première venait d'ajouter. Aucun message, aucun conflit, aucune trace :
les quantités disparaissaient, et on s'en apercevait à la consolidation, quand
la zone ne tombait plus juste.

``bump_sheet`` est un ``UPDATE`` conditionné sur ``row_version``. PostgreSQL
sérialise deux mises à jour de la même ligne, donc exactement une des deux voit
la version attendue : la course devient un refus, et le refus dit quoi faire.

Les contrôles marqués ``postgres`` ouvrent une vraie base et lancent de vraies
courses — deux fils sur la même feuille, dix fils sur la même feuille — parce
qu'une doublure ne peut pas prouver l'atomicité d'un ``UPDATE``.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest

from inventory.errors import ConflictError

pytestmark = pytest.mark.postgres


@pytest.fixture
def db():
    from inventory.config import Settings
    from inventory.db.engine import Database

    if not os.environ.get("PGHOST"):
        pytest.skip("PGHOST absent")
    return Database(Settings())


@pytest.fixture
def sheet(db):
    """Une campagne, une zone et une feuille, créées à la main.

    Passer par les services demanderait tout le séquencement d'une préparation ;
    ce qui est vérifié ici est une ligne de table et sa version.
    """
    campaign_id = str(uuid.uuid4())
    zone_id = str(uuid.uuid4())
    sheet_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO campaign (id, code, label, count_date, status, created_by) "
            "VALUES (%s, %s, 'Course', '2026-09-01', 'COUNTING', 'chef@usine')",
            (campaign_id, f"INV-{campaign_id[:8]}"),
        )
        cur.execute(
            "INSERT INTO zone (id, campaign_id, code, label) "
            "VALUES (%s, %s, 'Z1', 'Zone 1')",
            (zone_id, campaign_id),
        )
        cur.execute(
            "INSERT INTO count_sheet (id, campaign_id, zone_id, pass_no) "
            "VALUES (%s, %s, %s, 'PASS_1')",
            (sheet_id, campaign_id, zone_id),
        )
    yield campaign_id, sheet_id, zone_id
    with db.cursor() as cur:
        cur.execute("DELETE FROM count_sheet_line WHERE campaign_id = %s", (campaign_id,))
        cur.execute("DELETE FROM count_sheet WHERE campaign_id = %s", (campaign_id,))
        cur.execute("DELETE FROM zone WHERE campaign_id = %s", (campaign_id,))
        cur.execute("DELETE FROM audit_event WHERE campaign_id = %s", (campaign_id,))
        cur.execute("DELETE FROM campaign WHERE id = %s", (campaign_id,))


@pytest.fixture
def sheets(db):
    from inventory.db.repositories import SheetRepository

    return SheetRepository(db)


def version_of(db, sheet_id: str) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT row_version FROM count_sheet WHERE id = %s", (sheet_id,))
        return cur.fetchone()["row_version"]


# --------------------------------------------------------------------------- #
# La prise elle-même
# --------------------------------------------------------------------------- #

class TestTakingTheSheet:
    def test_the_right_version_is_accepted(self, db, sheets, sheet):
        campaign_id, sheet_id, _ = sheet
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=version_of(db, sheet_id),
            actor="a@usine",
        )

    def test_a_stale_version_is_refused(self, db, sheets, sheet):
        campaign_id, sheet_id, _ = sheet
        current = version_of(db, sheet_id)
        with pytest.raises(ConflictError):
            sheets.bump_sheet(
                campaign_id, sheet_id, expected_version=current - 1, actor="a@usine"
            )

    def test_taking_it_moves_the_version_on(self, db, sheets, sheet):
        """Sinon la deuxième prise verrait encore la version qu'elle attend."""
        campaign_id, sheet_id, _ = sheet
        before = version_of(db, sheet_id)
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=before, actor="a@usine"
        )
        assert version_of(db, sheet_id) == before + 1

    def test_the_same_version_cannot_be_taken_twice(self, db, sheets, sheet):
        campaign_id, sheet_id, _ = sheet
        current = version_of(db, sheet_id)
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=current, actor="a@usine"
        )
        with pytest.raises(ConflictError):
            sheets.bump_sheet(
                campaign_id, sheet_id, expected_version=current, actor="b@usine"
            )

    def test_it_records_who_took_it(self, db, sheets, sheet):
        """« Qui a écrasé quoi » est la question posée après coup."""
        campaign_id, sheet_id, _ = sheet
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=version_of(db, sheet_id),
            actor="claire@usine",
        )
        with db.cursor() as cur:
            cur.execute("SELECT updated_by FROM count_sheet WHERE id = %s", (sheet_id,))
            assert cur.fetchone()["updated_by"] == "claire@usine"

    def test_a_sheet_of_another_campaign_is_refused(self, db, sheets, sheet):
        """La barrière de campagne tient aussi ici : l'identifiant seul ne
        suffit pas à toucher une feuille."""
        _, sheet_id, _ = sheet
        with pytest.raises(ConflictError):
            sheets.bump_sheet(
                str(uuid.uuid4()), sheet_id,
                expected_version=version_of(db, sheet_id), actor="a@usine",
            )

    def test_the_refusal_says_what_to_do(self, db, sheets, sheet):
        """« Conflit de version » n'aide personne ; « rechargez-la » si."""
        campaign_id, sheet_id, _ = sheet
        with pytest.raises(ConflictError) as caught:
            sheets.bump_sheet(
                campaign_id, sheet_id, expected_version=0, actor="a@usine"
            )
        message = str(caught.value)
        assert "Rechargez" in message
        assert "effacerait" in message


# --------------------------------------------------------------------------- #
# La course, pour de vrai
# --------------------------------------------------------------------------- #

class TestTheRace:
    def test_two_writers_on_the_same_version_produce_one_winner(
        self, db, sheets, sheet
    ):
        """Le cœur du sujet, joué : deux fils, la même version, un seul passe."""
        campaign_id, sheet_id, _ = sheet
        current = version_of(db, sheet_id)
        ready = threading.Barrier(2)
        outcomes: list[str] = []

        def write(actor: str) -> None:
            ready.wait()
            try:
                sheets.bump_sheet(
                    campaign_id, sheet_id, expected_version=current, actor=actor
                )
                outcomes.append("écrit")
            except ConflictError:
                outcomes.append("refusé")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write, ["a@usine", "b@usine"]))

        assert sorted(outcomes) == ["refusé", "écrit"]

    def test_ten_writers_produce_exactly_one_winner(self, db, sheets, sheet):
        """Une feuille de zone très fréquentée, un matin d'inventaire."""
        campaign_id, sheet_id, _ = sheet
        current = version_of(db, sheet_id)
        ready = threading.Barrier(10)
        written = []

        def write(n: int) -> None:
            ready.wait()
            try:
                sheets.bump_sheet(
                    campaign_id, sheet_id, expected_version=current,
                    actor=f"personne-{n}@usine",
                )
                written.append(n)
            except ConflictError:
                pass

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(write, range(10)))

        assert len(written) == 1

    def test_the_version_advances_exactly_once(self, db, sheets, sheet):
        """Dix prises qui incrémenteraient toutes feraient sauter la version de
        dix, et la prochaine lecture serait périmée sans raison."""
        campaign_id, sheet_id, _ = sheet
        current = version_of(db, sheet_id)
        ready = threading.Barrier(10)

        def write(n: int) -> None:
            ready.wait()
            with contextlib.suppress(ConflictError):
                sheets.bump_sheet(
                    campaign_id, sheet_id, expected_version=current, actor=f"p{n}"
                )

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(write, range(10)))

        assert version_of(db, sheet_id) == current + 1

    def test_the_loser_can_reload_and_try_again(self, db, sheets, sheet):
        """Le refus doit être récupérable : recharger, puis réécrire."""
        campaign_id, sheet_id, _ = sheet
        first = version_of(db, sheet_id)
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=first, actor="a@usine"
        )
        with pytest.raises(ConflictError):
            sheets.bump_sheet(
                campaign_id, sheet_id, expected_version=first, actor="b@usine"
            )
        sheets.bump_sheet(
            campaign_id, sheet_id, expected_version=version_of(db, sheet_id),
            actor="b@usine",
        )


# --------------------------------------------------------------------------- #
# Le témoin : la faute existait
# --------------------------------------------------------------------------- #

class TestWithoutTheVersion:
    def test_the_erasure_happens(self, db, sheets, sheet):
        """Sans la prise, deux remplacements successifs s'effacent en silence.

        C'est le témoin de la faute : la même séquence, sans ``bump_sheet``,
        laisse la feuille dans l'état du second écrivain seul — les lignes du
        premier ont disparu sans que rien ne l'ait signalé.
        """
        from decimal import Decimal

        from inventory.domain.enums import CountSection, DataSource
        from inventory.domain.models import CountSheetLine

        campaign_id, sheet_id, _ = sheet

        def line(item: str, qty: str) -> CountSheetLine:
            return CountSheetLine(
                id=str(uuid.uuid4()), sheet_id=sheet_id, campaign_id=campaign_id,
                item_number=item, section=CountSection.LINE_SIDE,
                qty_manual=Decimal(qty), unit="PCE", source=DataSource.MANUAL,
                display_order=0,
            )

        # La première personne saisit deux lignes.
        sheets.replace_sheet_lines(
            sheet_id, [line("P-1", "10"), line("P-2", "20")], actor="a@usine"
        )
        # La seconde, partie de l'écran d'avant, n'en connaît qu'une.
        sheets.replace_sheet_lines(sheet_id, [line("P-1", "10")], actor="b@usine")

        remaining = [l.item_number for l in sheets.list_sheet_lines(sheet_id)]
        assert "P-2" not in remaining, "le témoin ne reproduit plus la faute"


# --------------------------------------------------------------------------- #
# Le câblage, sans base
# --------------------------------------------------------------------------- #

@pytest.mark.no_postgres
class TestTheServicePassesItOn:
    """Ce qui se vérifie sans base : que la version voyage, et *où* elle est
    prise."""

    ROW: ClassVar = [{"item_number": "P-1", "section": "LINE_SIDE"}]

    def service(self):
        from types import SimpleNamespace
        from typing import Any, cast

        from conftest import with_transactions

        from inventory.services.generic_service import GenericService

        calls: list[dict[str, Any]] = []
        campaign = cast(Any, SimpleNamespace(id="camp-1"))
        sheet = SimpleNamespace(
            id="sheet-1", campaign_id="camp-1", zone_id="zone-1", row_version=7
        )
        ctx = cast(Any, SimpleNamespace(
            actor="testeur",
            guard=lambda campaign, aspect: None,
            record=lambda **kw: None,
            sheets=SimpleNamespace(
                get_sheet=lambda sid: sheet,
                list_zones=lambda cid: [
                    SimpleNamespace(id="zone-1", code="Z1", allow_negative=False)
                ],
                list_sheet_lines=lambda sid: [],
                bump_sheet=lambda *a, **kw: calls.append(
                    {"what": "bump", **kw, "args": a}
                ),
                replace_sheet_lines=lambda *a, **kw: (
                    calls.append({"what": "replace"}) or 1
                ),
                upsert_sheet_lines=lambda *a, **kw: (
                    calls.append({"what": "upsert"}) or 1
                ),
            ),
        ))
        with_transactions(ctx)
        return GenericService(ctx), campaign, calls

    def test_a_replace_with_a_version_takes_the_sheet(self):
        service, campaign, calls = self.service()
        service.upsert_sheet_lines(
            campaign, "sheet-1", self.ROW, replace=True, expected_version=7
        )
        assert [c["what"] for c in calls] == ["bump", "replace"]

    def test_the_version_reaches_the_repository(self):
        service, campaign, calls = self.service()
        service.upsert_sheet_lines(
            campaign, "sheet-1", self.ROW, replace=True, expected_version=7
        )
        assert calls[0]["expected_version"] == 7

    def test_the_sheet_is_taken_inside_the_writing_transaction(self):
        """Le prendre à part laisserait une fenêtre entre la prise et le
        remplacement — exactement la course qu'il ferme."""
        service, campaign, calls = self.service()
        service.upsert_sheet_lines(
            campaign, "sheet-1", self.ROW, replace=True, expected_version=7
        )
        assert calls[0]["conn"] == service.ctx.db.connection

    def test_a_replace_without_a_version_still_works(self):
        """Une extraction IA écrit une feuille qu'elle vient de lire ; rien
        d'autre ne la touche, et lui imposer une version l'empêcherait d'écrire."""
        service, campaign, calls = self.service()
        service.upsert_sheet_lines(campaign, "sheet-1", self.ROW, replace=True)
        assert [c["what"] for c in calls] == ["replace"]

    def test_an_append_never_takes_the_sheet(self):
        """Ajouter des lignes n'efface rien : le refus serait gratuit."""
        service, campaign, calls = self.service()
        service.upsert_sheet_lines(
            campaign, "sheet-1", self.ROW, replace=False, expected_version=7
        )
        assert [c["what"] for c in calls] == ["upsert"]


@pytest.mark.no_postgres
class TestTheBrowserSendsIt:
    def source(self, relative: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (root / "frontend" / "src" / relative).read_text()

    def test_the_client_accepts_a_version(self):
        assert "expectedVersion?: number," in self.source("lib/api.ts")

    def test_the_client_sends_it(self):
        assert "JSON.stringify({ lines, replace, expectedVersion })" in self.source(
            "lib/api.ts"
        )

    def test_the_screen_sends_the_version_it_read(self):
        """Envoyer autre chose que ce qu'on a lu — la version d'après un
        rafraîchissement, par exemple — reviendrait à désactiver la garde."""
        assert "Number(query.data?.sheet?.row_version) || undefined" in self.source(
            "features/Generic.tsx"
        )

    def test_the_route_passes_it_to_the_service(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        source = (
            root / "app" / "inventory" / "api" / "routers" / "generic.py"
        ).read_text()
        assert "expected_version=payload.expected_version," in source

    def test_the_contract_carries_it(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        source = (root / "app" / "inventory" / "api" / "schemas.py").read_text()
        assert 'alias="expectedVersion"' in source
