"""Ce que le job lit dans Lakebase, et ce qu'il en fait.

Le job ouvre sa connexion avec ``row_factory=dict_row`` : le curseur rend déjà
des dictionnaires. ``fetch`` les rezippait pourtant avec les noms de colonnes —
et itérer un dictionnaire rend ses **clés**. Chaque champ recevait donc le nom
de sa propre colonne, sans que rien ne le signale : les longueurs étant égales,
le ``strict=True`` passait.

La publication s'arrêtait sur la première colonne non textuelle, « la valeur
'count_date' ne peut pas être convertie en DATE », après avoir accepté toutes
les colonnes de texte qui la précédaient. C'est le meilleur des cas. Sur un
schéma entièrement textuel, le job aurait publié une archive de noms de
colonnes en se déclarant réussie — et sur une partition
``campaign_id = 'campaign_id'``, puisque l'identifiant subissait le même sort.

Une archive est ce qui reste quand la base opérationnelle a évolué : elle ne
peut pas se tromper en silence.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
from typing import Any

import pytest

JOB = Path(__file__).resolve().parents[1] / "jobs" / "publish_campaign_to_delta.py"


def load_job() -> Any:
    spec = importlib.util.spec_from_file_location("publish_campaign_to_delta", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


publish = load_job()

#: Une ligne de campagne telle que Lakebase la rend, valeurs comprises.
CAMPAGNE = {
    "campaign_id": "9f1c2e64-0000-4000-8000-000000000001",
    "code": "TRY1",
    "count_date": dt.date(2026, 8, 24),
    "status": "counting",
}


class DictCursor:
    """Le curseur du job : ``row_factory=dict_row``, donc des dictionnaires."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple] = []

    def execute(self, query: str, params: dict[str, Any]) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        # Ses propres dictionnaires, pas des copies : c'est ce qui rend
        # observable le fait que `fetch` copie ce qu'il rend.
        return self._rows

    @property
    def description(self) -> list[Any]:
        noms = list(self._rows[0]) if self._rows else []
        return [type("C", (), {"name": nom})() for nom in noms]


class TupleCursor(DictCursor):
    """Un curseur sans ``row_factory`` : des tuples, à nommer par description."""

    def fetchall(self) -> list[tuple]:
        return [tuple(row.values()) for row in self._rows]


class TestWhatComesBackFromLakebase:
    def test_les_valeurs_reviennent_et_non_les_noms_de_colonnes(self):
        """Le défaut, nommé par sa conséquence exacte."""
        rendu = publish.fetch(DictCursor([CAMPAGNE]), "SELECT ...", {})

        assert rendu == [CAMPAGNE]

    def test_la_date_reste_une_date(self):
        """C'est la colonne sur laquelle la publication s'est arrêtée.

        Une chaîne à sa place fait échouer `CAST(count_date AS date)` — et
        c'est le bon scénario : sur une colonne textuelle, rien n'aurait
        protesté.
        """
        [ligne] = publish.fetch(DictCursor([CAMPAGNE]), "SELECT ...", {})

        assert ligne["count_date"] == dt.date(2026, 8, 24)
        assert not isinstance(ligne["count_date"], str)

    def test_l_identifiant_de_partition_est_l_identifiant(self):
        """Il porte le prédicat ``replaceWhere`` : s'il vaut « campaign_id »,
        l'archive s'écrit sur une partition qui n'est celle de personne."""
        [ligne] = publish.fetch(DictCursor([CAMPAGNE]), "SELECT ...", {})

        assert ligne["campaign_id"] == CAMPAGNE["campaign_id"]

    def test_plusieurs_lignes_gardent_chacune_les_siennes(self):
        """Une erreur d'appariement se voit sur deux lignes, pas sur une."""
        deux = [CAMPAGNE, {**CAMPAGNE, "code": "TRY2", "status": "closed"}]
        rendu = publish.fetch(DictCursor(deux), "SELECT ...", {})

        assert [ligne["code"] for ligne in rendu] == ["TRY1", "TRY2"]
        assert [ligne["status"] for ligne in rendu] == ["counting", "closed"]

    def test_un_curseur_en_tuples_reste_traite(self):
        """La fonction ne dépend pas d'un réglage posé ailleurs.

        C'est cette dépendance tacite — « le curseur rend des dictionnaires,
        mais la fonction fait comme si non » — qui a produit le défaut.
        """
        rendu = publish.fetch(TupleCursor([CAMPAGNE]), "SELECT ...", {})

        assert rendu == [CAMPAGNE]

    def test_aucune_ligne_ne_rend_aucune_ligne(self):
        """Le cas vide ne doit pas aller lire une description absente."""
        assert publish.fetch(DictCursor([]), "SELECT ...", {}) == []

    def test_la_requete_et_ses_parametres_sont_transmis(self):
        """La liaison reste paramétrée : jamais de code interpolé dans le SQL."""
        cursor = DictCursor([CAMPAGNE])
        publish.fetch(cursor, "SELECT ... WHERE code = %(code)s", {"code": "TRY1"})

        assert cursor.executed == [("SELECT ... WHERE code = %(code)s", {"code": "TRY1"})]


class TestTheCopyIsNotShared:
    """``dict(row)`` copie : le curseur ne garde pas la main sur ce qu'il a rendu."""

    def test_modifier_une_ligne_rendue_ne_touche_pas_la_source(self):
        source = [dict(CAMPAGNE)]
        [ligne] = publish.fetch(DictCursor(source), "SELECT ...", {})
        ligne["code"] = "MODIFIÉ"

        assert source[0]["code"] == "TRY1"


class TestColumnsWithNothingInThem:
    """Une colonne vide partout n'a pas de type, et Spark refuse tout le lot.

    `CANNOT_DETERMINE_TYPE` porte sur le DataFrame entier, pas sur la colonne :
    une seule colonne sans valeur fait échouer la publication de la table. Or
    c'est le cas ordinaire — une campagne en comptage n'a ni date de clôture ni
    date de gel des comptages.

    Elles sont donc retirées avant la construction, puis remises avec le type
    de la table. La valeur écrite est la même — NULL — mais elle est typée.
    """

    def test_une_colonne_vide_partout_est_reperee(self):
        rows = [
            {"code": "TRY1", "closed_at": None},
            {"code": "TRY2", "closed_at": None},
        ]
        assert publish._always_null(rows) == {"closed_at"}

    def test_une_colonne_vide_par_endroits_est_gardee(self):
        """Une seule valeur suffit à donner son type à la colonne."""
        rows = [
            {"code": "TRY1", "closed_at": None},
            {"code": "TRY2", "closed_at": dt.date(2026, 8, 24)},
        ]
        assert publish._always_null(rows) == set()

    def test_une_colonne_absente_d_une_ligne_compte_comme_vide(self):
        """Absente ici, nulle là : dans les deux cas, rien à déduire."""
        rows = [{"code": "TRY1"}, {"code": "TRY2", "closed_at": None}]
        assert publish._always_null(rows) == {"closed_at"}

    def test_une_ligne_entierement_vide_les_rend_toutes(self):
        assert publish._always_null([{"a": None, "b": None}]) == {"a", "b"}

    def test_aucune_ligne_ne_rend_aucune_colonne(self):
        assert publish._always_null([]) == set()

    def test_la_campagne_en_comptage_est_bien_le_cas_ordinaire(self):
        """Le scénario réel, pas un cas limite construit pour le contrôle."""
        en_comptage = {
            "campaign_id": "9f1c2e64-0000-4000-8000-000000000001",
            "code": "TRY1",
            "count_date": dt.date(2026, 8, 24),
            "status": "counting",
            "counting_frozen_at": None,
            "closed_at": None,
            "cloned_from_code": None,
        }
        assert publish._always_null([en_comptage]) == {
            "counting_frozen_at",
            "closed_at",
            "cloned_from_code",
        }


def test_l_ecriture_consulte_bien_les_colonnes_vides():
    """La garde structurelle, faute de pouvoir lancer Spark ici.

    `_write` n'est pas exerçable sans session Spark : ce contrôle vérifie donc
    seulement que le tri des colonnes vides est bien branché dessus. Un
    assistant qui existe sans appelant ne protège personne.
    """
    import ast

    arbre = ast.parse(JOB.read_text(encoding="utf-8"))
    ecriture = next(
        noeud
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.FunctionDef) and noeud.name == "_write"
    )
    appels = {
        noeud.func.id
        for noeud in ast.walk(ecriture)
        if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
    }

    assert "_always_null" in appels


@pytest.mark.parametrize("cursor_class", [DictCursor, TupleCursor], ids=["dict", "tuple"])
def test_les_deux_curseurs_rendent_la_meme_chose(cursor_class):
    """Le job doit pouvoir changer de ``row_factory`` sans changer de données."""
    assert publish.fetch(cursor_class([CAMPAGNE]), "SELECT ...", {}) == [CAMPAGNE]
