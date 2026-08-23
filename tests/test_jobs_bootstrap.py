"""Les deux jobs démarrent comme le calcul serverless les lance.

Un `spark_python_task` n'est pas toujours *importé* : sur le calcul serverless,
la plateforme lit le fichier et l'exécute par
``exec(compile(source, chemin, "exec"))`` dans un espace de noms ipykernel. Ce
détail a une conséquence exacte : le global ``__file__`` n'y est pas défini.

Les deux jobs s'en servaient pour mettre leur propre répertoire sur le chemin
d'import — la seule façon d'atteindre `lakebase.py` et `mirror.py`, qui sont
déployés à côté d'eux. La publication échouait donc sur un ``NameError`` avant
d'avoir lu sa première option, sur la ligne censée rendre ses voisins
atteignables.

Ces contrôles lancent les fichiers exactement comme la plateforme le fait.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parent.parent / "jobs"

#: Les fichiers lancés comme tâches, et le voisin dont chacun a besoin.
LAUNCHED = (
    ("publish_campaign_to_delta.py", "lakebase.py"),
    ("sync_erp_mirror.py", "lakebase.py"),
)

#: Reproduit le lancement de la plateforme : le fichier est lu, compilé sous un
#: chemin donné, puis exécuté dans un espace de noms **sans** ``__file__``. Ce
#: qui est rendu, c'est ce que l'exécution a ajouté au chemin d'import.
COMME_SERVERLESS = """
import json, sys
chemin, compile_sous = sys.argv[1], sys.argv[2]
avant = set(sys.path)
with open(chemin, "rb") as f:
    source = f.read()
exec(compile(source, compile_sous, "exec"), {"__name__": "__databricks__"})
print(json.dumps([p for p in sys.path if p not in avant]))
"""


def lancer(
    fichier: str, *, compile_sous: str | None = None
) -> subprocess.CompletedProcess:
    """Exécute un job comme le calcul serverless, dans un processus à part."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            COMME_SERVERLESS,
            str(JOBS / fichier),
            compile_sous or str(JOBS / fichier),
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("fichier,voisin", LAUNCHED)
def test_le_job_demarre_sans_variable_de_fichier(fichier: str, voisin: str) -> None:
    """Le lancement de la plateforme, à l'identique. Aucune exception."""
    rendu = lancer(fichier)

    assert rendu.returncode == 0, rendu.stderr
    assert "NameError" not in rendu.stderr


@pytest.mark.parametrize("fichier,voisin", LAUNCHED)
def test_le_job_atteint_ses_voisins(fichier: str, voisin: str) -> None:
    """Démarrer ne suffit pas : encore faut-il que le voisin soit atteignable.

    Un démarrage qui n'ajoute rien au chemin d'import passerait le contrôle
    précédent et échouerait en production sur le premier ``from lakebase``.
    """
    rendu = lancer(fichier)
    ajouts = json.loads(rendu.stdout)

    assert str(JOBS) in ajouts
    assert (JOBS / voisin).exists()


@pytest.mark.parametrize("fichier,voisin", LAUNCHED)
def test_un_chemin_de_compilation_inutilisable_n_ajoute_rien(
    fichier: str, voisin: str
) -> None:
    """``<string>`` désigne le répertoire courant : on ne l'ajoute pas.

    Faire confiance au chemin seul mettrait le répertoire de travail en tête du
    chemin d'import, ce qui n'est pas ce qu'on demandait — et masquerait
    n'importe quel module du même nom.
    """
    rendu = lancer(fichier, compile_sous="<string>")

    assert rendu.returncode == 0, rendu.stderr
    assert json.loads(rendu.stdout) == []


@pytest.mark.parametrize("fichier,voisin", LAUNCHED)
def test_aucun_job_ne_lit_la_variable_de_fichier(fichier: str, voisin: str) -> None:
    """La garde qui empêche le défaut de revenir.

    ``globals().get("__file__")`` est une chaîne, pas une lecture de global :
    interroger l'absence est permis, la supposer ne l'est pas.
    """
    arbre = ast.parse((JOBS / fichier).read_text(encoding="utf-8"))
    lectures = [
        noeud
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Name)
        and noeud.id == "__file__"
        and isinstance(noeud.ctx, ast.Load)
    ]

    assert lectures == [], [noeud.lineno for noeud in lectures]
