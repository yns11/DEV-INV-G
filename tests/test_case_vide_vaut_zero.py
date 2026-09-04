"""Une case laissée vide sur une feuille de comptage vaut zéro.

L'application distinguait « non compté » de « compté à zéro », et le
distinguait *partout* : la ligne sans quantité ne rejoignait pas le stock
compté, ne s'imprimait pas avec un chiffre, ne pesait rien.

Ce n'est pas ce qu'une feuille de comptage veut dire. Elle **énumère ce qu'on
s'attend à trouver dans la zone** : la référence y est parce que le stock ERP
la porte à cet endroit. Ne rien écrire en face, c'est donc dire qu'il n'y en
avait pas — un écart à expliquer, pas une mesure manquante. En l'écartant du
total, l'article gardait son stock ERP en face de rien : il n'apparaissait ni
dans le compté, ni dans le manquant. La ligne disparaissait de l'inventaire.

Reste une notion qu'il ne faut pas perdre, et c'est pour cela que le booléen a
changé de nom plutôt que de valeur : **quelqu'un a-t-il touché cette ligne ?**
C'est ce qui distingue une zone « à compter » d'une zone « en cours », et c'est
ce que le rapport d'un scan compte quand il annonce « 30 quantités extraites ».
La quantité, elle, est `qty`, et vaut zéro. Les deux portaient le même nom.
"""

from __future__ import annotations

from decimal import Decimal

from inventory.domain.consolidation import _index_lines
from inventory.domain.enums import CountLineKind, CountSection
from inventory.domain.models import CountSheetLine

CAMPAIGN, SHEET = "c", "s"


def line(number: str = "P-1", **kwargs) -> CountSheetLine:
    return CountSheetLine(
        id=f"l-{number}-{kwargs.get('display_order', 0)}",
        sheet_id=SHEET, campaign_id=CAMPAIGN, item_number=number, **kwargs,
    )


class TestLesDeuxNotionsNeSeConfondentPlus:
    def test_une_case_vide_compte_zero(self):
        assert line().qty == 0

    def test_et_personne_n_y_a_touche(self):
        assert line().has_entry is False

    def test_un_zero_saisi_est_une_saisie(self):
        """Le compteur a écrit « 0 » : la ligne est faite, pas en attente."""
        assert line(qty_manual=Decimal(0)).has_entry is True
        assert line(qty_manual=Decimal(0)).qty == 0

    def test_une_quantite_saisie_aussi(self):
        counted = line(qty_manual=Decimal(7))
        assert counted.has_entry is True and counted.qty == 7


class TestLeStockComptePrendLesCasesVides:
    """``_index_lines`` — ce qui remonte d'une feuille vers la consolidation."""

    def test_une_ligne_vide_pese_zero_au_lieu_de_disparaitre(self):
        """Le défaut : la clé n'existait pas, donc l'article n'était nulle part.

        Avec son stock ERP en face de rien, il ne comptait ni comme compté ni
        comme manquant — la seule chose qu'un inventaire ne doit pas faire.
        """
        totals = _index_lines([line("P-1")])
        assert totals == {("P-1", CountSection.LINE_SIDE): Decimal(0)}

    def test_une_ligne_vide_ne_retire_rien_a_une_ligne_comptee(self):
        """Le même article sur deux palettes, l'une trouvée et l'autre vide."""
        totals = _index_lines([
            line("P-1", qty_manual=Decimal(12)),
            line("P-1", display_order=1),
        ])
        assert totals == {("P-1", CountSection.LINE_SIDE): Decimal(12)}

    def test_les_sections_restent_distinctes(self):
        totals = _index_lines([
            line("P-1"), line("P-1", section=CountSection.WIP, display_order=1),
        ])
        assert set(totals) == {
            ("P-1", CountSection.LINE_SIDE), ("P-1", CountSection.WIP)
        }

    def test_un_intertitre_n_entre_pas_dans_le_total(self):
        """Il n'a pas d'article : il agrégerait une quantité sous la clé vide,
        et le stock compté porterait une référence « »."""
        totals = _index_lines([
            line("", line_kind=CountLineKind.SUBSECTION, label="Stock physique B15"),
            line("P-1", qty_manual=Decimal(3), display_order=1),
        ])
        assert totals == {("P-1", CountSection.LINE_SIDE): Decimal(3)}

    def test_une_ligne_vide_de_mise_en_page_non_plus(self):
        totals = _index_lines([
            line("", line_kind=CountLineKind.SPACER),
            line("P-1", qty_manual=Decimal(3), display_order=1),
        ])
        assert totals == {("P-1", CountSection.LINE_SIDE): Decimal(3)}
