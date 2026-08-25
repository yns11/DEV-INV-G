"""Le fichier ``.env`` est cherché là où le démarrage rapide le place.

``env_file=".env"`` se résout contre le répertoire **courant** du processus.
L'application, elle, ne démarre jamais depuis la racine du dépôt : ``make run``
fait ``cd app && python main.py``, et ``app.yaml`` lance la même commande depuis
la charge utile déployée. Le fichier que le README fait écrire à la racine
n'était donc lu par personne, et l'application démarrait dégradée — sans
Lakebase, avec pour seule trace un ``lakebaseConfigured: false`` dans
``/api/health``, jamais une erreur.

Ces contrôles fixent la propriété qui manquait : l'emplacement du fichier
dépend du dépôt, pas de l'endroit d'où on lance.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from inventory.config import ENV_FILES, Settings

ROOT = Path(__file__).resolve().parent.parent


def test_le_premier_fichier_est_celui_de_la_racine() -> None:
    """La racine du dépôt, déduite du module, pas du répertoire courant."""
    assert ENV_FILES[0] == ROOT / ".env"


def test_le_chemin_de_la_racine_est_absolu() -> None:
    """Un chemin relatif se résoudrait contre le répertoire courant.

    C'est exactement le défaut corrigé : le test le dit sur le chemin
    lui-même, sans dépendre d'un fichier présent.
    """
    assert ENV_FILES[0].is_absolute()


def test_le_repertoire_courant_reste_lu_et_l_emporte() -> None:
    """Le fichier trouvé jusqu'ici continue de l'être, et prime.

    ``pydantic-settings`` donne la priorité au **dernier** fichier de la
    liste : un ``app/.env`` posé par un développeur garde le dernier mot sur
    celui de la racine.
    """
    assert ENV_FILES[-1] == Path(".env")
    assert len(ENV_FILES) == 2


def test_les_reglages_lisent_bien_cette_liste() -> None:
    """Calculer la liste ne suffit pas : encore faut-il la brancher."""
    assert Settings.model_config["env_file"] == ENV_FILES


def test_la_liste_ne_depend_pas_du_repertoire_de_lancement(tmp_path: Path) -> None:
    """La même racine, vue depuis ``app/`` et depuis un répertoire tiers.

    Le contrôle passe par un sous-processus parce que c'est la seule façon de
    changer réellement le répertoire de travail à l'import du module.
    """
    programme = "from inventory.config import ENV_FILES; print(ENV_FILES[0])"
    vus = set()
    for depuis in (ROOT / "app", tmp_path):
        rendu = subprocess.run(
            [sys.executable, "-c", programme],
            cwd=depuis,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "app")},
        )
        assert rendu.returncode == 0, rendu.stderr
        vus.add(rendu.stdout.strip())

    assert vus == {str(ROOT / ".env")}


def test_oublier_le_postgres_ambiant_oublie_aussi_le_fichier(monkeypatch) -> None:
    """Un ``.env`` à la racine est un environnement ambiant comme un autre.

    Les contrôles qui décrivent un conteneur *sans* base vident ``PGHOST`` et
    ses voisines. Depuis que le fichier de la racine est lu, le vider ne suffit
    plus : il les y remet. Le fichier doit être écarté avec elles.
    """
    from conftest import forget_ambient_postgres

    forget_ambient_postgres(monkeypatch)

    assert Settings.model_config["env_file"] is None


def test_le_fichier_est_rendu_apres_coup(monkeypatch) -> None:
    """L'oubli dure le temps du contrôle, pas celui de la suite."""
    from conftest import forget_ambient_postgres

    with monkeypatch.context() as le_temps_du_controle:
        forget_ambient_postgres(le_temps_du_controle)

    assert Settings.model_config["env_file"] == ENV_FILES


def test_un_chemin_absolu_est_bien_lu_depuis_ailleurs(tmp_path, monkeypatch) -> None:
    """La propriété sur laquelle le correctif repose, vérifiée et non supposée.

    Le fichier est ailleurs, le répertoire courant est ailleurs encore, et la
    valeur arrive quand même.
    """
    fichier = tmp_path / "ailleurs.env"
    fichier.write_text("INV_LOG_LEVEL=CRITICAL\n", encoding="utf-8")

    class Reglages(BaseSettings):
        model_config = SettingsConfigDict(env_file=(fichier,), extra="ignore")

        log_level: str = Field(default="INFO", alias="INV_LOG_LEVEL")

    autre = tmp_path / "courant"
    autre.mkdir()
    monkeypatch.chdir(autre)
    monkeypatch.delenv("INV_LOG_LEVEL", raising=False)

    assert Reglages().log_level == "CRITICAL"
