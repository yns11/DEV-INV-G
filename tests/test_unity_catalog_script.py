"""Le premier geste d'un déploiement, qui n'avait jamais pu fonctionner.

Le README, le Makefile et l'en-tête du fichier SQL donnaient tous les trois :

    databricks sql query --warehouse-id <ID> --file sql/00_unity_catalog.sql

Cette commande n'existe pas — la CLI répond « unknown command "sql" » et propose
« psql ». `make uc` crée le schéma, le volume, les dix tables et les vues : rien
de tout cela n'était provisionnable comme documenté. Le défaut ne s'est vu que
des mois plus tard, quand une table a manqué à un job.

`scripts/apply_unity_catalog.py` découpe le fichier et l'exécute instruction par
instruction, par l'API d'exécution de requêtes du SDK — celle dont l'application
dépend déjà.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_unity_catalog.py"
DDL = ROOT / "sql" / "00_unity_catalog.sql"
MAKEFILE = ROOT / "Makefile"


def load() -> Any:
    spec = importlib.util.spec_from_file_location("apply_unity_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


uc = load()


class TestCuttingTheScriptIntoStatements:
    def test_les_instructions_sont_separees_sur_le_point_virgule(self):
        assert uc.split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_la_derniere_instruction_compte_sans_point_virgule_final(self):
        """Le fichier se termine ainsi — la perdre perdrait une vue."""
        assert uc.split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_un_point_virgule_dans_une_chaine_ne_separe_rien(self):
        """Un libellé de COMMENT est du texte, pas de la syntaxe."""
        sql = "COMMENT ON VIEW v IS 'scans ; imports ; rapports'; SELECT 1"

        assert uc.split_statements(sql) == [
            "COMMENT ON VIEW v IS 'scans ; imports ; rapports'",
            "SELECT 1",
        ]

    def test_une_apostrophe_echappee_ne_ferme_pas_la_chaine(self):
        """« d''un accident » : le fichier en est plein, il est en français."""
        sql = "COMMENT ON VIEW v IS 'fuite d''un accident ; pas deux'; SELECT 1"

        assert len(uc.split_statements(sql)) == 2

    def test_un_point_virgule_en_commentaire_ne_separe_rien(self):
        sql = "-- scans ; imports\nSELECT 1"

        assert uc.split_statements(sql) == ["SELECT 1"]

    def test_un_bloc_de_commentaire_est_traverse(self):
        sql = "/* un ; deux */ SELECT 1"

        assert uc.split_statements(sql) == ["/* un ; deux */ SELECT 1"]

    def test_les_lignes_de_commentaire_ne_font_pas_une_instruction(self):
        """Le fichier porte de longs blocs entre deux tables."""
        sql = "-- pourquoi cette table existe\n-- et ce qu'elle garantit\n;SELECT 1"

        assert uc.split_statements(sql) == ["SELECT 1"]

    def test_le_vide_ne_rend_rien(self):
        assert uc.split_statements("") == []
        assert uc.split_statements("   \n\n  ;  ") == []


class TestTheSessionIsCarriedByParameters:
    """``execute_statement`` ouvre une session par appel.

    Un ``USE CATALOG`` n'y survit donc pas à l'instruction suivante : envoyé tel
    quel, il ne servirait à rien et les tables seraient créées dans le catalogue
    par défaut du warehouse. Le fichier serait « appliqué » sans erreur, ailleurs.
    """

    def test_les_use_ne_sont_jamais_envoyes(self):
        rendu = list(uc.sessioned(["USE CATALOG c", "USE SCHEMA s", "SELECT 1"]))

        assert [instruction for instruction, _, _ in rendu] == ["SELECT 1"]

    def test_le_catalogue_et_le_schema_accompagnent_l_instruction(self):
        rendu = list(uc.sessioned(["USE CATALOG c", "USE SCHEMA s", "SELECT 1"]))

        assert rendu == [("SELECT 1", "c", "s")]

    def test_la_creation_du_schema_part_avec_un_catalogue_et_sans_schema(self):
        """Elle précède le ``USE SCHEMA`` — et pour cause, il n'existe pas encore."""
        rendu = list(
            uc.sessioned(["USE CATALOG c", "CREATE SCHEMA s", "USE SCHEMA s", "SELECT 1"])
        )

        assert rendu[0] == ("CREATE SCHEMA s", "c", None)

    def test_changer_de_catalogue_oublie_le_schema(self):
        """Un schéma n'a de sens que dans son catalogue."""
        rendu = list(
            uc.sessioned(["USE CATALOG a", "USE SCHEMA s", "USE CATALOG b", "SELECT 1"])
        )

        assert rendu == [("SELECT 1", "b", None)]

    @pytest.mark.parametrize(
        "ligne", ["USE CATALOG c", "use catalog c", "USE CATALOG `c`", "USE  CATALOG   c"]
    )
    def test_les_formes_du_use_sont_reconnues(self, ligne: str):
        assert list(uc.sessioned([ligne, "SELECT 1"])) == [("SELECT 1", "c", None)]

    def test_use_database_vaut_use_schema(self):
        assert list(uc.sessioned(["USE DATABASE s", "SELECT 1"])) == [
            ("SELECT 1", None, "s")
        ]


class FakeExecution:
    def __init__(self, state: str = "SUCCEEDED") -> None:
        self.calls: list[dict] = []
        self.state = state

    def execute_statement(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        statut = type("S", (), {"state": self.state, "error": None})()
        return type("R", (), {"status": statut})()


class FakeClient:
    def __init__(self, state: str = "SUCCEEDED") -> None:
        self.statement_execution = FakeExecution(state)


class TestApplyingTheRealFile:
    """Le fichier livré, découpé et adressé — sans warehouse."""

    def test_toutes_les_instructions_partent(self):
        client = FakeClient()
        combien = uc.apply(client, "w-1", DDL.read_text(encoding="utf-8"))

        assert combien == len(client.statement_execution.calls)
        assert combien > 15

    def test_le_schema_et_les_treize_tables_y_sont(self):
        client = FakeClient()
        uc.apply(client, "w-1", DDL.read_text(encoding="utf-8"))
        envoyees = [c["statement"] for c in client.statement_execution.calls]

        assert any(s.startswith("CREATE SCHEMA") for s in envoyees)
        creations = [s for s in envoyees if s.startswith("CREATE TABLE")]
        assert len(creations) == 13
        assert any("publication" in s for s in creations), "le manifeste qui manquait"
        # Le référentiel des causes : sans lui, le catalogue porte des codes
        # d'écart — « 1 », « 11 », « 99 » — que rien n'y permet de nommer.
        assert any("assignable_cause" in s for s in creations), "le vocabulaire"
        # Les deux tables des comptages avancés : sans elles, l'archive ne
        # dirait ni quels emplacements ont été comptés en avance, ni ce que
        # l'ERP en disait le jour J.
        for table in ("early_count_drift", "erp_journal_scope"):
            assert any(table in s for s in creations), table

        # Et celle qui portait les issues d'étiquette s'en va avec elles : une
        # étiquette scellée retrouvée ailleurs se regarde, elle ne se tranche
        # plus. La garder créerait, campagne après campagne, une table Delta
        # vide que personne ne saurait interpréter.
        assert not any("early_count_label_decision" in s for s in creations)

    def test_chaque_instruction_porte_le_catalogue_du_fichier(self):
        client = FakeClient()
        uc.apply(client, "w-1", DDL.read_text(encoding="utf-8"))

        catalogues = {c["catalog"] for c in client.statement_execution.calls}
        assert catalogues == {"emotors_data_champions"}

    def test_le_warehouse_est_celui_demande(self):
        client = FakeClient()
        uc.apply(client, "w-42", "SELECT 1")

        assert client.statement_execution.calls[0]["warehouse_id"] == "w-42"

    def test_un_refus_arrete_tout_et_nomme_l_instruction(self):
        """Continuer après un refus laisserait un schéma à moitié créé."""
        client = FakeClient(state="FAILED")

        with pytest.raises(RuntimeError, match="CREATE SCHEMA"):
            uc.apply(client, "w-1", "USE CATALOG c; CREATE SCHEMA s; SELECT 1")

        assert len(client.statement_execution.calls) == 1


class TestNothingStillPointsAtTheCommandThatDoesNotExist:
    """`databricks sql query` : la CLI répond « unknown command "sql" »."""

    def test_le_makefile_ne_l_appelle_plus(self):
        assert "databricks sql query" not in MAKEFILE.read_text(encoding="utf-8")

    def test_le_makefile_appelle_le_script(self):
        assert "scripts/apply_unity_catalog.py" in MAKEFILE.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "document",
        ["README.md", "sql/00_unity_catalog.sql", "docs/03-guide-deploiement.md"],
    )
    def test_la_documentation_ne_la_donne_plus(self, document: str):
        texte = (ROOT / document).read_text(encoding="utf-8")
        lignes = [
            ligne
            for ligne in texte.splitlines()
            if "databricks sql query" in ligne and "n'existe pas" not in ligne
        ]
        assert lignes == []


# --------------------------------------------------------------------------- #
# Une colonne ajoutée après coup, et le fichier qui reste rejouable
# --------------------------------------------------------------------------- #

class FakeFailing:
    """Un warehouse qui refuse tout, avec le message qu'on lui donne."""

    def __init__(self, message: str) -> None:
        self.calls: list[dict] = []
        self.message = message

    def execute_statement(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        erreur = type("E", (), {"message": self.message})()
        statut = type("S", (), {"state": "FAILED", "error": erreur})()
        return type("R", (), {"status": statut})()


class ClientRefusant:
    def __init__(self, message: str) -> None:
        self.statement_execution = FakeFailing(message)


class TestAddColumnsIsReplayable:
    """La seule instruction du fichier qui ne sache pas se rejouer.

    Tout le reste est en ``CREATE … IF NOT EXISTS`` ou ``CREATE OR REPLACE
    VIEW``. Ajouter une colonne à une table existante ne l'est pas : ``ADD
    COLUMNS`` n'admet pas de ``IF NOT EXISTS`` dans Databricks SQL, vérifié dans
    la référence du langage, et le second passage échoue sur « la colonne existe
    déjà ».

    Or ces ``ALTER`` sont nécessaires : ``CREATE TABLE IF NOT EXISTS`` ne touche
    pas une table déjà déployée, donc sans eux un catalogue existant garderait
    l'ancienne forme — et la publication y écrirait des libellés absents sans
    rien signaler, le job comblant par NULL les colonnes que la table n'a pas.

    L'en-tête du fichier promet « rejouable sans risque ». Ce contrôle est ce qui
    rend la promesse vraie.
    """

    AJOUT = "ALTER TABLE variance_analysis ADD COLUMNS (cause_label STRING)"

    @pytest.mark.parametrize(
        "message",
        [
            "[FIELDS_ALREADY_EXIST] Cannot add column cause_label because it already exists",
            "Column cause_label already exists in table variance_analysis",
            "DELTA_ADD_COLUMN_EXISTING: cause_label",
        ],
        ids=["classe", "phrase", "classe-delta"],
    )
    def test_une_colonne_deja_presente_passe_pour_un_succes(self, message: str):
        assert uc.tolerated(self.AJOUT, message)

    def test_et_le_script_ne_s_arrete_pas_dessus(self):
        client = ClientRefusant(
            "[FIELDS_ALREADY_EXIST] Cannot add column cause_label, it already exists"
        )
        assert uc.apply(client, "w-1", f"{self.AJOUT};") == 1

    def test_un_ajout_refuse_pour_autre_chose_echoue(self):
        """La tolérance porte sur une cause nommée, pas sur l'instruction.

        Une table absente, un type invalide : ce sont des déploiements cassés, et
        les faire passer pour appliqués est exactement le défaut que ce script a
        été écrit pour corriger.
        """
        assert not uc.tolerated(self.AJOUT, "TABLE_OR_VIEW_NOT_FOUND: variance_analysis")
        with pytest.raises(RuntimeError, match="TABLE_OR_VIEW_NOT_FOUND"):
            uc.apply(
                ClientRefusant("TABLE_OR_VIEW_NOT_FOUND: variance_analysis"),
                "w-1",
                f"{self.AJOUT};",
            )

    def test_et_rien_d_autre_qu_un_ajout_de_colonne_n_en_profite(self):
        """« existe déjà » sur une création est un vrai problème.

        Un ``CREATE TABLE`` sans ``IF NOT EXISTS`` qui échoue ainsi veut dire que
        le fichier a divergé de ce qui est déployé ; le taire laisserait une
        table dans une forme que personne n'a écrite.
        """
        deja = "Table variance_analysis already exists"
        assert not uc.tolerated("CREATE TABLE variance_analysis (a STRING)", deja)
        assert not uc.tolerated("DROP TABLE variance_analysis", deja)
        with pytest.raises(RuntimeError, match="already exists"):
            uc.apply(ClientRefusant(deja), "w-1", "CREATE TABLE t (a STRING);")

    def test_les_deux_conditions_sont_necessaires(self):
        """Ni l'instruction seule, ni le message seul."""
        assert not uc.tolerated(self.AJOUT, "something else entirely")
        assert not uc.tolerated("SELECT 1", "Column x already exists")

    def test_le_fichier_livre_s_applique_deux_fois_de_suite(self):
        """La promesse de l'en-tête, exercée sur le fichier réel.

        Le second passage reçoit le refus qu'un warehouse donne sur les trois
        ``ALTER`` déjà appliqués ; aucune instruction ne doit s'y perdre.
        """
        sql = DDL.read_text(encoding="utf-8")
        premier = FakeClient()
        combien = uc.apply(premier, "w-1", sql)

        class Rejoue:
            """SUCCEEDED partout, sauf sur un ajout de colonne."""

            def __init__(self) -> None:
                self.calls: list[dict] = []

            def execute_statement(self, **kwargs: Any) -> Any:
                self.calls.append(kwargs)
                if uc.ADD_COLUMNS.match(kwargs["statement"]):
                    erreur = type("E", (), {"message": "FIELDS_ALREADY_EXIST"})()
                    statut = type("S", (), {"state": "FAILED", "error": erreur})()
                else:
                    statut = type("S", (), {"state": "SUCCEEDED", "error": None})()
                return type("R", (), {"status": statut})()

        second = type("C", (), {"statement_execution": Rejoue()})()
        assert uc.apply(second, "w-1", sql) == combien

    def test_le_fichier_porte_bien_des_ajouts_a_tolerer(self):
        """Sans quoi le contrôle précédent ne vérifierait rien.

        C'est la façon la plus discrète pour une garantie de cesser de servir :
        la propriété reste vraie parce qu'il n'y a plus rien à garantir.
        """
        ajouts = [
            s for s in uc.split_statements(DDL.read_text(encoding="utf-8"))
            if uc.ADD_COLUMNS.match(s)
        ]
        assert len(ajouts) >= 3
