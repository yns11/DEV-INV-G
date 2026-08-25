"""La couture entre les routeurs et les services.

Le défaut qu'ils ferment
------------------------
Le découpage des services a déplacé ``check_duplicate`` de ``ImportService``
vers ``ImportBatches``. L'appelant, lui, est resté :

    duplicate = await offload(
        lambda: importer.check_duplicate(campaign.id, target, **kwargs)
    )

En production, **tout chargement de fichier échouait en 500** — les six
grilles, pas seulement le stock ERP :

    AttributeError: 'ImportService' object has no attribute 'check_duplicate'

Rien ne l'a signalé. Deux mille contrôles passaient, parce qu'ils appellent les
importeurs *directement* : ``service.import_book_stock(campaign, ...)``. La
couture entre le routeur et le service n'était vérifiée par rien, et c'est
précisément la couture qu'un déplacement de méthode casse.

Ce que ces contrôles vérifient
------------------------------
Pas le comportement — il a ses propres contrôles — mais que **ce que le routeur
appelle existe**. Deux façons de le nommer, donc deux contrôles :

* par son nom, ``importer.check_duplicate(...)`` — lu dans l'arbre syntaxique ;
* par une table, ``getattr(importer, _resolve(target))`` — la seule indirection
  du genre, et elle mérite d'autant plus d'être vérifiée qu'aucune analyse
  statique ne la suit.

Un contrôle de forme, assumé comme tel : il ne dit pas que l'appel est *juste*,
seulement qu'il ne lèvera pas d'``AttributeError`` à la première requête. C'est
exactement ce qui manquait.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parent.parent / "app" / "inventory" / "api" / "routers"

#: Ce qu'on ne cherche pas à résoudre : ce ne sont pas des services.
#:
#: `CampaignDep` porte un modèle du domaine, pas un service, et ses attributs
#: sont des champs Pydantic déjà tenus par le modèle lui-même.
NOT_A_SERVICE = {"CampaignDep", "CurrentUser", "Ctx"}


def router_modules() -> list[Path]:
    return sorted(
        p for p in ROUTERS.glob("*.py") if p.name != "__init__.py"
    )


def _services_of(tree: ast.Module, module) -> dict[str, type]:
    """Les alias ``X = Annotated[UnService, Depends(...)]`` du module.

    Résolus sur le **module importé**, pas sur le texte : c'est la classe réelle
    qu'il faut interroger, pas son nom.
    """
    aliases: dict[str, type] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id in NOT_A_SERVICE:
            continue
        value = node.value
        if not (isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Tuple)):
            continue
        first = value.slice.elts[0]
        if not isinstance(first, ast.Name):
            continue
        resolved = getattr(module, first.id, None)
        if isinstance(resolved, type):
            aliases[target.id] = resolved
    return aliases


def _calls_in(tree: ast.Module, aliases: dict[str, type]) -> list[tuple[str, str, type]]:
    """Chaque ``param.attribut`` où ``param`` est annoté par un alias de service.

    Rend ``(fonction, attribut, classe)``.
    """
    found: list[tuple[str, str, type]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        # Quel paramètre porte quel service ?
        bound: dict[str, type] = {}
        for arg in [*node.args.args, *node.args.kwonlyargs]:
            annotation = arg.annotation
            if isinstance(annotation, ast.Name) and annotation.id in aliases:
                bound[arg.arg] = aliases[annotation.id]
        if not bound:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Attribute)
                and isinstance(inner.value, ast.Name)
                and inner.value.id in bound
            ):
                found.append((node.name, inner.attr, bound[inner.value.id]))
    return found


@pytest.mark.parametrize("path", router_modules(), ids=lambda p: p.name)
def test_router_service_seam(path: Path):
    """Tout ce qu'un routeur appelle sur un service existe sur ce service."""
    module = importlib.import_module(f"inventory.api.routers.{path.stem}")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _services_of(tree, module)

    missing = [
        f"{path.name}::{function} appelle {service.__name__}.{attribute}, "
        "qui n'existe pas"
        for function, attribute, service in _calls_in(tree, aliases)
        if not hasattr(service, attribute)
    ]
    assert not missing, "\n".join(missing)


def test_the_seam_is_actually_inspected():
    """Une analyse qui ne trouve rien passerait toujours.

    C'est le mode d'échec propre à ce genre de contrôle : un alias renommé, une
    annotation déplacée, et la boucle ne parcourt plus rien tout en restant
    verte. Le compte n'a pas à être exact — seulement à ne pas être zéro.
    """
    total = 0
    for path in router_modules():
        module = importlib.import_module(f"inventory.api.routers.{path.stem}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += len(_calls_in(tree, _services_of(tree, module)))

    assert total > 100, f"seulement {total} appels inspectés : l'analyse ne voit plus rien"


def test_every_import_target_names_a_real_importer():
    """L'indirection ``getattr(importer, _resolve(target))``.

    Aucune analyse statique ne la suit : la table associe une cible d'import à
    un nom de méthode, et c'est à l'exécution que le nom devient un appel. Une
    méthode renommée y produirait le même 500 que celui qu'on vient de corriger,
    et sur la même route.
    """
    from inventory.api.routers.data import _TARGETS
    from inventory.services.import_service import ImportService

    missing = sorted(
        f"{target} → ImportService.{method}"
        for target, method in _TARGETS.items()
        if not callable(getattr(ImportService, method, None))
    )
    assert not missing, missing


class TestTheFacadeActuallyDelegates:
    """Exister ne suffit pas : encore faut-il que ça mène quelque part.

    Écrit après coup, parce que la vérification par mutation l'a réclamé :
    brancher la façade sur ``self.parser`` au lieu de ``self.batches`` ne
    faisait rien tomber. Le contrôle de couture ne regarde qu'un ``hasattr`` —
    la méthode était bien là, elle appelait simplement un objet qui ne la
    connaît pas, ce qui reproduit le 500 d'origine un cran plus loin.

    Trois lignes suffisent à le fermer, et ce sont les mêmes trois lignes qui
    disent ce que la détection de doublon fait réellement : la même charge utile
    déjà chargée est retrouvée, une autre ne l'est pas.
    """

    def service(self, *, known: dict[str, object]):
        from types import SimpleNamespace
        from typing import Any, cast

        from inventory.services.import_service import ImportService

        ctx = cast(Any, SimpleNamespace(
            actor="chef@usine",
            imports=SimpleNamespace(
                find_duplicate=lambda cid, target, digest: known.get(digest)
            ),
        ))
        return ImportService(ctx)

    def digest_of(self, payload: bytes) -> str:
        import hashlib

        return hashlib.sha256(payload).hexdigest()

    def test_une_charge_utile_deja_chargee_est_retrouvee(self):
        already = {"filename": "stock.csv", "rows_accepted": 1598}
        service = self.service(known={self.digest_of(b"x"): already})

        found = service.check_duplicate("camp-1", "book_stock", payload=b"x")

        assert found == already

    def test_une_charge_utile_inedite_ne_l_est_pas(self):
        service = self.service(known={self.digest_of(b"x"): {"filename": "a"}})

        assert service.check_duplicate("camp-1", "book_stock", payload=b"y") is None

    def test_sans_charge_utile_la_question_ne_se_pose_pas(self):
        """Une lecture ERP n'a pas de fichier : elle n'a pas de doublon non plus,
        et interroger la base pour l'apprendre serait un aller-retour pour rien."""
        service = self.service(known={})

        assert service.check_duplicate("camp-1", "items", mode="erp") is None
