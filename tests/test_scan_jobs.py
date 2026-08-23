"""Le scan multi-feuilles mené comme un travail suivi.

La lecture d'une pile de cent feuilles dure des minutes. Elle tenait jusqu'ici
dans la requête HTTP du chargement, avec trois conséquences dans cet ordre de
gravité : la passerelle coupait avant la fin et emportait ce qui avait été lu ;
l'écran restait figé sans rien pour distinguer « ça travaille » de « c'est
bloqué » ; un rafraîchissement relançait tout.

Ces contrôles portent sur les quatre propriétés qui font que ce n'est plus le
cas — la réponse immédiate, la progression, le refus au bon moment, et ce que
devient un travail dont le conteneur a disparu.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign
from inventory.errors import NotFoundError, PermissionDeniedError
from inventory.services import scan_jobs


def campaign(status: CampaignStatus = CampaignStatus.COUNTING) -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026-09", label="Inventaire",
        count_date="2026-09-01", status=status,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


class FakeJobs:
    """La table `scan_job`, en mémoire, avec le même contrat."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.progress_calls: list[dict[str, Any]] = []
        self._seq = 0

    def create(self, *, campaign_id, filename, content_type,
               overwrite_reviewed, actor, sheet_id=None) -> str:
        self._seq += 1
        job_id = f"job-{self._seq}"
        self.rows[job_id] = {
            "id": job_id, "campaign_id": campaign_id, "sheet_id": sheet_id,
            "filename": filename,
            "content_type": content_type, "overwrite_reviewed": overwrite_reviewed,
            "created_by": actor, "status": "QUEUED", "step": "",
            "total_pages": 0, "pages_routed": 0, "sheets_total": 0,
            "sheets_done": 0, "report": {}, "error": "",
            "created_at": None, "started_at": None, "finished_at": None,
        }
        return job_id

    def latest_for_sheet(self, sheet_id, campaign_id):
        rows = [
            r for r in self.rows.values()
            if r.get("sheet_id") == sheet_id and r["campaign_id"] == campaign_id
        ]
        return rows[-1] if rows else None

    def get(self, job_id, campaign_id):
        row = self.rows.get(job_id)
        return row if row and row["campaign_id"] == campaign_id else None

    def list(self, campaign_id, *, limit=20):
        return [r for r in self.rows.values() if r["campaign_id"] == campaign_id]

    def start(self, job_id, *, total_pages=0):
        self.rows[job_id].update(status="RUNNING", total_pages=total_pages)

    def progress(self, job_id, **fields):
        self.progress_calls.append({"job": job_id, **fields})
        self.rows[job_id].update({k: v for k, v in fields.items() if v is not None})

    def finish(self, job_id, *, report):
        self.rows[job_id].update(
            status="SUCCEEDED", step="Terminé", report=report,
            sheets_done=len(report.get("sheetsProcessed") or []),
        )

    def fail(self, job_id, *, error):
        self.rows[job_id].update(status="FAILED", step="Échec", error=error)

    def abandon_orphans(self, *, reason):
        touched = 0
        for row in self.rows.values():
            if row["status"] in ("QUEUED", "RUNNING"):
                row.update(status="FAILED", step="Échec", error=reason)
                touched += 1
        return touched


def service(*, actor: str = "chef@usine", managers=()):
    jobs = FakeJobs()
    ctx = SimpleNamespace(
        actor=actor,
        request_id="req-1",
        scan_jobs=jobs,
        progress=lambda c: SimpleNamespace(
            items=10, zones=2, book_stock_lines=5, book_stock_frozen=True
        ),
    )
    with_access(ctx, managers=managers)
    return scan_jobs.ScanJobService(cast(Any, ctx)), jobs


@pytest.fixture(autouse=True)
def no_real_worker(monkeypatch):
    """Le fil de travail est remplacé : ces contrôles portent sur le dépôt."""
    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        scan_jobs._workers, "submit",
        lambda fn, **kw: submitted.append(kw) or SimpleNamespace(),
    )
    scan_jobs._payloads.clear()
    yield submitted
    scan_jobs._payloads.clear()


class TestTheUploadReturnsImmediately:
    """C'est toute la raison d'être du travail suivi."""

    def test_a_queued_job_comes_back_not_a_report(self, no_real_worker):
        svc, _ = service()
        out = svc.queue(
            campaign(), payload=b"%PDF", filename="pile.pdf",
            content_type="application/pdf",
        )
        assert out["status"] == "QUEUED"
        assert out["isDone"] is False
        assert out["id"]

    def test_the_reading_is_handed_to_the_worker(self, no_real_worker):
        svc, _ = service()
        out = svc.queue(
            campaign(), payload=b"%PDF", filename="pile.pdf", content_type="",
        )
        assert len(no_real_worker) == 1
        assert no_real_worker[0]["job_id"] == out["id"]
        assert no_real_worker[0]["actor"] == "chef@usine"

    def test_the_pdf_waits_in_memory_for_the_worker(self, no_real_worker):
        svc, _ = service()
        out = svc.queue(
            campaign(), payload=b"%PDF-charge", filename="p.pdf", content_type="",
        )
        assert scan_jobs._payloads[out["id"]] == b"%PDF-charge"


class TestTheRefusalHappensAtTheUpload:
    """Un refus doit répondre pendant que l'utilisateur regarde.

    Passer la garde dans le fil de travail le ferait apparaître deux minutes
    plus tard, dans un travail en échec que personne n'a de raison d'ouvrir.
    """

    def test_a_reader_is_refused_before_anything_is_queued(self, no_real_worker):
        svc, jobs = service(actor="tiers@usine")
        with pytest.raises(PermissionDeniedError):
            svc.queue(
                campaign(), payload=b"%PDF", filename="p.pdf", content_type="",
            )
        assert jobs.rows == {}
        assert no_real_worker == []

    def test_a_frozen_phase_is_refused_the_same_way(self, no_real_worker):
        from inventory.errors import InventoryError

        svc, jobs = service()
        with pytest.raises(InventoryError):
            svc.queue(
                campaign(CampaignStatus.CLOSED), payload=b"%PDF",
                filename="p.pdf", content_type="",
            )
        assert jobs.rows == {}


class TestReadingTheJob:
    def test_a_job_of_another_campaign_is_not_readable(self, no_real_worker):
        svc, jobs = service()
        job_id = jobs.create(
            campaign_id="camp-VOISINE", filename="p.pdf", content_type="",
            overwrite_reviewed=False, actor="chef@usine",
        )
        with pytest.raises(NotFoundError):
            svc.get(campaign(), job_id)

    def test_an_unknown_job_is_a_404_not_a_crash(self, no_real_worker):
        svc, _ = service()
        with pytest.raises(NotFoundError):
            svc.get(campaign(), "job-inconnu")

    def test_the_percentage_is_computed_once_here(self, no_real_worker):
        """Trois écrans qui le calculeraient chacun en donneraient trois."""
        svc, jobs = service()
        job_id = jobs.create(
            campaign_id="camp-1", filename="p.pdf", content_type="",
            overwrite_reviewed=False, actor="chef@usine",
        )
        jobs.rows[job_id].update(sheets_total=8, sheets_done=6)
        assert svc.get(campaign(), job_id)["percent"] == 75

    def test_no_sheet_yet_is_zero_per_cent_not_a_division_by_zero(
        self, no_real_worker
    ):
        svc, jobs = service()
        job_id = jobs.create(
            campaign_id="camp-1", filename="p.pdf", content_type="",
            overwrite_reviewed=False, actor="chef@usine",
        )
        assert svc.get(campaign(), job_id)["percent"] == 0

    @pytest.mark.parametrize(
        ("status", "done"),
        [("QUEUED", False), ("RUNNING", False), ("SUCCEEDED", True),
         ("FAILED", True)],
    )
    def test_is_done_covers_both_terminal_states(
        self, no_real_worker, status, done
    ):
        """Sans ce drapeau, chaque écran connaîtrait la liste des statuts
        terminaux, et l'un d'eux finirait par en oublier un — l'échec."""
        svc, jobs = service()
        job_id = jobs.create(
            campaign_id="camp-1", filename="p.pdf", content_type="",
            overwrite_reviewed=False, actor="chef@usine",
        )
        jobs.rows[job_id]["status"] = status
        assert svc.get(campaign(), job_id)["isDone"] is done


class TestAContainerThatDisappeared:
    """Le PDF vit en mémoire : un travail en cours après un redémarrage
    appartient à une instance qui n'existe plus."""

    def test_running_jobs_are_failed_with_a_reason(self, monkeypatch):
        jobs = FakeJobs()
        for status in ("QUEUED", "RUNNING", "SUCCEEDED"):
            job_id = jobs.create(
                campaign_id="c", filename="p", content_type="",
                overwrite_reviewed=False, actor="a",
            )
            jobs.rows[job_id]["status"] = status
        monkeypatch.setattr(
            scan_jobs, "ServiceContext",
            lambda **kw: SimpleNamespace(scan_jobs=jobs),
        )
        assert scan_jobs.abandon_orphan_jobs() == 2
        statuses = [r["status"] for r in jobs.rows.values()]
        assert statuses == ["FAILED", "FAILED", "SUCCEEDED"]
        assert "redémarré" in jobs.rows["job-1"]["error"]

    def test_an_unreachable_database_does_not_stop_start_up(self, monkeypatch):
        """Le démarrage ne doit pas dépendre de cette reprise."""
        def boom(**_):
            raise RuntimeError("base injoignable")

        monkeypatch.setattr(scan_jobs, "ServiceContext", boom)
        assert scan_jobs.abandon_orphan_jobs() == 0


class TestTheProgressIsReported:
    """Six minutes de silence sont indistinguables d'une panne."""

    def test_every_stage_is_announced(self, monkeypatch):
        from inventory.ai.sheet_extraction import in_parallel

        seen: list[int] = []
        in_parallel(lambda n: n, [1, 2, 3, 4], 2, on_done=seen.append)
        assert sorted(seen) == [1, 2, 3, 4]

    def test_the_counter_is_safe_under_concurrency(self):
        """Quatre fils qui incrémentent sans verrou perdent des unités."""
        from inventory.ai.sheet_extraction import in_parallel

        seen: list[int] = []
        guard = threading.Lock()

        def note(n: int) -> None:
            with guard:
                seen.append(n)

        def work(_: int) -> None:
            time.sleep(0.001)

        in_parallel(work, list(range(40)), 8, on_done=note)
        assert sorted(seen) == list(range(1, 41))

    def test_a_progress_write_that_fails_does_not_lose_the_reading(self):
        """L'avancement n'est pas le travail."""
        from inventory.ai.sheet_extraction import in_parallel

        def refuse(_: int) -> None:
            raise RuntimeError("écriture d'avancement impossible")

        assert in_parallel(lambda n: n * 2, [1, 2], 2, on_done=refuse) == [2, 4]


# --------------------------------------------------------------------------- #
# Le scan d'UNE feuille, mené comme le scan d'une pile
# --------------------------------------------------------------------------- #

class FakeSheets:
    """Juste ce qu'il faut pour dire à quelle campagne une feuille appartient."""

    def __init__(self, owners: dict[str, str]) -> None:
        self.owners = owners

    def get_sheet(self, sheet_id: str):
        if sheet_id not in self.owners:
            raise NotFoundError("Feuille inconnue.")
        return SimpleNamespace(id=sheet_id, campaign_id=self.owners[sheet_id])


def sheet_service(owners: dict[str, str] | None = None, *, actor: str = "chef@usine"):
    svc, jobs = service(actor=actor)
    svc.ctx.sheets = FakeSheets(owners or {"sheet-1": "camp-1"})
    return svc, jobs


class TestScanningOneSheetIsAJobToo:
    """Une feuille seule est plus courte à lire qu'une pile, mais pas courte.

    Rendu des pages, un appel au modèle de vision, écriture des lignes : de dix
    secondes à plus d'une minute. Tenue dans la requête de chargement, l'attente
    n'offrait rien à regarder — un bouton grisé qui ne distingue pas un travail
    qui avance d'un appel qui a calé.
    """

    def test_the_upload_returns_a_job_carrying_the_sheet(self, no_real_worker):
        svc, _ = sheet_service()
        out = svc.queue(
            campaign(), payload=b"%PDF", filename="f.pdf",
            content_type="application/pdf", sheet_id="sheet-1",
        )
        assert out["status"] == "QUEUED"
        assert out["isDone"] is False
        assert out["sheetId"] == "sheet-1"

    def test_a_stack_carries_no_sheet(self, no_real_worker):
        """C'est ce champ, et lui seul, qui sépare les deux lectures."""
        svc, _ = sheet_service()
        out = svc.queue(campaign(), payload=b"%PDF", filename="p.pdf", content_type="")
        assert out["sheetId"] is None

    def test_a_sheet_of_another_campaign_is_refused_at_the_upload(
        self, no_real_worker
    ):
        """Le refus doit répondre au chargement, pas dans un travail en échec
        que personne n'a de raison d'ouvrir deux minutes plus tard."""
        svc, jobs = sheet_service({"sheet-9": "camp-VOISINE"})
        with pytest.raises(NotFoundError):
            svc.queue(
                campaign(), payload=b"%PDF", filename="f.pdf",
                content_type="", sheet_id="sheet-9",
            )
        assert jobs.rows == {}
        assert no_real_worker == []

    def test_a_reader_is_refused_before_the_sheet_is_even_looked_up(
        self, no_real_worker
    ):
        svc, jobs = sheet_service(actor="tiers@usine")
        with pytest.raises(PermissionDeniedError):
            svc.queue(
                campaign(), payload=b"%PDF", filename="f.pdf",
                content_type="", sheet_id="sheet-1",
            )
        assert jobs.rows == {}


class TestFindingAReadingAgainAfterARefresh:
    """Un navigateur rafraîchi perd l'identifiant du travail.

    Sans ce rappel, l'écran revient inerte et invite à relancer un scan qui
    tourne déjà — deux lectures concurrentes sur la même feuille, dont la
    seconde écrase la première.
    """

    def test_the_last_job_of_the_sheet_comes_back(self, no_real_worker):
        svc, _ = sheet_service()
        first = svc.queue(
            campaign(), payload=b"%PDF", filename="a.pdf",
            content_type="", sheet_id="sheet-1",
        )
        second = svc.queue(
            campaign(), payload=b"%PDF", filename="b.pdf",
            content_type="", sheet_id="sheet-1",
        )
        found = svc.latest_for_sheet(campaign(), "sheet-1")
        assert found is not None
        assert found["id"] == second["id"] != first["id"]

    def test_a_sheet_never_scanned_gives_nothing_rather_than_a_404(
        self, no_real_worker
    ):
        """L'écran interroge à chaque ouverture : une erreur ici ferait clignoter
        un toast rouge sur toute feuille jamais scannée."""
        svc, _ = sheet_service()
        assert svc.latest_for_sheet(campaign(), "sheet-1") is None


class TestTheWorkerReadsWhatTheJobNames:
    """Une colonne sépare les deux lectures ; le fil doit la lire.

    Se tromper d'aiguillage ici est silencieux et coûteux : une feuille traitée
    comme une pile part au routage, aucune page ne porte le pied de page attendu,
    et le travail se termine « réussi » avec zéro quantité écrite.
    """

    def worker(self, monkeypatch, *, sheet_id):
        """Fait tourner `_run` sur une doublure, et note ce qui a été appelé."""
        jobs = FakeJobs()
        job_id = jobs.create(
            campaign_id="camp-1", sheet_id=sheet_id, filename="f.pdf",
            content_type="application/pdf", overwrite_reviewed=False,
            actor="chef@usine",
        )
        scan_jobs._payloads[job_id] = b"%PDF"
        calls: list[tuple[str, tuple[Any, ...]]] = []

        class FakeGeneric:
            def __init__(self, _ctx): pass

            def extract_from_scan(self, campaign, sheet, **kw):
                calls.append(("one", (campaign.id, sheet)))
                kw["on_progress"](step="Lecture par le modèle")
                return {"report": {"counted": 3}}

            def extract_from_multi_scan(self, campaign, **kw):
                calls.append(("many", (campaign.id,)))
                return {"sheetsProcessed": [1, 2]}

        monkeypatch.setattr(
            "inventory.services.scan_service.ScanService", FakeGeneric
        )
        monkeypatch.setattr(
            scan_jobs, "ServiceContext",
            lambda **kw: SimpleNamespace(
                scan_jobs=jobs,
                campaigns=SimpleNamespace(get=lambda _id: campaign()),
            ),
        )
        scan_jobs._run(
            job_id=job_id, campaign_id="camp-1", actor="chef@usine", request_id="r",
        )
        return jobs, job_id, calls

    def test_a_job_naming_a_sheet_reads_that_sheet(self, monkeypatch):
        jobs, job_id, calls = self.worker(monkeypatch, sheet_id="sheet-1")
        assert calls == [("one", ("camp-1", "sheet-1"))]
        assert jobs.rows[job_id]["status"] == "SUCCEEDED"
        assert jobs.rows[job_id]["report"] == {"counted": 3}

    def test_a_job_naming_no_sheet_reads_the_whole_stack(self, monkeypatch):
        _, _, calls = self.worker(monkeypatch, sheet_id=None)
        assert calls == [("many", ("camp-1",))]

    def test_the_report_of_a_single_sheet_is_the_extraction_report(self, monkeypatch):
        """Pas l'enveloppe `{report, sheet}` : l'écran lit un rapport, et il en
        lit un seul, quel que soit le chemin qui l'a produit."""
        jobs, job_id, _ = self.worker(monkeypatch, sheet_id="sheet-1")
        assert "report" not in jobs.rows[job_id]["report"]

    def test_the_progress_of_a_single_sheet_is_written(self, monkeypatch):
        jobs, _, _ = self.worker(monkeypatch, sheet_id="sheet-1")
        assert any(
            "Lecture par le modèle" in (c.get("step") or "")
            for c in jobs.progress_calls
        )
