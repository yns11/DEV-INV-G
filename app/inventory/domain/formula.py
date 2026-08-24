"""Les quantités écrites comme des opérations.

Sur le papier, un compteur n'écrit pas toujours un nombre. Devant trois palettes
de quarante-huit et un fond de bac de sept, il écrit ce qu'il voit :

    3*48+7

C'est même la bonne façon de compter — le calcul est *devant* les yeux de qui
relira, alors qu'un « 151 » nu ne se recompte pas. Jusqu'ici l'application ne
savait lire qu'un nombre : la saisie refusait la ligne, et le scan rendait une
case vide sur une feuille qui n'était pourtant ni vierge ni douteuse.

Ce module évalue ces expressions comme le ferait une cellule de tableur, et
**garde le texte d'origine à côté du résultat** (``count_sheet_line.qty_formula``,
migration 023). C'est ce qui sépare cette fonctionnalité d'une commodité de
saisie : six mois plus tard, on peut relire ce que le compteur a réellement
écrit, et pas seulement ce que la machine en a conclu.

Ce qui est accepté, et rien d'autre
-----------------------------------
Les quatre opérations, les parenthèses, le moins unaire, la virgule décimale
française, et un ``=`` de tête que l'habitude d'Excel fait taper. Pas de nom, pas
d'appel, pas de puissance, pas d'indexation : ce qui est évalué vient d'une
feuille scannée ou d'un champ de saisie ouvert à tout l'atelier, et un
évaluateur qui accepte plus que ce qu'on lui destine est une porte.

C'est un arbre syntaxique parcouru nœud par nœud, jamais un ``eval``. La
différence n'est pas théorique : ``eval`` sur ``__import__('os').system(...)``
fait ce qu'on lui demande, et ce texte-là arrive d'un formulaire.

Le calcul se fait en :class:`~decimal.Decimal`, comme tout le reste des
quantités de ce dossier : ``0.1 + 0.2`` doit valoir ``0.3``, pas
``0.30000000000000004``, sur un chiffre qui finit dans un écart valorisé.
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal, DecimalException, InvalidOperation

__all__ = [
    "FORMULA_MAX_LENGTH",
    "FormulaError",
    "evaluate",
    "looks_like_formula",
    "resolve_quantity",
]

#: Longueur maximale d'une expression acceptée.
#:
#: Une quantité écrite à la main tient en quelques termes. Au-delà, ce n'est
#: plus un comptage : c'est un collage, ou un texte qui n'a rien à faire là. La
#: borne est posée avant l'analyse syntaxique, qui est le seul endroit de ce
#: module dont le coût dépende de l'entrée.
FORMULA_MAX_LENGTH = 200

#: Ce qui fait dire d'un texte qu'il *tente* d'être une opération.
#:
#: Sert à distinguer « ce n'est pas un nombre » de « c'est un calcul » — deux
#: refus qui n'appellent pas le même message. La détection est volontairement
#: large : mieux vaut tenter d'évaluer « 12 ans » et refuser proprement que de
#: le classer « nombre illisible » sans jamais nommer les formules.
_OPERATORS = re.compile(r"[+\-*/()]")


class FormulaError(ValueError):
    """L'expression n'est pas une opération arithmétique évaluable."""


#: Les nœuds autorisés. Une liste blanche, jamais une liste noire : une version
#: de Python qui ajouterait une syntaxe la ferait passer d'office avec une liste
#: noire, et personne ne s'en apercevrait avant l'incident.
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def looks_like_formula(text: str) -> bool:
    """Le texte porte-t-il un opérateur ou un ``=`` de tête ?

    Ne dit pas qu'il est *valide* — seulement qu'il se donne pour un calcul.

    >>> looks_like_formula("3*48+7")
    True
    >>> looks_like_formula("=12")
    True
    >>> looks_like_formula("151")
    False
    >>> looks_like_formula("-4")
    True
    """
    stripped = text.strip()
    return stripped.startswith("=") or bool(_OPERATORS.search(stripped))


def evaluate(text: str) -> Decimal:
    """Le résultat de l'expression, ou :class:`FormulaError`.

    >>> evaluate("3*48+7")
    Decimal('151')
    >>> evaluate("=(10+2)/4")
    Decimal('3')
    >>> evaluate("2,5*4")
    Decimal('10.0')
    >>> evaluate("12")
    Decimal('12')
    """
    expression = text.strip().removeprefix("=").strip()
    if not expression:
        raise FormulaError("Expression vide.")
    if len(expression) > FORMULA_MAX_LENGTH:
        raise FormulaError(
            f"Expression trop longue ({len(expression)} caractères, "
            f"maximum {FORMULA_MAX_LENGTH})."
        )
    # La virgule est le séparateur décimal de ceux qui remplissent ces feuilles ;
    # elle est aussi, en Python, ce qui fait d'une expression un tuple. La
    # remplacer avant l'analyse est donc autant une commodité qu'une garde.
    expression = expression.replace(",", ".")
    # Les espaces des milliers — « 1 200 + 30 » — se lisent bien sur le papier
    # et font deux termes juxtaposés pour l'analyseur.
    expression = re.sub(r"(?<=\d)[  ](?=\d)", "", expression)

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise FormulaError("Opération incompréhensible.") from exc

    try:
        return _reduce(tree.body)
    except FormulaError:
        raise
    except (InvalidOperation, DecimalException, ArithmeticError) as exc:
        raise FormulaError("Opération impossible à calculer.") from exc


def _reduce(node: ast.AST) -> Decimal:
    """Un nœud, une valeur. Tout ce qui n'est pas prévu est refusé."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise FormulaError("Seuls des nombres sont acceptés.")
        # Par le texte, jamais par le flottant : `Decimal(0.1)` porte l'erreur de
        # représentation du binaire, `Decimal("0.1")` ne la porte pas.
        return Decimal(str(node.value))

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise FormulaError("Seuls +, -, * et / sont acceptés.")
        left, right = _reduce(node.left), _reduce(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise FormulaError("Division par zéro.")
        return left / right

    if isinstance(node, ast.UnaryOp):
        # Le moins unaire seulement : un comptage négatif existe — un bac rendu,
        # une correction — et « -4 » doit s'écrire. `not` et `~` n'ont rien à
        # faire sur une quantité.
        if isinstance(node.op, ast.USub):
            return -_reduce(node.operand)
        if isinstance(node.op, ast.UAdd):
            return _reduce(node.operand)
        raise FormulaError("Opérateur non autorisé.")

    raise FormulaError("Seules les opérations arithmétiques sont acceptées.")


def resolve_quantity(value: object, *, allow_formulas: bool) -> tuple[Decimal, str]:
    """La quantité, et l'expression qui l'a produite quand il y en a une.

    Le second membre est vide dès que la saisie était déjà un nombre : garder
    « 151 » comme « formule de 151 » n'apprendrait rien à personne et ferait
    afficher une colonne de doublons.

    ``allow_formulas`` est le réglage de campagne. Éteint, une opération est
    refusée **en nommant le réglage** : « 3*48+7 n'est pas un nombre » enverrait
    corriger la feuille, alors que la feuille est juste et que c'est
    l'application qui ne sait pas encore la lire.

    :raises FormulaError: quand la valeur n'est ni un nombre ni une opération
        évaluable, ou quand c'est une opération et qu'elles sont refusées.
    """
    if isinstance(value, Decimal):
        return value, ""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return Decimal(str(value)), ""

    text = str(value if value is not None else "").strip()
    if not text:
        raise FormulaError("Quantité vide.")

    if not looks_like_formula(text):
        try:
            return Decimal(text.replace(",", ".").replace(" ", "")), ""
        except (InvalidOperation, ValueError) as exc:
            raise FormulaError(f"« {text} » n'est pas une quantité.") from exc

    if not allow_formulas:
        raise FormulaError(
            f"« {text} » est une opération, et les formules ne sont pas activées "
            "sur cette campagne. Activez « Accepter des formules dans les "
            "comptages » dans Paramètres, ou saisissez le résultat."
        )
    return evaluate(text), text
