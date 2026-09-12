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


# --------------------------------------------------------------------------- #
# Un routeur qui recopie un schéma à la main finit par en oublier un champ
# --------------------------------------------------------------------------- #

class TestUnChampDuSchemaArriveAuService:
    """Le second défaut de couture, et il ne ressemble pas au premier.

    Là, aucun ``AttributeError`` : le routeur appelle une méthode qui existe,
    avec un dictionnaire qu'il **construit à la main** à partir du schéma. Il
    suffit qu'un champ ajouté au schéma ne soit pas recopié dans ce
    dictionnaire pour qu'il n'arrive jamais au service.

    C'est ce qui est arrivé à la **désignation de feuille** : le schéma
    l'acceptait, le service la lisait, le routeur ne la transmettait pas.
    L'écran annonçait « ligne enregistrée » — tout le reste l'était — et la
    désignation restait celle d'avant, à l'écran comme sur le papier. Un défaut
    silencieux, sans trace, et que ni les contrôles du schéma ni ceux du service
    ne pouvaient voir : chacun avait raison de son côté.

    Le contrôle est **dynamique** plutôt que textuel : il remplit toutes les
    valeurs du schéma, appelle la fonction de route, et regarde ce qui est
    arrivé. Un champ ajouté demain y sera donc soumis sans que personne n'ait à
    y penser — ce qui est exactement ce qui a manqué.
    """

    def _forwarded(self, **over):
        """Ce que la route transmet réellement au service, pour une ligne."""
        from inventory.api.routers.generic import upsert_sheet_lines
        from inventory.api.schemas import SheetLinesRequest

        seen: list[dict] = []

        class Service:
            def upsert_sheet_lines(self, campaign, sheet_id, rows, **kwargs):
                seen.extend(rows)
                return len(rows)

        payload = SheetLinesRequest.model_validate({
            "lines": [{
                "id": "ligne-1",
                "itemNumber": "P-00001",
                "section": "WIP",
                "lineKind": "ARTICLE",
                "label": "Intertitre",
                "name": "CARTER ARRIÈRE M3 GEN2",
                "qty": "3*48+7",
                "unit": "KG",
                "comment": "bac du fond",
                "displayOrder": 4,
                **over,
            }]
        })
        upsert_sheet_lines(
            campaign=object(), sheet_id="sheet-1",
            payload=payload, service=Service(),
        )
        return seen[0]

    def test_la_designation_arrive(self):
        """Le défaut lui-même : elle n'arrivait pas."""
        assert self._forwarded()["name"] == "CARTER ARRIÈRE M3 GEN2"

    def test_et_tous_les_autres_champs_avec(self):
        """Le contrôle qui vaudra pour le prochain champ ajouté.

        Il ne nomme pas les champs : il les lit sur le schéma. Un champ que le
        routeur oublierait de transmettre le ferait donc échouer sans qu'on ait
        pensé à lui — c'est la seule forme de contrôle qui tienne contre un
        oubli, puisqu'un oubli est par définition ce à quoi on n'a pas pensé.
        """
        from inventory.api.schemas import SheetLineRow

        forwarded = self._forwarded()
        absents = [name for name in SheetLineRow.model_fields if name not in forwarded]

        assert absents == [], (
            f"le routeur ne transmet pas {absents} au service : le schéma les "
            "accepte, le service les lit, et entre les deux ils se perdent"
        )

    def test_une_desigation_absente_reste_absente(self):
        """« Je ne parle pas de la désignation » doit survivre au passage.

        C'est ce qui protège l'aperçu de mise en page : il renvoie l'ordre des
        lignes et jamais les noms, et réordonner une feuille ne doit pas décider
        de la façon dont elle nomme ses articles. Transmettre une chaîne vide à
        la place de l'absence effacerait l'écrasement de toute la feuille.
        """
        assert self._forwarded(name=None)["name"] is None
