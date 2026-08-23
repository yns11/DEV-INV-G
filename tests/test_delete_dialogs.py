"""Une suppression dit ce qu'elle emporte, et si elle se rattrape.

Trois suppressions passaient par ``window.confirm``. Le défaut n'est pas
d'apparence.

Il **bloque le fil du navigateur** : rien ne se rafraîchit tant que la boîte est
ouverte. Il n'accepte que du texte brut, ce qui pousse à résumer — « Supprimer
40 ligne(s) de feuille ? » ne dit ni lesquelles, ni si elles reviendront. Et
surtout il ne distingue pas le réversible de l'irréversible : une zone
supprimée emporte ses feuilles et les lignes qu'on a mis une matinée à
corriger, un lien de nomenclature se recharge depuis l'ERP en dix secondes.
Les deux posaient la même question, avec le même bouton, au même endroit.

Ces contrôles lisent le source de l'interface. Le dépôt n'a pas de banc de test
navigateur — l'audit le note comme un chantier à part — et ce qui se vérifie
ici est ce qu'un tel banc ne verrait pas non plus : qu'aucune suppression ne
reste sur l'ancienne boîte, et que chacune nomme ses conséquences.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
UI = SRC / "components" / "ui.tsx"

#: Les écrans qui suppriment quelque chose.
DELETING = ("features/zones.tsx", "features/Preparation.tsx", "features/Campaigns.tsx")


def code_of(relative: str) -> str:
    """Le source d'un module, commentaires de bloc et de ligne retirés.

    Les commentaires y citent `window.confirm` pour expliquer ce qui change ;
    les lire comme du code ferait échouer le contrôle sur sa propre explication.
    """
    from conftest import screen_source

    text = screen_source(relative)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("//", "*"))
    )


class TestNothingUsesTheBrowserBox:
    @pytest.mark.parametrize("module", DELETING)
    def test_no_screen_still_calls_confirm(self, module):
        assert "window.confirm" not in code_of(module)

    @pytest.mark.parametrize("module", DELETING)
    def test_no_screen_calls_the_bare_global_either(self, module):
        """`confirm(...)` sans `window.` fait exactement la même chose."""
        assert not re.search(r"(?<![.\w])confirm\s*\(", code_of(module))

    def test_no_screen_uses_alert_or_prompt(self, module=None):
        """Mêmes défauts, même fil bloqué."""
        for name in DELETING:
            source = code_of(name)
            assert not re.search(r"window\.(alert|prompt)\s*\(", source), name


class TestTheDialogSaysWhatGoes:
    def ui(self) -> str:
        return UI.read_text()

    def test_it_exists(self):
        assert "export function ConfirmDelete(" in self.ui()

    def test_it_names_what_disappears_in_the_title_and_the_button(self):
        """« Confirmer » ne dit rien ; « Supprimer la zone Z1 » le dit."""
        ui = self.ui()
        assert "title={`Supprimer ${what} ?`}" in ui
        assert "{pending ? 'Suppression…' : `Supprimer ${what}`}" in ui

    def test_it_lists_the_consequences(self):
        assert "Ce qui part avec :" in self.ui()

    def test_an_empty_list_is_stated_rather_than_left_blank(self):
        """« Rien d'autre » est une information ; un vide est un oubli."""
        assert "Rien d’autre ne disparaît avec." in self.ui()

    def test_it_distinguishes_the_reversible_from_the_rest(self):
        ui = self.ui()
        assert "Rattrapable" in ui
        assert "Sans retour depuis l’application" in ui

    def test_the_destructive_button_looks_destructive(self):
        assert 'variant="danger"' in self.ui()

    def test_it_cannot_be_clicked_twice(self):
        """Une double suppression sur un réseau lent est un classique."""
        assert "onClick={onConfirm} disabled={pending}" in self.ui().replace("\n", " ")


class TestEachSiteSaysSomethingTrue:
    def test_deleting_zones_names_the_sheets_and_the_counted_lines(self):
        source = code_of("features/zones.tsx")
        assert "feuille(s) de comptage" in source
        assert "des quantités déjà saisies sur" in source

    def test_it_only_mentions_counted_quantities_when_there_are_some(self):
        """Annoncer une perte qui n'existe pas apprend à ignorer l'avertissement."""
        source = code_of("features/zones.tsx")
        assert "counted.length" in source

    def test_deleting_a_bom_link_is_declared_reversible(self):
        """Il se recharge depuis l'ERP : le dire évite une inquiétude inutile."""
        source = code_of("features/Preparation.tsx")
        block = source[source.index("le lien ${removingLink.parent}") :][:600]
        assert "reversible" in block

    def test_deleting_sheet_lines_is_not(self):
        """Cette liste est un travail de préparation, pas une copie de l'ERP."""
        source = code_of("features/Preparation.tsx")
        block = source[source.index("ligne(s) de feuille`") :][:600]
        assert "reversible" not in block

    def test_deleting_sheet_lines_warns_about_the_quantities(self):
        source = code_of("features/Preparation.tsx")
        assert "Les quantités déjà saisies sur ces lignes partent avec elles." in source


class TestTheDialogIsUsedWhereverSomethingIsDeleted:
    @pytest.mark.parametrize("module", ["features/zones.tsx", "features/Preparation.tsx"])
    def test_the_screen_imports_it(self, module):
        assert "ConfirmDelete," in code_of(module)

    @pytest.mark.parametrize("module", ["features/zones.tsx", "features/Preparation.tsx"])
    def test_the_screen_renders_it(self, module):
        assert "<ConfirmDelete" in code_of(module)

    def test_the_campaign_screen_keeps_its_own_dialog(self):
        """Il en dit davantage — propriétaire, dépendances — et reste meilleur."""
        assert "DeleteCampaignModal" in code_of("features/Campaigns.tsx")
