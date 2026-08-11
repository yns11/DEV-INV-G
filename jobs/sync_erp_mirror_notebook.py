# Databricks notebook source
# MAGIC %md
# MAGIC # Synchronisation du miroir ERP
# MAGIC
# MAGIC Copie `silver_base_article` et `silver_bom` d'Unity Catalog vers le miroir
# MAGIC local de l'application, dans sa base Lakebase.
# MAGIC
# MAGIC **Pourquoi ce miroir.** Lire les tables silver depuis l'application exige
# MAGIC `USE CATALOG` sur le catalogue de l'ERP pour *son* service principal, et ce
# MAGIC privilège ne s'accorde que par un propriétaire du catalogue. Ce notebook
# MAGIC renverse la contrainte : il tourne sous **votre** identité, qui a déjà accès
# MAGIC à l'ERP, et dépose la copie là où l'application est chez elle.
# MAGIC
# MAGIC **Mode d'emploi.** Renseignez les widgets en haut, puis « Exécuter tout ».
# MAGIC Pour le planifier : *Schedule* → tous les jours, avant la journée de
# MAGIC comptage. Les valeurs des widgets sont conservées par la planification.
# MAGIC
# MAGIC | Widget | Où le trouver |
# MAGIC |---|---|
# MAGIC | `pg_host` | Console Lakebase → le projet → l'endpoint en écriture. C'est aussi le `PGHOST` visible dans l'onglet *Environment* de l'App. |
# MAGIC | `pg_password` | Facultatif : laissez vide, le notebook cherche un jeton lui-même et dit ce qu'il a trouvé. |
# MAGIC | `pg_user` | Facultatif. Vide, c'est l'identité qui exécute le notebook. |
# MAGIC | `lakebase_branch` | Déjà rempli. Sert à demander un credential dédié à l'endpoint. |
# MAGIC
# MAGIC **Prérequis : l'App doit avoir démarré depuis le dernier déploiement.**
# MAGIC C'est elle qui crée les tables du miroir et les fait évoluer — droits
# MAGIC d'écriture, colonnes nouvelles. Ce notebook ne fait que les remplir, et il
# MAGIC le vérifie avant de lire quoi que ce soit : si l'App est en retard, il
# MAGIC s'arrête en une seconde en le disant, au lieu de le découvrir à la dernière
# MAGIC instruction après avoir chargé tout le référentiel.

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

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ce que le notebook copie
# MAGIC
# MAGIC Une copie **brute** : les colonnes gardent les noms de l'ERP. La traduction
# MAGIC en vocabulaire de campagne — groupe fonctionnel → type d'article, prix ramené
# MAGIC à l'unité, « Commun » → COMMON — reste dans l'application, appliquée à
# MAGIC l'import comme pour une lecture directe. Deux vocabulaires finiraient par
# MAGIC diverger ; il n'y en a qu'un.
# MAGIC
# MAGIC L'ordre des colonnes ci-dessous est un **contrat** : l'application lit le
# MAGIC miroir positionnellement. Une colonne ajoutée ici et pas là-bas décalerait
# MAGIC chaque champ d'un rang, chargeant des prix dans des codes unité, sans que
# MAGIC rien ne lève. Un test du dépôt compare les deux listes.

# COMMAND ----------

ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)

# `statut` (Actif / Inactif) a remplacé `approved` : la table silver contient
# maintenant toutes les versions d'une nomenclature, actives comme inactives.
# Toutes sont copiées — c'est l'application qui n'éclate que celles en vigueur,
# et qui distingue « recette retirée » de « aucune recette ».
BOM_COLUMNS = (
    "parent_itemid", "child_itemid", "child_qty", "child_unitid", "statut",
)

BATCH = 5_000

conf = {name: dbutils.widgets.get(name).strip() for name in (
    "pg_host", "pg_password", "pg_user", "pg_database", "pg_schema",
    "erp_catalog", "erp_schema", "limit", "lakebase_branch",
)}

if not conf["pg_host"]:
    raise ValueError(
        "Renseignez « 1. Hôte Lakebase » : console Lakebase → le projet → "
        "l'endpoint en écriture, ou le PGHOST de l'App (onglet Environment)."
    )

items_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.silver_base_article"
bom_fqn = f"{conf['erp_catalog']}.{conf['erp_schema']}.silver_bom"
limit = int(conf["limit"] or 0)

print(f"ERP    : {items_fqn}\n         {bom_fqn}")
print(f"Miroir : {conf['pg_host']} / {conf['pg_database']} / {conf['pg_schema']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Connexion à Lakebase
# MAGIC
# MAGIC Lakebase authentifie une identité Databricks sous son propre nom, avec un
# MAGIC jeton en guise de mot de passe — mais **seulement un JWT**. Un jeton
# MAGIC personnel (`dapi…`), qui ouvre pourtant toute l'API REST, est refusé :
# MAGIC « Provided authentication token is not a valid JWT encoding ». C'est là que
# MAGIC la version précédente s'est arrêtée.
# MAGIC
# MAGIC Les fournisseurs possibles sont donc essayés dans l'ordre, et la cellule dit
# MAGIC ce que chacun a donné. Si aucun ne convient, le message le dit avec le
# MAGIC détail — plutôt qu'un échec identique au précédent.

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

    Aucun n'est disponible partout : l'API Lakebase du SDK n'existe qu'à partir
    de la version 0.81, le jeton OAuth dépend du mode d'authentification du
    runtime, et le jeton de session n'est un JWT que dans certains contextes.
    Les essayer dans l'ordre, en disant ce que chacun a donné, transforme un
    échec en information.
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
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
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


with psycopg.connect(conninfo) as check:
    check.execute(f"SET search_path TO {conf['pg_schema']}, public")
    assert_mirror_shape(check, "erp_base_article", ITEM_COLUMNS)
    assert_mirror_shape(check, "erp_bom", BOM_COLUMNS)
print("Miroir conforme.")
# COMMAND ----------

# MAGIC %md
# MAGIC ## Lecture de l'ERP
# MAGIC
# MAGIC Une colonne absente de la table silver est copiée à NULL plutôt que
# MAGIC d'arrêter la synchronisation : `statut` n'existait pas avant que la table
# MAGIC porte toutes les versions, et l'application traite son absence comme
# MAGIC « en vigueur ».

# COMMAND ----------

def read(fqn, columns, unique_on=""):
    """Les colonnes demandées, celles qui manquent copiées à NULL.

    `unique_on` déduplique à la source. La table des articles a livré deux
    lignes pour le même `item_id` — le programme y est calculé en cascade, et
    une remontée de nomenclature peut faire éventail — ce qui violait la clé
    primaire du miroir en fin de chargement, après tout le travail utile. Cette
    clé n'est pas du confort : un article y est une ligne, et l'application lit
    le miroir en supposant exactement cela. On déduplique donc plutôt que de la
    lever, de façon déterministe pour que deux exécutions donnent le même
    miroir, et le nombre de lignes écartées est affiché : c'est une anomalie de
    la source, pas une routine.
    """
    available = {f.name.lower() for f in spark.table(fqn).schema.fields}
    missing = [c for c in columns if c.lower() not in available]
    if missing:
        print(f"  {fqn} : absentes, copiées à NULL — {', '.join(missing)}")

    projection = ", ".join(
        c if c.lower() in available else f"CAST(NULL AS STRING) AS {c}"
        for c in columns
    )
    query = f"SELECT {projection} FROM {fqn}"
    if unique_on and unique_on.lower() in available:
        query = (
            f"SELECT {', '.join(columns)} FROM ("
            f"  SELECT {projection}, ROW_NUMBER() OVER ("
            f"    PARTITION BY {unique_on} ORDER BY {', '.join(columns)}"
            f"  ) AS _rang FROM {fqn}"
            f") WHERE _rang = 1"
        )
    if limit:
        query += f" LIMIT {limit}"

    rows = [tuple(row) for row in spark.sql(query).collect()]
    if unique_on and not limit:
        total = spark.table(fqn).count()
        if total > len(rows):
            print(f"  {fqn} : {total - len(rows)} ligne(s) en double sur "
                  f"{unique_on}, une seule conservée par clé")
    return rows


items = read(items_fqn, ITEM_COLUMNS, unique_on="item_id")
boms = read(bom_fqn, BOM_COLUMNS)
print(f"\n{len(items)} articles, {len(boms)} liens de nomenclature")

# Écraser un référentiel valide par un vide fait disparaître la possibilité même
# de lancer une campagne. Un ERP qui ne renvoie rien est une anomalie, pas une
# mise à jour.
if not items:
    raise RuntimeError(
        f"{items_fqn} n'a renvoyé aucune ligne — miroir laissé intact."
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Remplacement atomique
# MAGIC
# MAGIC Chargement dans une table temporaire, puis substitution dans une seule
# MAGIC transaction : une exécution interrompue laisse le miroir précédent intact
# MAGIC plutôt qu'un référentiel à moitié écrit, sur lequel une campagne partirait
# MAGIC sans rien remarquer.

# COMMAND ----------

def swap(conn, table, columns, rows, unique_on=""):
    """`unique_on` filtre une dernière fois à l'insertion.

    La déduplication a déjà eu lieu à la lecture, mais c'est ici que l'échec
    coûte le plus cher : il survient après le chargement complet, sur la
    dernière instruction. Deux mots de SQL rendent la violation impossible
    plutôt que rare.
    """
    staging = f"{table}_staging"
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    distinct = f"DISTINCT ON ({unique_on}) " if unique_on else ""
    order = f" ORDER BY {unique_on}, {names}" if unique_on else ""

    conn.execute(
        f"CREATE TEMP TABLE {staging} (LIKE {table} INCLUDING DEFAULTS) "
        "ON COMMIT DROP"
    )
    with conn.cursor() as cur:
        for start in range(0, len(rows), BATCH):
            cur.executemany(
                f"INSERT INTO {staging} ({names}) VALUES ({placeholders})",
                rows[start:start + BATCH],
            )
    conn.execute(f"TRUNCATE {table}")
    conn.execute(
        f"INSERT INTO {table} ({names}, synced_at) "
        f"SELECT {distinct}{names}, now() FROM {staging}{order}"
    )
    print(f"  {table} : {len(rows)} lignes")


with connection as conn:
    conn.execute(f"SET search_path TO {conf['pg_schema']}, public")
    try:
        swap(conn, "erp_base_article", ITEM_COLUMNS, items, unique_on="item_id")
        swap(conn, "erp_bom", BOM_COLUMNS, boms)
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

print(f"\nMiroir synchronisé : {len(items)} articles, {len(boms)} liens.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Vérification
# MAGIC
# MAGIC Ce que l'application lira, et de quand ça date. C'est cette date qui
# MAGIC s'affiche à côté du bouton « Lire depuis l'ERP », signalée au-delà de sept
# MAGIC jours : charger un référentiel d'un mois sans le voir est exactement
# MAGIC l'erreur que l'application existe pour supprimer.

# COMMAND ----------

with psycopg.connect(conninfo) as conn:
    conn.execute(f"SET search_path TO {conf['pg_schema']}, public")
    for table in ("erp_base_article", "erp_bom"):
        row = conn.execute(
            f"SELECT count(*), max(synced_at) FROM {table}"
        ).fetchone()
        print(f"{table:<20} {row[0]:>8} lignes   synchronisé le {row[1]}")
