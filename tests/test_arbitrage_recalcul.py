"""Le besoin d'arbitrage se réévalue partout où une feuille change.

Le défaut, et pourquoi il a survécu à un correctif
--------------------------------------------------
La règle était écrite, et juste : *une décision d'arbitrage porte sur deux
chiffres, et meurt avec eux*. ``build_arbitration_lines`` la tenait — elle
reposait la ligne sans sa signature dès que l'un des deux comptages avait
bougé — et un contrôle le vérifiait.

Sur le domaine. Une couche plus bas, l'écriture disait :

    decided_by = COALESCE(EXCLUDED.decided_by, arbitration.decided_by)

et ``COALESCE(NULL, l'ancien)`` rend l'ancien. Le domaine effaçait la
signature, la base la remettait, et la décision périmée survivait — invisible,
puisque tout ce qui était contrôlé était juste.

Le second défaut est jumeau du premier : le recalcul était une méthode de
``GenericService``, appelée par la saisie à l'écran et par la fermeture d'une
zone. Les trois autres façons d'écrire des quantités — lire un scan, lire une
pile, importer une liste, reclasser un WIP — vivent dans d'autres services, et
rien ne disait qu'il leur manquait quelque chose.

Ce que ce module tient
----------------------
* la couche SQL n'a pas le droit de survivre à ce que le domaine efface ;
* **toute** fonction de service qui écrit des lignes de feuille recalcule, ou
  figure dans une liste d'exemptions qui dit pourquoi — c'est cette liste qui
  transforme l'ajout d'un sixième chemin d'écriture en décision consciente
  plutôt qu'en oubli ;
* le recalcul ne regarde pas le statut de la zone.
"""

from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from inventory.domain.consolidation import ZoneCounts, build_arbitration_lines
from inventory.domain.enums import CountSection, SheetPass
from inventory.domain.models import ArbitrationLine, CountSheet, CountSheetLine, Zone

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "app" / "inventory" / "services"
SHEET_REPO = ROOT / "app" / "inventory" / "db" / "repositories" / "sheet.py"

#: Les deux façons d'écrire des lignes de feuille dans un dépôt.
WRITES = {"replace_sheet_lines", "upsert_sheet_lines"}

#: Les deux façons de redemander le recalcul.
REFRESHES = {"refresh_zone_arbitrations", "refresh_after_sheet_writes"}

#: Les fonctions qui écrivent des lignes **sans** recalculer, et la raison.
#:
#: Une exemption est une décision, pas une tolérance : ajouter une entrée ici
#: demande d'écrire pourquoi, et c'est tout l'objet de cette liste.
EXEMPTED: dict[str, str] = {
    "clone": (
        "Duplication : la campagne créée n'a aucun arbitrage à périmer, et ses "
        "feuilles sont copiées vides de toute quantité."
    ),
    "_mirror_document": (
        "Recopie interne du document sur le second passage, appelée depuis "
        "upsert_sheet_lines qui recalcule juste après — recalculer ici le "
        "ferait deux fois par enregistrement."
    ),
}


def functions_writing_sheet_lines() -> list[tuple[str, str, ast.AST]]:
    """(module, fonction, nœud) pour chaque fonction qui écrit des lignes."""
    found: list[tuple[str, str, ast.AST]] = []
    for path in sorted(SERVICES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        seen: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in WRITES):
                continue
            # Le dépôt lui-même définit ces noms ; on ne veut que les appels.
            if not isinstance(func.value, ast.Attribute):
                continue
            enclosing: ast.AST = node
            while enclosing in parents and not isinstance(
                enclosing, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                enclosing = parents[enclosing]
            name = getattr(enclosing, "name", "<module>")
            if name in seen:
                continue
            seen.add(name)
            found.append((path.name, name, enclosing))
    return found


def calls_in(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                out.add(func.id)
            elif isinstance(func, ast.Attribute):
                out.add(func.attr)
    return out


WRITERS = functions_writing_sheet_lines()


class TestChaqueEcritureRedemandeLeRecalcul:
    def test_il_existe_bien_des_ecritures_a_surveiller(self):
        """Un contrôle qui ne trouve plus rien passerait pour vert.

        Renommer une méthode de dépôt viderait la liste et rendrait toutes les
        assertions vraies sans que rien ne les tienne.
        """
        assert len(WRITERS) >= 5, [w[:2] for w in WRITERS]

    @pytest.mark.parametrize(
        "module,name,node", WRITERS, ids=[f"{m}::{n}" for m, n, _ in WRITERS]
    )
    def test_elle_recalcule_ou_dit_pourquoi_elle_ne_le_fait_pas(
        self, module: str, name: str, node: ast.AST
    ) -> None:
        if name in EXEMPTED:
            assert EXEMPTED[name].strip(), f"{name} : exemption sans raison écrite"
            return
        assert calls_in(node) & REFRESHES, (
            f"{module}::{name} écrit des lignes de feuille sans redemander le "
            "recalcul des arbitrages. Appelez refresh_after_sheet_writes(), ou "
            "ajoutez la fonction à EXEMPTED en disant pourquoi."
        )

    def test_aucune_exemption_ne_designe_une_fonction_disparue(self):
        """Une exemption périmée cache la fonction qui la remplace."""
        written = {name for _, name, _ in WRITERS}
        orphans = sorted(set(EXEMPTED) - written)
        assert not orphans, (
            f"Exemption(s) sans fonction correspondante : {orphans}. "
            "La fonction a été renommée ou supprimée."
        )


class TestLaCoucheSqlNeSurvitPasAuDomaine:
    """Ce que le domaine efface doit s'effacer en base."""

    def test_lupsert_nefface_pas_la_decision_avec_un_coalesce(self):
        source = SHEET_REPO.read_text(encoding="utf-8")
        upsert = source[source.index("def upsert_arbitrations"):]
        upsert = upsert[: upsert.index("def delete_arbitrations")]
        offenders = [
            column
            for column in ("decided_by", "decided_at", "qty_arbitrated")
            if f"COALESCE(EXCLUDED.{column}" in upsert.replace("\n", " ")
        ]
        assert not offenders, (
            f"{offenders} repris par COALESCE : la ligne que le domaine repose "
            "sans signature garderait l'ancienne, et un arbitrage périmé "
            "survivrait à la modification qui l'a périmé."
        )

    def test_les_trois_colonnes_sont_bien_reprises_de_la_ligne_posee(self):
        source = SHEET_REPO.read_text(encoding="utf-8")
        upsert = source[source.index("def upsert_arbitrations"):]
        upsert = upsert[: upsert.index("def delete_arbitrations")]
        flat = upsert.replace("\n", " ")
        for column in ("decided_by", "decided_at", "qty_arbitrated"):
            assert f"{column} = EXCLUDED.{column}" in flat, column


class TestLeRecalculNeRegardePasLeStatutDeLaZone:
    def test_aucune_condition_sur_la_fermeture(self):
        """« Quel que soit le statut de la zone » : c'est le cas où l'oubli
        coûte le plus cher, puisque plus rien en aval ne repose la question."""
        from inventory.services import arbitration_service

        source = Path(arbitration_service.__file__).read_text(encoding="utf-8")
        body = source[source.index("def refresh_zone_arbitrations"):]
        body = body[: body.index("def refresh_after_sheet_writes")]
        assert "closed_at" not in body
        assert "closed" not in body.split('"""')[2]


# --------------------------------------------------------------------------- #
# La règle elle-même, sans base de données
# --------------------------------------------------------------------------- #

DECIDED_AT = dt.datetime(2026, 6, 30, 9, 0, tzinfo=dt.UTC)


def _zone(qty_1: str, qty_2: str, *, prior: ArbitrationLine | None = None) -> ZoneCounts:
    """Une zone à deux passages, une référence, et l'arbitrage déjà pris."""
    zone = Zone(id="z-1", campaign_id="c", code="Z1", passes=2)
    sheets = [
        CountSheet(id="s-1", campaign_id="c", zone_id=zone.id, pass_no=SheetPass.PASS_1),
        CountSheet(id="s-2", campaign_id="c", zone_id=zone.id, pass_no=SheetPass.PASS_2),
    ]
    lines = {
        "s-1": [CountSheetLine(
            id="l-1", sheet_id="s-1", campaign_id="c", item_number="VIS",
            section=CountSection.LINE_SIDE, qty_manual=Decimal(qty_1),
        )],
        "s-2": [CountSheetLine(
            id="l-2", sheet_id="s-2", campaign_id="c", item_number="VIS",
            section=CountSection.LINE_SIDE, qty_manual=Decimal(qty_2),
        )],
    }
    return ZoneCounts(
        zone=zone, sheets=sheets, lines_by_sheet=lines,
        arbitrations=(prior,) if prior else (),
    )


def _prior(qty_1: str, qty_2: str) -> ArbitrationLine:
    """« Entre ces deux chiffres-là, je retiens 95 », signé."""
    return ArbitrationLine(
        id="a-1", campaign_id="c", zone_id="z-1", item_number="VIS",
        section=CountSection.LINE_SIDE,
        qty_pass_1=Decimal(qty_1), qty_pass_2=Decimal(qty_2),
        qty_arbitrated=Decimal("95"), decided_at=DECIDED_AT, decided_by="alice",
    )


class TestUneDecisionMeurtAvecLesChiffresQuElleTranche:
    """Le cœur de la règle, tenu **sans** PostgreSQL.

    Elle ne l'était que par un contrôle de bout en bout, ignoré partout où la
    base n'est pas là — c'est-à-dire dans la plupart des exécutions. Supprimer
    la détection ne faisait donc rien échouer.
    """

    def test_le_comptage_bouge_et_la_signature_part(self):
        # 100 / 90 tranchés à 95, puis le second passage passe à 40.
        lines = build_arbitration_lines(
            _zone("100", "40", prior=_prior("100", "90")),
            campaign_id="c", id_factory=lambda: "neuf",
        )
        line = next(l for l in lines if l.item_number == "VIS")
        assert line.decided_at is None
        assert line.decided_by is None
        assert not line.is_resolved

    def test_la_proposition_reste_pour_ne_pas_faire_retaper(self):
        lines = build_arbitration_lines(
            _zone("100", "40", prior=_prior("100", "90")),
            campaign_id="c", id_factory=lambda: "neuf",
        )
        line = next(l for l in lines if l.item_number == "VIS")
        assert line.qty_arbitrated == Decimal("95")
        assert "changé" in line.comment

    def test_la_ligne_garde_son_identite(self):
        """Une ligne rouverte n'est pas une ligne neuve : la remplacer par une
        autre perdrait la proposition et l'historique qui s'y rattache."""
        lines = build_arbitration_lines(
            _zone("100", "40", prior=_prior("100", "90")),
            campaign_id="c", id_factory=lambda: "neuf",
        )
        assert next(l for l in lines if l.item_number == "VIS").id == "a-1"

    def test_un_comptage_inchange_garde_sa_decision(self):
        """L'inverse compte autant : recalculer ne doit pas effacer un
        arbitrage que personne n'a rendu caduc — sur une zone de quarante
        écarts, ce serait quarante décisions à reprendre à chaque frappe."""
        lines = build_arbitration_lines(
            _zone("100", "90", prior=_prior("100", "90")),
            campaign_id="c", id_factory=lambda: "neuf",
        )
        line = next(l for l in lines if l.item_number == "VIS")
        assert line.decided_at == DECIDED_AT
        assert line.decided_by == "alice"
        assert line.is_resolved

    def test_le_premier_passage_compte_autant_que_le_second(self):
        lines = build_arbitration_lines(
            _zone("7", "90", prior=_prior("100", "90")),
            campaign_id="c", id_factory=lambda: "neuf",
        )
        assert not next(l for l in lines if l.item_number == "VIS").is_resolved
