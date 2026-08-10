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
# MAGIC | `pg_password` | Facultatif. Vide, le notebook utilise le jeton de la session. Sinon un jeton personnel (*Paramètres → Développeur → Jetons d'accès*). |
# MAGIC | `pg_user` | Facultatif. Vide, c'est l'identité qui exécute le notebook. |
# MAGIC
# MAGIC Prérequis, une seule fois : l'App doit avoir démarré au moins une fois
# MAGIC depuis la migration `006` — c'est elle qui ouvre l'écriture du miroir à une
# MAGIC autre identité que la sienne.

# COMMAND ----------

# MAGIC %pip install psycopg[binary]==3.2.3
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

BOM_COLUMNS = (
    "parent_itemid", "child_itemid", "child_qty", "child_unitid", "approved",
)

BATCH = 5_000

conf = {name: dbutils.widgets.get(name).strip() for name in (
    "pg_host", "pg_password", "pg_user", "pg_database", "pg_schema",
    "erp_catalog", "erp_schema", "limit",
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
# MAGIC ## Lecture de l'ERP
# MAGIC
# MAGIC Une colonne absente de la table silver est copiée à NULL plutôt que
# MAGIC d'arrêter la synchronisation : `approved` en particulier n'existe pas
# MAGIC partout, et l'application sait la traiter comme inconnue.

# COMMAND ----------

def read(fqn, columns):
    available = {f.name.lower() for f in spark.table(fqn).schema.fields}
    missing = [c for c in columns if c.lower() not in available]
    if missing:
        print(f"  {fqn} : absentes, copiées à NULL — {', '.join(missing)}")

    projection = ", ".join(
        c if c.lower() in available else f"CAST(NULL AS STRING) AS {c}"
        for c in columns
    )
    query = f"SELECT {projection} FROM {fqn}"
    if limit:
        query += f" LIMIT {limit}"
    return [tuple(row) for row in spark.sql(query).collect()]


items = read(items_fqn, ITEM_COLUMNS)
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
# MAGIC ## Connexion à Lakebase
# MAGIC
# MAGIC Lakebase authentifie une identité Databricks sous son propre nom, avec un
# MAGIC jeton en guise de mot de passe. À défaut de jeton fourni en widget, celui de
# MAGIC la session sert — c'est le chemin qui ne dépend d'aucune version de SDK, et
# MAGIC c'est là que la version en ligne de commande s'était arrêtée.

# COMMAND ----------

user = conf["pg_user"] or spark.sql("SELECT current_user()").collect()[0][0]

password = conf["pg_password"]
if not password:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    password = ctx.apiToken().get()

if not password:
    raise RuntimeError(
        "Aucun jeton disponible. Collez un jeton personnel dans le widget « 2 » "
        "(Paramètres → Développeur → Jetons d'accès)."
    )

print(f"Identité : {user}")

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

# MAGIC %md
# MAGIC ## Remplacement atomique
# MAGIC
# MAGIC Chargement dans une table temporaire, puis substitution dans une seule
# MAGIC transaction : une exécution interrompue laisse le miroir précédent intact
# MAGIC plutôt qu'un référentiel à moitié écrit, sur lequel une campagne partirait
# MAGIC sans rien remarquer.

# COMMAND ----------

def swap(conn, table, columns, rows):
    staging = f"{table}_staging"
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

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
        f"SELECT {names}, now() FROM {staging}"
    )
    print(f"  {table} : {len(rows)} lignes")


with connection as conn:
    conn.execute(f"SET search_path TO {conf['pg_schema']}, public")
    try:
        swap(conn, "erp_base_article", ITEM_COLUMNS, items)
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
