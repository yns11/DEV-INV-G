"""Le classeur de repli : les mêmes chiffres, sans l'application.

Ce que ces contrôles gardent tient en une phrase : **le classeur doit rendre,
une fois recalculé par un tableur, exactement ce que le moteur produit sur les
mêmes données.** C'est une exigence inhabituelle, et elle vient de ce que le
fichier est : une seconde écriture des règles de consolidation, en formules.
Cette duplication est voulue — un repli qui recopierait les résultats du moteur
ne serait pas un repli — mais elle ne tient que si quelque chose la surveille.

Deux étages, et ils ne protègent pas la même chose.

Le premier **recalcule réellement** le classeur et compare ligne à ligne avec
:func:`~inventory.domain.consolidation.consolidate_generic`. C'est le seul
contrôle qui attrape une formule juste en apparence : une plage d'une ligne trop
courte, un indice de colonne de ``VLOOKUP`` décalé, un critère qui ne
correspond à aucune section. Il exige LibreOffice Calc et s'ignore sans lui.

Le second ne dépend de rien et tourne toujours : il relit le classeur écrit et
vérifie que ses plages couvrent ce qui a été écrit, que les feuilles citées
existent, et que les colonnes cherchées sont à la place où l'en-tête les met.
Ce sont les ruptures qu'un renommage ou une colonne insérée produit — et elles
casseraient silencieusement le fichier, qui continuerait de s'ouvrir.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
from decimal import Decimal

import pytest
import tableur

from inventory.domain.bom import BomIndex
from inventory.domain.consolidation import (
    ConsolidationInput,
    ZoneCounts,
    consolidate_generic,
    zone_pass_lines,
)
from inventory.domain.enums import CountLineKind, CountSection, ItemType, SheetPass
from inventory.domain.models import (
    ArbitrationLine,
    BomLink,
    CountSheet,
    CountSheetLine,
    Item,
    Zone,
)
from inventory.reporting.fallback import (
    SPARE_ROWS_PER_ZONE,
    build_consolidation_fallback,
)

openpyxl = pytest.importorskip("openpyxl")

_ids = itertools.count(1)


def _id() -> str:
    return f"id-{next(_ids)}"


# --------------------------------------------------------------------------- #
# Un jeu de données qui porte chaque règle au moins une fois
# --------------------------------------------------------------------------- #

def _item(number: str, item_type=ItemType.COMPONENT, **kw) -> Item:
    return Item(campaign_id="c", item_number=number, item_type=item_type, **kw)


ITEMS = {
    i.item_number: i
    for i in [
        _item("MEL", ItemType.FINISHED, name="Moteur assemblé", std_price="1000"),
        _item("STATOR", ItemType.SEMI_FINISHED, name="Stator", std_price="300"),
        _item("SOUS-ENS", ItemType.SEMI_FINISHED, name="Sous-ensemble",
              std_price="40"),
        _item("VIS", name="Vis M6", std_price="0.5"),
        _item("COLLE", name="Colle", std_price="80", unit="KG"),
        _item("AIMANT", name="Aimant", std_price="12"),
        # Exclu de GENERIQUE : compté, jamais posté.
        _item("EMBLG", ItemType.PACKAGING, name="Emballage", std_price="2",
              exclusions=["GENERIC"]),
        # Exclu des nomenclatures : il disparaît de l'éclatement.
        _item("HUILE", name="Huile", std_price="5", exclusions=["BOM"]),
        # Jamais compté, mais l'ERP en porte : le journal doit le solder à zéro.
        _item("JOINT", name="Joint", std_price="1.2"),
    ]
}

BOM_LINKS = [
    BomLink(campaign_id="c", parent_item="MEL", child_item="STATOR", qty_per="1"),
    BomLink(campaign_id="c", parent_item="MEL", child_item="SOUS-ENS", qty_per="2"),
    BomLink(campaign_id="c", parent_item="MEL", child_item="HUILE", qty_per="0.25"),
    BomLink(campaign_id="c", parent_item="SOUS-ENS", child_item="VIS", qty_per="4"),
    BomLink(campaign_id="c", parent_item="SOUS-ENS", child_item="AIMANT",
            qty_per="1.5"),
    # Version périmée : elle ne doit compter nulle part.
    BomLink(campaign_id="c", parent_item="MEL", child_item="COLLE", qty_per="9",
            active=False),
]

BOOK_STOCK = {
    "VIS": Decimal("500"),
    "JOINT": Decimal("40"),
    "COLLE": Decimal("3"),
}

CLOSED = dt.datetime(2026, 6, 30, 12, 0, tzinfo=dt.UTC)


def _sheet(zone_id: str, pass_no: SheetPass) -> CountSheet:
    return CountSheet(id=_id(), campaign_id="c", zone_id=zone_id, pass_no=pass_no)


def _line(sheet_id: str, row) -> CountSheetLine:
    number, section, qty = row
    return CountSheetLine(
        id=_id(), sheet_id=sheet_id, campaign_id="c", item_number=number,
        section=section, qty_manual=qty,
    )


def _zone(
    *, code: str, rows_1, rows_2=None, arbitrations=(), passes=2, closed=CLOSED,
) -> ZoneCounts:
    zone = Zone(
        id=_id(), campaign_id="c", code=code, label=f"Zone {code}",
        passes=passes, closed_at=closed,
    )
    s1 = _sheet(zone.id, SheetPass.PASS_1)
    sheets = [s1]
    lines = {s1.id: [_line(s1.id, row) for row in rows_1]}
    if passes >= 2:
        s2 = _sheet(zone.id, SheetPass.PASS_2)
        sheets.append(s2)
        lines[s2.id] = [
            _line(s2.id, row) for row in (rows_1 if rows_2 is None else rows_2)
        ]
    return ZoneCounts(
        zone=zone, sheets=sheets, lines_by_sheet=lines, arbitrations=list(arbitrations)
    )


def _decision(zone: ZoneCounts, number, section, *, p1, p2, qty) -> ArbitrationLine:
    return ArbitrationLine(
        id=_id(), campaign_id="c", zone_id=zone.zone.id, item_number=number,
        section=section, qty_pass_1=p1, qty_pass_2=p2, qty_arbitrated=qty,
        decided_by="alice", decided_at=CLOSED,
    )


def _payload() -> ConsolidationInput:
    """Une campagne miniature qui touche chaque règle du moteur.

    Elle est petite exprès : un écart entre le classeur et le moteur doit
    pouvoir se lire à l'œil sur le fichier produit, pas seulement dans une
    assertion.
    """
    # Les deux passages s'accordent : le comptage n°2 fait foi.
    b15 = _zone(code="B15", rows_1=[
        ("VIS", CountSection.LINE_SIDE, 120),
        ("COLLE", CountSection.LINE_SIDE, "2.5"),
        ("EMBLG", CountSection.LINE_SIDE, 30),
    ])
    # Une divergence tranchée, une divergence en attente, un article compté par
    # une seule équipe.
    b6est = _zone(
        code="B6 EST",
        rows_1=[
            ("VIS", CountSection.LINE_SIDE, 100),
            ("AIMANT", CountSection.LINE_SIDE, 12),
            ("STATOR", CountSection.WIP_OK, 4),
        ],
        rows_2=[
            ("VIS", CountSection.LINE_SIDE, 90),
            ("AIMANT", CountSection.LINE_SIDE, 20),
        ],
    )
    b6est.arbitrations.append(
        _decision(b6est, "VIS", CountSection.LINE_SIDE, p1=100, p2=90, qty=95)
    )
    # Du WIP à éclater, un produit fini compté en bord de ligne (écarté), et une
    # référence absente du référentiel.
    atelier = _zone(code="ATELIER", rows_1=[
        ("MEL", CountSection.WIP, 3),
        ("MEL", CountSection.LINE_SIDE, 1),
        ("INCONNU-42", CountSection.LINE_SIDE, 7),
        ("SOUS-ENS", CountSection.WIP, 5),
    ])
    # Zone à un seul passage, et pas encore terminée.
    labo = _zone(code="LABO", rows_1=[("COLLE", CountSection.LINE_SIDE, "0.75")],
                 passes=1, closed=None)

    return ConsolidationInput(
        campaign_id="c",
        zones=[b15, b6est, atelier, labo],
        items=ITEMS,
        bom=BomIndex(
            BOM_LINKS,
            excluded_children={
                i.item_number for i in ITEMS.values() if i.excluded_from_bom
            },
            max_depth=10,
        ),
        book_stock=BOOK_STOCK,
        require_done_zones=False,
    )


def _build(payload: ConsolidationInput) -> bytes:
    return build_consolidation_fallback(
        payload,
        campaign_code="INV-2026-06",
        campaign_label="Inventaire de juin",
        count_date=dt.date(2026, 6, 30),
        generic_key="B06VRAC / GENERIQUE",
        provenance={"Généré par": "alice"},
    )


@pytest.fixture(scope="module")
def payload() -> ConsolidationInput:
    return _payload()


@pytest.fixture(scope="module")
def workbook(payload: ConsolidationInput) -> bytes:
    return _build(payload)


@pytest.fixture(scope="module")
def book(workbook: bytes):
    import io

    return openpyxl.load_workbook(io.BytesIO(workbook))


@pytest.fixture(scope="module")
def recalculated(workbook: bytes) -> dict[str, list[list]]:
    if not tableur.AVAILABLE:
        pytest.skip(tableur.WHY_NOT)
    return tableur.recalculate(workbook)


# --------------------------------------------------------------------------- #
# Lire le classeur recalculé
# --------------------------------------------------------------------------- #

def _rows(sheets: dict[str, list[list]], name: str) -> list[dict]:
    """Les lignes d'une feuille, nommées par ses en-têtes."""
    grid = sheets[name]
    headers = [str(h) for h in grid[0]]
    return [dict(zip(headers, row, strict=False)) for row in grid[1:]]


def _journal(sheets: dict[str, list[list]]) -> dict[str, dict]:
    return {
        row["Référence"]: row
        for row in _rows(sheets, "Journal consolidé")
        if row.get("Référence")
    }


def _engine(payload: ConsolidationInput) -> dict[str, dict[str, Decimal]]:
    result = consolidate_generic(payload)
    return {
        line.item_number: {
            "total": line.qty,
            "lineSide": line.qty_line_side,
            "wipOk": line.qty_wip_ok,
            "wipExploded": line.qty_wip_exploded,
        }
        for line in result.lines
    }


def _close(value, expected: Decimal) -> bool:
    """Le tableur calcule en binaire, le moteur en décimal.

    Comparer à 1e-6 près est ce que la précision des quantités autorise — six
    décimales — et non une tolérance choisie pour faire passer le contrôle.
    """
    return abs(Decimal(str(value or 0)) - expected) <= Decimal("0.000001")


# --------------------------------------------------------------------------- #
# 1. Le contrôle qui compte : recalculer, et comparer au moteur
# --------------------------------------------------------------------------- #

class TestLeClasseurRendLesChiffresDuMoteur:
    def test_chaque_ligne_postee_par_le_moteur_est_dans_le_classeur(
        self, recalculated, payload
    ):
        journal = _journal(recalculated)
        for number, expected in _engine(payload).items():
            row = journal.get(number)
            assert row is not None, f"{number} n'a pas de ligne au classeur"
            assert row["Postée"] == "oui", f"{number} n'est pas postée"
            assert _close(row["Quantité totale"], expected["total"]), (
                f"{number} : classeur {row['Quantité totale']}, "
                f"moteur {expected['total']}"
            )

    def test_les_trois_sections_se_retrouvent_une_par_une(
        self, recalculated, payload
    ):
        """Le total juste par compensation ne serait pas une bonne nouvelle.

        Un bord de ligne comptant du WIP et un WIP comptant du bord de ligne
        donnent le même total et un journal faux : la ligne postée serait juste,
        et la décomposition qu'on relit pour l'expliquer serait fausse.
        """
        journal = _journal(recalculated)
        for number, expected in _engine(payload).items():
            row = journal[number]
            for column, key in (
                ("Bord de ligne", "lineSide"),
                ("WIP assemblé", "wipOk"),
                ("WIP éclaté", "wipExploded"),
            ):
                assert _close(row[column], expected[key]), (
                    f"{number} / {column} : classeur {row[column]}, "
                    f"moteur {expected[key]}"
                )

    def test_le_classeur_ne_poste_rien_que_le_moteur_ne_poste_pas(
        self, recalculated, payload
    ):
        posted = {
            number for number, row in _journal(recalculated).items()
            if row["Postée"] == "oui"
        }
        assert posted == set(_engine(payload))

    def test_la_valeur_suit_la_quantite(self, recalculated, payload):
        journal = _journal(recalculated)
        for number, expected in _engine(payload).items():
            price = ITEMS[number].std_price
            assert _close(
                journal[number]["Valeur €"],
                (expected["total"] * price).quantize(Decimal("0.01")),
            )


class TestLesReglesMetierTiennentDansLeTableur:
    def test_un_arbitrage_tranche(self, recalculated):
        """95, et non 100 ni 90 : c'est la décision qui fait foi."""
        row = next(
            r for r in _rows(recalculated, "Z_B6 EST")
            if r["Référence"] == "VIS"
        )
        assert row["État"] == "arbitré"
        assert _close(row["Quantité retenue"], Decimal("95"))

    def test_une_divergence_non_tranchee_ne_compte_nulle_part(self, recalculated):
        """12 contre 20, personne n'a tranché : la ligne reste vide.

        Retenir l'un des deux « en attendant » posterait un chiffre que
        personne n'a choisi — et l'écart qui en sortirait serait discuté comme
        s'il était mesuré.
        """
        row = next(
            r for r in _rows(recalculated, "Z_B6 EST")
            if r["Référence"] == "AIMANT"
        )
        assert row["État"] == "à arbitrer"
        assert row["Quantité retenue"] in (None, "")

    def test_un_seul_passage_porte_la_reference(self, recalculated):
        """Le stator n'est que sur la feuille n°1 : c'est elle qui compte."""
        row = next(
            r for r in _rows(recalculated, "Z_B6 EST")
            if r["Référence"] == "STATOR"
        )
        assert row["État"] == "un seul comptage"
        assert _close(row["Quantité retenue"], Decimal("4"))

    def test_un_produit_fini_compte_en_bord_de_ligne_ne_compte_pas(
        self, recalculated
    ):
        """Ses composants sont déjà comptés par l'éclatement, et il vaut plus cher."""
        assert _close(_journal(recalculated)["MEL"]["Bord de ligne"], Decimal("0"))

    def test_un_article_exclu_de_generique_ne_part_pas(self, recalculated):
        row = _journal(recalculated)["EMBLG"]
        assert row["Statut"] == "exclu"
        assert row["Postée"] == "non"
        assert _close(row["Quantité totale"], Decimal("0"))

    def test_un_composant_exclu_des_nomenclatures_ne_sort_pas_de_leclatement(
        self, recalculated
    ):
        """L'huile est dans la structure du MEL et hors de son éclatement."""
        parents = {
            (r["Composé"], r["Composant"])
            for r in _rows(recalculated, "Éclatement")
            if r["Composé"]
        }
        assert ("MEL", "HUILE") not in parents
        assert _close(_journal(recalculated)["HUILE"]["WIP éclaté"], Decimal("0"))

    def test_une_version_perimee_nexplose_rien(self, recalculated):
        """La colle n'est plus au MEL : seul le comptage direct la porte."""
        assert _close(_journal(recalculated)["COLLE"]["WIP éclaté"], Decimal("0"))

    def test_leclatement_sarrete_au_premier_article_qui_porte_du_stock(
        self, recalculated
    ):
        """Trois MEL donnent six sous-ensembles, et non vingt-quatre vis.

        Un sous-ensemble compté en WIP n'a pas encore été consommé dans l'ERP :
        il y porte son propre stock, et créditer ses vis à sa place les
        compterait deux fois. L'éclatement s'arrête donc au premier article qui
        porte du stock — les cinq sous-ensembles comptés directement, eux,
        donnent bien leurs vingt vis.

        Le tableur ne sait pas descendre une structure ; c'est la feuille
        « Éclatement », aplatie à la génération, qui le fait pour lui, et ce
        contrôle dit qu'elle s'arrête au même endroit que le moteur.
        """
        journal = _journal(recalculated)
        assert _close(journal["SOUS-ENS"]["WIP éclaté"], Decimal("6"))
        assert _close(journal["VIS"]["WIP éclaté"], Decimal("20"))

    def test_un_article_que_lerp_porte_et_que_personne_na_compte_est_solde(
        self, recalculated
    ):
        row = _journal(recalculated)["JOINT"]
        assert row["Statut"] == "soldé à zéro"
        assert row["Postée"] == "oui"

    def test_une_reference_hors_referentiel_est_nommee_sans_etre_postee(
        self, recalculated
    ):
        """Écartée du journal, visible dans « Data » : la quantité n'est pas perdue."""
        assert "INCONNU-42" not in _journal(recalculated)
        unknown = [
            r for r in _rows(recalculated, "Data")
            if r["Référence"] == "INCONNU-42"
        ]
        assert unknown and all(r["Au référentiel"] == "non" for r in unknown)

    def test_les_controles_de_la_premiere_feuille_disent_ce_qui_cloche(
        self, recalculated
    ):
        readme = {
            str(row[0]): row[1]
            for row in recalculated["Lisez-moi"] if row and row[0]
        }
        assert readme["Lignes en attente d'arbitrage"] == 1
        assert readme["Lignes hors référentiel"] == 1
        assert readme["Articles retenus au journal"] == len(
            [
                n for n, r in _journal(recalculated).items()
                if r["Statut"] == "retenu"
            ]
        )

    def test_le_total_du_classeur_est_celui_du_moteur(self, recalculated, payload):
        readme = {
            str(row[0]): row[1]
            for row in recalculated["Lisez-moi"] if row and row[0]
        }
        assert _close(
            readme["Quantité postée au journal"],
            consolidate_generic(payload).total_qty,
        )


class TestLeClasseurSeRecalculeQuandUneDonneeChange:
    """La demande, prise au mot : corriger une case doit tout mettre à jour.

    Le contrôle modifie le classeur **comme un utilisateur le ferait** — une
    quantité de comptage, rien d'autre — puis le fait recalculer et regarde le
    journal. C'est ce qui distingue des formules de vraies formules : un
    classeur dont les cellules porteraient les chiffres du moteur passerait
    tous les contrôles précédents et échouerait ici.
    """

    @staticmethod
    def _edit(content: bytes, sheet: str, cell: str, value) -> bytes:
        import io

        book = openpyxl.load_workbook(io.BytesIO(content))
        book[sheet][cell] = value
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    @staticmethod
    def _row_of(payload: ConsolidationInput, zone: int, number: str) -> int:
        lines = zone_pass_lines(payload.zones[zone])
        return 2 + next(
            i for i, line in enumerate(lines) if line.item_number == number
        )

    def test_corriger_un_comptage_deplace_le_journal(self, workbook, payload):
        if not tableur.AVAILABLE:
            pytest.skip(tableur.WHY_NOT)
        before = _journal(tableur.recalculate(workbook))["VIS"]["Quantité totale"]

        # B15 compte 120 vis en bord de ligne sur les deux passages ; on
        # recompte à deux, il y en a 130.
        row = self._row_of(payload, 0, "VIS")
        edited = self._edit(workbook, "Z_B15", f"E{row}", 130)
        edited = self._edit(edited, "Z_B15", f"F{row}", 130)
        after = _journal(tableur.recalculate(edited))["VIS"]["Quantité totale"]

        assert _close(after, Decimal(str(before)) + 10)

    def test_corriger_un_seul_des_deux_comptages_rouvre_larbitrage(
        self, workbook, payload
    ):
        """La règle survit au passage en tableur, et c'est le point.

        Corriger un seul passage crée une divergence : la ligne cesse de
        compter et redemande un arbitrage. Un classeur qui aurait retenu 130
        « puisque c'est le dernier chiffre saisi » aurait posté une quantité que
        la seconde équipe n'a jamais confirmée.
        """
        if not tableur.AVAILABLE:
            pytest.skip(tableur.WHY_NOT)
        before = _journal(tableur.recalculate(workbook))["VIS"]["Quantité totale"]

        row = self._row_of(payload, 0, "VIS")
        sheets = tableur.recalculate(self._edit(workbook, "Z_B15", f"F{row}", 130))

        zone = next(
            r for r in _rows(sheets, "Z_B15") if r["Référence"] == "VIS"
        )
        assert zone["État"] == "à arbitrer"
        assert _close(
            _journal(sheets)["VIS"]["Quantité totale"],
            Decimal(str(before)) - 120,
        )

    def test_trancher_un_arbitrage_dans_le_classeur_le_fait_compter(
        self, workbook, payload
    ):
        if not tableur.AVAILABLE:
            pytest.skip(tableur.WHY_NOT)
        # 12 contre 20 : tant que personne ne tranche, l'aimant ne compte pas.
        journal = _journal(tableur.recalculate(workbook))
        assert _close(journal["AIMANT"]["Bord de ligne"], Decimal("0"))

        # Une quantité écrite dans la colonne « Quantité arbitrée » suffit : la
        # décision se prend dans le classeur, comme elle se prendrait à l'écran.
        row = self._row_of(payload, 1, "AIMANT")
        after = _journal(
            tableur.recalculate(self._edit(workbook, "Z_B6 EST", f"G{row}", 16))
        )
        assert _close(after["AIMANT"]["Bord de ligne"], Decimal("16"))
        # Et le total suit : 16 comptés plus les 7,5 que les cinq
        # sous-ensembles en WIP lui apportaient déjà.
        assert _close(after["AIMANT"]["Quantité totale"], Decimal("23.5"))

    def test_une_ligne_libre_compte_des_qu_on_ecrit_dedans(self, workbook, payload):
        """Les lignes réservées ne sont pas décoratives : elles sont câblées."""
        if not tableur.AVAILABLE:
            pytest.skip(tableur.WHY_NOT)
        spare = 2 + len(zone_pass_lines(payload.zones[0]))
        edited = workbook
        for cell, value in (
            (f"A{spare}", "JOINT"),
            (f"C{spare}", str(CountSection.LINE_SIDE)),
            (f"E{spare}", 7),
            (f"F{spare}", 7),
        ):
            edited = self._edit(edited, "Z_B15", cell, value)

        row = _journal(tableur.recalculate(edited))["JOINT"]
        assert _close(row["Bord de ligne"], Decimal("7"))
        assert row["Statut"] == "retenu"


# --------------------------------------------------------------------------- #
# 2. Ce qui tient sans tableur : la structure du fichier
# --------------------------------------------------------------------------- #

class TestLeClasseurALesFeuillesDemandees:
    def test_toutes_et_dans_cet_ordre(self, book):
        assert book.sheetnames == [
            "Lisez-moi", "Articles", "Nomenclatures", "Éclatement",
            "Z_B15", "Z_B6 EST", "Z_ATELIER", "Z_LABO",
            "Data", "Journal consolidé",
        ]

    def test_une_seule_feuille_par_zone_meme_comptee_deux_fois(self, book, payload):
        """Le point de départ de la demande : à la fin, une quantité par ligne."""
        zone_sheets = [n for n in book.sheetnames if n.startswith("Z_")]
        assert len(zone_sheets) == len(payload.zones)
        headers = [c.value for c in book["Z_B6 EST"][1]]
        assert "Comptage n°1" in headers
        assert "Comptage n°2" in headers
        assert headers.count("Quantité retenue") == 1

    def test_chaque_zone_reserve_ses_lignes_libres(self, book, payload):
        for page, counts in zip(
            ("Z_B15", "Z_B6 EST", "Z_ATELIER", "Z_LABO"), payload.zones, strict=True
        ):
            expected = len(zone_pass_lines(counts)) + SPARE_ROWS_PER_ZONE
            assert book[page].max_row == expected + 1, page

    def test_une_case_vide_et_un_zero_ne_sont_pas_la_meme_chose(self, book, payload):
        """« Non compté par cette équipe » n'est pas « compté, rien trouvé ».

        Remplir de zéros les passages qui ne portent pas la référence
        transformerait chaque comptage simple en divergence, et enverrait
        arbitrer des lignes que personne n'a contredites.
        """
        lines = zone_pass_lines(payload.zones[1])
        row = 2 + next(
            i for i, line in enumerate(lines) if line.item_number == "STATOR"
        )
        assert book["Z_B6 EST"][f"E{row}"].value == 4
        assert book["Z_B6 EST"][f"F{row}"].value is None


class TestLesFormulesPointentSurCeQuiExiste:
    """La rupture qu'un renommage produit, et qu'aucune ouverture ne signale.

    Une feuille renommée, une colonne insérée, une ligne ajoutée sans étendre
    la plage : le classeur continue de s'ouvrir et rend zéro. Ces contrôles
    lisent les formules écrites et vérifient qu'elles désignent bien ce que le
    classeur contient — sans tableur, donc partout.
    """

    @staticmethod
    def _formulas(book) -> list[tuple[str, str, str]]:
        out = []
        for name in book.sheetnames:
            for row in book[name].iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        out.append((name, cell.coordinate, cell.value))
        return out

    def test_aucune_formule_ne_cite_une_feuille_absente(self, book):
        known = set(book.sheetnames)
        for sheet, cell, formula in self._formulas(book):
            for cited in re.findall(r"'([^']+)'!", formula):
                assert cited in known, f"{sheet}!{cell} cite « {cited} »"

    def test_les_plages_couvrent_toutes_les_lignes_ecrites(self, book):
        """Une plage d'une ligne trop courte perd la dernière référence.

        C'est la panne du classeur qu'on remplace, et elle ne se voit pas : le
        total est juste à une ligne près, et la ligne manquante est la dernière
        qu'on a ajoutée.
        """
        for sheet, cell, formula in self._formulas(book):
            for cited, first, last in re.findall(
                r"'([^']+)'!\$[A-Z]+\$(\d+):\$[A-Z]+\$(\d+)", formula
            ):
                rows = book[cited].max_row
                assert int(first) == 2, f"{sheet}!{cell} n'exclut pas l'en-tête"
                assert int(last) >= rows, (
                    f"{sheet}!{cell} s'arrête à la ligne {last} alors que "
                    f"« {cited} » en porte {rows}"
                )

    def test_chaque_vlookup_va_chercher_la_colonne_que_len_tete_annonce(self, book):
        """L'indice d'un ``VLOOKUP`` est un numéro de colonne écrit à la main.

        Insérer une colonne dans « Articles » les décale tous, et le journal se
        met à lire le prix dans la case du type sans que rien ne proteste. Ce
        contrôle relie chaque indice à l'en-tête qu'il est censé viser.
        """
        headers = [c.value for c in book["Articles"][1]]
        expected = {
            "Désignation": 1, "Type": 2, "Unité": 3, "Exclu GENERIQUE": 4,
            "Produit fini": 5, "Prix standard €": 13, "Stock ERP GENERIQUE": 10,
        }
        journal = book["Journal consolidé"]
        for label, column in expected.items():
            formula = journal.cell(row=2, column=column + 1).value
            index = int(re.search(r",(\d+),0\)", formula).group(1))
            assert index == headers.index(label) + 1, (
                f"la colonne « {journal.cell(row=1, column=column + 1).value } » "
                f"lit la colonne {index} d'« Articles », "
                f"soit « {headers[index - 1]} » et non « {label} »"
            )

    def test_les_criteres_de_section_sont_ceux_du_domaine(self, book):
        """Un critère qui ne correspond à aucune section additionne zéro.

        En silence : ``SUMIFS`` ne se plaint pas d'un critère introuvable.
        """
        journal = book["Journal consolidé"]
        assert f'"{CountSection.LINE_SIDE}"' in journal["G2"].value
        assert f'"{CountSection.WIP_OK}"' in journal["H2"].value
        assert f'"{CountSection.WIP}"' in book["Éclatement"]["F2"].value

    def test_data_lit_les_zones_et_ne_recopie_rien(self, book):
        """Le mot de la demande : « Data » *scrappe*, elle ne duplique pas.

        Une feuille Data en dur donnerait le bon chiffre à l'ouverture et le
        garderait après correction — c'est-à-dire le contraire d'un repli.
        """
        data = book["Data"]
        for column in ("B", "C", "D", "E"):
            value = data[f"{column}2"].value
            assert isinstance(value, str) and value.startswith("=")
            assert "'Z_B15'!" in value

    def test_le_journal_ne_porte_aucune_quantite_ecrite_en_dur(self, book):
        """Chaque chiffre du journal se déduit ; aucun n'est recopié."""
        journal = book["Journal consolidé"]
        for row in journal.iter_rows(min_row=2):
            for cell in row[1:]:
                assert not isinstance(cell.value, (int, float)), (
                    f"{cell.coordinate} porte une valeur figée"
                )


class TestCeQueLeClasseurAnnonce:
    def test_il_dit_de_quel_emplacement_il_parle(self, book):
        text = _readme_text(book)
        assert "B06VRAC / GENERIQUE" in text
        assert "INV-2026-06" in text

    def test_il_dit_que_leclatement_ne_suit_pas_les_nomenclatures(self, book):
        """La seule chose du classeur qui ne se recalcule pas, écrite en clair.

        La taire ferait corriger une quantité par composé dans « Nomenclatures »
        et attendre que le journal bouge, ce qui n'arrivera pas.
        """
        text = _readme_text(book)
        assert "instantané" in text
        assert "Nomenclatures" in text

    def test_il_dit_comment_ajouter_une_ligne(self, book):
        assert str(SPARE_ROWS_PER_ZONE) in _readme_text(book)

    def test_il_dit_que_lapplication_reste_la_source_de_verite(self, book):
        assert "source de vérité" in _readme_text(book)

    def test_la_tolerance_est_une_case_et_non_un_nombre_recopie(self, book, payload):
        """Elle se modifie, et les zones se recalculent : c'est un réglage.

        Figée dans chaque formule, la changer aurait voulu dire rouvrir les
        quarante feuilles de zone.
        """
        names = dict(book.defined_names.items())
        assert "Tolerance" in names

        sheet, cell = names["Tolerance"].value.split("!")
        assert book[sheet.strip("'")][cell.replace("$", "")].value == float(
            payload.arbitration_tolerance
        )
        assert "Tolerance" in book["Z_B15"]["H2"].value


def _readme_text(book) -> str:
    return "\n".join(
        str(cell.value)
        for row in book["Lisez-moi"].iter_rows()
        for cell in row
        if cell.value is not None
    )


# --------------------------------------------------------------------------- #
# 3. Les cas où il n'y a rien à mettre dans le classeur
# --------------------------------------------------------------------------- #

class TestUneCampagneVideProduitUnFichierOuvrable:
    def test_sans_zone_ni_article(self):
        """Une plage vide s'écrit ``$D$2:$D$2``, jamais ``$D$2:$D$1``.

        Une plage inversée fait refuser le fichier à l'ouverture, et l'export
        d'une campagne qu'on vient de créer est le premier geste qu'on tente.
        """
        import io

        content = _build(ConsolidationInput(
            campaign_id="c", zones=[], items={},
            bom=BomIndex([]), require_done_zones=False,
        ))
        book = openpyxl.load_workbook(io.BytesIO(content))
        assert book["Journal consolidé"].max_row == 1
        assert "Aucune zone" in _readme_text(book)

    def test_une_ligne_de_mise_en_page_ne_devient_pas_un_article(self):
        """Intertitres et lignes vides ne portent aucune quantité.

        Les compter agrégerait tout sous une référence vide — et la feuille de
        zone afficherait une ligne sans nom portant un total.
        """
        import io

        zone = Zone(id="z", campaign_id="c", code="Z", closed_at=CLOSED, passes=1)
        sheet = CountSheet(id="s", campaign_id="c", zone_id="z",
                           pass_no=SheetPass.PASS_1)
        lines = [
            CountSheetLine(id="l1", sheet_id="s", campaign_id="c", item_number="",
                           line_kind=CountLineKind.SUBSECTION, label="Stock B15"),
            CountSheetLine(id="l2", sheet_id="s", campaign_id="c", item_number="",
                           line_kind=CountLineKind.SPACER),
            CountSheetLine(id="l3", sheet_id="s", campaign_id="c",
                           item_number="VIS", section=CountSection.LINE_SIDE,
                           qty_manual=5),
        ]
        content = _build(ConsolidationInput(
            campaign_id="c",
            zones=[ZoneCounts(zone=zone, sheets=[sheet],
                              lines_by_sheet={"s": lines})],
            items=ITEMS, bom=BomIndex([]), require_done_zones=False,
        ))
        book = openpyxl.load_workbook(io.BytesIO(content))
        written = [
            book["Z_Z"].cell(row=r, column=1).value
            for r in range(2, 2 + SPARE_ROWS_PER_ZONE + 1)
        ]
        assert written[0] == "VIS"
        assert all(value is None for value in written[1:])

    def test_deux_zones_aux_codes_voisins_ont_deux_feuilles(self):
        """Trente et un caractères : au-delà, deux codes deviennent le même nom."""
        import io

        long_code = "ATELIER-ASSEMBLAGE-MOTEURS-EST"
        zones = [
            ZoneCounts(
                zone=Zone(id=f"z{i}", campaign_id="c", code=f"{long_code}-{i}",
                          closed_at=CLOSED, passes=1),
                sheets=[], lines_by_sheet={},
            )
            for i in (1, 2)
        ]
        content = _build(ConsolidationInput(
            campaign_id="c", zones=zones, items={}, bom=BomIndex([]),
            require_done_zones=False,
        ))
        book = openpyxl.load_workbook(io.BytesIO(content))
        pages = [n for n in book.sheetnames if n.startswith("Z_")]
        assert len(pages) == 2
        assert len(set(pages)) == 2
        assert all(len(n) <= 31 for n in pages)


# --------------------------------------------------------------------------- #
# 4. Le service qui l'engendre, et non seulement le module qui l'écrit
# --------------------------------------------------------------------------- #

class TestLeServiceLeProduitPourDeVrai:
    """Un classeur parfait qu'aucun service n'engendre n'existe pas.

    C'est la panne habituelle de ce dépôt, et elle ne se voit pas en relisant le
    module : celui-ci se teste très bien tout seul. Ces contrôles passent donc
    par ``ReportService``, qui est ce que la route appelle.
    """

    @staticmethod
    def _context(payload: ConsolidationInput, campaign):
        """Un contexte de service réduit à ce que le chemin du repli traverse."""
        from types import SimpleNamespace
        from typing import Any, cast

        sheets = [s for zone in payload.zones for s in zone.sheets]
        lines = {
            sheet_id: rows
            for zone in payload.zones
            for sheet_id, rows in zone.lines_by_sheet.items()
        }
        recorded: list[dict] = []
        ctx = cast(Any, SimpleNamespace(
            actor="alice",
            recorded=recorded,
            record=lambda **kw: recorded.append(kw),
            referentials=SimpleNamespace(
                items_by_number=lambda cid: payload.items,
                list_bom_links=lambda cid: BOM_LINKS,
            ),
            sheets=SimpleNamespace(
                list_zones=lambda cid: [z.zone for z in payload.zones],
                list_sheets=lambda cid: sheets,
                lines_by_sheet=lambda cid: lines,
            ),
            arbitrations=SimpleNamespace(
                list_arbitrations=lambda cid: [
                    a for zone in payload.zones for a in zone.arbitrations
                ],
            ),
            book_stock=SimpleNamespace(list=lambda cid: []),
        ))
        return ctx

    @staticmethod
    def _campaign():
        from inventory.domain.models import Campaign

        return Campaign(
            id="c", code="INV-2026-06", label="Inventaire de juin",
            count_date=dt.date(2026, 6, 30),
            created_by="alice", created_at=CLOSED,
        )

    def _produce(self, payload: ConsolidationInput):
        from inventory.services.report_service import ReportService

        campaign = self._campaign()
        ctx = self._context(payload, campaign)
        content, filename = ReportService(ctx).consolidation_fallback(campaign)
        return ctx, content, filename

    def test_le_fichier_se_nomme_pour_ne_pas_etre_confondu(self, payload):
        _, _, filename = self._produce(payload)
        assert filename == "repli-consolidation-generique_INV-2026-06.xlsx"

    def test_lexport_est_trace(self, payload):
        """Un fichier de repli qui circule doit dire d'où il sort."""
        ctx, _, _ = self._produce(payload)
        trace = next(
            entry for entry in ctx.recorded
            if entry["entity_type"] == "consolidation"
        )
        assert "repli" in trace["summary"]

    def test_une_zone_encore_en_cours_est_dans_le_classeur(self, payload):
        """Le repli sert **pendant** le comptage, pas seulement après.

        La consolidation postée n'accepte que les zones terminées, à raison :
        elle produit un journal opposable. Le classeur, lui, est ce qu'on ouvre
        quand rien ne répond — et ce jour-là les zones ne sont pas toutes
        closes. Les écarter en rendrait la moitié vide, sans le dire.

        La feuille dit où en est chaque zone plutôt que de la retirer : c'est
        l'information dont on a besoin pour se servir du chiffre, et la retirer
        aurait posé la question à la place du lecteur.
        """
        import io

        assert any(zone.zone.closed_at is None for zone in payload.zones), (
            "le jeu de données doit porter une zone non terminée"
        )
        _, content, _ = self._produce(payload)
        book = openpyxl.load_workbook(io.BytesIO(content))

        assert "Z_LABO" in book.sheetnames
        assert book["Z_LABO"]["A2"].value == "COLLE"
        assert "zone en cours" in _readme_text(book)
