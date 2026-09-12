"""Le groupe d'articles se lit à l'écran, pas seulement dans le classeur.

Le groupe fonctionnel de l'ERP — ``item_group_label`` à la source — traversait
déjà toute la pile : la lecture Unity Catalog le rapporte, le modèle le porte,
la base le stocke, la réponse HTTP l'envoie, et le classeur exporté a une
colonne « Groupe ». Un seul endroit ne le montrait pas : l'écran. C'est là que
quelqu'un décide d'exclure une famille entière du périmètre, et il devait pour
cela exporter le classeur, regarder, puis revenir cocher des lignes.

Deux moitiés font que la colonne n'est pas vide, et chacune se casse sans
bruit : la réponse porte la clé, et l'écran lit **cette** clé. Une colonne
déclarée sur un nom voisin — ``itemGroup``, ``group`` — rend une colonne de
tirets sans que rien n'échoue. Les deux sont donc vérifiées ici, ensemble.
"""

from __future__ import annotations

import re

from tests.conftest import screen_source

from inventory.api.routers.data import _item_json
from inventory.domain.models import Item

#: Le nom de la colonne, écrit une fois et lu des deux côtés — c'est le point.
KEY = "item_group"


def an_item(**changes) -> Item:
    return Item(
        campaign_id="c", item_number="P-00042", name="VIS TETE HEXAGONALE M6",
        **{"item_group": "VISSERIE", **changes},
    )


class TestLaReponseLePorte:
    def test_sous_le_nom_que_l_ecran_lit(self):
        assert _item_json(an_item())[KEY] == "VISSERIE"

    def test_un_article_sans_groupe_rend_une_chaîne_vide(self):
        """Et non l'absence de clé : une colonne qui disparaît sur certaines
        lignes est une colonne qui se lit de travers sur toutes les autres."""
        payload = _item_json(an_item(item_group=""))
        assert KEY in payload and payload[KEY] == ""


class TestLEcranLAffiche:
    def source(self) -> str:
        return screen_source("features/Preparation.tsx")

    def test_la_colonne_groupe_existe(self):
        assert re.search(
            rf"key: '{KEY}',\s*label: 'Groupe'", self.source()
        ), "la vue Articles ne déclare pas de colonne « Groupe »"

    def test_elle_se_filtre_par_valeurs(self):
        """C'est la maille d'une exclusion de famille : « montre-moi la
        visserie » veut dire cocher une valeur, pas taper un mot."""
        block = re.search(
            rf"\{{ key: '{KEY}'.*?\}},", self.source(), re.S
        )
        assert block and "filter: 'choice'" in block.group(0)
