"""« Stock physique » nomme la quantité des deux côtés.

Ce qui est arrivé
-----------------
L'ERP intitule sa colonne de quantité « Stock physique », dans l'export du stock
comme dans celui des lignes de journaux de comptage. La grille du stock ERP
l'acceptait — c'est son propre intitulé — mais celle des journaux attendait
« Quantité comptée » et rien d'autre. Le fichier arrivait donc refusé sur une
colonne obligatoire absente, alors qu'elle était là, sous le nom que l'ERP lui
donne. Il fallait la renommer à la main avant chaque chargement, c'est-à-dire
rouvrir le fichier — l'aller-retour par le tableur que cette application existe
pour supprimer.

Ce que ces contrôles tiennent
-----------------------------
L'intitulé sur les deux grilles, la quantité qui arrive bien à destination, et
le fait qu'aucun alias n'en masque un autre : ``_alias_map`` retient la première
colonne qui revendique un nom, et une collision enverrait silencieusement la
quantité dans le mauvais champ.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from inventory.ingest.contracts import CONTRACTS, get_contract
from inventory.ingest.parser import _alias_map, normalise_header, parse_clipboard

#: La grille, et le champ que « Stock physique » doit y désigner.
QUANTITY_OF = {"book_stock": "qty", "count_journal_lines": "counted_quantity"}


class TestThePhysicalStockHeading:
    @pytest.mark.parametrize("key,field", sorted(QUANTITY_OF.items()))
    def test_it_names_the_quantity_column(self, key, field):
        spec = _alias_map(get_contract(key)).get(normalise_header("Stock physique"))
        assert spec is not None, f"{key} refuse « Stock physique »"
        assert spec.name == field

    @pytest.mark.parametrize(
        "spelling", ["Stock physique", "STOCK PHYSIQUE", "  Stock  physique "]
    )
    def test_the_spelling_of_the_export_does_not_matter(self, spelling):
        spec = _alias_map(get_contract("count_journal_lines")).get(
            normalise_header(spelling)
        )
        assert spec is not None and spec.name == "counted_quantity"

    @staticmethod
    def _pasted(heading: str, quantity: str):
        """Le geste réel : un bloc copié depuis l'export, en-tête compris."""
        block = (
            f"N° de journal\tNuméro d'article\tEntrepôt\t{heading}\n"
            f"INVV-000123\tP-1\tB06\t{quantity}"
        )
        return parse_clipboard(get_contract("count_journal_lines"), block)

    def test_the_quantity_actually_lands_in_the_row(self):
        """Reconnaître l'en-tête sans porter la valeur ne servirait à rien."""
        result = self._pasted("Stock physique", "12,5")
        assert result.rows, result.errors
        assert str(result.rows[0]["counted_quantity"]) == "12.5"
        assert "Stock physique" not in result.unknown_columns

    def test_the_former_heading_still_works(self):
        """L'ajout d'un nom n'en retire aucun : les fichiers en circulation
        portent encore « Quantité comptée »."""
        result = self._pasted("Quantité comptée", "7")
        assert result.rows, result.errors
        assert str(result.rows[0]["counted_quantity"]) == "7"


class TestNoAliasShadowsAnother:
    """Le premier champ qui revendique un nom l'emporte, en silence.

    Ajouter un alias à une colonne est un geste d'une ligne, et c'est là que la
    collision se glisse : deux colonnes qui répondent à « quantité » laisseraient
    la seconde vide sans que rien ne le dise — un journal chargé à zéro plutôt
    qu'un import refusé.
    """

    #: La seule collision existante, laissée telle quelle et nommée ici.
    #:
    #: Dans l'export « Transactions de stock », « Référence » désigne la nature
    #: du mouvement (« Comptage », « Ajustement de stock ») ; ailleurs, c'est un
    #: numéro d'article, et c'est ce sens-là qui l'emporte puisque
    #: ``item_number`` est déclaré en premier. Trancher demanderait de savoir
    #: lequel des deux fichiers arrive réellement, et se tromper enverrait des
    #: numéros d'article dans une colonne de nature. La ligne est donc épinglée
    #: plutôt que corrigée à l'aveugle — et toute **autre** collision échoue.
    KNOWN: ClassVar[dict[str, set[str]]] = {"adjustments": {"reference"}}

    @staticmethod
    def _clashes(key: str) -> list[str]:
        claimed: dict[str, str] = {}
        found: list[str] = []
        for spec in CONTRACTS[key].fields:
            for candidate in (spec.name, spec.label, *spec.aliases):
                name = normalise_header(candidate)
                if not name:
                    continue
                first = claimed.setdefault(name, spec.name)
                if first != spec.name:
                    found.append(name)
        return found

    @pytest.mark.parametrize("key", sorted(CONTRACTS))
    def test_within_one_grid_every_name_designates_one_column(self, key):
        unexpected = set(self._clashes(key)) - TestNoAliasShadowsAnother.KNOWN.get(
            key, set()
        )
        assert not unexpected, (
            f"{key} — noms revendiqués par deux colonnes : {sorted(unexpected)}. "
            "La seconde restera vide sans que rien ne le signale."
        )

    def test_the_pinned_clash_is_still_there(self):
        """Épingler une collision résolue depuis longtemps masquerait la
        suivante : la ligne s'enlève le jour où elle ne décrit plus rien."""
        assert "reference" in self._clashes("adjustments")

    def test_there_are_grids_to_check(self):
        assert len(CONTRACTS) >= 8
