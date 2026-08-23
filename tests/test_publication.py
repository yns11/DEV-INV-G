"""L'archive Delta porte l'identifiant, se déclare complète, et se déploie.

Trois défauts du rapport d'audit, sur la même chaîne : celle qui produit la
copie opposable d'une campagne.

**La partition était le code métier.** Un code se réutilise — l'application ne
supprime que logiquement — et créer une campagne « INV-2026-06 » après en avoir
retiré une du même nom faisait écraser l'archive de la première par les données
de la seconde. En silence, et sans recours : l'archive est précisément ce qui
reste quand la base opérationnelle a évolué.

**Rien ne distinguait une publication complète d'une publication interrompue.**
Delta n'offre pas de transaction couvrant plusieurs tables ; une panne au milieu
laissait quelques tables à la nouvelle version et les autres à l'ancienne, sans
que rien ne le dise. Un manifeste écrit en dernier tranche : une campagne est
publiée si, et seulement si, elle y figure.

**Le job n'était pas déployable.** Il attendait ``PGHOST`` / ``PGDATABASE`` /
``PGUSER`` comme l'application, alors qu'un job n'est pas une App et ne reçoit
aucune ressource. Le bundle ne les lui passait pas davantage. Son repli appelait
en outre ``w.database.generate_database_credential`` — l'API du palier
provisionné — sur un projet Autoscaling.

Ces contrôles lisent le source du job et le bundle : faire tourner Spark et un
workspace Databricks n'est pas à leur portée, et ce qui est en cause ici est
justement ce qui se décide avant qu'ils démarrent.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "jobs" / "publish_campaign_to_delta.py"
SYNC = ROOT / "jobs" / "sync_erp_mirror.py"
SHARED = ROOT / "jobs" / "lakebase.py"
SCHEMA = ROOT / "sql" / "00_unity_catalog.sql"
BUNDLE = ROOT / "databricks.yml"


def code_of(path: Path) -> str:
    """Le code d'un module, docstrings et commentaires retirés.

    Les docstrings de ces jobs citent l'ancienne API et l'ancien prédicat pour
    expliquer ce qui change ; les lire comme du code ferait échouer un contrôle
    sur sa propre explication.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            node.value.value = ""
    return ast.unparse(tree)


def sql_without_comments() -> str:
    return "\n".join(
        line for line in SCHEMA.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


# --------------------------------------------------------------------------- #
# La partition
# --------------------------------------------------------------------------- #

class TestThePartitionIsTheImmutableKey:
    def test_no_table_is_partitioned_by_the_business_code(self):
        """Un code se réutilise après une suppression logique."""
        assert "PARTITIONED BY (campaign_code)" not in sql_without_comments()

    def test_every_partitioned_table_uses_the_identifier(self):
        sql = sql_without_comments()
        assert sql.count("PARTITIONED BY (campaign_id)") >= 8

    def test_the_job_replaces_a_slice_named_by_the_identifier(self):
        source = JOB.read_text()
        assert "campaign_id = '{_escape(campaign_id)}'" in source

    def test_the_job_never_scopes_a_write_by_the_code(self):
        """C'était le prédicat de `replaceWhere` : la faute exacte."""
        assert "campaign_code = '{_escape(code)}'" not in code_of(JOB)

    def test_the_code_survives_as_a_readable_column(self):
        """Un humain qui parcourt l'archive cherche « INV-2026-06 », pas un UUID."""
        assert '"campaign_code": code' in JOB.read_text()
        assert "campaign_code STRING" in sql_without_comments()


class TestTheViewsJoinOnTheIdentifierToo:
    """Sinon deux campagnes homonymes additionnent leurs stocks dans une ligne."""

    def test_no_view_groups_by_the_code(self):
        sql = sql_without_comments()
        assert "GROUP BY campaign_code" not in sql
        assert "GROUP BY w.campaign_code" not in sql

    def test_no_view_joins_on_the_code(self):
        sql = sql_without_comments()
        for join in ("b.campaign_code = c.campaign_code",
                     "w.campaign_code = i.campaign_code"):
            assert join not in sql, join

    def test_the_recurrence_view_counts_distinct_identifiers(self):
        assert "COUNT(DISTINCT campaign_id)" in sql_without_comments()


# --------------------------------------------------------------------------- #
# Le manifeste
# --------------------------------------------------------------------------- #

class TestTheManifest:
    def test_the_table_exists(self):
        assert "CREATE TABLE IF NOT EXISTS publication (" in sql_without_comments()

    def test_it_carries_the_per_table_counts(self):
        """« L'archive est-elle fidèle » sans relire les neuf tables."""
        sql = sql_without_comments()
        assert "row_counts" in sql
        assert "MAP<STRING, BIGINT>" in sql

    def test_the_job_writes_it_last(self):
        """Écrit en premier, il déclarerait complet un dossier qui ne l'est pas."""
        source = JOB.read_text()
        manifest = source.index('"publication",')
        for other in ("item_snapshot", "book_stock_snapshot"):
            assert source.index(f'"{other}"') < manifest, other

    def test_nothing_else_writes_it(self):
        """Sa valeur tient entièrement à ce qu'une seule chose la produise."""
        assert JOB.read_text().count('"publication",') == 1

    def test_the_manifest_row_is_shaped_as_the_table_expects(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("publish_job", JOB)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
        row = module.manifest("id-1", "INV-2026-06", at, {"campaign": 1, "item": 40})
        assert row["campaign_id"] == "id-1"
        assert row["campaign_code"] == "INV-2026-06"
        assert row["published_at"] == at
        assert row["table_count"] == 2
        assert row["row_total"] == 41
        assert row["row_counts"] == {"campaign": 1, "item": 40}

    def test_the_counts_are_sorted_so_two_runs_read_alike(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("publish_job2", JOB)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = module.manifest(
            "id", "C", dt.datetime(2026, 9, 1, tzinfo=dt.UTC), {"z": 1, "a": 2}
        )
        assert list(row["row_counts"]) == ["a", "z"]


# --------------------------------------------------------------------------- #
# La déployabilité
# --------------------------------------------------------------------------- #

class TestTheJobCanActuallyConnect:
    def test_it_no_longer_demands_variables_a_job_never_receives(self):
        assert "PGHOST, PGDATABASE and PGUSER must be set" not in code_of(JOB)

    def test_it_no_longer_calls_the_provisioned_tier_api(self):
        """`w.database.generate_database_credential` : le mauvais palier."""
        assert "generate_database_credential" not in code_of(JOB)

    def test_it_accepts_the_branch_the_bundle_passes(self):
        assert '"--branch"' in JOB.read_text()

    @pytest.mark.parametrize(
        "option", ["--branch", "--lakebase-endpoint", "--pg-host", "--pg-user"]
    )
    def test_it_declares_the_same_options_as_the_sync_job(self, option):
        """Le module partagé les lit sous ces noms-là, pour les deux appelants."""
        assert f'"{option}"' in JOB.read_text()


class TestTheBundleSuppliesWhatTheJobNeeds:
    def bundle(self) -> dict:
        return yaml.safe_load(BUNDLE.read_text())

    def publish_parameters(self) -> list[str]:
        jobs = self.bundle()["resources"]["jobs"]
        task = jobs["inventory_publish_campaign"]["tasks"][0]
        return [str(p) for p in task["spark_python_task"]["parameters"]]

    def test_the_branch_is_passed(self):
        assert "--branch" in self.publish_parameters()

    def test_the_branch_is_built_from_the_same_variables_as_the_app(self):
        """Deux constructions différentes désigneraient deux branches."""
        params = self.publish_parameters()
        branch = params[params.index("--branch") + 1]
        assert branch == (
            "projects/${var.lakebase_project}/branches/${var.lakebase_branch}"
        )

    def test_both_jobs_are_given_the_same_branch(self):
        jobs = self.bundle()["resources"]["jobs"]
        branches = []
        for name in ("inventory_publish_campaign", "inventory_sync_erp_mirror"):
            params = [
                str(p)
                for p in jobs[name]["tasks"][0]["spark_python_task"]["parameters"]
            ]
            branches.append(params[params.index("--branch") + 1])
        assert branches[0] == branches[1]


# --------------------------------------------------------------------------- #
# Une seule découverte d'endpoint, pour deux jobs
# --------------------------------------------------------------------------- #

class TestOneImplementationTwoCallers:
    """La logique était juste dans un job et périmée dans l'autre.

    C'est exactement ce qui rend un correctif invisible : il est appliqué à
    l'endroit où le défaut a été constaté, et pas à son jumeau.
    """

    def test_the_shared_module_exposes_the_connection_in_both_forms(self):
        """``conninfo`` pour psycopg, ``jdbc_of`` pour les exécuteurs.

        Deux formes, une seule découverte : redécouvrir l'hôte pour l'écriture
        distribuée ferait exactement ce que ce module existe pour empêcher —
        deux versions d'une même résolution, dont une périmée.
        """
        tree = ast.parse(SHARED.read_text())
        exported = [
            node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        ]
        assert exported == ["conninfo", "jdbc_of"]

    def test_the_jdbc_form_is_derived_and_not_rediscovered(self):
        """Elle prend la chaîne déjà construite, pas les arguments du job."""
        source = SHARED.read_text()
        block = source[source.index("def jdbc_of(") :][:900]
        assert "conninfo_string" in block
        assert "WorkspaceClient" not in block

    @pytest.mark.parametrize("job", [JOB, SYNC], ids=["publish", "sync"])
    def test_both_jobs_import_it(self, job):
        assert "from lakebase import conninfo" in job.read_text()

    @pytest.mark.parametrize("job", [JOB, SYNC], ids=["publish", "sync"])
    def test_both_jobs_make_the_sibling_importable(self, job):
        """Un `spark_python_task` ne met pas toujours ce dossier sur le chemin."""
        source = job.read_text()
        assert "sys.path.insert(0, str(Path(__file__).resolve().parent))" in source

    def test_the_discovery_lives_in_one_place_only(self):
        """Deux copies dérivent, et c'est ainsi que le défaut était né."""
        for job in (JOB, SYNC):
            assert "_read_write_endpoint" not in code_of(job), job.name
