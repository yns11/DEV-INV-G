"""Ce que l'écran fait de la mise en page — et ce qu'il en dit deux fois.

Deux dangers, et ils sont différents.

Le premier est le défaut habituel de ce dépôt : une colonne posée en base, un
paramètre ajouté au générateur, et aucun écran pour les relier. Ces contrôles
partent donc de l'écran et vérifient qu'il appelle bien ce qui existe.

Le second est propre au texte imprimé : les trois en-têtes par défaut vivent
côté serveur — c'est lui qui les dessine sur le PDF — **et** côté navigateur,
où l'écran d'aperçu les affiche en filigrane du champ vide. Deux copies d'une
même phrase finissent toujours par diverger, et la divergence ne se verrait
qu'en comparant une capture d'écran à une feuille sortie de l'imprimante. Le
dernier contrôle les compare ici.
"""

from __future__ import annotations

import re

from tests.conftest import screen_source

from inventory.reporting.exports import DEFAULT_SECTION_TITLES


class TestLEcranDeSaisieRendCompteDeLaMiseEnPage:
    """La feuille ouverte pour saisir montre le document, pas une liste."""

    def source(self) -> str:
        return screen_source("features/Generic.tsx")

    def test_le_genre_de_ligne_repart_avec_l_enregistrement(self):
        """Sinon un intertitre revient en ligne d'article sans référence.

        L'enregistrement remplace la feuille entière : ce que l'écran omet
        d'envoyer est effacé.
        """
        assert "lineKind:" in self.source()

    def test_et_le_texte_de_l_intertitre_aussi(self):
        assert "label: String(row.label" in self.source()

    def test_l_intertitre_s_affiche_en_toutes_lettres(self):
        assert "SUBSECTION" in self.source()

    def test_ses_cellules_ne_s_editent_pas(self):
        """Un champ de saisie sur un séparateur invite à en faire un article."""
        assert "editableRow" in self.source()


class TestLApercuEstBrancheSurCeQuiExiste:
    def source(self) -> str:
        return screen_source("features/Generic.tsx")

    def test_le_bouton_ouvrir_mene_a_l_apercu_en_preparation(self):
        source = self.source()
        assert "SheetLayoutModal" in source
        assert "PREPARATION" in source

    def test_l_apercu_enregistre_les_en_tetes(self):
        assert "setSectionLabels" in self.source()

    def test_et_l_appel_existe_dans_le_client(self):
        """« Existe mais n'est pas branché », dans l'autre sens.

        Un écran qui appelle une méthode absente du client ne compile pas ; un
        client qui porte une méthode que personne n'appelle est du code mort qui
        se lit comme une fonctionnalité livrée.
        """
        client = screen_source("lib/api.ts")
        assert "section-labels" in client


class TestLesTroisTextesParDefautSontLesMemesDesDeuxCotes:
    """Le serveur les imprime ; le navigateur les affiche en filigrane."""

    def browser_titles(self) -> dict[str, str]:
        source = screen_source("lib/format.ts")
        block = re.search(
            r"DEFAULT_SECTION_TITLES: Record<string, string> = \{(.*?)\n\}",
            source,
            re.S,
        )
        assert block, "DEFAULT_SECTION_TITLES introuvable dans format.ts"
        return {
            code: text.replace("\n", " ").strip()
            for code, text in re.findall(
                r"(\w+):\s*\n?\s*'((?:[^'\\]|\\.)*)'", block.group(1)
            )
        }

    def test_les_memes_sections(self):
        assert set(self.browser_titles()) == set(DEFAULT_SECTION_TITLES)

    def test_et_les_memes_phrases(self):
        """Mot pour mot : c'est une consigne de comptage, pas une étiquette.

        « MOM : OK — Si MEL ou STATORS PHEV… notez le numéro de Galia » dit au
        compteur ce qu'il doit faire. Une version raccourcie à l'écran et une
        version complète sur le papier, et la moitié des feuilles reviennent
        sans numéro de série.
        """
        assert self.browser_titles() == DEFAULT_SECTION_TITLES
