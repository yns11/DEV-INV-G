"""Ce qui relève de la nomenclature, et ce qui relève des articles.

Deux écrans, deux lecteurs : qui répare une structure ne peut rien faire d'un
prix manquant, et qui tient les prix ne regarde jamais la santé des
nomenclatures. Un constat rangé au mauvais endroit n'est pas seulement mal
classé — il n'est traité par personne.
"""

from __future__ import annotations

from inventory.domain.controls import check_items, check_referentials
from inventory.domain.enums import ItemType
from inventory.domain.models import BomLink, Item


def item(number: str, item_type=ItemType.SEMI_FINISHED, **kwargs) -> Item:
    return Item(campaign_id="c", item_number=number, item_type=item_type, **kwargs)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


class TestWhereThePriceWarningLives:
    def test_it_is_not_a_bill_of_materials_problem(self):
        findings = check_referentials(
            items={"P-1": item("P-1", ItemType.COMPONENT, std_price=0)}, bom_links=[]
        )
        assert "ITEMS_WITHOUT_PRICE" not in codes(findings)

    def test_it_is_an_article_problem(self):
        findings = check_items(
            items={"P-1": item("P-1", ItemType.COMPONENT, std_price=0)}
        )
        assert "ITEMS_WITHOUT_PRICE" in codes(findings)

    def test_a_priced_article_says_nothing(self):
        assert check_items(
            items={"P-1": item("P-1", ItemType.COMPONENT, std_price=12)}
        ) == []

    def test_an_article_out_of_scope_is_not_reported(self):
        """Ses écarts ne seront pas valorisés parce qu'il n'y en aura pas."""
        assert check_items(
            items={"P-1": item("P-1", ItemType.COMPONENT, std_price=0,
                               exclusions=["ALL"])}
        ) == []

    def test_each_article_gets_its_own_finding_so_the_list_can_be_read(self):
        """Un seul constat comptant « 105 articles » ne dit pas *lesquels*, et
        c'est précisément la liste qu'on va relire pour aller chercher les prix.
        Le regroupement les ramène à une ligne à l'écran de toute façon."""
        findings = check_items(items={
            f"P-{n}": item(f"P-{n}", ItemType.COMPONENT, std_price=0)
            for n in range(40)
        })
        assert len(findings) == 40
        assert {f.item_number for f in findings} == {f"P-{n}" for n in range(40)}


class TestAnAssemblyDeliberatelyIgnoredInBills:
    """« Ignoré en nomenclature » est une décision, pas un oubli.

    L'article ne sera jamais éclaté ; lui réclamer une structure signale comme
    manquant ce que quelqu'un a explicitement retiré, et noie les vrais trous.
    """

    def test_it_is_not_reported_as_missing_a_bill(self):
        findings = check_referentials(
            items={"SF-1": item("SF-1", exclusions=["BOM"])}, bom_links=[]
        )
        assert "ASSEMBLY_WITHOUT_BOM" not in codes(findings)

    def test_an_assembly_still_in_scope_is_reported(self):
        findings = check_referentials(items={"SF-1": item("SF-1")}, bom_links=[])
        assert "ASSEMBLY_WITHOUT_BOM" in codes(findings)

    def test_the_generic_exclusion_alone_does_not_silence_it(self):
        """Hors GENERIQUE mais toujours éclaté ailleurs : la structure compte."""
        findings = check_referentials(
            items={"SF-1": item("SF-1", exclusions=["GENERIC"])}, bom_links=[]
        )
        assert "ASSEMBLY_WITHOUT_BOM" in codes(findings)

    def test_the_two_exclusions_combine(self):
        """Un article peut être hors GENERIQUE *et* ignoré en nomenclature."""
        article = item("SF-1", exclusions=["GENERIC", "BOM"])
        assert article.excluded_from_generic and article.excluded_from_bom
        assert not article.excluded_everywhere
        findings = check_referentials(items={"SF-1": article}, bom_links=[])
        assert "ASSEMBLY_WITHOUT_BOM" not in codes(findings)

    def test_a_retired_bill_is_silenced_the_same_way(self):
        findings = check_referentials(
            items={"SF-1": item("SF-1", exclusions=["BOM"])},
            bom_links=[
                BomLink(campaign_id="c", parent_item="SF-1", child_item="C-1",
                        qty_per=1, active=False)
            ],
        )
        assert "ASSEMBLY_BOM_RETIRED" not in codes(findings)
