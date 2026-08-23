"""Les services extraits ne reviennent pas dans celui dont ils sortent.

Ce qu'étaient les deux fichiers
-------------------------------
``generic_service`` faisait 1771 lignes et ``import_service`` 1364. Ni l'un ni
l'autre n'était faux ; les deux étaient devenus l'endroit où l'on ajoute, parce
que c'était déjà l'endroit où tout se trouvait.

Ce qui en est sorti, et pourquoi ceux-là
----------------------------------------
Trois concerns s'en détachaient d'eux-mêmes, et le reste non :

* **La lecture des scans** parle à un modèle hébergé quand tout le reste parle
  à la base. Elle est la seule partie du service à pouvoir échouer parce qu'un
  point de terminaison est lent.
* **La consolidation** lit les feuilles sans les écrire, et emploie trois
  vocabulaires — ERP, nomenclatures, valeur — que les zones et les feuilles
  n'emploient jamais.
* **La provenance des imports** — d'où vient un chiffre, l'a-t-on déjà chargé,
  peut-on le relire — accompagne les six importeurs sans appartenir à aucun.

Les six importeurs, eux, restent ensemble : ce sont six variantes d'une même
chose. Les séparer aurait produit six classes partageant une base, c'est-à-dire
de l'indirection sans découpage.

Ce que ces contrôles tiennent
-----------------------------
Un service extrait a une pente : redevenir une méthode de celui d'où il vient,
parce que « c'est là qu'on avait la campagne sous la main ». Ces contrôles
refusent le retour, et tiennent le sens de chaque extraction plutôt que sa
forme — le client de vision ne se joint que depuis la lecture de scans, et
personne ne consolide en dehors du service qui consolide.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "app" / "inventory" / "services"

#: Au-delà, le module est redevenu l'endroit où l'on ajoute.
#:
#: Douze cent cinquante, et non mille : deux services que ce découpage n'a pas
#: touchés — l'analyse (1199) et la réconciliation de flux (1107) — passent
#: juste en dessous. Les faire échouer ici ferait porter à ce contrôle une
#: décision qui n'a pas été prise ; ils sont les deux prochains candidats, et
#: le plafond attrape déjà le retour aux dix-sept cents lignes d'avant.
CEILING = 1250

#: Les services extraits, et ce qui les définit.
EXTRACTED = {
    "scan_service.py": "ScanService",
    "consolidation_service.py": "ConsolidationService",
    "import_parsing.py": "ImportParser",
    "import_batches.py": "ImportBatches",
}


def source(name: str) -> str:
    return (SERVICES / name).read_text()


def imported_names(name: str) -> set[str]:
    """Ce qu'un module importe vraiment.

    Chercher un nom dans le texte trouverait aussi les explications : les
    modules extraits disent d'où ils viennent, et un contrôle qui lirait cette
    phrase comme une dépendance échouerait sur sa propre documentation.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source(name))):
        if isinstance(node, ast.ImportFrom | ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def modules() -> list[Path]:
    return sorted(
        p for p in SERVICES.glob("*.py") if p.name != "__init__.py"
    )


# --------------------------------------------------------------------------- #
# Aucun module ne redevient l'endroit où l'on ajoute
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", modules(), ids=lambda p: p.stem)
def test_no_service_is_a_catch_all(path: Path) -> None:
    assert len(path.read_text().splitlines()) <= CEILING, path.name


@pytest.mark.parametrize("name,klass", sorted(EXTRACTED.items()))
def test_each_extracted_service_exists(name: str, klass: str) -> None:
    classes = [
        node.name for node in ast.parse(source(name)).body
        if isinstance(node, ast.ClassDef)
    ]
    assert klass in classes, f"{name} ne porte plus {klass}"


@pytest.mark.parametrize("name,klass", sorted(EXTRACTED.items()))
def test_each_extracted_service_says_why_it_exists(name: str, klass: str) -> None:
    """Un module extrait sans phrase d'ouverture est un fichier de plus."""
    docstring = ast.get_docstring(ast.parse(source(name))) or ""
    assert len(docstring.strip().splitlines()[0] if docstring.strip() else "") >= 25


# --------------------------------------------------------------------------- #
# Chaque extraction tient son sens, pas seulement sa forme
# --------------------------------------------------------------------------- #

class TestTheVisionModelIsReachedFromOnePlace:
    """La lecture de scans est la seule à parler à un modèle hébergé.

    C'est ce qui la distinguait au point de justifier un module : elle est la
    seule partie du service à dépendre d'un point de terminaison, donc la seule
    à pouvoir être lente ou indisponible pour une raison qui n'est pas la base.
    """

    def test_the_extractor_is_joined_from_the_scan_service(self):
        assert "SheetExtractor" in imported_names("scan_service.py")

    @pytest.mark.parametrize(
        "name",
        ["generic_service.py", "consolidation_service.py", "import_service.py",
         "import_parsing.py", "import_batches.py"],
    )
    def test_nobody_else_joins_it(self, name):
        assert "SheetExtractor" not in imported_names(name), name


class TestConsolidationDoesNotReachBack:
    def test_the_zones_service_does_not_import_it(self):
        """« C'est là qu'on avait la campagne sous la main » est exactement la
        phrase par laquelle un service extrait redevient une méthode."""
        assert "ConsolidationService" not in imported_names("generic_service.py")

    def test_it_does_not_import_the_zones_service(self):
        assert "GenericService" not in imported_names("consolidation_service.py")

    def test_it_does_not_import_the_scan_service(self):
        assert "ScanService" not in imported_names("consolidation_service.py")

    def test_the_scan_service_stands_alone_too(self):
        names = imported_names("scan_service.py")
        assert "GenericService" not in names
        assert "ConsolidationService" not in names


class TestTheImportServiceComposes:
    """Composition, pas héritage : les deux collaborateurs sont des attributs.

    Une base partagée aurait donné les mêmes lignes dans un autre ordre, avec
    en prime une hiérarchie à lire pour savoir d'où vient une méthode.
    """

    def test_it_holds_the_parser(self):
        assert "self.parser = ImportParser(ctx)" in source("import_service.py")

    def test_it_holds_the_batches(self):
        assert "self.batches = ImportBatches(ctx)" in source("import_service.py")

    def test_it_inherits_from_nothing(self):
        node = next(
            n for n in ast.parse(source("import_service.py")).body
            if isinstance(n, ast.ClassDef) and n.name == "ImportService"
        )
        assert node.bases == []

    @pytest.mark.parametrize("name", ["import_parsing.py", "import_batches.py"])
    def test_no_collaborator_reaches_back(self, name):
        assert "ImportService" not in imported_names(name), name

    def test_the_parser_writes_nothing(self):
        """C'est ce qui le sépare des six importeurs : il produit des lignes,
        il ne décide pas de ce qu'on en fait."""
        text = source("import_parsing.py")
        for writing in ("ctx.db.transaction", "record_batch", "upsert", "replace_"):
            assert writing not in text, writing


# --------------------------------------------------------------------------- #
# La porte n'a pas bougé
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name",
    ["ConsolidationService", "GenericService", "ImportService", "ScanService"],
)
def test_the_services_package_exports_it(name: str) -> None:
    import inventory.services as services

    assert getattr(services, name, None) is not None, name
    assert name in services.__all__, name


def test_the_entry_points_kept_by_the_import_service_still_answer() -> None:
    """``parse`` et ``preview`` restent sur le service.

    Ce sont les noms que l'API et vingt-six contrôles connaissent ; une façade
    d'une ligne coûte moins qu'un renommage de vingt-six appels, et le contrôle
    dit que c'est un choix, pas un oubli.
    """
    from inventory.services.import_service import ImportService

    for name in ("parse", "preview"):
        assert callable(getattr(ImportService, name, None)), name
