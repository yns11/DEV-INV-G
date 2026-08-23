"""Un scan a des limites, et pas seulement une taille de fichier.

Le plafond de téléversement borne les **octets reçus**. Il ne dit rien de ce
qu'ils deviennent une fois lus, et c'est là que la mémoire part.

``render(scale=dpi/72)`` alloue son bitmap lui-même : la garde anti-bombe de
PIL — qui refuse une image dont l'en-tête annonce plus de 89 mégapixels — ne le
voit jamais. Un PDF de quelques kilo-octets peut déclarer une page de deux cents
pouces de côté ; à 150 dpi cela fait trente mille pixels par côté, soit neuf
cents mégaoctets pour **une** page, sur un conteneur qui en a six mille et les
partage entre tous ceux qui travaillent ce jour-là.

Le nombre de pages était déjà borné, et refusé franchement quand la pile est
trop épaisse. La résolution, elle, est **réduite** plutôt que refusée : un
MediaBox démesuré est presque toujours un artefact de scanner, et une feuille
rendue à cent dpi au lieu de cent cinquante reste lisible. Refuser priverait
l'utilisateur de sa lecture pour un défaut qui n'est pas le sien.
"""

from __future__ import annotations

import pytest

from inventory.ai.sheet_extraction import safe_scale
from inventory.config import Settings

#: Une page A4, en points PostScript (1/72 pouce).
A4 = (595.0, 842.0)

#: Ce que le plafond par défaut autorise : un A4 à 600 dpi tient dedans.
CEILING = Settings().scan_max_pixels


def pixels(size: tuple[float, float], scale: float) -> float:
    return (size[0] * scale) * (size[1] * scale)


class TestAnOrdinaryPageIsRenderedAsAsked:
    def test_an_a4_at_150_dpi_is_untouched(self):
        """La garde ne doit pas dégrader ce qu'elle n'a pas à dégrader."""
        assert safe_scale(*A4, dpi=150, ceiling=CEILING) == pytest.approx(150 / 72)

    @pytest.mark.parametrize("dpi", [72, 150, 200, 300])
    def test_every_usable_resolution_passes(self, dpi):
        assert safe_scale(*A4, dpi=dpi, ceiling=CEILING) == pytest.approx(dpi / 72)

    def test_an_a4_at_600_dpi_still_fits_the_default_ceiling(self):
        """Le plafond est choisi pour ne jamais gêner un scan légitime."""
        assert pixels(A4, safe_scale(*A4, dpi=600, ceiling=CEILING)) <= CEILING


class TestAnAbsurdPageIsReducedNotRefused:
    #: Deux cents pouces de côté — ce qu'un PDF forgé, ou un scanner mal réglé,
    #: peut déclarer sans que le fichier pèse quoi que ce soit.
    HUGE = (200 * 72.0, 200 * 72.0)

    def test_without_the_guard_it_would_be_nine_hundred_megapixels(self):
        """Le témoin : la faute, chiffrée."""
        assert pixels(self.HUGE, 150 / 72) > 800_000_000

    def test_the_render_is_brought_under_the_ceiling(self):
        scale = safe_scale(*self.HUGE, dpi=150, ceiling=CEILING)
        assert pixels(self.HUGE, scale) <= CEILING * 1.001

    def test_it_is_reduced_rather_than_refused(self):
        """Une feuille reste lisible à cent dpi ; ne pas la lire, non."""
        assert safe_scale(*self.HUGE, dpi=150, ceiling=CEILING) > 0

    def test_the_reduction_is_proportional(self):
        """Deux fois plus de pixels demandés, l'échelle divisée par racine de deux."""
        one = safe_scale(*self.HUGE, dpi=150, ceiling=CEILING)
        two = safe_scale(*self.HUGE, dpi=150, ceiling=CEILING * 4)
        assert two == pytest.approx(one * 2, rel=1e-6)

    def test_a_page_exactly_at_the_ceiling_is_not_reduced(self):
        """La limite est atteignable, pas un seuil d'alarme."""
        import math

        side = math.sqrt(CEILING) / (150 / 72)
        assert safe_scale(side, side, dpi=150, ceiling=CEILING) == pytest.approx(
            150 / 72, rel=1e-6
        )


class TestTheDegenerateCases:
    @pytest.mark.parametrize("size", [(0.0, 842.0), (595.0, 0.0), (0.0, 0.0)])
    def test_a_page_with_no_area_asks_for_no_reduction(self, size):
        """Un PDF corrompu ne doit pas provoquer une division par zéro."""
        assert safe_scale(*size, dpi=150, ceiling=CEILING) == pytest.approx(150 / 72)

    def test_a_tiny_ceiling_still_yields_a_usable_scale(self):
        scale = safe_scale(*A4, dpi=150, ceiling=1_000_000)
        assert 0 < scale < 150 / 72
        assert pixels(A4, scale) <= 1_000_001


class TestTheCeilingIsConfigurable:
    def test_it_has_a_default_that_covers_a_600_dpi_a4(self):
        assert Settings().scan_max_pixels >= 35_000_000

    def test_the_environment_can_raise_it(self, monkeypatch):
        from inventory.config import get_settings

        monkeypatch.setenv("INV_SCAN_MAX_PIXELS", "80000000")
        get_settings.cache_clear()
        try:
            assert get_settings().scan_max_pixels == 80_000_000
        finally:
            monkeypatch.delenv("INV_SCAN_MAX_PIXELS", raising=False)
            get_settings.cache_clear()

    def test_it_cannot_be_set_absurdly_low(self):
        """Un plafond d'un pixel rendrait toute lecture impossible."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            Settings(INV_SCAN_MAX_PIXELS=100)


class TestTheRendererUsesIt:
    def source(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (
            root / "app" / "inventory" / "ai" / "sheet_extraction.py"
        ).read_text()

    def test_the_render_never_takes_the_raw_dpi_scale(self):
        """`scale=dpi / 72` était l'appel exact qui allouait sans borne."""
        assert "render(scale=dpi / 72" not in self.source()

    def test_it_asks_the_page_for_its_declared_size_first(self):
        source = self.source()
        assert "page.get_size()" in source
        assert "safe_scale(width, height" in source

    def test_a_reduction_is_logged_rather_than_silent(self):
        """Une lecture moins fine que demandée doit se voir dans les journaux."""
        source = self.source()
        assert "rendue à %.0f dpi au lieu de %d" in source

    def test_the_page_ceiling_is_read_from_the_settings(self):
        assert "get_settings().scan_max_pixels" in self.source()
