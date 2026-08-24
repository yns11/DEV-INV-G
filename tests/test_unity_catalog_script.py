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

    def test_le_schema_et_les_dix_tables_y_sont(self):
        client = FakeClient()
        uc.apply(client, "w-1", DDL.read_text(encoding="utf-8"))
        envoyees = [c["statement"] for c in client.statement_execution.calls]

        assert any(s.startswith("CREATE SCHEMA") for s in envoyees)
        creations = [s for s in envoyees if s.startswith("CREATE TABLE")]
        assert len(creations) == 10
        assert any("publication" in s for s in creations), "le manifeste qui manquait"

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
