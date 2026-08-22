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
               overwrite_reviewed, actor) -> str:
        self._seq += 1
        job_id = f"job-{self._seq}"
        self.rows[job_id] = {
            "id": job_id, "campaign_id": campaign_id, "filename": filename,
            "content_type": content_type, "overwrite_reviewed": overwrite_reviewed,
            "created_by": actor, "status": "QUEUED", "step": "",
            "total_pages": 0, "pages_routed": 0, "sheets_total": 0,
            "sheets_done": 0, "report": {}, "error": "",
            "created_at": None, "started_at": None, "finished_at": None,
        }
        return job_id

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
