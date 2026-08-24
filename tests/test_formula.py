"""Les quantités écrites comme des opérations.

Devant trois palettes de quarante-huit et un fond de bac de sept, un compteur
écrit « 3*48+7 » — et c'est la bonne façon de compter : le calcul reste devant
les yeux de qui relira, ce qu'un « 151 » nu ne permet plus. L'application ne
savait lire qu'un nombre : la saisie refusait la ligne, le scan rendait une case
vide sur une feuille pourtant ni vierge ni douteuse.

Trois choses se vérifient ici, et la deuxième est la plus importante :

1. **Le calcul est juste**, en décimal — ces chiffres finissent dans un écart
   valorisé, et `0.1 + 0.2` doit valoir `0.3`.
2. **Rien d'autre que l'arithmétique ne passe.** Ce texte arrive d'un formulaire
   ouvert à tout l'atelier et d'une feuille scannée. Un évaluateur qui accepte
   plus que ce qu'on lui destine n'est pas une commodité, c'est une porte —
   d'où la liste blanche de nœuds plutôt qu'une liste noire, et d'où ces
   contrôles-ci, qui tentent d'y faire passer autre chose.
3. **Le refus dit quoi faire.** « 3*48+7 n'est pas un nombre » envoie corriger
   la feuille, alors que la feuille est juste et que c'est un réglage qui
   manque.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from inventory.domain.formula import (
    FORMULA_MAX_LENGTH,
    FormulaError,
    evaluate,
    looks_like_formula,
    resolve_quantity,
)


class TestTheArithmeticIsRight:
    @pytest.mark.parametrize(
        "written,expected",
        [
            ("3*48+7", "151"),
            ("12", "12"),
            ("-4", "-4"),
            ("(2+3)*4", "20"),
            ("10/4", "2.5"),
            ("100-1-1", "98"),
            ("2*3*4", "24"),
            ("-(3+2)", "-5"),
            ("+7", "7"),
        ],
    )
    def test_les_quatre_operations_et_les_parentheses(self, written, expected):
        assert evaluate(written) == Decimal(expected)

    def test_la_precedence_est_celle_de_l_arithmetique(self):
        """Sinon « 3*48+7 » vaudrait 165 : trois palettes de cinquante-cinq."""
        assert evaluate("3*48+7") == Decimal("151")
        assert evaluate("7+3*48") == Decimal("151")

    def test_le_calcul_est_decimal_et_non_flottant(self):
        """Ces chiffres finissent dans un écart valorisé : 0,30000000000000004
        y serait faux, et invisible jusqu'à l'euro près."""
        assert evaluate("0,1+0,2") == Decimal("0.3")

    def test_le_signe_egal_de_tete_est_accepte(self):
        """L'habitude d'Excel, et le geste que la case invite à faire."""
        assert evaluate("=(10+2)/4") == Decimal("3")
        assert evaluate("= 3 * 2") == Decimal("6")

    def test_la_virgule_est_le_separateur_decimal(self):
        """C'est celui qu'écrivent les gens qui remplissent ces feuilles."""
        assert evaluate("2,5*4") == Decimal("10.0")

    def test_les_espaces_des_milliers_ne_font_pas_deux_termes(self):
        """« 1 200 + 30 » se lit bien sur le papier et se tape tel quel."""
        assert evaluate("1 200 + 30") == Decimal("1230")

    def test_une_quantite_negative_reste_possible(self):
        """Un bac rendu, une correction : la feuille peut porter un moins, et
        c'est la zone qui décide si elle l'accepte — pas cet évaluateur."""
        assert evaluate("-4") == Decimal("-4")
        assert evaluate("10-14") == Decimal("-4")


class TestNothingButArithmeticGetsThrough:
    """Ce texte vient d'un formulaire et d'un scan. La liste est blanche."""

    @pytest.mark.parametrize(
        "attempt",
        [
            "__import__('os').system('rm -rf /')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__",
            "[1, 2, 3]",
            "{'a': 1}",
            "lambda: 1",
            "1 if True else 2",
            "x + 1",
            "abs(-3)",
            "print(1)",
            "1 and 2",
            "not 1",
            "1 < 2",
            "'texte'",
            "f'{1}'",
        ],
    )
    def test_rien_de_tout_cela_ne_s_evalue(self, attempt):
        with pytest.raises(FormulaError):
            evaluate(attempt)

    def test_la_puissance_est_refusee(self):
        """`2**10000000000` bloque un cœur pendant des minutes sans lever :
        aucun comptage n'en a besoin, donc l'opérateur n'existe pas ici."""
        with pytest.raises(FormulaError):
            evaluate("2**64")

    def test_le_modulo_et_la_division_entiere_aussi(self):
        """Non parce qu'ils seraient dangereux, mais parce qu'une quantité
        écrite avec un modulo est bien plus probablement une faute de frappe."""
        for attempt in ("7%2", "7//2"):
            with pytest.raises(FormulaError):
                evaluate(attempt)

    def test_un_booleen_n_est_pas_une_quantite(self):
        with pytest.raises(FormulaError):
            evaluate("True")

    def test_la_division_par_zero_est_dite_et_non_levee_brutalement(self):
        with pytest.raises(FormulaError) as refusal:
            evaluate("10/0")
        assert "zéro" in str(refusal.value)

    def test_une_expression_demesuree_est_refusee_avant_d_etre_analysee(self):
        """L'analyse syntaxique est le seul endroit dont le coût dépende de
        l'entrée : la borne est posée devant elle, pas derrière."""
        with pytest.raises(FormulaError) as refusal:
            evaluate("1+" * FORMULA_MAX_LENGTH + "1")
        assert "trop longue" in str(refusal.value)

    def test_une_expression_vide_ne_vaut_pas_zero(self):
        for attempt in ("", "   ", "="):
            with pytest.raises(FormulaError):
                evaluate(attempt)


class TestWhatCountsAsAFormula:
    @pytest.mark.parametrize("text", ["3*48+7", "=12", "10-4", "(2)", "2/4", "-4"])
    def test_un_operateur_ou_un_egal_de_tete(self, text):
        assert looks_like_formula(text) is True

    @pytest.mark.parametrize("text", ["151", "0", "2,5", " 12 ", ""])
    def test_un_nombre_nu_n_en_est_pas_une(self, text):
        assert looks_like_formula(text) is False


class TestTheSettingDecides:
    def test_un_nombre_passe_dans_les_deux_reglages(self):
        for allow in (True, False):
            assert resolve_quantity("151", allow_formulas=allow) == (Decimal("151"), "")

    def test_une_operation_acceptee_rend_le_resultat_et_le_texte(self):
        """Les deux, et c'est tout l'objet : sans le texte, « 151 » calculé et
        « 151 » tapé seraient indistinguables, et le comptage cesserait d'être
        recomptable."""
        assert resolve_quantity("3*48+7", allow_formulas=True) == (
            Decimal("151"), "3*48+7",
        )

    def test_un_nombre_ne_se_garde_pas_comme_sa_propre_formule(self):
        """Sinon la colonne se remplirait de doublons sans rien apprendre."""
        _, formula = resolve_quantity("151", allow_formulas=True)
        assert formula == ""

    def test_une_operation_refusee_nomme_le_reglage(self):
        """« 3*48+7 n'est pas un nombre » envoie corriger la feuille — or la
        feuille est juste, et c'est l'application qui ne sait pas la lire."""
        with pytest.raises(FormulaError) as refusal:
            resolve_quantity("3*48+7", allow_formulas=False)

        message = str(refusal.value)
        assert "Paramètres" in message
        assert "Accepter des formules dans les comptages" in message

    def test_un_texte_qui_n_est_ni_l_un_ni_l_autre_est_refuse_en_le_citant(self):
        """Sur une feuille de cent lignes, « quantité invalide » sans dire
        laquelle oblige à toutes les relire."""
        with pytest.raises(FormulaError) as refusal:
            resolve_quantity("douze", allow_formulas=True)
        assert "douze" in str(refusal.value)

    def test_un_decimal_deja_construit_traverse_sans_detour(self):
        """Les appelants internes passent des `Decimal` : les faire transiter
        par du texte y ajouterait un aller-retour et une occasion de perdre une
        décimale."""
        assert resolve_quantity(Decimal("12.5"), allow_formulas=False) == (
            Decimal("12.5"), "",
        )

    def test_une_valeur_vide_est_refusee_plutot_que_comptee_zero(self):
        """Vide ≠ zéro : c'est la règle de toute l'application, et l'appelant
        écarte la case avant d'arriver ici."""
        for empty in ("", "   ", None):
            with pytest.raises(FormulaError):
                resolve_quantity(empty, allow_formulas=True)
