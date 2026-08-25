# Databricks notebook source
# MAGIC %md
# MAGIC # Synchronisation du miroir ERP
# MAGIC
# MAGIC Copie vers la base Lakebase de l'application : `silver_base_article`,
# MAGIC `silver_bom`, `fact_ecart_backflush` et `mouvements`.
# MAGIC
# MAGIC **Pourquoi.** Lire Unity Catalog depuis l'application exige `USE CATALOG`
# MAGIC pour *son* service principal, que seul un propriétaire de catalogue peut
# MAGIC accorder. Ce notebook tourne sous **votre** identité, qui a déjà l'accès.
# MAGIC
# MAGIC **Remplacement, pas ajout.** Chaque table est vidée puis réécrite dans une
# MAGIC seule transaction : une référence retirée de l'ERP disparaît du miroir, et
# MAGIC une exécution interrompue laisse la copie précédente intacte.
# MAGIC
# MAGIC **Les deux tables datées sont bornées** (widgets 13 et 15) : elles
# MAGIC grossissent indéfiniment. La dernière cellule affiche l'intervalle
# MAGIC réellement couvert — c'est la réponse quand un écran n'affiche rien.
# MAGIC
# MAGIC **Mode d'emploi.** Renseignez les widgets, « Exécuter tout », puis
# MAGIC *Schedule* → tous les jours avant la journée de comptage.
# MAGIC
# MAGIC | Widget | Où le trouver |
# MAGIC |---|---|
# MAGIC | `pg_host` | Console Lakebase → le projet → l'endpoint en écriture, ou le `PGHOST` de l'App (onglet *Environment*). |
# MAGIC | `pg_password`, `pg_user` | Facultatifs : vides, le notebook prend le jeton et l'identité de la session. |
# MAGIC | `lakebase_branch` | Déjà rempli. Sert à demander un credential dédié. |
# MAGIC | `*_since` | La date à partir de laquelle copier. Une période hors de cet intervalle ne renverra rien. |
# MAGIC
# MAGIC **Prérequis : l'App doit avoir démarré depuis le dernier déploiement** —
# MAGIC c'est elle qui crée les tables du miroir. Le notebook le vérifie avant de
# MAGIC lire quoi que ce soit, plutôt que de l'apprendre à la dernière instruction.

# COMMAND ----------

# MAGIC %pip install psycopg[binary]==3.2.3
# MAGIC # Le SDK récent expose l'API Lakebase, qui délivre le seul jeton que
# MAGIC # Postgres accepte ici. L'installation peut être ignorée sous serverless,
# MAGIC # dont les versions sont figées : le notebook essaie alors d'autres
# MAGIC # sources, et le dit. Sur un cluster classique elle aboutit.
# MAGIC %pip install databricks-sdk==0.81.0
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("pg_host", "", "1. Hôte Lakebase")
dbutils.widgets.text("pg_password", "", "2. Jeton (vide = session)")
dbutils.widgets.text("pg_user", "", "3. Rôle Postgres (vide = vous)")
dbutils.widgets.text("pg_database", "databricks_postgres", "4. Base Postgres")
dbutils.widgets.text("pg_schema", "inventory", "5. Schéma de l'application")
dbutils.widgets.text("erp_catalog", "emotors_data_champions", "6. Catalogue ERP")
dbutils.widgets.text("erp_schema", "silver_erp_ye", "7. Schéma ERP")
dbutils.widgets.text("limit", "0", "8. Limite (0 = tout)")
dbutils.widgets.text(
    "lakebase_branch", "projects/inventaire/branches/production",
    "9. Branche Lakebase",
)
# Publié par un autre pipeline, donc dans son propre schéma.
dbutils.widgets.dropdown(
    "sync_backflush", "oui", ["oui", "non"], "10. Copier l'écart backflush",
)
dbutils.widgets.text("backflush_schema", "backflush", "11. Schéma backflush")
dbutils.widgets.text(
    "backflush_table", "fact_ecart_backflush", "12. Table de faits",
)
# Le notebook ramène ces tables au pilote pour les écrire : la borne garde le
# volume borné. Par défaut, le début d'historique publié.
dbutils.widgets.text(
    "backflush_since", "2026-03-30", "13. Écart backflush depuis (lundi ISO)",
)
# Dans le schéma du référentiel : même catalogue, même grant.
dbutils.widgets.dropdown(
    "sync_movements", "oui", ["oui", "non"], "14. Copier les mouvements de stock",
)
dbutils.widgets.text(
    "movements_since", "2026-03-30", "15. Mouvements depuis (date)",
)
# Le stock physique : une photo par jour, dont les plus récentes sont copiées.
# L'écran d'import laisse choisir la date ; n'en copier qu'une, comme c'était le
# cas, rendait ce choix inatteignable.
dbutils.widgets.dropdown(
    "sync_stock", "oui", ["oui", "non"], "16. Copier le stock physique",
)
dbutils.widgets.text("stock_days", "7", "17. Photos de stock à garder")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ce que le notebook copie
# MAGIC
# MAGIC Une copie **brute** : les colonnes gardent les noms de l'ERP. La
# MAGIC traduction en vocabulaire de campagne reste dans l'application.
# MAGIC
# MAGIC Leur ordre est un **contrat** — le miroir est lu positionnellement. Une
# MAGIC colonne ajoutée ici et pas là-bas décalerait chaque champ d'un rang sans
# MAGIC que rien ne lève ; un test du dépôt compare les deux listes.

# COMMAND ----------

ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)

# Toutes les versions sont copiées, actives ou non : c'est l'application qui
# n'éclate que celles en vigueur.
BOM_COLUMNS = (
    "parent_itemid", "child_itemid", "child_qty", "child_unitid", "statut",
)

# Maille semaine, celle de la source : les bornes se choisissent campagne par
# campagne, un job ne peut pas pré-agréger sur une période qu'il ignore.
BACKFLUSH_COLUMNS = (
    "semaine_debut", "parent_itemid", "child_itemid", "child_name", "child_unite",
    "qty_parent_produite", "conso_theorique", "conso_reelle", "ecart_brut",
    "loaded_at",
)

# Une ligne par référence et par jour, une colonne par flux.
MOVEMENT_COLUMNS = (
    "reference", "date_mouvement", "reception", "expedition", "production",
    "conso_theorique", "consommation", "rebut",
)

# Une ligne par article × entrepôt × emplacement, pour un jour donné.
STOCK_COLUMNS = (
    "item_id", "entrepot", "emplacement", "stock_physique", "unite",
    "snapshot_date",
)


conf = {name: dbutils.widgets.get(name).strip() for name in (
    "pg_host", "pg_password", "pg_user", "pg_database", "pg_schema",
    "erp_catalog", "erp_schema", "limit", "lakebase_branch",
    "sync_backflush", "backflush_schema", "backflush_table", "backflush_since",
    "sync_movements", "movements_since", "sync_stock", "stock_days",
)}

if not conf["pg_host"]:
    raise ValueError(
        "Renseignez « 1. Hôte Lakebase » : console Lakebase → le projet → "
        "l'endpoint en écriture, ou le PGHOST de l'App (onglet Environment)."
    )

items_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.silver_base_article"
bom_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.silver_bom"
backflush_fqn = (
    f"{conf['erp_catalog']}.{conf['backflush_schema']}.{conf['backflush_table']}"
)
limit = int(conf["limit"] or 0)
with_backflush = conf["sync_backflush"] == "oui"
backflush_since = conf["backflush_since"]

movements_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.mouvements"
stock_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.stock_snapshot"
with_stock = conf["sync_stock"] == "oui"
# Des jours **publiés**, pas des jours du calendrier : la source ne publie pas
# le week-end, et un « depuis sept jours » aurait donné cinq photos une semaine
# et sept la suivante. Au moins une : zéro viderait le miroir.
stock_days = max(1, int(conf["stock_days"] or 7))
stock_where = (
    f"snapshot_date >= (SELECT min(d) FROM (SELECT DISTINCT snapshot_date AS d "
    f"FROM {stock_fqn} ORDER BY d DESC LIMIT {stock_days}))"
)
with_movements = conf["sync_movements"] == "oui"
movements_since = conf["movements_since"]

# La source porte des lignes sans référence — un mouvement qui ne se rattache à
# aucun article. Le miroir les refuserait (clé primaire), et l'application n'en
# ferait rien : tout y est indexé par référence. Elles sont donc écartées, mais
# comptées et affichées : une quantité qui disparaît en silence est pire que
# l'anomalie qu'elle signale.
movements_where = " AND ".join(clause for clause in (
    "reference IS NOT NULL",
    f"date_mouvement >= DATE '{movements_since}'" if movements_since else "",
) if clause)

print(f"ERP    : {items_fqn}\n         {bom_fqn}")
if with_backflush:
    print(f"         {backflush_fqn}"
          + (f"  (depuis le {backflush_since})" if backflush_since else ""))
if with_movements:
    print(f"         {movements_fqn}"
          + (f"  (depuis le {movements_since})" if movements_since else ""))
print(f"Miroir : {conf['pg_host']} / {conf['pg_database']} / {conf['pg_schema']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Connexion à Lakebase
# MAGIC
# MAGIC Lakebase accepte un jeton en guise de mot de passe, mais **seulement un
# MAGIC JWT** : un jeton personnel (`dapi…`) est refusé. Les fournisseurs sont donc
# MAGIC essayés dans l'ordre, et la cellule dit ce que chacun a donné.

# COMMAND ----------

user = conf["pg_user"] or spark.sql("SELECT current_user()").collect()[0][0]


def looks_like_a_jwt(token):
    """Lakebase refuse tout ce qui n'est pas un JWT.

    Un jeton personnel Databricks commence par ``dapi`` et n'a pas de points :
    il authentifie parfaitement l'API REST, et Lakebase le rejette par
    « Provided authentication token is not a valid JWT encoding ». Les deux
    sortes de jetons se ressemblent à l'usage ; seule leur forme les distingue,
    et la vérifier ici évite d'aller le découvrir au bout d'une connexion.
    """
    return bool(token) and token.count(".") == 2 and token.startswith("ey")


def token_sources():
    """Les fournisseurs de jeton, du plus ciblé au plus général.

    Aucun n'est disponible partout, et les essayer dans l'ordre en disant ce que
    chacun a donné transforme un échec en information.
    """
    yield "widget « 2. Jeton »", lambda: conf["pg_password"]

    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient()

    def dedicated_credential():
        api = getattr(client, "postgres", None)
        if api is None:
            return None
        for endpoint in api.list_endpoints(conf["lakebase_branch"]):
            if "READ_WRITE" in str(getattr(endpoint.status, "endpoint_type", "")):
                return api.generate_database_credential(endpoint.name).token
        return None

    def legacy_credential():
        api = getattr(client, "database", None)
        if api is None:
            return None
        import uuid

        return api.generate_database_credential(
            request_id=str(uuid.uuid4()), instance_names=[conf["pg_host"]]
        ).token

    yield "credential Lakebase (w.postgres)", dedicated_credential
    yield "credential Lakebase (w.database)", legacy_credential
    yield "jeton OAuth du SDK", lambda: client.config.oauth_token().access_token
    yield "jeton de session (dbutils)", lambda: (
        dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        .apiToken().get()
    )


password = ""
for label, source in token_sources():
    try:
        candidate = source()
    except Exception as exc:
        print(f"  {label:<34} indisponible ({type(exc).__name__})")
        continue
    if not candidate:
        print(f"  {label:<34} vide")
    elif not looks_like_a_jwt(candidate):
        print(f"  {label:<34} pas un JWT ({candidate[:4]}…, "
              f"{len(candidate)} caractères)")
    else:
        print(f"  {label:<34} JWT retenu")
        password = candidate
        break

if not password:
    raise RuntimeError(
        "Aucun JWT disponible — voir le détail par source ci-dessus. Deux "
        "recours : exécuter ce notebook sur un cluster classique après avoir "
        "installé « databricks-sdk==0.81.0 » (l'API Lakebase du SDK y devient "
        "disponible, contrairement au serverless dont les versions sont figées), "
        "ou créer dans la console Lakebase un rôle Postgres natif avec mot de "
        "passe et le renseigner dans les widgets 2 et 3."
    )

print(f"\nIdentité : {user}")

# COMMAND ----------

import psycopg

conninfo = (
    f"host={conf['pg_host']} port=5432 dbname={conf['pg_database']} "
    f"user={user} password={password} sslmode=require"
)

try:
    connection = psycopg.connect(conninfo)
except Exception as exc:
    message = str(exc)
    if "does not exist" in message and "role" in message.lower():
        raise RuntimeError(
            f"L'identité « {user} » n'a pas de rôle Postgres dans la base "
            "Lakebase. Ajoutez-la dans la console Lakebase (le projet → Roles), "
            f"puis relancez. Détail : {message}"
        ) from exc
    raise RuntimeError(f"Connexion à Lakebase impossible : {message}") from exc

# La même connexion, dans la forme que Spark attend. Les exécuteurs
# n'utilisent pas psycopg : ils écrivent par JDBC, chacun sa partition, sans
# que rien ne converge vers le driver. Traduite depuis la chaîne déjà
# construite plutôt que redécouverte — deux découvertes finissent par diverger.
jdbc_url = (
    f"jdbc:postgresql://{conf['pg_host']}:5432/{conf['pg_database']}"
    "?sslmode=require"
)
jdbc_properties = {
    "user": user,
    "password": password,
    "driver": "org.postgresql.Driver",
}

print("Connecté.")

# COMMAND ----------
def assert_mirror_shape(conn, table, columns):
    """Refuse de démarrer si le miroir n'a pas les colonnes à écrire.

    Les tables du miroir appartiennent à l'application, qui les crée et les fait
    évoluer à son démarrage ; ce notebook ne fait que les remplir. Quand les deux
    se désynchronisent — une colonne ajoutée à la source et à l'application, mais
    l'application pas encore redéployée — Postgres refuse la toute dernière
    instruction, après que le référentiel entier a été lu et transmis.
    L'interroger d'abord transforme cela en un arrêt immédiat et explicite.
    """
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s",
        (table,),
    ).fetchall()
    present = {str(r[0]).lower() for r in rows}
    if not present:
        raise RuntimeError(
            f"La table « {table} » n'existe pas dans le schéma du miroir. "
            "Démarrez l'application une fois : c'est elle qui crée et fait "
            "évoluer ces tables."
        )
    missing = [c for c in columns if c.lower() not in present]
    if missing:
        raise RuntimeError(
            f"Le miroir « {table} » n'a pas la ou les colonnes "
            f"{', '.join(missing)}. Elles arrivent avec une migration de "
            "l'application : redéployez-la, laissez-la démarrer une fois, puis "
            "relancez cette synchronisation."
        )
    # Les types sont rendus avec la vérification : ils servent à copier à NULL,
    # **avec le bon type**, une colonne que la source ne publie pas. Un NULL de
    # type chaîne dans une colonne numérique est refusé par la base.
    from mirror import spark_type

    return {str(r[0]).lower(): spark_type(str(r[1])) for r in rows}


shapes = {}
with psycopg.connect(conninfo) as check:
    check.execute(f"SET search_path TO {conf['pg_schema']}, public")
    shapes["erp_base_article"] = assert_mirror_shape(
        check, "erp_base_article", ITEM_COLUMNS)
    shapes["erp_bom"] = assert_mirror_shape(check, "erp_bom", BOM_COLUMNS)
    if with_backflush:
        shapes["erp_ecart_backflush"] = assert_mirror_shape(
            check, "erp_ecart_backflush", BACKFLUSH_COLUMNS)
    if with_movements:
        shapes["erp_mouvements"] = assert_mirror_shape(
            check, "erp_mouvements", MOVEMENT_COLUMNS)
    if with_stock:
        shapes["erp_stock_snapshot"] = assert_mirror_shape(
            check, "erp_stock_snapshot", STOCK_COLUMNS)
print("Miroir conforme.")
# COMMAND ----------

# MAGIC %md
# MAGIC ## Lecture de l'ERP
# MAGIC
# MAGIC Une colonne absente de la source est copiée à NULL plutôt que d'arrêter
# MAGIC la synchronisation.

# COMMAND ----------

# Le module partagé, importé plutôt que recopié. Les deux synchronisations
# avaient divergé une fois — la reprise des mouvements n'était passée que par
# ici — et la copie est justement la partie qu'on ne veut pas voir diverger
# deux fois. Le dossier du notebook est mis sur le chemin d'import : c'est le
# mécanisme des fichiers dans un dépôt Databricks, le même qui permet au job en
# ligne de commande d'importer « lakebase ».
import os
import sys

_here = os.path.dirname(os.path.abspath(
    dbutils.notebook.entry_point.getDbutils().notebook()
    .getContext().notebookPath().get()
)) if "dbutils" in dir() else os.getcwd()
for _candidate in (_here, os.getcwd(), "/Workspace" + _here):
    if os.path.exists(os.path.join(_candidate, "mirror.py")):
        sys.path.insert(0, _candidate)
        break

try:
    from mirror import frame_of, stage, swap
except ImportError as exc:  # pragma: no cover - dépend de l'espace de travail
    raise RuntimeError(
        "« mirror.py » introuvable à côté de ce notebook. Il porte la copie "
        "partagée par les deux synchronisations ; déployez le dossier « jobs » "
        "entier, ou activez les fichiers dans le dépôt."
    ) from exc


def prepare(fqn, columns, table, unique_on="", where=""):
    """Prépare une table dans son attente, et rend le nombre de lignes.

    Rien ne transite par le driver : chaque exécuteur écrit sa partition par
    JDBC. Le nombre revient de la base — l'appelant en a besoin *avant* la
    substitution, une source qui ne renvoie rien étant une anomalie et non une
    mise à jour.
    """
    frame = frame_of(
        spark, fqn, columns, where=where, limit=limit, unique_on=unique_on,
        types=shapes.get(table, {}),
        warn=lambda message: print(f"  {message}"),
    )
    return stage(
        connection, frame, table, columns,
        jdbc_url=jdbc_url, jdbc_properties=jdbc_properties,
    )


items = prepare(items_fqn, ITEM_COLUMNS, "erp_base_article", unique_on="item_id")
boms = prepare(bom_fqn, BOM_COLUMNS, "erp_bom")
print(f"\n{items} articles, {boms} liens de nomenclature")

# L'écart backflush est lu après le référentiel, et son échec n'annule pas ce
# dernier : un pipeline gold indisponible ne doit pas priver l'application de
# ses articles. Le miroir garde alors sa copie précédente, dont la fraîcheur est
# affichée à l'écran de l'application.
backflush = 0
if with_backflush:
    try:
        backflush = prepare(
            backflush_fqn, BACKFLUSH_COLUMNS, "erp_ecart_backflush",
            where=(
                f"semaine_debut >= DATE '{backflush_since}'"
                if backflush_since else ""
            ),
        )
        print(f"{backflush} ligne(s) d'écart backflush")
    except Exception as exc:
        print(f"\n⚠ {backflush_fqn} illisible, miroir de l'écart laissé "
              f"intact : {type(exc).__name__} — {exc}")
        with_backflush = False

# Même règle que pour le backflush : indisponible, il ne prive pas l'application
# de son référentiel et le miroir garde sa copie précédente.
movements = 0
if with_movements:
    try:
        movements = prepare(
            movements_fqn, MOVEMENT_COLUMNS, "erp_mouvements",
            where=movements_where,
        )
        print(f"{movements} ligne(s) de mouvement de stock")
        orphelines = spark.sql(
            f"SELECT count(*), coalesce(sum(reception + expedition + production "
            f"+ conso_theorique + consommation + rebut), 0) FROM {movements_fqn} "
            f"WHERE reference IS NULL"
            + (f" AND date_mouvement >= DATE '{movements_since}'"
               if movements_since else "")
        ).collect()[0]
        if orphelines[0]:
            print(f"  ⚠ {orphelines[0]} ligne(s) sans référence écartée(s), "
                  f"{orphelines[1]:,.2f} de quantité au total. Un mouvement sans "
                  "article ne se rattache à aucun stock : à signaler à la "
                  "plateforme si le total est significatif.")
    except Exception as exc:
        print(f"\n⚠ {movements_fqn} illisible, miroir des mouvements "
              f"laissé intact : {type(exc).__name__} — {exc}")
        with_movements = False

# Même règle encore, mais sur une fenêtre. L'application ne lit qu'un jour à la
# fois — un comptage se compare à *un* état du système, jamais à un stock
# additionné sur trois mois — mais **lequel** est un choix, et l'écran d'import
# le propose : le comptage a commencé samedi, la reprise se fait le lundi, et
# c'est la photo de samedi qui fait foi. Le miroir n'en gardait qu'une, et la
# liste « Photo du » n'offrait donc jamais que la plus récente.
stock = 0
if with_stock:
    try:
        stock = prepare(
            stock_fqn, STOCK_COLUMNS, "erp_stock_snapshot", where=stock_where,
        )
        # Les dates viennent de la base, pas de lignes gardées en mémoire :
        # plus rien ne transite par le driver.
        jours = connection.execute(
            "SELECT count(DISTINCT snapshot_date), max(snapshot_date) "
            "FROM erp_stock_snapshot_staging"
        ).fetchone() if stock else (0, "—")
        print(f"{stock} ligne(s) de stock physique, "
              f"{jours[0]} photo(s), la plus récente au {jours[1]}")
    except Exception as exc:
        print(f"\n⚠ {stock_fqn} illisible, miroir du stock laissé "
              f"intact : {type(exc).__name__} — {exc}")
        with_stock = False

# Un ERP qui ne renvoie rien est une anomalie, pas une mise à jour : le
# remplacement étant intégral, une lecture vide effacerait tout.
for label, fqn, loaded in (("articles", items_fqn, items),
                           ("nomenclatures", bom_fqn, boms)):
    if not loaded:
        raise RuntimeError(
            f"{fqn} ({label}) n'a renvoyé aucune ligne — miroir laissé intact."
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Remplacement atomique
# MAGIC
# MAGIC Table temporaire puis substitution, en une transaction : une exécution
# MAGIC interrompue laisse le miroir précédent intact plutôt qu'à moitié écrit.

# COMMAND ----------

def _say(message):
    """Le notebook parle par `print` ; le module ignore lequel des deux l'appelle."""
    print(f"  {message}")


with connection as conn:
    conn.execute(f"SET search_path TO {conf['pg_schema']}, public")
    try:
        swap(conn, "erp_base_article", ITEM_COLUMNS, unique_on="item_id", say=_say)
        swap(conn, "erp_bom", BOM_COLUMNS, say=_say)
        # Une lecture vide garde la copie précédente, sans interrompre le
        # reste : le référentiel, lui, est passé.
        if with_backflush and backflush:
            swap(conn, "erp_ecart_backflush", BACKFLUSH_COLUMNS, say=_say)
        elif with_backflush:
            print(f"  erp_ecart_backflush : {backflush_fqn} n'a renvoyé aucune "
                  "ligne sur la période — miroir laissé intact")
        # Pas de `unique_on` : le grain déclaré de la source *est* la clé du
        # miroir, et un DISTINCT masquerait une source non conforme.
        if with_movements and movements:
            swap(conn, "erp_mouvements", MOVEMENT_COLUMNS, say=_say)
        elif with_movements:
            print(f"  erp_mouvements : {movements_fqn} n'a renvoyé aucune ligne "
                  "sur la période — miroir laissé intact")
        if with_stock and stock:
            swap(conn, "erp_stock_snapshot", STOCK_COLUMNS, say=_say)
        elif with_stock:
            print(f"  erp_stock_snapshot : {stock_fqn} n'a renvoyé aucune ligne "
                  "— miroir du stock laissé intact")
    except Exception as exc:
        if "permission denied" in str(exc).lower():
            raise RuntimeError(
                "Droits d'écriture manquants sur le miroir. Les tables "
                "appartiennent au service principal de l'App ; la migration 006 "
                "les ouvre. Démarrez l'App une fois, puis relancez. "
                f"Détail : {exc}"
            ) from exc
        raise
    conn.commit()

print(f"\nMiroir synchronisé : {items} articles, {boms} liens"
      + (f", {backflush} ligne(s) d'écart backflush" if backflush else "")
      + (f", {movements} ligne(s) de mouvement" if movements else "")
      + (f", {stock} ligne(s) de stock physique" if stock else "")
      + ".")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Vérification
# MAGIC
# MAGIC Ce que l'application lira, et de quand ça date — la date affichée à côté
# MAGIC du bouton « Lire depuis l'ERP », signalée au-delà de sept jours.

# COMMAND ----------

# La source et le miroir côte à côte. Un écart a deux causes possibles, que la
# date de synchronisation départage : une exécution qui a échoué, ou deux
# chiffres qui ne comptent pas la même chose.
sources = {
    "erp_base_article": spark.table(items_fqn).count(),
    "erp_bom": spark.table(bom_fqn).count(),
}
# Même borne que la copie, sans quoi l'historique laissé de côté ressemblerait
# à un écart permanent.
if with_backflush:
    sources["erp_ecart_backflush"] = spark.sql(
        f"SELECT count(*) FROM {backflush_fqn}"
        + (f" WHERE semaine_debut >= DATE '{backflush_since}'"
           if backflush_since else "")
    ).collect()[0][0]
# Mêmes filtres que la copie — borne et lignes sans référence —, sans quoi ce
# qui a été volontairement écarté ressemblerait à un écart permanent.
if with_movements:
    sources["erp_mouvements"] = spark.sql(
        f"SELECT count(*) FROM {movements_fqn} WHERE {movements_where}"
    ).collect()[0][0]

with psycopg.connect(conninfo) as conn:
    conn.execute(f"SET search_path TO {conf['pg_schema']}, public")
    print(f"{'Table':<20}{'Source':>9}{'Miroir':>9}   Synchronisé le")
    for table, source_rows in sources.items():
        rows, synced = conn.execute(
            f"SELECT count(*), max(synced_at) FROM {table}"
        ).fetchone()
        flag = "" if rows == source_rows else "   ⚠ écart"
        print(f"{table:<20}{source_rows:>9}{rows:>9}   {synced}{flag}")

    dupes = conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT parent_itemid, child_itemid, statut, count(*) AS n"
        "  FROM erp_bom GROUP BY 1, 2, 3 HAVING count(*) > 1"
        ") d"
    ).fetchone()[0]
    if dupes:
        print(f"\n{dupes} couple(s) parent/enfant présents plusieurs fois dans le "
              "même statut. Plusieurs versions d'une même recette sont normales ; "
              "plusieurs fois la même version ne l'est pas.")

    # « Ma période est-elle dans le miroir ? » — la seule question que pose un
    # écran vide.
    if with_backflush:
        first, last, weeks = conn.execute(
            "SELECT min(semaine_debut), max(semaine_debut), "
            "count(DISTINCT semaine_debut) FROM erp_ecart_backflush"
        ).fetchone()
        if weeks:
            print(f"\nÉcart backflush : {weeks} semaine(s) couvertes, "
                  f"du {first} au {last} inclus.")
            print("Une période d'inventaire hors de cet intervalle ne renverra "
                  "rien — c'est la borne du widget 13 qu'il faut alors reculer.")
        else:
            print("\nÉcart backflush : le miroir est vide. L'écran Backflush et "
                  "la comparaison entre campagnes ne renverront rien tant que "
                  "cette cellule n'affiche pas de semaines.")

    # Même question, posée aux mouvements : « Tout charger de l'ERP » borne sa
    # lecture sur les deux dates d'inventaire, et un « 0 réception » ne dit pas
    # tout seul si la période est hors de la copie ou si l'usine n'a rien reçu.
    # Le détail par nature parce qu'elles viennent de trois tables : les rebuts
    # peuvent manquer là où les réceptions sont là.
    if with_movements:
        # Le détail par flux : une période couverte peut n'avoir aucun rebut,
        # et « 0 rebut » est alors une information sur l'usine, pas sur la copie.
        first, last, rows = conn.execute(
            "SELECT min(date_mouvement), max(date_mouvement), count(*) "
            "FROM erp_mouvements"
        ).fetchone()
        if rows:
            print(f"\nMouvements de stock : {rows} ligne(s) référence × jour, "
                  f"du {first} au {last} inclus.")
            totaux = conn.execute(
                "SELECT sum(reception), sum(expedition), sum(production), "
                "sum(conso_theorique), sum(consommation), sum(rebut) "
                "FROM erp_mouvements"
            ).fetchone()
            for label, total in zip(
                ("réception", "expédition", "production", "conso théo.",
                 "consommation", "rebut"),
                totaux,
                strict=True,
            ):
                print(f"  {label:<13} {total:>16,.2f}")
            print("Une période d'inventaire hors de cet intervalle ne renverra "
                  "rien — c'est la borne du widget 15 qu'il faut alors reculer.")
        else:
            print("\nMouvements de stock : le miroir est vide. « Tout charger de "
                  "l'ERP » ne renverra rien tant que cette cellule n'affiche pas "
                  "de lignes.")
