"""Ce que quatre extractions réelles du même jour ont appris.

Le jour de l'inventaire, l'export des journaux de comptage est rejoué toutes
les quinze à trente minutes sur des journaux encore en cours de remplissage, et
chaque extraction est rechargée dans l'application. Quatre extractions d'essai
— 614, 637, 1 061 et 1 075 lignes, prises à 07:41, 07:51, 11:42 et 11:54 — ont
servi à répéter cela en petit.

Ce qu'elles ont montré
----------------------
**Le « Numéro de ligne » n'est pas celui de l'ERP.** Sur les journaux comptés
par étiquette, l'export descend « 1, -1, -2, … -79 » ; un journal porte un
« 13,5 ». La chaîne d'extraction les invente pour départager des lignes que
l'ERP numérote pareil. Ils dépendent donc de l'**ordre des lignes** : une
étiquette saisie entre deux extractions décale tout ce qui suit, et la même
palette physique change de clé d'un quart d'heure à l'autre — c'est le scénario
de `TestUneEtiquetteInsereeNeDecaleRien`.

**Et comme clé d'unicité, il manquait des deux côtés à la fois.** Trop strict :
« 13,5 » ne se lit pas en entier, et le lecteur refusait la ligne — une quantité
comptée perdue pour une colonne technique. Trop lâche : la colonne est
facultative, le contrôle de doublon exemptait les lignes qui ne la portent pas,
et l'index unique tenait deux NULL pour distincts.

**Les coordonnées, elles, ne dépendent de rien.** Journal, site, entrepôt,
emplacement, étiquette, article : clé vérifiée unique sur les quatre fichiers
— 0 doublon sur 1 075 lignes — et stable de l'un à l'autre.

Les lignes de ces contrôles sont celles des fichiers, à l'identique : la série
d'étiquettes de NPEM-563610, où seule l'étiquette distingue cinq lignes du même
article au même endroit, et la ligne « 13,5 » de NPEM-563678.
"""

from __future__ import annotations

import io

import pytest

from inventory.ingest import get_contract, map_journal_lines
from inventory.ingest.parser import parse_tabular_bytes

CONTRACT = get_contract("count_journal_lines")

#: Les en-têtes de l'export, dans leur orthographe d'origine.
HEADERS = (
    "Journal ERP", "Numéro de ligne", "Site", "Entrepôt", "Emplacement",
    "Etiquette", "Numéro de série", "Numéro d'article", "Stock ERP",
    "Qté Comptée", "Statut qualité", "Journal ERP Source", "Description Journal",
    "Type Journal", "Est posté ERP", "Date et heure postage ERP",
)

#: Cinq lignes de NPEM-563610, telles qu'elles sortent de l'export : même
#: journal, même site, même entrepôt, même emplacement, **même article** — seule
#: l'étiquette les distingue. L'export les numérote « 1, -1, -2, -3, -4 ».
ETIQUETTES = ("405914630", "405914632", "405914633", "405914635", "405914639")
NUMEROS = (1, -1, -2, -3, -4)


def _ligne(
    *, journal="NPEM-563610", numero=1, entrepot="QUAL", emplacement="APQP/EI/PP",
    etiquette="405914630", article="P-00208427", stock=0, compte=9,
    description="Inventaire par étiquette", poste="08/09/2026 07:09",
) -> tuple:
    return (
        journal, numero, "TRE", entrepot, emplacement, etiquette, None, article,
        stock, compte, None, journal, description, "INVE", "Yes", poste,
    )


def _classeur(*lignes: tuple) -> bytes:
    """Un vrai `.xlsx`, lu par le vrai lecteur : c'est le chemin de l'import."""
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(HEADERS))
    for ligne in lignes:
        sheet.append(list(ligne))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _lire(*lignes: tuple):
    result = parse_tabular_bytes(CONTRACT, _classeur(*lignes), filename="j.xlsx")
    return result


def _serie(numeros=NUMEROS, etiquettes=ETIQUETTES) -> list[tuple]:
    return [
        _ligne(numero=numero, etiquette=etiquette)
        for numero, etiquette in zip(numeros, etiquettes, strict=True)
    ]


def _cles(lignes) -> list[tuple]:
    return [
        (l.journal_number, l.site_id, l.warehouse_id, l.location_id,
         l.label_id, l.item_number)
        for l in lignes
    ]


class TestLaSerieDEtiquettesArriveEntiere:
    """Le cas dominant de l'export : une ligne par étiquette."""

    def test_les_cinq_lignes_passent_sans_erreur(self):
        result = _lire(*_serie())
        assert result.errors == []
        assert len(result.rows) == 5

    def test_aucune_n_est_signalee_comme_doublon(self):
        """Sous une clé sans étiquette, quatre d'entre elles le seraient."""
        result = _lire(*_serie())
        assert result.duplicate_keys == []

    def test_les_cinq_arrivent_jusqu_aux_lignes_mappees(self):
        result = _lire(*_serie())
        lignes, erreurs, _ = map_journal_lines(result.rows)
        assert erreurs == []
        assert [l.label_id for l in lignes] == list(ETIQUETTES)


class TestUnNumeroAberrantNeRejetteRien:
    """« Ignore complètement le numéro de ligne, peu importe son format. »

    La ligne « 13,5 » de NPEM-563678 était refusée en entier par le lecteur,
    qui attendait un entier. Une quantité comptée disparaissait donc pour une
    colonne technique dont l'application n'a plus l'usage.
    """

    #: La ligne réelle, telle qu'elle est dans les quatre fichiers.
    TREIZE_ET_DEMI = _ligne(
        journal="NPEM-563678", numero=13.5, entrepot="ATP", emplacement="SJ1",
        etiquette="001673906", article="P-00250672", stock=1, compte=1,
        description="Inv Hebdo TCR", poste="08/09/2026 08:03",
    )

    @pytest.mark.parametrize("numero", [13.5, "13,5", -79, 0, None, "", "abc"])
    def test_la_ligne_arrive_quel_que_soit_le_numero(self, numero):
        ligne = (*self.TREIZE_ET_DEMI[:1], numero, *self.TREIZE_ET_DEMI[2:])
        result = _lire(ligne)
        assert result.errors == []
        lignes, erreurs, _ = map_journal_lines(result.rows)
        assert erreurs == []
        assert [l.qty for l in lignes] == [1]

    def test_la_colonne_est_annoncee_comme_inutilisee(self):
        """L'écran le dit, et c'est exact : elle est lue, puis laissée."""
        result = _lire(self.TREIZE_ET_DEMI)
        assert "Numéro de ligne" in result.unknown_columns


class TestUneEtiquetteInsereeNeDecaleRien:
    """Le cœur du changement, et la raison pour laquelle il fallait le faire.

    Entre deux extractions distantes d'un quart d'heure, une étiquette est
    saisie **au milieu** de la série. L'export renumérote tout ce qui suit : la
    palette 405914633 passe de « -2 » à « -3 » sans avoir bougé.

    Rien dans la chaîne ne pourrait alors dire « c'est la même palette qu'il y a
    un quart d'heure » : ni le doublon signalé à l'exploitant, qui nommerait une
    ligne qui n'existe plus, ni l'index unique, ni un rapprochement entre deux
    extractions. La clé doit donc tenir à ce que la ligne **désigne**, et c'est
    ce que ces contrôles vérifient : les coordonnées ne bougent pas quand les
    numéros, eux, bougent tous.
    """

    #: L'étiquette saisie entre-temps, insérée en troisième position.
    INSEREE = "405914631"

    def _apres(self) -> list[tuple]:
        etiquettes = (*ETIQUETTES[:2], self.INSEREE, *ETIQUETTES[2:])
        return [
            _ligne(numero=numero, etiquette=etiquette)
            for numero, etiquette in zip(
                (1, -1, -2, -3, -4, -5), etiquettes, strict=True
            )
        ]

    def test_le_numero_de_ligne_bouge_bel_et_bien(self):
        """Le fait de départ : sans lui, le reste ne prouverait rien."""
        avant = {e: n for n, e in zip(NUMEROS, ETIQUETTES, strict=True)}
        apres = {ligne[5]: ligne[1] for ligne in self._apres()}
        assert avant["405914633"] == -2
        assert apres["405914633"] == -3

    def test_les_cles_des_lignes_communes_ne_bougent_pas(self):
        avant, _, _ = map_journal_lines(_lire(*_serie()).rows)
        apres, _, _ = map_journal_lines(_lire(*self._apres()).rows)

        communes = [c for c in _cles(apres) if c[4] != self.INSEREE]
        assert communes == _cles(avant)

    def test_la_nouvelle_etiquette_est_une_ligne_de_plus(self):
        """Elle s'ajoute, elle ne remplace personne."""
        apres, erreurs, _ = map_journal_lines(_lire(*self._apres()).rows)
        assert erreurs == []
        assert len(apres) == len(ETIQUETTES) + 1
        assert self.INSEREE in {l.label_id for l in apres}


class TestLeDoublonResteVu:
    """Élargir la clé ne doit pas revenir à ne plus rien signaler."""

    def test_la_meme_etiquette_deux_fois_est_un_doublon(self):
        result = _lire(*_serie(), _ligne(etiquette=ETIQUETTES[0]))
        assert len(result.duplicate_keys) == 1

    def test_le_meme_article_deux_fois_sans_etiquette_aussi(self):
        """Un journal en quantité (INVV) ne porte pas d'étiquette.

        Sa clé devient « une ligne par article et par emplacement » — son grain
        exact — et deux lignes identiques y sont un doublon d'extraction, pas
        deux palettes.
        """
        result = _lire(_ligne(etiquette=None), _ligne(etiquette=None))
        assert len(result.duplicate_keys) == 1

    def test_deux_emplacements_ne_le_sont_pas(self):
        """La palette retrouvée ailleurs : un départ et une arrivée."""
        result = _lire(
            _ligne(),
            _ligne(entrepot="ATP", emplacement="QUAI EXP", stock=0, compte=9),
        )
        assert result.duplicate_keys == []
