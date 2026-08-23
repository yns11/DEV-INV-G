"""Se connecter à Lakebase depuis un job, qui ne reçoit rien de la plateforme.

Une App Databricks reçoit ``PGHOST`` / ``PGDATABASE`` / ``PGUSER`` parce qu'une
ressource ``postgres`` lui est attachée. **Un job n'en reçoit rien** : ce n'est
pas une App, et il n'a pas de ressources. Les deux jobs de ce dépôt écrivaient
pourtant le contrat de l'application et s'arrêtaient net au premier lancement,
sur un « PGHOST, PGDATABASE and PGUSER must be set » que rien dans le bundle
n'aurait pu satisfaire.

Ce qu'un job connaît, c'est la **branche** Lakebase — passée en paramètre,
construite depuis les mêmes variables de bundle que la ressource de l'App, si
bien que les deux ne peuvent pas désigner des branches différentes. Le reste
s'en déduit : l'endpoint en écriture, son hôte, l'identité qui exécute le job,
et un credential OAuth frais.

Ce module existe parce que la logique était juste une fois, dans le job de
synchronisation, et fausse dans celui de publication — qui appelait encore
``w.database.generate_database_credential``, l'API du palier provisionné, sur
un projet Lakebase Autoscaling. Une seule implémentation, deux appelants.

Les variables d'environnement restent prioritaires quand elles sont présentes :
exécution locale, ou secret scope pour un rôle Postgres dédié.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("lakebase")

__all__ = ["conninfo"]


def conninfo(args: Any, client: Any = None) -> str:
    """La chaîne de connexion, découverte plutôt qu'attendue.

    *args* doit porter ``pg_host``, ``pg_database``, ``pg_user``, ``branch`` et
    ``lakebase_endpoint`` — les deux jobs déclarent ces options sous les mêmes
    noms, précisément pour que ce module n'ait pas à connaître lequel l'appelle.
    """
    return _lakebase_conninfo(args, client)


def _lakebase_conninfo(args: Any, client: Any = None) -> str:
    """Chaîne de connexion Lakebase, découverte plutôt qu'attendue.

    Une App Databricks reçoit PGHOST / PGDATABASE / PGUSER de la plateforme
    parce qu'une ressource ``postgres`` lui est attachée. **Un job n'en reçoit
    rien** : ce n'est pas une App, et il n'a pas de ressources. La première
    version de ce fichier reprenait le contrat de l'application et s'arrêtait
    donc net au premier lancement.

    Ce que le job connaît, c'est la branche Lakebase — passée en paramètre,
    construite depuis les mêmes variables de bundle que la ressource de l'App,
    si bien que les deux ne peuvent pas désigner des branches différentes. Le
    reste s'en déduit : l'endpoint en écriture, son hôte, l'identité qui exécute
    le job, et un credential OAuth frais.

    Les variables d'environnement restent prioritaires quand elles sont là :
    exécution locale, ou secret scope pour un rôle Postgres dédié.
    """
    host = os.environ.get("PGHOST") or args.pg_host
    database = os.environ.get("PGDATABASE") or args.pg_database
    user = os.environ.get("PGUSER") or args.pg_user
    password = os.environ.get("PGPASSWORD")

    if not (host and user and password):
        client = client or _workspace_client()
        log.info("SDK Databricks %s", _sdk_version())
        user = user or _current_identity(client)
        api = getattr(client, "postgres", None)

        name = args.lakebase_endpoint
        if not host:
            if api is None:
                raise RuntimeError(
                    "Hôte Lakebase inconnu, et le SDK de cet environnement ne "
                    f"connaît pas l'API Lakebase Autoscaling (version "
                    f"{_sdk_version()}, w.postgres apparaît en 0.81). Sa version "
                    "est figée par le runtime serverless : passez --pg-host, "
                    "relevé dans la console Lakebase."
                )
            found, resolved_host = _read_write_endpoint(api, args.branch)
            name = name or found
            host = resolved_host

        password = password or _password(client, api, name)
        log.info("Lakebase : hôte %s, identité %s, base %s", host, user, database)

    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")
    return (
        f"host={host} port={port} dbname={database} user={user} "
        f"password={password} sslmode={sslmode}"
    )


def _workspace_client() -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _sdk_version() -> str:
    """La version du SDK, dans le journal du job.

    L'environnement serverless d'un job n'est pas celui de l'App : il apporte sa
    propre version du SDK, et une API absente s'y présente comme une erreur
    d'attribut au milieu d'un appel. Une ligne de journal rend la question
    tranchable en un coup d'œil au lieu d'un aller-retour.
    """
    try:
        from importlib.metadata import version

        return version("databricks-sdk")
    except Exception:  # pragma: no cover — dépend de l'environnement
        return "inconnue"


def _password(client: Any, api: Any, endpoint: str) -> str:
    """Le mot de passe Postgres, par ordre de préférence décroissante.

    Le credential dédié de ``w.postgres`` est le meilleur : il porte sur un
    endpoint précis et expire vite. Mais cette API n'existe qu'à partir de
    databricks-sdk 0.81, et **la version du SDK ne peut pas être relevée dans un
    job** : elle figure dans les contraintes immuables du runtime serverless, si
    bien qu'en demander une autre fait échouer l'installation entière — c'est ce
    qui est arrivé.

    Le repli est le jeton OAuth de l'identité qui exécute le job, que Lakebase
    accepte comme mot de passe. Moins ciblé, disponible partout. Un job qui
    refuserait de tourner faute d'une dépendance impossible à satisfaire ne
    serait utile à personne.
    """
    if api is not None and endpoint:
        return _mint(api, endpoint)

    for source in ("oauth_token", "token"):
        try:
            credential = getattr(client.config, source)()
        except Exception:  # pragma: no cover — dépend de la version du SDK
            continue
        token = getattr(credential, "access_token", None) or getattr(
            credential, "token", None
        )
        if token:
            log.info("Authentification par jeton OAuth (%s)", source)
            return str(token)

    raise RuntimeError(
        "Aucun moyen d'authentifier la connexion Lakebase : ni credential "
        f"dédié (SDK {_sdk_version()}, w.postgres apparaît en 0.81, et sa "
        "version est figée par le runtime), ni jeton OAuth. Exportez "
        "PGPASSWORD depuis un secret scope."
    )


def _read_write_endpoint(api: Any, branch: str) -> tuple[str, str]:
    """Le chemin de ressource de l'endpoint en écriture, et son hôte.

    On écrit : un endpoint en lecture seule ferait échouer la synchronisation
    au premier INSERT, après avoir lu tout le référentiel.
    """
    if not branch:
        raise RuntimeError(
            "Branche Lakebase inconnue : passez --branch "
            "projects/<projet>/branches/<branche>, ou exportez PGHOST, PGUSER "
            "et PGPASSWORD depuis un secret scope."
        )
    try:
        endpoints = list(api.list_endpoints(branch))
    except Exception as exc:
        # La cause décide du geste — droits manquants, branche inexistante,
        # méthode absente d'un SDK plus ancien — et elle ne doit donc jamais
        # être avalée : sans elle, les trois se ressemblent.
        raise RuntimeError(
            f"Impossible de lister les endpoints de « {branch} » : "
            f"{type(exc).__name__}: {exc}. Vérifiez que la branche existe et que "
            "l'identité qui exécute le job a accès au projet Lakebase ; à défaut, "
            "passez --lakebase-endpoint et --pg-host."
        ) from exc

    for endpoint in endpoints:
        status = getattr(endpoint, "status", None)
        if "READ_WRITE" not in str(getattr(status, "endpoint_type", "")):
            continue
        hosts = getattr(status, "hosts", None)
        host = getattr(hosts, "host", None) or getattr(
            hosts, "read_write_pooled_host", None
        )
        if host:
            return endpoint.name, str(host)

    raise RuntimeError(
        f"Aucun endpoint en écriture sur « {branch} ». Vérifiez la branche, ou "
        "exportez PGHOST / PGUSER / PGPASSWORD depuis un secret scope."
    )


def _current_identity(client: Any) -> str:
    """Le rôle Postgres de l'identité qui exécute le job.

    Lakebase authentifie une identité Databricks sous son propre nom : l'adresse
    e-mail pour une personne, l'application id pour un service principal.
    """
    try:
        name = client.current_user.me().user_name
    except Exception as exc:
        raise RuntimeError(
            "Impossible de déterminer l'identité qui exécute le job ; "
            "passez --pg-user."
        ) from exc
    if not name:
        raise RuntimeError("Identité sans nom d'utilisateur ; passez --pg-user.")
    return str(name)


def _mint(api: Any, endpoint: str) -> str:
    """Un credential Lakebase, comme l'application en demande un.

    ``postgres.generate_database_credential`` prend le chemin de ressource d'un
    endpoint. L'API ``database.*`` de l'ancien palier provisionné, appelée avec
    un nom d'hôte, échoue par « Database instance not found » — elle n'est pas
    tentée.
    """
    try:
        credential = api.generate_database_credential(endpoint)
    except Exception as exc:
        raise RuntimeError(
            f"Credential Lakebase refusé pour « {endpoint} » : "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    token = getattr(credential, "token", None)
    if not token:
        raise RuntimeError("Databricks n'a pas renvoyé de credential Lakebase.")
    return str(token)

