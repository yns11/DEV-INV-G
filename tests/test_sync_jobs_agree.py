"""Les deux synchronisations du miroir ERP copient bien la même chose.

Le miroir se remplit par deux chemins, et c'est délibéré : le notebook, qui est
la voie opérationnelle parce qu'il tient son jeton du contexte de session, et le
job en ligne de commande, dont la planification reste en pause tant que le SDK
figé par le runtime serverless n'expose pas l'API Lakebase.

Deux chemins, donc deux copies du même code — et elles ont divergé. La reprise
des mouvements de stock n'a été portée que sur le notebook : le job en ligne de
commande a continué de copier articles, nomenclatures et écart backflush, sans
jamais remplir ``erp_mouvements``. Rien n'échouait. La comparaison lisant le
miroir n'y trouvait simplement aucun mouvement, ce qui ressemble à une période
sans activité.

Ces contrôles portent sur ce qui doit être identique — les tables copiées, leurs
colonnes, l'exclusion des lignes sans référence — et laissent différer ce qui
tient à la forme : widgets contre arguments, ``print`` contre journal.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parents[1] / "jobs"
CLI = JOBS / "sync_erp_mirror.py"
NOTEBOOK = JOBS / "sync_erp_mirror_notebook.py"

#: Les tuples de colonnes définissent le contrat avec le miroir : même ordre,
#: mêmes noms, sinon l'insertion écrit une colonne dans une autre.
COLUMN_TUPLES = (
    "ITEM_COLUMNS", "BOM_COLUMNS", "BACKFLUSH_COLUMNS", "MOVEMENT_COLUMNS",
    "STOCK_COLUMNS",
)


def constants(path: Path) -> dict[str, tuple[str, ...]]:
    """Les tuples de chaînes définis au niveau du module."""
    out: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        values = [
            e.value for e in node.value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if values:
            out[target.id] = tuple(values)
    return out


@pytest.mark.parametrize("name", COLUMN_TUPLES)
def test_both_jobs_copy_the_same_columns(name: str) -> None:
    cli, notebook = constants(CLI), constants(NOTEBOOK)
    assert name in cli, f"{CLI.name} ne définit pas {name}"
    assert name in notebook, f"{NOTEBOOK.name} ne définit pas {name}"
    assert cli[name] == notebook[name], (
        f"{name} diverge entre les deux synchronisations :\n"
        f"  {CLI.name}      : {cli[name]}\n"
        f"  {NOTEBOOK.name} : {notebook[name]}"
    )


@pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=lambda p: p.name)
@pytest.mark.parametrize("table", ["erp_base_article", "erp_bom",
                                   "erp_ecart_backflush", "erp_mouvements",
                                   "erp_stock_snapshot"])
def test_every_mirror_table_is_written_by_both(path: Path, table: str) -> None:
    """Une table oubliée d'un côté reste vide, sans que rien ne le signale."""
    source = path.read_text(encoding="utf-8")
    assert re.search(rf'swap\(\s*conn,\s*"{table}"', source), (
        f"{path.name} ne remplit jamais {table}."
    )


@pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=lambda p: p.name)
def test_rows_without_a_reference_are_filtered_out(path: Path) -> None:
    """La clé primaire du miroir est la référence : sans elle, l'insertion échoue.

    C'est la panne qu'a connue le notebook — ``NotNullViolation`` sur
    ``reference``, la synchronisation entière perdue sur des lignes dont
    l'application n'aurait rien fait.
    """
    source = path.read_text(encoding="utf-8")
    assert "reference IS NOT NULL" in source, (
        f"{path.name} copie les mouvements sans écarter les lignes sans "
        "référence : l'insertion échouera sur la contrainte de clé primaire."
    )


# --------------------------------------------------------------------------- #
# Le type d'une colonne absente, des deux côtés
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
def test_both_read_the_column_types_with_the_names(path: Path) -> None:
    """Une colonne que la source cesse de publier est copiée à NULL — et ce
    NULL doit porter le type de la colonne du miroir.

    Un NULL de type chaîne dans une colonne numérique est refusé par la base, à
    la dernière instruction, une fois toute la lecture faite. C'est précisément
    la situation que la copie à NULL existe pour traverser.
    """
    assert "column_name, data_type" in path.read_text()


@pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
def test_both_translate_the_types_through_the_shared_module(path: Path) -> None:
    """Deux traductions PostgreSQL → Spark finiraient par ne plus s'accorder ;
    c'est exactement ainsi que les deux jobs avaient divergé."""
    source = path.read_text()
    assert "from mirror import spark_type" in source
    assert "spark_type(str(r[1]))" in source


@pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
def test_both_hand_the_types_to_the_reader(path: Path) -> None:
    """Les lire sans les transmettre ne servirait à rien, et rien ne le dirait :
    la copie continuerait de fonctionner tant qu'aucune colonne ne manque.

    C'est la forme relevée du miroir qui doit voyager, pas n'importe quel
    paramètre nommé `types` — les fonctions relais en portent un aussi.
    """
    assert "types=shapes" in path.read_text()
