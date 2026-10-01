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
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "jobs" / "publish_campaign_to_delta.py"
SYNC = ROOT / "jobs" / "sync_erp_mirror.py"
SHARED = ROOT / "jobs" / "lakebase.py"
SCHEMA = ROOT / "sql" / "00_unity_catalog.sql"
BUNDLE = ROOT / "databricks.yml"


def load_job() -> Any:
    """Le module du job, importé.

    Les contrôles de ce fichier lisent du texte — le source, le DDL, le bundle —
    parce que Spark et un workspace ne sont pas à leur portée. Mais les deux
    tables de requêtes, elles, sont des données ordinaires : les lire comme des
    objets plutôt que comme des chaînes évite d'épingler une mise en forme.

    Le module n'importe ni psycopg ni pyspark au chargement ; ils sont demandés
    dans ``main``, précisément pour que ce genre de lecture reste possible.
    """
    spec = importlib.util.spec_from_file_location("publish_campaign_to_delta", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


publish = load_job()


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
        """Un `spark_python_task` ne met pas toujours ce dossier sur le chemin.

        Ce contrôle épinglait la ligne, mot pour mot. Il a donc certifié
        pendant tout ce temps une ligne qui ne s'exécutait pas : sur le calcul
        serverless, ``__file__`` n'existe pas et les deux jobs mouraient
        dessus. Une ligne présente n'est pas une ligne qui marche.

        Ce qui est vérifié ici est la propriété — le dossier devient
        atteignable — et `test_jobs_bootstrap.py` la vérifie en lançant les
        fichiers comme la plateforme le fait.
        """
        source = job.read_text()
        reads_the_global = any(
            isinstance(node, ast.Name)
            and node.id == "__file__"
            and isinstance(node.ctx, ast.Load)
            for node in ast.walk(ast.parse(source))
        )
        assert "sys.path.insert" in source
        assert not reads_the_global

    def test_the_discovery_lives_in_one_place_only(self):
        """Deux copies dérivent, et c'est ainsi que le défaut était né."""
        for job in (JOB, SYNC):
            assert "_read_write_endpoint" not in code_of(job), job.name


# --------------------------------------------------------------------------- #
# La cause publiée se nomme
# --------------------------------------------------------------------------- #

class TestThePublishedCauseHasAName:
    """Un code d'écart que le catalogue ne sait pas traduire.

    La table `variance_analysis` ne publiait que `cause_code` : « 1 », « 11 »,
    « 99 ». Le référentiel qui les nomme — `assignable_cause`, quatorze lignes
    de code et libellé — vivait uniquement dans Lakebase, et aucune des quatre
    vues ne portait la cause. Un lecteur du catalogue, humain ou génératif,
    voyait donc des causes qu'il lui était impossible de nommer, et un rapport
    de synthèse disait « cause 11 » là où il fallait lire « écart consommation
    (backflush) ».

    Une archive qui ne se comprend pas sans la base opérationnelle qu'elle est
    censée survivre n'est pas une archive.
    """

    def test_le_libelle_part_avec_la_decision(self):
        assert "cause_label" in publish.QUERIES["variance_analysis"]
        assert "cause_family" in publish.QUERIES["variance_analysis"]

    def test_la_proposition_du_modele_se_nomme_aussi(self):
        """Elle est affichée à côté de la décision ; elle se lit comme elle."""
        assert "ai_suggested_cause_label" in publish.QUERIES["variance_analysis"]

    @pytest.mark.parametrize(
        "colonne", ["cause_label", "cause_family", "ai_suggested_cause_label"]
    )
    def test_les_trois_colonnes_existent_dans_le_ddl(self, colonne: str):
        """La colonne est **déclarée**, et non simplement citée quelque part.

        Chercher le nom dans le bloc laissait passer le retrait de
        `cause_label` : `ai_suggested_cause_label` le contient comme sous-chaîne,
        et la recherche restait vraie sur la mauvaise ligne. Une mutation l'a
        montré. Ce qui est cherché est donc le début d'une déclaration.
        """
        sql = sql_without_comments()
        bloc = sql[sql.index("CREATE TABLE IF NOT EXISTS variance_analysis ("):]
        bloc = bloc[: bloc.index(";")]
        declarations = {
            ligne.split()[0] for ligne in bloc.splitlines() if ligne.startswith("    ")
        }
        assert colonne in declarations

    def test_un_deploiement_existant_les_recoit(self):
        """`CREATE TABLE IF NOT EXISTS` ne touche pas une table déjà là.

        Sans ces trois `ALTER`, un catalogue déjà déployé garderait l'ancienne
        forme et la publication y écrirait des libellés absents — le job aligne
        sur le schéma cible et comble les colonnes manquantes par NULL, donc
        rien n'échouerait. Une archive muette sur ses causes, sans un message.
        """
        sql = sql_without_comments()
        for colonne in ("cause_label", "cause_family", "ai_suggested_cause_label"):
            assert f"ADD COLUMNS ({colonne} STRING" in sql, colonne

    def test_le_libelle_est_recopie_et_non_joint(self):
        """Pourquoi la dénormalisation est ici la forme juste.

        Le référentiel est de site : il n'est pas gelé avec la campagne.
        Reformuler la cause 7 l'an prochain changerait rétroactivement ce que
        dit un dossier clos — or un dossier clos est précisément ce qui ne doit
        plus bouger. La ligne porte le libellé de l'époque ; la table de
        référentiel porte celui d'aujourd'hui.
        """
        requete = publish.QUERIES["variance_analysis"]
        assert "LEFT JOIN inventory.assignable_cause c" in requete
        assert "LEFT JOIN inventory.assignable_cause s" in requete
        # Et la jointure est faite **à la publication**, pas laissée à la vue :
        # la colonne est matérialisée dans la table Delta.
        assert "cause_label" in sql_without_comments()


class TestTheSiteReferentialIsPublished:
    """Le vocabulaire, et ce qu'il dit que les lignes ne disent pas."""

    def test_il_a_sa_requete(self):
        assert "assignable_cause" in publish.REFERENTIAL

    def test_il_porte_la_description_et_la_famille(self):
        """Ce que les libellés recopiés sur les lignes ne portent pas."""
        requete = publish.REFERENTIAL["assignable_cause"]
        for colonne in ("code", "label", "family", "description", "active"):
            assert colonne in requete, colonne

    def test_il_n_est_pas_dans_les_tables_de_campagne(self):
        """Il n'appartient à aucune campagne, et sa table n'est pas partitionnée.

        Le mettre dans `QUERIES` l'aurait fait écrire par la boucle qui passe
        `campaign_id` en paramètre et pose un prédicat de remplacement sur la
        partition — sur une table qui n'a ni l'un ni l'autre.
        """
        assert "assignable_cause" not in publish.QUERIES
        sql = sql_without_comments()
        bloc = sql[sql.index("CREATE TABLE IF NOT EXISTS assignable_cause ("):]
        bloc = bloc[: bloc.index(";")]
        assert "PARTITIONED BY" not in bloc

    def test_la_table_existe_dans_le_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS assignable_cause (" in sql_without_comments()

    def test_le_job_verifie_sa_presence_avant_de_lire_la_campagne(self):
        """Comme les autres : découvrir une table absente au bout du travail
        utile est l'échec le plus coûteux possible."""
        arbre = ast.parse(JOB.read_text())
        principale = next(
            n for n in ast.walk(arbre)
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        appel = next(
            n for n in ast.walk(principale)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_missing_tables"
        )
        assert "REFERENTIAL" in ast.unparse(appel.args[2])

    def test_il_est_ecrit_sans_predicat_de_remplacement(self):
        """Une table de site est réécrite en entier ; elle n'a pas de tranche."""
        source = code_of(JOB)
        bloc = source[source.index("for table, query in REFERENTIAL.items()"):][:800]
        assert "replace_predicate=None" in bloc

    def test_tout_ce_qui_porte_une_campagne_garde_son_predicat(self):
        """La permission donnée au référentiel ne s'étend pas aux campagnes.

        Un `replace_predicate=None` sur une table partitionnée réécrirait la
        table entière : toutes les campagnes effacées par la publication d'une
        seule.
        """
        source = code_of(JOB)
        bloc = source[source.index("for table, query in QUERIES.items()"):]
        bloc = bloc[: bloc.index("REFERENTIAL")]
        assert "replace_predicate=campaign_slice" in bloc
        assert "replace_predicate=None" not in bloc


class TestTheVarianceViewCarriesTheCause:
    """La vue qu'une synthèse atteint en premier, et où la cause manquait.

    `v_variance` joignait le stock, le comptage, les ajustements et le
    référentiel articles — jamais l'analyse. Demander « quelle est la principale
    cause d'écart de cette campagne » obligeait donc à connaître une table que
    la vue ne nomme pas, ce qu'un lecteur génératif ne devine pas.
    """

    def test_la_vue_joint_l_analyse(self):
        vue = _view("v_variance")
        assert "FROM variance_analysis" in vue
        assert "LEFT JOIN analysis an" in vue

    def test_elle_rend_le_code_le_libelle_et_la_famille(self):
        vue = _view("v_variance")
        for colonne in ("an.cause_code", "an.cause_label", "an.cause_family"):
            assert colonne in vue, colonne

    def test_et_le_commentaire_qui_porte_le_constat(self):
        """« −412 pièces » est une mesure ; le commentaire est la décision."""
        assert "an.cause_comment" in _view("v_variance")

    def test_la_jointure_porte_l_identifiant_et_l_article(self):
        """Sur le code métier, deux campagnes homonymes mélangeraient leurs
        causes — la règle de toutes les jointures de ce fichier."""
        vue = _view("v_variance")
        bloc = vue[vue.index("LEFT JOIN analysis an"):]
        assert "an.campaign_id" in bloc
        assert "an.item_number" in bloc


def _view(name: str) -> str:
    """Le corps d'une vue, du CREATE au point-virgule."""
    sql = sql_without_comments()
    start = sql.index(f"CREATE OR REPLACE VIEW {name} AS")
    return sql[start : sql.index(";", start)]
