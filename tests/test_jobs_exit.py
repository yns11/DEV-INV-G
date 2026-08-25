"""Comment un job se termine, et ce que la plateforme en comprend.

Deux défauts, symétriques, sur la dernière ligne de chaque fichier.

**La publication réussie était rapportée en échec.** Le calcul serverless
exécute le fichier dans un espace de noms ipykernel : un ``SystemExit(0)`` n'y
est pas une sortie de processus, c'est une exception que le noyau remonte. La
tâche passait donc au rouge après une publication *complète* — dix tables
écrites, manifeste posé, ``published_at`` inscrit dans Lakebase — avec pour
seule trace « SystemExit: 0 » (run 867703449816183). Un exploitant qui voit
rouge republie ; croire l'archive absente alors qu'elle est là est pire, car
c'est la clôture qui la consulte.

**La synchronisation du miroir, elle, n'avait aucun point d'entrée.** Le fichier
ne faisait que *définir* ``main``. Lancé comme ``spark_python_task``, il se
terminait aussitôt : tâche verte, miroir inchangé, et l'application lisant un
référentiel périmé en croyant l'avoir rafraîchi. Le silence complet.

Les deux jobs sortent maintenant de la même façon, et par le même code.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

JOBS = Path(__file__).resolve().parents[1] / "jobs"
LANCES = ("publish_campaign_to_delta.py", "sync_erp_mirror.py")


def load(nom: str) -> Any:
    spec = importlib.util.spec_from_file_location(nom.removesuffix(".py"), JOBS / nom)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def entry_point(nom: str) -> ast.If | None:
    """Le bloc ``if __name__ == "__main__"`` du fichier, s'il en a un."""
    arbre = ast.parse((JOBS / nom).read_text(encoding="utf-8"))
    for noeud in arbre.body:
        if isinstance(noeud, ast.If) and "__name__" in ast.unparse(noeud.test):
            return noeud
    return None


@pytest.mark.parametrize("nom", LANCES)
class TestLeavingWithoutLying:
    def test_une_reussite_ne_leve_rien(self, nom: str):
        """Zéro est une réussite. La lever la ferait passer pour une panne."""
        load(nom)._exit(0)

    @pytest.mark.parametrize("code", [1, 2, 3])
    def test_un_echec_leve_et_porte_son_code(self, nom: str, code: int):
        """Les trois refus du job — code absent, campagne absente, tables absentes."""
        with pytest.raises(SystemExit) as sortie:
            load(nom)._exit(code)

        assert sortie.value.code == code


@pytest.mark.parametrize("nom", LANCES)
class TestEveryJobIsActuallyRun:
    def test_le_fichier_a_un_point_d_entree(self, nom: str):
        """Sans lui, le job définit `main` et s'arrête là — en vert.

        C'est ce que faisait la synchronisation du miroir : aucune ligne
        copiée, aucune erreur, une tâche réussie.
        """
        assert entry_point(nom) is not None, "aucun if __name__ == '__main__'"

    def test_le_point_d_entree_appelle_main(self, nom: str):
        bloc = entry_point(nom)
        appels = {
            noeud.func.id
            for noeud in ast.walk(bloc)
            if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
        }

        assert "main" in appels

    def test_la_sortie_passe_par_la_fonction_qui_ne_ment_pas(self, nom: str):
        """``raise SystemExit(main())`` remonte un zéro comme une exception."""
        bloc = entry_point(nom)
        rendu = ast.unparse(bloc)

        assert "_exit(main())" in rendu
        assert "raise SystemExit(main())" not in rendu
