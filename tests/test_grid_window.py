"""Une grande grille reste manœuvrable.

Le référentiel articles d'une campagne réelle fait des dizaines de milliers de
lignes, et la grille les mettait toutes dans le DOM. Une ligne de dix colonnes
fait onze éléments : à vingt mille lignes, deux cent mille éléments à
construire, à styler et à garder en mémoire, pour en montrer trente.

**Mesuré**, dans Chromium, sur cinq mille lignes — soit un quart du plafond :

===========================  ==============  ==============
                             sans fenêtre    avec fenêtre
===========================  ==============  ==============
cellules dans le DOM                 50 010             271
page prête (ms)                       4 586           1 136
un clic de tri (ms)                     571             228
===========================  ==============  ==============

Ce module contrôle deux choses. Le **calcul** de la fenêtre, qui est la seule
partie capable d'être fausse sans qu'on le voie : une erreur d'un cran affiche
les bonnes lignes au mauvais endroit, ou laisse un blanc en fin de liste, et on
l'attribue au navigateur avant de l'attribuer au calcul. Et la **frontière** :
que la fenêtre ne décide de rien d'autre que ce qui entre dans le DOM.

Le calcul est en TypeScript ; il est lu ici, et son arithmétique rejouée en
Python à partir des constantes du source. Un banc Vitest le prendra
directement — c'est un chantier à part, et l'audit le note comme tel.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRID = ROOT / "frontend" / "src" / "components" / "DataGrid.tsx"


def source() -> str:
    return GRID.read_text()


def constant(name: str) -> int:
    match = re.search(rf"^const {name} = (\d+)$", source(), re.M)
    assert match, f"{name} introuvable"
    return int(match.group(1))


# --------------------------------------------------------------------------- #
# Les constantes, et pourquoi elles valent ce qu'elles valent
# --------------------------------------------------------------------------- #

class TestTheThresholds:
    def test_small_grids_are_left_alone(self):
        """La fenêtre coûte une hypothèse — des lignes de hauteur égale — et
        une conséquence : Ctrl+F ne voit plus que les lignes rendues. Sur
        quarante lignes ce prix n'achète rien."""
        assert constant("VIRTUAL_FROM") >= 100

    def test_the_threshold_is_below_what_a_referential_holds(self):
        """Un référentiel de campagne se compte en milliers : le seuil doit
        tomber bien avant, sinon il ne sert jamais."""
        assert constant("VIRTUAL_FROM") <= 1000

    def test_there_is_an_overscan(self):
        """Sans marge, un défilement rapide montre du blanc le temps d'un rendu."""
        assert constant("OVERSCAN") >= 4

    def test_the_overscan_stays_cheap(self):
        assert constant("OVERSCAN") <= 50

    def test_a_fallback_row_height_exists(self):
        """Avant la première mesure, il faut bien rendre quelque chose."""
        assert 20 <= constant("ROW_HEIGHT_GUESS") <= 80

    def test_a_windowed_grid_always_gets_a_scrolling_frame(self):
        """Si c'est la page qui défile, le cadre ne bouge jamais et la fenêtre
        reste collée en haut : on ne verrait que les trente premières lignes."""
        assert constant("VIRTUAL_MAX_HEIGHT") > 0
        # La condition, pas seulement le repli : sans « || windowed », une
        # grille qui n'impose pas de hauteur n'obtient pas de cadre — et la
        # ligne de repli reste dans le source sans jamais s'appliquer.
        assert "maxHeight || windowed" in source()
        assert "maxHeight ?? VIRTUAL_MAX_HEIGHT" in source()


# --------------------------------------------------------------------------- #
# L'arithmétique, rejouée
# --------------------------------------------------------------------------- #

def window_of(
    *, total: int, scroll_top: float, row_height: float, viewport: float,
    overscan: int | None = None,
) -> dict[str, float]:
    """Le calcul de ``windowOf``, transcrit depuis le source TypeScript.

    Transcrit et non réimplémenté : chaque ligne correspond à une ligne du
    source, et le contrôle ci-dessous vérifie que le source dit bien cela.
    """
    if overscan is None:
        overscan = constant("OVERSCAN")
    height = row_height if row_height > 0 else constant("ROW_HEIGHT_GUESS")
    start = max(0, math.floor(scroll_top / height) - overscan)
    end = min(total, math.ceil((scroll_top + viewport) / height) + overscan)
    end = max(start, end)
    return {
        "start": start,
        "end": end,
        "before": start * height,
        "after": max(0, total - end) * height,
    }


class TestTheTranscriptionMatchesTheSource:
    """Un contrôle qui rejouerait un autre calcul ne prouverait rien."""

    @pytest.mark.parametrize(
        "fragment",
        [
            "const start = Math.max(0, Math.floor(scrollTop / height) - overscan)",
            "const end = Math.min(total, Math.ceil((scrollTop + viewport) / height)"
            " + overscan)",
            "before: start * height,",
            "after: Math.max(0, total - Math.max(start, end)) * height,",
            # Les deux gardes. Sans elles nommées ici, une transcription qui
            # les porte pour son compte laisserait passer leur disparition du
            # source : le contrôle vérifierait sa propre copie.
            "const height = rowHeight > 0 ? rowHeight : ROW_HEIGHT_GUESS",
            "end: Math.max(start, end),",
        ],
    )
    def test_the_line_is_there(self, fragment):
        assert fragment in source()

    def test_the_function_is_exported_so_it_can_be_tested_alone(self):
        """Enfermée dans le composant, elle ne se vérifierait qu'à travers un
        navigateur — c'est-à-dire rarement."""
        assert "export function windowOf(" in source()


class TestTheTopOfTheList:
    def test_at_rest_the_window_starts_at_the_first_row(self):
        got = window_of(total=5000, scroll_top=0, row_height=37, viewport=560)
        assert got["start"] == 0

    def test_nothing_is_reserved_above_the_first_row(self):
        got = window_of(total=5000, scroll_top=0, row_height=37, viewport=560)
        assert got["before"] == 0

    def test_the_visible_rows_are_all_rendered(self):
        """La fenêtre doit couvrir la hauteur visible, sinon le bas est blanc."""
        got = window_of(total=5000, scroll_top=0, row_height=37, viewport=560)
        assert got["end"] * 37 >= 560


class TestScrolling:
    def test_the_window_follows_the_scroll(self):
        got = window_of(total=5000, scroll_top=3700, row_height=37, viewport=560)
        assert got["start"] == 100 - constant("OVERSCAN")

    def test_what_is_above_is_reserved_exactly(self):
        """La cale doit valoir la hauteur des lignes absentes, au pixel près :
        une cale trop courte fait remonter le tableau sous le curseur."""
        got = window_of(total=5000, scroll_top=3700, row_height=37, viewport=560)
        assert got["before"] == got["start"] * 37

    def test_the_two_spacers_and_the_rows_add_up_to_the_whole_table(self):
        """C'est ce qui fait que la barre de défilement dit la vérité."""
        got = window_of(total=5000, scroll_top=3700, row_height=37, viewport=560)
        rendered = (got["end"] - got["start"]) * 37
        assert got["before"] + rendered + got["after"] == pytest.approx(5000 * 37)

    def test_the_last_row_is_reachable(self):
        """Le témoin du défaut le plus courant : une fenêtre qui s'arrête un
        cran trop tôt rend la fin de la liste inatteignable."""
        height, viewport, total = 37, 560, 5000
        bottom = total * height - viewport
        got = window_of(
            total=total, scroll_top=bottom, row_height=height, viewport=viewport
        )
        assert got["end"] == total
        assert got["after"] == 0

    def test_scrolling_past_the_end_does_not_produce_an_empty_window(self):
        """Un rebond élastique, ou une mesure en retard d'un rendu."""
        got = window_of(total=100, scroll_top=999_999, row_height=37, viewport=560)
        assert got["end"] >= got["start"]
        assert got["after"] >= 0


class TestTheDegenerateCases:
    def test_a_row_height_of_zero_does_not_divide_by_zero(self):
        """Elle vaut zéro entre le premier rendu et la première mesure."""
        got = window_of(total=5000, scroll_top=100, row_height=0, viewport=560)
        assert got["end"] > got["start"]

    def test_an_empty_list_yields_an_empty_window(self):
        got = window_of(total=0, scroll_top=0, row_height=37, viewport=560)
        assert (got["start"], got["end"], got["after"]) == (0, 0, 0)

    def test_a_list_shorter_than_the_viewport_is_rendered_whole(self):
        got = window_of(total=5, scroll_top=0, row_height=37, viewport=560)
        assert got["end"] == 5
        assert got["after"] == 0


# --------------------------------------------------------------------------- #
# La frontière : la fenêtre ne décide que du DOM
# --------------------------------------------------------------------------- #

class TestTheWindowDecidesNothingElse:
    """Une fenêtre qui déciderait de ce qu'on additionne ou de ce qu'on exporte
    serait un piège, pas une optimisation. Le contrôle porte sur le nom de la
    liste consultée : ``sorted`` est l'ensemble, ``shown`` la fenêtre."""

    def test_the_totals_are_computed_on_the_whole_filtered_set(self):
        assert "for (const row of sorted)" in source()

    def test_the_export_carries_the_whole_filtered_set(self):
        """« J'ai exporté et il manque des lignes » serait le pire des défauts :
        silencieux, et découvert dans un tableur."""
        assert "        : sorted" in source()

    def test_select_all_selects_the_whole_filtered_set(self):
        assert "new Set(sorted.map((row, index) => getRowId(row, index)))" in source()

    def test_the_row_count_at_the_foot_is_the_whole_filtered_set(self):
        assert "{sorted.length.toLocaleString('fr-FR')}" in source()

    def test_only_the_rendered_rows_come_from_the_window(self):
        assert "{shown.map((row, offset) => {" in source()

    def test_a_rendered_row_keeps_its_index_in_the_whole_list(self):
        """Sans ce décalage, éditer la première ligne visible modifierait la
        première ligne de la liste — quelqu'un d'autre, plus haut."""
        assert "const index = start + offset" in source()


class TestTheSpacersAreInvisibleToAssistiveTechnology:
    def test_they_are_hidden(self):
        """Deux cellules vides annoncées comme des lignes du tableau feraient
        dire au lecteur d'écran qu'il y en a deux de plus qu'il n'y en a."""
        assert source().count('<tr aria-hidden="true">') == 2

    def test_they_carry_no_border(self):
        """Une bordure sur une cale se lit comme une ligne vide."""
        assert source().count("padding: 0, border: 'none'") == 2


class TestTheMeasurementIsMadeAndNotAssumed:
    def test_the_row_height_is_measured_on_a_real_row(self):
        """`dense`, la densité du navigateur et le zoom la changent : une valeur
        écrite en dur décalerait progressivement la fenêtre du défilement."""
        assert "querySelector<HTMLElement>('tr[data-row]')" in source()
        assert "getBoundingClientRect().height" in source()

    def test_the_frame_height_is_observed_rather_than_read_once(self):
        """Replier un bloc change le nombre de lignes visibles."""
        assert "new ResizeObserver(measure)" in source()

    def test_a_browser_without_resize_observer_still_renders(self):
        assert "typeof ResizeObserver === 'undefined'" in source()

    def test_filtering_returns_to_the_top(self):
        """Sans cela, un filtre qui ramène la liste à dix lignes laisse le cadre
        défilé à la hauteur de vingt mille : la fenêtre calculée est vide, et
        l'écran montre un tableau vide sur un résultat qui ne l'est pas."""
        block = source()
        assert "if (scrollRef.current) scrollRef.current.scrollTop = 0" in block
        assert "}, [search, sort, filters])" in block
