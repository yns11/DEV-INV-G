"""Une transaction ne se contourne pas par une seconde connexion.

Deux pannes de production sortent de la même erreur, écrite à deux endroits
différents et à des mois d'intervalle.

**L'étape rebuts qui n'aboutit pas.** `mark_scrap_loaded` était appelée dans une
transaction ayant déjà modifié la même ligne, sans recevoir sa connexion. Elle
en empruntait donc une seconde, qui attendait un verrou que la première ne
relâcherait qu'au retour de cet appel. Le pool expirait quinze secondes plus
tard en annonçant « Connexion Lakebase impossible » — le symptôme, jamais la
cause.

**La nomenclature perdue.** Sur un import « remplacer », `clear_bom` supprimait
sur une connexion séparée, donc validait immédiatement, pendant que les lignes
de remplacement restaient dans la transaction. Une erreur à l'insertion annulait
celle-ci et laissait la campagne sans aucune nomenclature.

Relire chaque appel un par un ne tient pas à l'échelle. Ce contrôle lit les
services comme du texte et refuse la forme, où qu'elle apparaisse.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = sorted((Path(__file__).resolve().parents[1] / "app" / "inventory"
                   / "services").glob("*.py"))

#: Lectures sans effet de bord. Les laisser sur une autre connexion ne corrompt
#: rien — elles y verraient seulement l'état d'avant la transaction — mais elles
#: consomment une connexion du pool, et le contrôle les signale à part.
READ_PREFIXES = ("list", "get", "count", "fetch", "lines_by", "items_by", "progress")


class NestedCalls(ast.NodeVisitor):
    """Appels de dépôt situés dans un `with ctx.db.transaction()`."""

    def __init__(self) -> None:
        self.depth = 0
        self.found: list[tuple[int, str, str, bool]] = []

    def visit_With(self, node: ast.With) -> None:
        opens = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "transaction"
            for item in node.items
        )
        self.depth += opens
        self.generic_visit(node)
        self.depth -= opens

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            self.depth
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "ctx"
            and func.value.attr != "db"
        ):
            takes_conn = any(kw.arg == "conn" for kw in node.keywords)
            self.found.append(
                (node.lineno, func.value.attr, func.attr, takes_conn)
            )
        self.generic_visit(node)


def nested_calls(path: Path) -> list[tuple[int, str, str, bool]]:
    visitor = NestedCalls()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.found


@pytest.mark.parametrize("path", SERVICES, ids=lambda p: p.name)
def test_a_write_inside_a_transaction_takes_its_connection(path: Path) -> None:
    """La règle qui aurait évité les deux pannes.

    Une écriture sur une seconde connexion se valide toute seule : elle survit à
    l'annulation de la transaction, et si elle touche une ligne que celle-ci a
    déjà verrouillée, elle attend un verrou qui ne tombera jamais.
    """
    guilty = [
        f"{path.name}:{line} — ctx.{repo}.{method}()"
        for line, repo, method, takes_conn in nested_calls(path)
        if not takes_conn and not method.startswith(READ_PREFIXES)
    ]
    assert not guilty, (
        "Écriture dans une transaction sans recevoir sa connexion :\n  "
        + "\n  ".join(guilty)
        + "\nPassez `conn=conn`. Si la méthode ne l'accepte pas encore, "
        "ajoutez-lui le paramètre."
    )


@pytest.mark.parametrize("path", SERVICES, ids=lambda p: p.name)
def test_a_read_inside_a_transaction_takes_it_too(path: Path) -> None:
    """Moins grave, mais faux quand même.

    Une lecture sur une autre connexion ne voit pas ce que la transaction vient
    d'écrire. Le clonage d'une campagne relisait ainsi les feuilles qu'il venait
    de créer : cela ne marchait que parce que l'écriture, elle aussi hors
    transaction, était déjà validée. Corriger l'une sans l'autre aurait vidé la
    copie sans rien signaler.
    """
    guilty = [
        f"{path.name}:{line} — ctx.{repo}.{method}()"
        for line, repo, method, takes_conn in nested_calls(path)
        if not takes_conn and method.startswith(READ_PREFIXES)
    ]
    assert not guilty, (
        "Lecture dans une transaction sans recevoir sa connexion :\n  "
        + "\n  ".join(guilty)
    )
