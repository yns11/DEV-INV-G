"""Les dépôts restent un module par agrégat.

Ce qu'était le fichier
----------------------
Près de trois mille lignes, quatorze dépôts à la suite, sans rien qui les relie
autrement que l'ordre. Rien n'y était faux ; deux choses y étaient pénibles.
Ouvrir « les dépôts » pour corriger une requête de comptage obligeait à
traverser le référentiel, l'audit et la réconciliation. Et deux personnes
travaillant sur deux agrégats différents se retrouvaient dans le même fichier à
chaque fusion.

Ce que ces contrôles tiennent
-----------------------------
Un découpage ne tient pas tout seul : la pente naturelle est qu'un module
regrossisse, qu'un dépôt en appelle un autre « juste pour cette requête », ou
qu'un nouveau module n'apparaisse nulle part. Les trois se corrigent une fois
et reviennent, sauf si quelque chose les refuse.

**La surface publique ne bouge pas.** C'est ce qui a rendu le découpage sûr :
le paquet réexporte exactement ce que le module exportait, sous les mêmes noms.
Ce contrôle-là vaut au-delà du découpage — il dit qu'aucun dépôt ne disparaît
de l'API interne sans qu'on le décide.

**Un agrégat n'en appelle pas un autre.** Deux dépôts qui se parlent
recréeraient le couplage que le découpage retire, et le feraient sans que la
taille des fichiers ne le montre.

**Aucun module ne redevient un fourre-tout.** Le plafond est haut : il ne
prétend pas dicter un style, il attrape le retour à l'état d'avant.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "app" / "inventory" / "db" / "repositories"

#: Les quatorze noms que la couche au-dessus connaît.
#:
#: Écrits ici plutôt que relus depuis le paquet : un contrôle qui lirait
#: ``__all__`` pour le comparer à lui-même passerait toujours.
PUBLIC = (
    "new_id",
    "AdjustmentRepository",
    "AnalysisRepository",
    "AuditRepository",
    "BackflushRepository",
    "BookStockRepository",
    "CampaignRepository",
    "ConsolidationRepository",
    "EarlyCountBatchRepository",
    "EarlyCountDriftRepository",
    "ErpJournalRepository",
    "EvidenceBlobRepository",
    "ImportBatchRepository",
    "JournalRepository",
    "OperationsRepository",
    "ReferentialRepository",
    "ScanJobRepository",
    "SheetRepository",
    "StockFlowRepository",
)

#: Le seul dépôt que ``inventory.db`` ne réexporte pas.
#:
#: Il ne sert aucun service : la sonde de métriques le charge à la demande,
#: parce qu'interroger la base sur son propre état n'est pas un cas d'usage
#: métier. Le nommer ici plutôt que l'ignorer évite qu'un contrôle passe en
#: silence sur une exception que personne n'a décidée.
APART = ("OperationsRepository",)

#: Au-delà, le module est redevenu ce qu'on vient de défaire.
#:
#: Huit cents lignes laissent de la marge au plus gros agrégat — les zones,
#: leurs feuilles et les arbitrages — sans laisser passer un fichier de trois
#: mille.
CEILING = 800


def modules() -> list[Path]:
    """Les modules d'agrégat, sans la façade ni les fondations."""
    return sorted(
        path for path in PACKAGE.glob("*.py")
        if path.name not in ("__init__.py", "_base.py")
    )


def classes_of(path: Path) -> list[str]:
    return [
        node.name for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef)
    ]


# --------------------------------------------------------------------------- #
# La surface publique
# --------------------------------------------------------------------------- #

class TestNothingLeftTheFrontDoor:
    def test_the_package_exports_exactly_the_known_names(self):
        from inventory.db import repositories

        assert sorted(repositories.__all__) == sorted(PUBLIC)

    @pytest.mark.parametrize("name", PUBLIC)
    def test_each_name_resolves(self, name):
        """Figurer dans ``__all__`` ne suffit pas : il faut que l'objet existe."""
        from inventory.db import repositories

        assert getattr(repositories, name, None) is not None, name

    @pytest.mark.parametrize("name", [n for n in PUBLIC if n != "new_id"])
    def test_each_repository_is_a_class(self, name):
        from inventory.db import repositories

        assert isinstance(getattr(repositories, name), type), name

    @pytest.mark.parametrize("name", [n for n in PUBLIC if n not in APART])
    def test_the_db_package_re_exports_it_too(self, name):
        """``inventory.db`` est la porte que les services franchissent."""
        import inventory.db as db

        assert getattr(db, name, None) is not None, name

    @pytest.mark.parametrize("name", APART)
    def test_the_exception_is_still_reachable_where_it_is_used(self, name):
        """Hors de la porte principale, mais pas hors d'atteinte."""
        from inventory.db import repositories

        assert getattr(repositories, name, None) is not None

    def test_every_class_in_the_package_is_exported(self):
        """Un dépôt qu'aucune façade ne nomme est un dépôt que personne ne
        trouve — et il finira recopié ailleurs."""
        defined = {
            name for path in modules() for name in classes_of(path)
            if not name.startswith("_")
        }
        assert defined <= set(PUBLIC), defined - set(PUBLIC)


# --------------------------------------------------------------------------- #
# Un agrégat par module
# --------------------------------------------------------------------------- #

class TestTheAggregatesStayApart:
    def test_there_is_more_than_one_module(self):
        """Un contrôle qui passerait sur le fichier unique d'avant ne
        contrôlerait rien."""
        assert len(modules()) >= 10

    @pytest.mark.parametrize(
        "path", modules(), ids=lambda p: p.stem
    )
    def test_no_module_imports_another_aggregate(self, path: Path):
        """Les fondations, oui ; un dépôt voisin, non.

        Un dépôt qui en appelle un autre « juste pour cette requête » recrée le
        couplage que le découpage retire, et le fait sans que la taille des
        fichiers ne le montre.
        """
        siblings = {p.stem for p in modules()} - {path.stem}
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                assert node.module not in siblings, (
                    f"{path.name} importe {node.module}"
                )

    @pytest.mark.parametrize("path", modules(), ids=lambda p: p.stem)
    def test_no_module_is_a_catch_all(self, path: Path):
        assert len(path.read_text().splitlines()) <= CEILING, path.name

    @pytest.mark.parametrize("path", modules(), ids=lambda p: p.stem)
    def test_every_module_carries_at_least_one_repository(self, path: Path):
        assert any(name.endswith("Repository") for name in classes_of(path)), path.name

    @pytest.mark.parametrize("path", modules(), ids=lambda p: p.stem)
    def test_every_module_says_what_it_holds(self, path: Path):
        """Sans phrase d'ouverture, treize fichiers valent une liste de noms.

        C'est la **première ligne** qui compte : c'est elle qu'un éditeur
        affiche à côté du fichier, et la seule que quelqu'un qui cherche où
        aller lira. Mesurer le texte entier laisserait passer un titre vidé
        au-dessus d'un renvoi resté intact.
        """
        docstring = ast.get_docstring(ast.parse(path.read_text())) or ""
        summary = docstring.strip().splitlines()[0] if docstring.strip() else ""
        assert len(summary) >= 25, f"{path.name} : « {summary} »"


# --------------------------------------------------------------------------- #
# Les fondations n'existent qu'une fois
# --------------------------------------------------------------------------- #

class TestTheFoundationsAreNotCopied:
    @pytest.mark.parametrize("name", ["_Base", "_NullContext"])
    def test_it_is_defined_once(self, name):
        """Recopiées, elles auraient dérivé — et c'est ainsi qu'un découpage
        se paie deux ans plus tard."""
        homes = [
            path.name for path in PACKAGE.glob("*.py")
            if name in classes_of(path)
        ]
        assert homes == ["_base.py"], homes

    def test_the_identifier_factory_is_defined_once(self):
        homes = [
            path.name for path in PACKAGE.glob("*.py")
            if any(
                isinstance(node, ast.FunctionDef) and node.name == "new_id"
                for node in ast.parse(path.read_text()).body
            )
        ]
        assert homes == ["_base.py"], homes

    @pytest.mark.parametrize("path", modules(), ids=lambda p: p.stem)
    def test_every_repository_stands_on_them(self, path: Path):
        assert "from ._base import" in path.read_text(), path.name
