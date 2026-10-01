"""Applique ``sql/00_unity_catalog.sql`` sur un SQL warehouse.

    python scripts/apply_unity_catalog.py --warehouse-id <ID> --profile PROD

Pourquoi un script plutôt qu'une commande
------------------------------------------
Le README, le Makefile et l'en-tête du fichier SQL donnaient tous les trois :

    databricks sql query --warehouse-id <ID> --file sql/00_unity_catalog.sql

Cette commande n'existe pas. La CLI répond « unknown command "sql" » et propose
« psql ». `make uc` — le premier geste de tout déploiement, celui qui crée le
schéma, le volume, les dix tables et les vues — n'a donc jamais pu fonctionner
tel qu'il était documenté. Le défaut ne s'est vu qu'au moment où une table a
manqué à un job, des mois plus tard.

L'API d'exécution de requêtes, elle, est stable et présente dans le SDK dont
l'application dépend déjà.

Une session par instruction
---------------------------
``execute_statement`` ouvre une session par appel : un ``USE CATALOG`` n'y
survivrait pas à l'instruction suivante. Le catalogue et le schéma courants sont
donc suivis ici et passés en paramètres — ce qui reproduit exactement la
sémantique du fichier, ``CREATE SCHEMA`` compris, qui doit s'exécuter avec un
catalogue mais sans schéma.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "sql" / "00_unity_catalog.sql"

#: ``USE CATALOG x`` / ``USE SCHEMA y``, sous leurs formes acceptées.
USE = re.compile(r"^\s*USE\s+(CATALOG|SCHEMA|DATABASE)\s+`?([^\s`;]+)`?\s*$", re.I)

#: ``ALTER TABLE … ADD COLUMN(S) …``, la seule instruction non rejouable du
#: fichier.
#:
#: Tout le reste est en ``CREATE … IF NOT EXISTS`` ou ``CREATE OR REPLACE VIEW``,
#: donc rejouable par construction. Ajouter une colonne à une table existante ne
#: l'est pas : ``ADD COLUMNS`` n'admet pas de ``IF NOT EXISTS`` dans Databricks
#: SQL — la référence du langage ne le prévoit pas — et le second passage échoue
#: sur « la colonne existe déjà ».
#:
#: Les trois issues possibles étaient : ne rien ajouter et laisser un déploiement
#: existant sans les nouvelles colonnes, ce qui fait publier une archive muette ;
#: documenter un geste manuel, et ce dépôt porte un fichier de contrôles entier
#: sur la fois où ``make uc`` ne marchait pas comme documenté ; ou rendre la
#: rejouabilité vraie ici. C'est la troisième.
ADD_COLUMNS = re.compile(r"^\s*ALTER\s+TABLE\s+.*\bADD\s+COLUMNS?\b", re.I | re.S)

#: Ce que répond le warehouse quand la colonne demandée est déjà là.
#:
#: Plusieurs formulations plutôt qu'une : le message est de l'outil, pas du
#: contrat, et la classe d'erreur comme la phrase ont déjà changé de nom entre
#: deux versions du moteur.
ALREADY_THERE = re.compile(
    r"already exist|FIELDS_ALREADY_EXIST|FIELD_ALREADY_EXISTS|DELTA_ADD_COLUMN_EXISTING",
    re.I,
)


def split_statements(sql: str) -> list[str]:
    """Les instructions d'un script SQL, séparées sur les ``;`` de premier rang.

    Un ``;`` dans un commentaire ou dans une chaîne ne sépare rien. Le fichier
    n'en contient pas aujourd'hui, mais il porte des libellés en français pleins
    d'apostrophes échappées — ``d''un accident`` — et découper naïvement
    marcherait jusqu'au jour où quelqu'un écrit « scans ; imports » dans un
    COMMENT. Un déploiement qui casse sur une virgule de rédaction est
    exactement ce qu'on ne veut pas.

    La dernière instruction est rendue même sans ``;`` final.
    """
    statements: list[str] = []
    current: list[str] = []
    quoted = False
    comment = False
    block = False
    index = 0

    while index < len(sql):
        char = sql[index]
        pair = sql[index : index + 2]

        if comment:
            if char == "\n":
                comment = False
            current.append(char)
        elif block:
            current.append(char)
            if pair == "*/":
                current.append(sql[index + 1])
                index += 1
                block = False
        elif quoted:
            current.append(char)
            if char == "'":
                # Une apostrophe échappée s'écrit `''` : deux quotes, donc une
                # parité inchangée. Elle ferme puis rouvre la chaîne, et son
                # contenu reste protégé — inutile de la traiter à part, et une
                # branche qu'aucune mutation ne fait tomber n'a rien à faire ici.
                quoted = False
        elif pair == "--":
            comment = True
            current.append(char)
        elif pair == "/*":
            block = True
            current.append(char)
        elif char == "'":
            quoted = True
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1

    statements.append("".join(current))
    return [s for s in (stripped(s) for s in statements) if s]


def stripped(statement: str) -> str:
    """L'instruction sans ses lignes de commentaire ni ses blancs de bordure.

    Une instruction qui n'est *que* du commentaire ne doit rien exécuter : le
    fichier en porte de longs blocs entre deux tables.
    """
    lines = [
        line for line in statement.splitlines() if not line.lstrip().startswith("--")
    ]
    return "\n".join(lines).strip()


def sessioned(statements: list[str]) -> Iterator[tuple[str, str | None, str | None]]:
    """Chaque instruction avec le catalogue et le schéma qui doivent la porter.

    Les ``USE`` sont consommés ici et jamais envoyés : ils n'auraient aucun
    effet, chaque appel ouvrant sa propre session.
    """
    catalog: str | None = None
    schema: str | None = None
    for statement in statements:
        use = USE.match(statement)
        if use:
            kind, name = use.group(1).upper(), use.group(2)
            if kind == "CATALOG":
                catalog, schema = name, None
            else:
                schema = name
            continue
        yield statement, catalog, schema


def apply(client: Any, warehouse_id: str, sql: str) -> int:
    """Exécute le script, une instruction après l'autre. Rend le décompte."""
    statements = split_statements(sql)
    done = 0
    for statement, catalog, schema in sessioned(statements):
        head = " ".join(statement.split())[:70]
        print(f"  [{done + 1:>2}] {head}", flush=True)
        response = client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=warehouse_id,
            catalog=catalog,
            schema=schema,
            wait_timeout="50s",
        )
        state = getattr(getattr(response, "status", None), "state", None)
        if str(getattr(state, "value", state)) not in ("SUCCEEDED", "PENDING", "RUNNING"):
            error = getattr(getattr(response, "status", None), "error", None)
            message = str(getattr(error, "message", error) or "")
            if tolerated(statement, message):
                print("       déjà présente, ignorée", flush=True)
                done += 1
                continue
            raise RuntimeError(
                f"Instruction refusée ({state}) : {message}\n  {head}"
            )
        done += 1
    return done


def tolerated(statement: str, message: str) -> bool:
    """Un refus qui veut dire « c'est déjà fait », et seulement celui-là.

    Deux conditions, toutes les deux nécessaires : l'instruction doit être un
    ajout de colonne — la seule du fichier qui ne sache pas se rejouer — et le
    refus doit nommer une colonne déjà présente. Un ``ADD COLUMNS`` refusé pour
    une autre raison, une table absente ou un type invalide, échoue comme le
    reste.

    Avaler large serait pire que de ne rien avaler : un déploiement qui se
    déclare appliqué sans l'être est exactement le défaut que ce script a été
    écrit pour corriger.
    """
    return bool(ADD_COLUMNS.match(statement)) and bool(ALREADY_THERE.search(message))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "databricks-sdk est absent de cet interpréteur. Installez-le :\n"
            "    python -m pip install databricks-sdk\n"
            "ou posez toutes les dépendances du projet d'un coup : make install",
            file=sys.stderr,
        )
        return 2

    client = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    print(f"Application de {args.file.name} sur le warehouse {args.warehouse_id}")
    done = apply(client, args.warehouse_id, args.file.read_text(encoding="utf-8"))
    print(f"{done} instruction(s) appliquée(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
