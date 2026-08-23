"""Comment le job de synchronisation trouve la base qu'il doit écrire.

Un job Databricks n'est pas une App : aucune ressource ne lui est attachée, et
la plateforme ne lui injecte donc ni ``PGHOST`` ni ``PGUSER``. La première
version reprenait le contrat de l'application et s'arrêtait au premier
lancement, après avoir lu tout le référentiel — l'échec le plus coûteux
possible, puisqu'il arrive au bout du travail utile.

Ces tests pilotent un faux client SDK : ils vérifient ce que le job demande à
la plateforme et ce qu'il en déduit, sans workspace.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

JOB = Path(__file__).resolve().parents[1] / "jobs" / "sync_erp_mirror.py"


def load_job() -> Any:
    spec = importlib.util.spec_from_file_location("sync_erp_mirror", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


sync = load_job()

#: Le module partagé par les deux jobs. Il est importable parce que le job vient
#: de mettre « jobs/ » sur le chemin — le même geste qu'en production, et c'est
#: bien ce module-là que le job importera.
lakebase = importlib.import_module("lakebase")


class Args:
    """Les arguments de ligne de commande dont la connexion dépend."""

    def __init__(self, **overrides: Any) -> None:
        self.branch = "projects/inventaire/branches/production"
        self.pg_database = "databricks_postgres"
        self.pg_user = ""
        self.pg_host = ""
        self.lakebase_endpoint = ""
        self.__dict__.update(overrides)


def endpoint(name: str, kind: str, host: str | None, **hosts: Any) -> Any:
    return type("E", (), {
        "name": name,
        "status": type("S", (), {
            "endpoint_type": kind,
            "hosts": type("H", (), {"host": host, **hosts})(),
        })(),
    })()


class FakeClient:
    def __init__(self, endpoints: list[Any], *, identity: str = "u@example.com",
                 token: str = "tok-1") -> None:
        self._endpoints = endpoints
        self._token = token
        self.credential_for: list[str] = []
        self.postgres = self
        self.current_user = self
        self._identity = identity

    def list_endpoints(self, branch: str) -> list[Any]:
        self.branch = branch
        return self._endpoints

    def generate_database_credential(self, endpoint_name: str) -> Any:
        self.credential_for.append(endpoint_name)
        return type("C", (), {"token": self._token})()

    def me(self) -> Any:
        return type("U", (), {"user_name": self._identity})()


class OldSdk:
    """Un SDK antérieur à 0.81 : pas de ``w.postgres``, mais un jeton OAuth."""

    def __init__(self, token: str = "oauth-tok") -> None:
        self._token = token
        self.current_user = type("C", (), {
            "me": lambda s: type("U", (), {"user_name": "u@example.com"})()
        })()
        self.config = type("Cfg", (), {
            "oauth_token": lambda s: type("T", (), {"access_token": token})()
        })()


READ_WRITE = [
    endpoint("projects/p/branches/b/endpoints/replica", "READ_ONLY", "ro.example"),
    endpoint("projects/p/branches/b/endpoints/primary", "READ_WRITE", "rw.example"),
]


@pytest.fixture(autouse=True)
def no_ambient_postgres(monkeypatch):
    """Le shell du développeur n'a pas son mot à dire ici.

    `_lakebase_conninfo` consulte l'environnement en premier — c'est sa règle,
    et elle est testée plus bas, ce test-là reposant les variables lui-même.
    Les autres portent sur ce que la fonction *déduit* quand rien n'est fourni.
    """
    from conftest import forget_ambient_postgres

    forget_ambient_postgres(monkeypatch)


class TestFindingTheDatabase:
    def test_the_endpoint_is_deduced_from_the_branch(self):
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(), client)
        assert "host=rw.example" in conninfo
        assert client.branch == "projects/inventaire/branches/production"

    def test_a_read_only_endpoint_is_never_chosen(self):
        """Il lirait sans broncher, puis refuserait le premier INSERT."""
        client = FakeClient([
            endpoint("projects/p/branches/b/endpoints/replica", "READ_ONLY", "ro.example")
        ])
        with pytest.raises(RuntimeError, match="écriture"):
            sync._lakebase_conninfo(Args(), client)

    def test_the_pooled_host_serves_when_the_direct_one_is_absent(self):
        client = FakeClient([
            endpoint("projects/p/branches/b/endpoints/primary", "READ_WRITE", None,
                     read_write_pooled_host="pool.example")
        ])
        assert "host=pool.example" in sync._lakebase_conninfo(Args(), client)

    def test_the_credential_is_minted_for_that_endpoint(self):
        """L'API prend un chemin de ressource ; un nom d'hôte échoue."""
        client = FakeClient(READ_WRITE)
        sync._lakebase_conninfo(Args(), client)
        assert client.credential_for == ["projects/p/branches/b/endpoints/primary"]

    def test_the_role_is_the_identity_running_the_job(self):
        client = FakeClient(READ_WRITE, identity="younes@societe.com")
        assert "user=younes@societe.com" in sync._lakebase_conninfo(Args(), client)

    def test_an_explicit_role_wins_over_the_running_identity(self):
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(pg_user="sync_bot"), client)
        assert "user=sync_bot" in conninfo

    def test_the_postgres_database_name_is_used_not_the_resource_id(self):
        """`databricks-postgres` est l'id de ressource ; la base est `databricks_postgres`."""
        conninfo = sync._lakebase_conninfo(Args(), FakeClient(READ_WRITE))
        assert "dbname=databricks_postgres" in conninfo

    def test_without_a_branch_it_says_what_to_pass(self):
        with pytest.raises(RuntimeError, match="--branch"):
            sync._lakebase_conninfo(Args(branch=""), FakeClient([]))

    def test_the_environment_still_wins_when_it_is_set(self, monkeypatch):
        """Exécution locale, ou rôle dédié sorti d'un secret scope."""
        monkeypatch.setenv("PGHOST", "localhost")
        monkeypatch.setenv("PGUSER", "app")
        monkeypatch.setenv("PGPASSWORD", "secret")
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(), client)
        assert "host=localhost" in conninfo and "user=app" in conninfo
        # Aucun appel à la plateforme : rien à découvrir.
        assert client.credential_for == []


class TestWhatTheJobSaysWhenItCannotWrite:
    """Les deux échecs attendus au premier lancement se ressemblent à l'écran
    et n'ont pas le même remède ; le job doit nommer le bon."""

    def test_an_identity_without_a_postgres_role_is_told_to_create_one(self):
        advice = sync._connection_advice(
            Exception('FATAL: role "u@example.com" does not exist')
        )
        assert "rôle Postgres" in advice and "Roles" in advice

    def test_a_refused_credential_points_at_the_connect_permission(self):
        advice = sync._connection_advice(
            Exception("FATAL: password authentication failed for user")
        )
        assert "CAN_CONNECT" in advice

    def test_a_missing_grant_points_at_the_migration_that_carries_it(self):
        advice = sync._write_advice(
            Exception("permission denied for table erp_base_article"), "inventory"
        )
        assert "migration 006" in advice

    def test_an_unexpected_failure_is_still_reported_verbatim(self):
        """Ne jamais avaler une cause qu'on n'a pas prévue."""
        assert "disque plein" in sync._connection_advice(Exception("disque plein"))
        assert "disque plein" in sync._write_advice(Exception("disque plein"), "inventory")


def declared_in(path: Path, name: str) -> tuple:
    """La valeur d'une constante lue sans exécuter le fichier.

    Le notebook appelle ``dbutils`` au niveau module : il ne s'importe pas hors
    d'un workspace. Sa liste de colonnes doit pourtant rester vérifiable ici,
    puisque c'est elle qui décide de ce qui atterrit dans le miroir.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if targets and getattr(targets[0], "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} introuvable dans {path.name}")


class TestTheColumnsCopied:
    """Le miroir est lu positionnellement : l'ordre des colonnes est un contrat.

    Trois fichiers le déclarent — l'application, le script de job, le notebook —
    et une divergence ne lève rien : elle décale chaque champ d'un rang et charge
    des prix dans des codes unité. D'où ces égalités.
    """

    def application_columns(self) -> tuple:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from inventory.ingest.erp import ITEM_COLUMNS

        return ITEM_COLUMNS

    def test_the_job_copies_the_columns_the_application_reads(self):
        assert self.application_columns() == sync.ITEM_COLUMNS

    def test_the_notebook_copies_them_too(self):
        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        assert self.application_columns() == declared_in(notebook, "ITEM_COLUMNS")

    def test_the_notebook_and_the_script_agree_on_the_bom_columns(self):
        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        assert declared_in(notebook, "BOM_COLUMNS") == sync.BOM_COLUMNS

    def test_the_three_agree_on_the_backflush_columns_too(self):
        """La table de faits est lue de la même façon : positionnellement.

        Une colonne ajoutée au notebook et pas à l'application décalerait
        `conso_theorique` sur `qty_parent_produite`, et le rapport de
        réconciliation soustrairait une production au lieu d'une consommation —
        des chiffres plausibles, faux, et que rien ne signalerait.
        """
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from inventory.ingest.erp import BACKFLUSH_COLUMNS

        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        assert BACKFLUSH_COLUMNS == sync.BACKFLUSH_COLUMNS
        assert declared_in(notebook, "BACKFLUSH_COLUMNS") == BACKFLUSH_COLUMNS

    def test_the_notebook_and_the_application_agree_on_the_movements(self):
        """Mêmes colonnes, même ordre : le miroir se lit positionnellement.

        Une inversion de `item_id` et `mouvement_date` chargerait des dates dans
        des références. La copie serait acceptée — les deux sont du texte — et
        « Tout charger de l'ERP » ne trouverait plus aucun article.
        """
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from inventory.ingest.erp import MOVEMENT_COLUMNS

        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        assert declared_in(notebook, "MOVEMENT_COLUMNS") == MOVEMENT_COLUMNS

    def test_every_column_a_step_reads_is_one_the_notebook_copies(self):
        """Chaque étape lit une colonne ; le miroir doit la porter.

        Les deux déclarations ne se rencontrent nulle part à l'exécution. Une
        colonne oubliée à la copie donnerait un miroir rempli dans lequel une
        étape lirait zéro — sans la moindre erreur, et sur un rapport qui reste
        parfaitement lisible.
        """
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from inventory.ingest.erp import _FLOW_COLUMNS

        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        copied = set(declared_in(notebook, "MOVEMENT_COLUMNS"))
        for kind, column in _FLOW_COLUMNS.items():
            assert column in copied, (
                f"le miroir ne copie pas « {column} », que l'étape {kind} lit"
            )

    def test_production_and_theoretical_consumption_are_copied_too(self):
        """Les cinq flux d'une comparaison sortent de la même copie."""
        notebook = JOB.with_name("sync_erp_mirror_notebook.py")
        copied = set(declared_in(notebook, "MOVEMENT_COLUMNS"))
        assert {"production", "conso_theorique"} <= copied


class TestARowWithoutAReferenceIsDropped:
    """La source en publie, et le miroir les refuse.

    Vu en production : une ligne à `reference` nulle portant 276 442 de
    réception a fait échouer le chargement sur la clé primaire, après les trois
    autres tables. Un mouvement sans article ne se rattache à aucun stock —
    l'application indexe tout par référence — donc il est écarté. Mais compté et
    affiché : une quantité de cet ordre qui disparaît en silence serait pire que
    l'anomalie qu'elle signale.
    """

    def source(self) -> str:
        return JOB.with_name("sync_erp_mirror_notebook.py").read_text(
            encoding="utf-8"
        )

    def test_the_read_filters_them_out(self):
        assert "reference IS NOT NULL" in self.source()

    def test_what_was_dropped_is_reported(self):
        source = self.source()
        assert "sans référence écartée(s)" in source

    def test_the_source_count_uses_the_same_filter(self):
        """Sinon l'écartage volontaire ressemblerait à un écart de copie.

        La cellule de vérification compare source et miroir ligne à ligne et
        signale une différence. Compter la source sans le filtre afficherait un
        « ⚠ écart » permanent à chaque exécution, jusqu'à ce que plus personne
        ne le lise.
        """
        source = self.source()
        assert 'FROM {movements_fqn} WHERE {movements_where}' in source


class TestWhenTheDiscoveryIsRefused:
    """Le deuxième lancement en production s'est arrêté sur « Impossible de
    lister les endpoints » — un message qui avalait sa propre cause, et couvrait
    donc trois pannes sans rapport : des droits manquants sur le projet, une
    branche qui n'existe pas, et un SDK trop ancien pour connaître l'API. Sans
    la cause, aucune ne se distingue des autres.
    """

    class Refusing(FakeClient):
        def __init__(self, exc: Exception) -> None:
            super().__init__([])
            self._exc = exc

        def list_endpoints(self, branch: str) -> list[Any]:
            raise self._exc

    def test_the_cause_reaches_the_message(self):
        client = self.Refusing(PermissionError("PERMISSION_DENIED on project"))
        with pytest.raises(RuntimeError, match="PERMISSION_DENIED on project"):
            sync._lakebase_conninfo(Args(), client)

    def test_the_exception_type_is_named_too(self):
        """« AttributeError » et « PermissionError » n'appellent pas le même geste."""
        client = self.Refusing(AttributeError("no attribute 'list_endpoints'"))
        with pytest.raises(RuntimeError, match="AttributeError"):
            sync._lakebase_conninfo(Args(), client)

    def test_an_sdk_without_the_lakebase_api_asks_for_the_host_not_an_upgrade(self):
        """La version du SDK est figée par le runtime serverless.

        En exiger une autre fait échouer l'installation entière contre
        ``immutable-package-constraints.txt`` — c'est arrivé. Le job doit donc
        demander ce qui est fournissable : l'hôte, relevé une fois dans la
        console.
        """
        with pytest.raises(RuntimeError, match="--pg-host"):
            sync._lakebase_conninfo(Args(), OldSdk())

    def test_a_refused_credential_names_the_endpoint_and_the_cause(self):
        class NoCredential(FakeClient):
            def generate_database_credential(self, endpoint_name: str) -> Any:
                raise PermissionError("not authorized")

        with pytest.raises(RuntimeError, match="not authorized"):
            sync._lakebase_conninfo(Args(), NoCredential(READ_WRITE))


class TestTheEscapeHatches:
    """De quoi avancer sans la découverte, le temps qu'un droit soit accordé."""

    def test_an_explicit_endpoint_skips_the_enumeration(self):
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(
            Args(lakebase_endpoint="projects/p/branches/b/endpoints/e",
                 pg_host="direct.example"),
            client,
        )
        assert "host=direct.example" in conninfo
        assert not hasattr(client, "branch"), "aucun appel à list_endpoints"
        assert client.credential_for == ["projects/p/branches/b/endpoints/e"]

    def test_a_host_and_a_password_avoid_the_sdk_entirely(self, monkeypatch):
        """Le dernier recours : rien n'est demandé à la plateforme."""
        monkeypatch.setenv("PGPASSWORD", "depuis-un-secret-scope")

        class Forbidden:
            def __getattr__(self, name: str) -> Any:
                raise AssertionError(f"le SDK ne doit pas être appelé ({name})")

        conninfo = sync._lakebase_conninfo(
            Args(pg_host="direct.example", pg_user="sync_bot"), Forbidden()
        )
        assert "host=direct.example" in conninfo
        assert "password=depuis-un-secret-scope" in conninfo


class HttpSdk:
    """Un SDK sans ``w.postgres``, mais avec son client HTTP.

    C'est le SDK que le runtime serverless apporte réellement : la 0.49, qui
    ignore l'API Lakebase Autoscaling mais sait parfaitement émettre une requête
    authentifiée. Les réponses reproduisent la forme documentée de l'API.
    """

    def __init__(self, pages: list[dict] | None = None, token: str = "rest-tok") -> None:
        self.pages = pages if pages is not None else [{
            "endpoints": [
                {
                    "name": "projects/p/branches/b/endpoints/replica",
                    "status": {
                        "endpoint_type": "ENDPOINT_TYPE_READ_ONLY",
                        "hosts": {"host": "ro.example"},
                    },
                },
                {
                    "name": "projects/p/branches/b/endpoints/primary",
                    "status": {
                        "endpoint_type": "ENDPOINT_TYPE_READ_WRITE",
                        "hosts": {"host": "rw.example"},
                    },
                },
            ]
        }]
        self._token = token
        self.calls: list[tuple] = []
        self.api_client = self
        self.current_user = type("C", (), {
            "me": lambda s: type("U", (), {"user_name": "u@example.com"})()
        })()

    def do(self, method: str, path: str, **kwargs: Any) -> dict:
        self.calls.append((method, path, kwargs))
        if path == lakebase.CREDENTIALS_PATH:
            return {"token": self._token, "expire_time": "2026-08-24T00:00:00Z"}
        index = 0
        page_token = (kwargs.get("query") or {}).get("page_token")
        if page_token is not None:
            index = int(page_token)
        page = dict(self.pages[index])
        if index + 1 < len(self.pages):
            page["next_page_token"] = str(index + 1)
        return page


class TestTheApiWithoutItsTypedFacade:
    """Le runtime fige la version du SDK, pas l'API qui est derrière.

    ``w.postgres`` n'apparaît qu'en databricks-sdk 0.81 et la version ne peut
    pas être relevée dans un job : elle figure dans les contraintes immuables du
    runtime serverless, si bien qu'en demander une autre fait échouer
    l'installation entière. La publication s'arrêtait donc là, en demandant à
    l'exploitant de relever un hôte à la main.

    Or ce que la 0.81 fait de plus, ce sont deux appels HTTP. Le SDK présent
    sait les émettre.
    """

    def test_l_hote_est_decouvert_sans_la_facade_typee(self):
        """Plus de « passez --pg-host » : la découverte redevient automatique."""
        client = HttpSdk()
        assert "host=rw.example" in sync._lakebase_conninfo(Args(), client)

    def test_l_appel_suit_le_chemin_documente(self):
        client = HttpSdk()
        sync._lakebase_conninfo(Args(), client)

        method, path, _ = client.calls[0]
        assert method == "GET"
        assert path == "/api/2.0/postgres/projects/inventaire/branches/production/endpoints"

    def test_un_endpoint_en_lecture_seule_n_est_pas_plus_choisi_ici(self):
        """La règle vit dans un seul endroit ; les deux chemins la subissent."""
        client = HttpSdk(pages=[{"endpoints": [{
            "name": "projects/p/branches/b/endpoints/replica",
            "status": {
                "endpoint_type": "ENDPOINT_TYPE_READ_ONLY",
                "hosts": {"host": "ro.example"},
            },
        }]}])
        with pytest.raises(RuntimeError, match="écriture"):
            sync._lakebase_conninfo(Args(), client)

    def test_la_pagination_est_suivie(self):
        """L'endpoint en écriture peut être sur la seconde page.

        S'arrêter à la première ferait échouer le job sur « aucun endpoint en
        écriture » alors qu'il y en a un — l'erreur la plus trompeuse possible.
        """
        client = HttpSdk(pages=[
            {"endpoints": [{
                "name": "projects/p/branches/b/endpoints/replica",
                "status": {
                    "endpoint_type": "ENDPOINT_TYPE_READ_ONLY",
                    "hosts": {"host": "ro.example"},
                },
            }]},
            {"endpoints": [{
                "name": "projects/p/branches/b/endpoints/primary",
                "status": {
                    "endpoint_type": "ENDPOINT_TYPE_READ_WRITE",
                    "hosts": {"host": "page2.example"},
                },
            }]},
        ])
        assert "host=page2.example" in sync._lakebase_conninfo(Args(), client)

    def test_le_credential_dedie_est_demande_pour_cet_endpoint(self):
        """Le jeton OAuth de l'identité n'est plus le seul recours."""
        client = HttpSdk()
        conninfo = sync._lakebase_conninfo(Args(), client)

        posts = [c for c in client.calls if c[0] == "POST"]
        assert len(posts) == 1
        assert posts[0][1] == "/api/2.0/postgres/credentials"
        assert posts[0][2]["body"] == {
            "endpoint": "projects/p/branches/b/endpoints/primary"
        }
        assert "password=rest-tok" in conninfo

    def test_la_facade_typee_reste_preferee_quand_elle_existe(self):
        """Rien n'est appelé en direct sur un SDK qui expose l'API."""
        class TypedAndHttp(FakeClient):
            def __init__(self) -> None:
                super().__init__(READ_WRITE)
                self.http_calls: list[tuple] = []
                self.api_client = type("H", (), {
                    "do": lambda s, *a, **k: self.http_calls.append(a)
                })()

        client = TypedAndHttp()
        sync._lakebase_conninfo(Args(), client)
        assert client.http_calls == []


class TestAuthenticatingWithTheSdkThatIsThere:
    """Le job ne peut pas choisir sa version du SDK.

    Elle est figée par les contraintes immuables du runtime serverless : la
    demander autrement fait échouer l'installation de tout l'environnement, et
    le job ne démarre même pas. Il s'accommode donc de ce qui est présent.
    """

    def test_the_dedicated_credential_is_preferred_when_the_api_exists(self):
        client = FakeClient(READ_WRITE)
        sync._lakebase_conninfo(Args(), client)
        assert client.credential_for == ["projects/p/branches/b/endpoints/primary"]

    def test_an_older_sdk_falls_back_to_the_oauth_token(self):
        """Lakebase accepte le jeton de l'identité comme mot de passe."""
        conninfo = sync._lakebase_conninfo(Args(pg_host="direct.example"), OldSdk())
        assert "password=oauth-tok" in conninfo
        assert "host=direct.example" in conninfo

    def test_without_any_credential_it_names_the_secret_scope(self):
        class Nothing(OldSdk):
            def __init__(self) -> None:
                super().__init__()
                self.config = type("Cfg", (), {})()

        with pytest.raises(RuntimeError, match="PGPASSWORD"):
            sync._lakebase_conninfo(Args(pg_host="direct.example"), Nothing())


class TestTheFinalInsertCannotViolateTheKey:
    """L'échec le plus cher du job : la dernière instruction.

    La source a livré deux lignes pour ``mass-00046610`` — le programme y est
    calculé par une remontée de nomenclature qui fait éventail — et la clé
    primaire du miroir a sauté après le chargement complet, soit après tout le
    travail utile. La déduplication a lieu à la lecture ; ce filtre-ci est la
    ceinture, à l'endroit exact où ça a cassé.
    """

    class RecordingConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str) -> Any:
            self.statements.append(statement)

            class Count:
                def fetchone(self):
                    # Le remplacement compte les lignes avant et après ; la
                    # valeur importe peu ici, la présence du comptage si.
                    return (0,)

            return Count()

        def cursor(self):
            outer = self

            class Cur:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def executemany(self, statement, rows):
                    outer.statements.append(statement)

            return Cur()

    def statements_for(self, **kwargs) -> str:
        conn = self.RecordingConn()
        sync._swap(conn, "erp_base_article", ("item_id", "item_name"), **kwargs)
        return "\n".join(conn.statements)

    def test_the_article_load_keeps_one_row_per_key(self):
        assert "DISTINCT ON (item_id)" in self.statements_for(unique_on="item_id")

    def test_and_orders_so_two_runs_choose_the_same_one(self):
        """Sans ORDER BY, DISTINCT ON retient une ligne arbitraire."""
        assert "ORDER BY item_id" in self.statements_for(unique_on="item_id")

    def test_the_bom_load_keeps_every_row(self):
        """Une nomenclature a plusieurs versions par couple : c'est normal."""
        assert "DISTINCT" not in self.statements_for()

    def test_the_swap_stays_atomic(self):
        """TRUNCATE puis INSERT dans la transaction ouverte par l'appelant."""
        statements = self.statements_for(unique_on="item_id").split("\n")
        truncate = next(i for i, s in enumerate(statements) if s.startswith("TRUNCATE"))
        final = next(
            i for i, s in enumerate(statements)
            if s.startswith("INSERT INTO erp_base_article (")
        )
        assert truncate < final


class TestTheMirrorIsCheckedBeforeAnythingIsRead:
    """Le miroir appartient à l'application ; le job ne fait que le remplir.

    Quand les deux se désynchronisent — une colonne ajoutée à la source et à
    l'application, mais l'application pas encore redéployée — Postgres refusait
    la toute dernière instruction, après que le référentiel entier avait été lu
    et transmis. C'est arrivé sur `statut`, et c'est la troisième fois qu'un
    échec bon marché se paie au prix d'un chargement complet.
    """

    class Catalogue:
        """Le catalogue, avec les deux colonnes que la requête demande.

        `information_schema` rend le nom **et** le type : le type sert à copier
        à NULL une colonne que la source ne publie pas, avec le bon type. Une
        doublure qui n'en rendrait qu'un ferait échouer le code livré sur un
        index absent.
        """

        def __init__(self, columns: list[str], kind: str = "text") -> None:
            self._columns = columns
            self._kind = kind

        def execute(self, statement: str, params: Any = None) -> Any:
            class Result:
                def __init__(self, rows): self._rows = rows
                def fetchall(self): return self._rows
            return Result([(c, self._kind) for c in self._columns])

    def test_a_missing_column_names_itself_and_the_remedy(self):
        conn = self.Catalogue(["parent_itemid", "child_itemid"])
        with pytest.raises(RuntimeError) as raised:
            sync._assert_mirror_shape(conn, "erp_bom", ("parent_itemid", "statut"))
        assert "statut" in str(raised.value)
        assert "redéployez" in str(raised.value)

    def test_an_absent_table_says_the_application_creates_them(self):
        with pytest.raises(RuntimeError, match="crée"):
            sync._assert_mirror_shape(self.Catalogue([]), "erp_bom", ("statut",))

    def test_a_mirror_in_step_passes_quietly(self):
        conn = self.Catalogue(["parent_itemid", "statut", "synced_at"])
        shape = sync._assert_mirror_shape(
            conn, "erp_bom", ("parent_itemid", "statut")
        )
        assert shape == {
            "parent_itemid": "STRING", "statut": "STRING", "synced_at": "STRING",
        }

    def test_the_comparison_ignores_case(self):
        conn = self.Catalogue(["PARENT_ITEMID", "STATUT"])
        shape = sync._assert_mirror_shape(
            conn, "erp_bom", ("parent_itemid", "statut")
        )
        assert set(shape) == {"parent_itemid", "statut"}

    def test_the_types_travel_so_a_missing_column_keeps_its_own(self):
        """Un NULL de type chaîne dans une colonne numérique est refusé par la
        base, à la dernière instruction — une fois toute la lecture faite."""
        conn = self.Catalogue(["std_cost_price"], kind="numeric")
        shape = sync._assert_mirror_shape(conn, "erp_bom", ("std_cost_price",))
        assert shape["std_cost_price"] == "DECIMAL(18,6)"


class TestTheMirrorIsReplacedNotAppended:
    """Chaque synchronisation remplace intégralement les deux tables.

    C'était déjà le cas — TRUNCATE puis INSERT, dans la transaction ouverte par
    l'appelant — mais rien ne le montrait : le journal annonçait « 1735 lignes »
    sans dire que 1740 venaient de partir. Une référence retirée de l'ERP
    disparaît bien du miroir ; encore faut-il pouvoir le constater sans lire le
    code.
    """

    def test_the_table_is_emptied_before_being_written(self):
        conn = TestTheFinalInsertCannotViolateTheKey.RecordingConn()
        sync._swap(conn, "erp_bom", ("parent_itemid",))
        assert "TRUNCATE erp_bom" in conn.statements

    def test_the_rows_are_counted_on_both_sides_of_the_replacement(self):
        conn = TestTheFinalInsertCannotViolateTheKey.RecordingConn()
        sync._swap(conn, "erp_bom", ("parent_itemid",))
        counts = [s for s in conn.statements if s.startswith("SELECT count(*)")]
        assert len(counts) == 2, "avant et après, sinon le chiffre ne veut rien dire"
