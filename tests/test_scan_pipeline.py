"""Ce qui rend une pile de cent feuilles traitable.

Un audit externe a mesuré la lecture d'un scan multi-feuilles et conclu que le
coût dominant n'était pas la vitesse du modèle mais la forme du traitement. Le
code lui a donné raison sur trois points, et en a révélé un quatrième que
personne ne cherchait :

* **la pile était tronquée en silence.** Le plafond était de quarante pages ;
  cent feuilles recto-verso en font deux cents. Les cent soixante dernières
  disparaissaient avec une ligne de journal, et le rapport annonçait un succès ;
* **le routage partait en un seul appel**, portant toutes les pages entières —
  vingt-sept mégaoctets une fois encodées, pour lire une ligne de pied de page ;
* **les feuilles étaient lues l'une après l'autre**, cent latences additionnées ;
* **le budget de sortie était fixe** à 8192 tokens, pour une feuille de trente
  lignes comme pour une de trois cents.

Ces contrôles portent sur les bornes et sur la forme, pas sur la qualité de la
lecture — celle-ci se juge sur du papier réel, pas ici.
"""

from __future__ import annotations

import io
import time

import pytest

from inventory.ai.sheet_extraction import (
    FOOTER_BAND,
    extraction_tokens,
    footer_strips,
    in_parallel,
    page_count,
    render_pdf_pages,
)

# --------------------------------------------------------------------------- #
# Un PDF réel, produit par l'application elle-même
# --------------------------------------------------------------------------- #

def counting_sheet(lines: int = 30) -> bytes:
    """La feuille telle qu'elle sort de l'imprimante, pied de page compris."""
    import datetime as dt

    from inventory.reporting.exports import build_counting_sheet_pdf

    return build_counting_sheet_pdf(
        campaign_code="INV-2026-09",
        campaign_label="INV-2026-09 — Inventaire septembre",
        count_date=dt.date(2026, 9, 1),
        zone_code="FI ASSY",
        zone_label="Assemblage FI",
        pass_no=1,
        sheet_id="aaaaaaaa-1111-2222-3333-444444444444",
        lines=[
            {
                "item_number": f"MASS-{40000 + n}",
                "name": f"CARTER ARRIERE ALU M3 REV{n:02d}",
                "section": "LINE_SIDE",
                "unit": "PCE",
                "qty": None,
            }
            for n in range(lines)
        ],
    )


class TestTheFooterBand:
    """Le routage ne lit qu'une ligne : il ne doit recevoir qu'elle."""

    def test_the_band_is_a_fraction_of_the_page(self):
        pages = render_pdf_pages(counting_sheet(), max_pages=4)
        strips = footer_strips(pages)
        assert len(strips) == len(pages)
        assert sum(len(s) for s in strips) < sum(len(p) for p in pages) / 5

    def test_the_band_is_the_bottom_of_the_page_pixel_for_pixel(self):
        """La bande du haut a la même taille et ne contient pas l'identité.

        Vérifier la taille seule laissait passer un découpage du haut : même
        largeur, même hauteur, et aucun pied de page dedans — le routage ne
        rendrait plus que des pages non attribuées, sans rien qui l'explique.
        On compare donc les pixels à la région attendue de la page.
        """
        from PIL import Image

        pages = render_pdf_pages(counting_sheet(), max_pages=1)
        page = Image.open(io.BytesIO(pages[0])).convert("L")
        strip = Image.open(io.BytesIO(footer_strips(pages)[0])).convert("L")

        assert strip.width == page.width
        assert strip.height == pytest.approx(page.height * FOOTER_BAND, rel=0.05)

        top = page.height - strip.height
        bottom_band = page.crop((0, top, page.width, page.height))
        assert strip.tobytes() == bottom_band.tobytes()

    def test_the_band_carries_ink_where_the_footer_is_printed(self):
        """Une bande blanche est une bande prise au mauvais endroit."""
        from PIL import Image

        pages = render_pdf_pages(counting_sheet(), max_pages=1)
        strip = Image.open(io.BytesIO(footer_strips(pages)[0])).convert("L")
        # L'identité est imprimée à 8 mm du bas d'un A4 (297 mm), soit au tiers
        # bas de la bande de 10 %. C'est là qu'il doit y avoir de l'encre.
        height = strip.height
        band = strip.crop((0, int(height * 0.55), strip.width, int(height * 0.85)))
        dark = sum(1 for pixel in band.tobytes() if pixel < 200)
        assert dark > 200, "aucun texte à l'endroit où le pied de page est imprimé"

    def test_an_unreadable_page_goes_through_whole(self):
        """Mieux vaut une page entière à router qu'une page perdue."""
        strips = footer_strips([b"ceci n'est pas une image"])
        assert strips == [b"ceci n'est pas une image"]


class TestRenderingCost:
    """Le rendu est du CPU pur, payé sur chaque page de la pile."""

    def test_pages_are_rendered_in_grayscale(self):
        """Une feuille de comptage est du trait noir sur blanc : trois canaux
        pour une information monochrome, c'est trois fois les octets."""
        from PIL import Image

        pages = render_pdf_pages(counting_sheet(), max_pages=1)
        assert Image.open(io.BytesIO(pages[0])).mode == "L"

    def test_the_page_count_is_read_without_rendering(self):
        """Refuser une pile trop épaisse ne doit pas coûter son rendu."""
        pdf = counting_sheet()
        started = time.perf_counter()
        total = page_count(pdf)
        assert total >= 1
        assert time.perf_counter() - started < 1.0


class TestTokenBudget:
    """8192 tokens pour trente lignes, c'est une réservation qui se paie."""

    def test_a_short_sheet_gets_a_short_budget(self):
        assert extraction_tokens(30) < 4500

    def test_the_budget_grows_with_the_sheet(self):
        assert extraction_tokens(60) > extraction_tokens(30)

    def test_an_empty_sheet_still_has_room_to_answer(self):
        """Le modèle doit pouvoir rendre l'enveloppe JSON et les métadonnées."""
        assert extraction_tokens(0) >= 500

    def test_a_very_long_sheet_stays_under_the_ceiling(self):
        assert extraction_tokens(10_000) == 8192


class TestBoundedConcurrency:
    """Les appels au modèle en parallèle, sans que rien ne se perde."""

    def test_the_order_of_the_results_is_the_order_of_the_items(self):
        """Une feuille dont l'appel revient en premier ne prend pas la place
        d'une autre dans le rapport."""
        def slow(n: int) -> int:
            time.sleep(0.02 if n == 0 else 0.001)
            return n

        assert in_parallel(slow, list(range(6)), 4) == list(range(6))

    def test_a_failure_is_a_value_at_its_own_index(self):
        """Sur cent feuilles, un refus du modèle sur la douzième ne peut pas
        emporter les quatre-vingt-huit autres."""
        def sometimes(n: int) -> int:
            if n == 2:
                raise RuntimeError("refus du modèle")
            return n * 10

        out = in_parallel(sometimes, [0, 1, 2, 3], 4)
        assert out[0] == 0 and out[1] == 10 and out[3] == 30
        assert isinstance(out[2], RuntimeError)

    def test_it_actually_runs_in_parallel(self):
        """Sinon tout le reste de ce module ne sert à rien."""
        def wait(_: int) -> None:
            time.sleep(0.05)

        started = time.perf_counter()
        in_parallel(wait, list(range(8)), 4)
        elapsed = time.perf_counter() - started
        # Huit tâches de 50 ms : ~400 ms en série, ~100 ms à quatre de front.
        assert elapsed < 0.30

    def test_one_worker_is_a_plain_loop(self):
        calls: list[int] = []
        in_parallel(calls.append, [1, 2, 3], 1)
        assert calls == [1, 2, 3]

    def test_nothing_to_do_is_not_an_error(self):
        assert in_parallel(lambda n: n, [], 4) == []


class TestTheStackCeiling:
    """Cent feuilles recto-verso font deux cents pages."""

    def test_the_default_ceiling_covers_a_hundred_sheets(self):
        from inventory.config import Settings

        assert Settings().scan_max_pages >= 200

    def test_a_printed_sheet_really_takes_more_than_one_page(self):
        """C'est ce qui rendait le plafond de quarante pages si trompeur : il
        annonçait quarante feuilles et n'en portait que vingt."""
        assert page_count(counting_sheet(lines=30)) > 1


class TestTheRetryBudget:
    """Trois tentatives à 90 secondes, c'est cinq minutes sur un seul appel."""

    def test_only_one_retry(self):
        from inventory.ai.client import LlmClient

        stop = LlmClient.complete.retry.stop
        assert getattr(stop, "max_attempt_number", None) == 2

    def test_a_model_refusal_is_not_retried(self):
        """La deuxième réponse serait la même, une minute plus tard."""
        from inventory.ai.client import _is_transient

        assert not _is_transient(_status_error(400))
        assert not _is_transient(_status_error(401))
        assert not _is_transient(_status_error(500))

    def test_a_queue_or_a_cold_start_is_retried(self):
        from inventory.ai.client import _is_transient

        assert _is_transient(_status_error(429))
        assert _is_transient(_status_error(503))
        assert _is_transient(_status_error(504))


def _status_error(status: int) -> Exception:
    exc = RuntimeError(f"HTTP {status}")
    exc.status_code = status  # type: ignore[attr-defined]
    return exc


class TestTheScanEndpoint:
    """Transcrire des chiffres manuscrits n'appelle aucun raisonnement."""

    def test_it_falls_back_to_the_general_endpoint(self):
        from inventory.config import Settings

        settings = Settings(INV_LLM_ENDPOINT="modele-general")
        assert settings.scan_endpoint == "modele-general"

    def test_a_dedicated_endpoint_wins(self):
        from inventory.config import Settings

        settings = Settings(
            INV_LLM_ENDPOINT="modele-general", INV_SCAN_LLM_ENDPOINT="vision-rapide"
        )
        assert settings.scan_endpoint == "vision-rapide"

    def test_the_scan_client_is_not_the_assistant_client(self):
        """Deux endpoints, deux négociations de paramètres, deux délais."""
        import inventory.ai.client as client_module

        client_module._client = None
        client_module._scan_client = None
        from inventory.config import Settings

        settings = Settings(
            INV_LLM_ENDPOINT="modele-general", INV_SCAN_LLM_ENDPOINT="vision-rapide"
        )
        assert client_module.get_llm_client(settings) is not (
            client_module.get_scan_client(settings)
        )
        assert client_module.get_scan_client(settings).endpoint == "vision-rapide"
        client_module._client = None
        client_module._scan_client = None


# --------------------------------------------------------------------------- #
# Le service, bout à bout
# --------------------------------------------------------------------------- #

def multi_scan_service(monkeypatch, *, routing, results, sheets_count=2):
    """Le service de scan multi-feuilles, avec un extracteur en doublure."""
    import datetime as dt
    from types import SimpleNamespace
    from typing import cast

    from conftest import with_access

    from inventory.ai.sheet_extraction import ExtractionResult, PageRouting
    from inventory.config import Settings
    from inventory.domain.enums import CampaignStatus, SheetPass, SheetStatus
    from inventory.domain.models import Campaign, CountSheet, Zone
    from inventory.services import generic_service as module

    campaign = Campaign(
        id="camp-1", code="INV-2026-09", label="Inventaire",
        count_date="2026-09-01", status=CampaignStatus.COUNTING,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )
    zones = [Zone(id=f"z-{n}", campaign_id="camp-1", code=f"ZONE-{n}") for n in range(sheets_count)]
    sheets = [
        CountSheet(id=f"s-{n}", campaign_id="camp-1", zone_id=f"z-{n}",
                   pass_no=SheetPass.PASS_1, status=SheetStatus.PENDING)
        for n in range(sheets_count)
    ]
    lines_by_sheet = {
        s.id: [
            module.CountSheetLine(
                id=f"{s.id}-l1", sheet_id=s.id, campaign_id="camp-1",
                item_number="MASS-1",
            )
        ]
        for s in sheets
    }
    written: list[str] = []

    class _Extractor:
        """Rend ce que le test a prévu pour chaque feuille — résultat ou panne."""

        def __init__(self, *a, **k) -> None:
            pass

        def route_pages(self, **kwargs) -> PageRouting:
            return routing

        def expected_from_items(self, lines, items):
            return list(lines)

        def extract(self, *, sheet_id: str, **kwargs):
            outcome = results[sheet_id]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        extract_free_entry = extract

    import inventory.ai as ai_module

    monkeypatch.setattr(ai_module, "SheetExtractor", _Extractor)

    ctx = SimpleNamespace(
        actor="chef@usine",
        settings=Settings(),
        progress=lambda c: SimpleNamespace(
            items=10, zones=2, book_stock_lines=5, book_stock_frozen=True
        ),
        evidence=SimpleNamespace(put=lambda *a, **k: "/Volumes/x/scan.pdf"),
        sheets=SimpleNamespace(
            list_zones=lambda cid: zones,
            list_sheets=lambda cid: sheets,
            lines_by_sheet=lambda cid: lines_by_sheet,
            replace_sheet_lines=lambda sid, lines, *, actor: written.append(sid),
            update_sheet=lambda sid, **k: None,
        ),
        referentials=SimpleNamespace(items_by_number=lambda cid: {}),
        record=lambda **kw: "evt",
    )
    with_access(ctx)
    return module.GenericService(cast(object, ctx)), campaign, written, ExtractionResult


class TestTheStackIsNotTruncatedInSilence:
    """Le défaut le plus grave du pipeline, et le seul qui perdait des données."""

    def test_a_stack_over_the_ceiling_is_refused_by_name(self, monkeypatch):
        from inventory.ai.sheet_extraction import PageRouting
        from inventory.errors import ValidationError

        service, campaign, _, _ = multi_scan_service(
            monkeypatch, routing=PageRouting(), results={}
        )
        # Une pile de deux pages, un plafond d'une : le refus doit nommer les
        # deux nombres, pas rendre un rapport de succès sur la moitié.
        service.ctx.settings.scan_max_pages = 1
        with pytest.raises(ValidationError) as caught:
            service.extract_from_multi_scan(
                campaign, payload=counting_sheet(), filename="pile.pdf",
                content_type="application/pdf",
            )
        assert "2 pages" in str(caught.value)
        assert caught.value.details["maxPages"] == 1

    def test_the_refusal_says_what_to_do(self, monkeypatch):
        from inventory.ai.sheet_extraction import PageRouting
        from inventory.errors import ValidationError

        service, campaign, _, _ = multi_scan_service(
            monkeypatch, routing=PageRouting(), results={}
        )
        service.ctx.settings.scan_max_pages = 1
        with pytest.raises(ValidationError) as caught:
            service.extract_from_multi_scan(
                campaign, payload=counting_sheet(), filename="pile.pdf",
                content_type="application/pdf",
            )
        assert "deux fois" in str(caught.value)


class TestOneSheetDoesNotLoseTheStack:
    """Cent feuilles, une qui échoue : quatre-vingt-dix-neuf doivent aboutir."""

    def test_a_failed_sheet_is_named_and_the_others_are_written(self, monkeypatch):
        from inventory.ai.sheet_extraction import ExtractionResult, PageRouting

        service, campaign, written, _ = multi_scan_service(
            monkeypatch,
            routing=PageRouting(pages_by_sheet={"s-0": [0], "s-1": [1]}),
            results={
                "s-0": RuntimeError("le modèle a refusé cette page"),
                "s-1": ExtractionResult(pages=1, tokens_used=42),
            },
        )
        report = service.extract_from_multi_scan(
            campaign, payload=counting_sheet(), filename="pile.pdf",
            content_type="application/pdf",
        )
        assert written == ["s-1"]
        assert [f["sheetId"] for f in report["sheetsFailed"]] == ["s-0"]
        assert "refusé" in report["sheetsFailed"][0]["reason"]
        assert [p["sheetId"] for p in report["sheetsProcessed"]] == ["s-1"]

    def test_the_report_carries_the_timings(self, monkeypatch):
        from inventory.ai.sheet_extraction import PageRouting

        service, campaign, _, _ = multi_scan_service(
            monkeypatch, routing=PageRouting(), results={}
        )
        report = service.extract_from_multi_scan(
            campaign, payload=counting_sheet(), filename="pile.pdf",
            content_type="application/pdf",
        )
        timings = report["timings"]
        assert timings["pdf_render_ms"] >= 0
        assert timings["routing_ms"] >= 0
        assert timings["totalMs"] >= 0
        assert timings["pages"] == 2
        assert timings["imageBytes"] > 0
        assert timings["maxWorkers"] >= 1


# --------------------------------------------------------------------------- #
# La lecture d'UNE feuille annonce elle aussi où elle en est
# --------------------------------------------------------------------------- #

def one_sheet_bench(monkeypatch, *, free_entry: bool = False, pages: int = 1):
    """Le banc de `extract_from_scan`, réduit à ce que l'avancement traverse."""
    import datetime as dt
    from types import SimpleNamespace
    from typing import cast

    from conftest import with_access

    from inventory.ai.sheet_extraction import ExtractionResult
    from inventory.config import Settings
    from inventory.domain.enums import CampaignStatus, SheetPass, SheetStatus
    from inventory.domain.models import Campaign, CountSheet, Zone
    from inventory.services import generic_service as module

    campaign = Campaign(
        id="camp-1", code="INV-2026-09", label="Inventaire",
        count_date="2026-09-01", status=CampaignStatus.COUNTING,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )
    zone = Zone(id="z-1", campaign_id="camp-1", code="FI ASSY", free_entry=free_entry)
    sheet = CountSheet(
        id="s-1", campaign_id="camp-1", zone_id="z-1",
        pass_no=SheetPass.PASS_1, status=SheetStatus.COUNTING,
    )
    expected = (
        []
        if free_entry
        else [
            module.CountSheetLine(
                id="s-1-l1", sheet_id="s-1", campaign_id="camp-1",
                item_number="MASS-1",
            )
        ]
    )

    class _Extractor:
        def __init__(self, *a, **k) -> None:
            pass

        def expected_from_items(self, lines, items):
            return list(lines)

        def extract(self, **kwargs):
            return ExtractionResult(pages=pages)

        extract_free_entry = extract

    import inventory.ai as ai_module

    monkeypatch.setattr(ai_module, "SheetExtractor", _Extractor)
    monkeypatch.setattr(
        ai_module, "render_pdf_pages", lambda *a, **k: [b"page"] * pages
    )

    ctx = SimpleNamespace(
        actor="chef@usine",
        settings=Settings(),
        progress=lambda c: SimpleNamespace(
            items=10, zones=2, book_stock_lines=5, book_stock_frozen=True
        ),
        evidence=SimpleNamespace(put=lambda *a, **k: "/Volumes/x/scan.pdf"),
        sheets=SimpleNamespace(
            get_sheet=lambda sid: sheet,
            list_zones=lambda cid: [zone],
            list_sheet_lines=lambda sid: expected,
            replace_sheet_lines=lambda sid, lines, *, actor: None,
            update_sheet=lambda sid, **k: None,
        ),
        referentials=SimpleNamespace(items_by_number=lambda cid: {}),
        record=lambda **kw: "evt",
    )
    with_access(ctx)
    return module.GenericService(cast(object, ctx)), campaign


class TestASingleSheetAnnouncesItsStages:
    """Dix secondes à une minute, et rien à regarder pendant ce temps.

    Le scan d'une feuille était resté dans la requête HTTP du chargement : le
    bouton disait « Lecture en cours… » et plus rien ne bougeait. Devenu un
    travail suivi, il doit dire *ce qu'il fait* — sinon l'écran d'avancement
    affiche une étape vide et n'apprend rien de plus qu'un bouton grisé.
    """

    def steps(self, monkeypatch, **kw) -> list[str]:
        service, campaign = one_sheet_bench(monkeypatch, **kw)
        seen: list[str] = []
        service.extract_from_scan(
            campaign, "s-1", payload=b"%PDF", filename="f.pdf",
            content_type="application/pdf",
            on_progress=lambda **p: seen.append(str(p.get("step") or "")),
        )
        return [s for s in seen if s]

    def test_every_stage_is_named(self, monkeypatch):
        steps = self.steps(monkeypatch)
        assert len(steps) >= 4, steps
        assert any("Archivage" in s for s in steps)
        assert any("Rendu" in s for s in steps)
        assert any("Lecture" in s for s in steps)
        assert any("Écriture" in s for s in steps)

    def test_the_reading_stage_says_what_it_is_reading(self, monkeypatch):
        """« Lecture par le modèle » seul ne distingue pas une feuille de trois
        lignes d'une de trois cents, alors que l'attente n'a rien à voir."""
        reading = next(s for s in self.steps(monkeypatch, pages=3) if "Lecture" in s)
        assert "3 page(s)" in reading
        assert "1 ligne(s) attendues" in reading

    def test_a_free_entry_sheet_has_no_expected_lines_to_announce(self, monkeypatch):
        """Annoncer « 0 ligne(s) attendues » sur une saisie libre décrirait un
        manque, alors que c'est la définition de la feuille."""
        reading = next(
            s for s in self.steps(monkeypatch, free_entry=True) if "Lecture" in s
        )
        assert "attendues" not in reading

    def test_the_last_word_is_that_it_is_over(self, monkeypatch):
        assert self.steps(monkeypatch)[-1] == "Terminé"

    def test_a_reading_without_a_reporter_still_works(self, monkeypatch):
        """L'avancement n'est pas le travail : le chemin sans suivi doit vivre."""
        service, campaign = one_sheet_bench(monkeypatch)
        out = service.extract_from_scan(
            campaign, "s-1", payload=b"%PDF", filename="f.pdf",
            content_type="application/pdf",
        )
        assert "report" in out
