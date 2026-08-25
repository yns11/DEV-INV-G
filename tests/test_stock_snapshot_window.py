"""Le miroir garde plusieurs photos de stock, et l'écran peut donc en choisir une.

Ce qui est arrivé
-----------------
L'import du stock ERP propose « Photo du » : la journée de comptage n'est pas
toujours celle du chargement — le comptage a commencé samedi matin, la reprise
se fait le lundi, et c'est la photo de samedi qui fait foi. Tout était en place
pour ce choix : la liste des dates, la liste déroulante, le paramètre
``snapshot_date`` porté jusqu'à la lecture.

Et la liste n'a jamais offert qu'une date. Le job de synchronisation ne copiait
dans le miroir que ``snapshot_date = max(snapshot_date)`` : l'application ne
pouvait donc proposer que le jour le plus récent, c'est-à-dire précisément celui
que le choix existe pour ne pas subir. Rien n'échouait, et la liste déroulante
d'une seule entrée ressemblait à une source qui n'aurait publié qu'un jour.

Ce que ces contrôles tiennent
-----------------------------
La fenêtre elle-même, le fait qu'elle compte des jours **publiés** et non des
jours du calendrier, et le fait que les **deux** synchronisations la portent —
c'est en n'en corrigeant qu'une que le miroir avait déjà cessé de recevoir les
mouvements de stock.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

JOBS = Path(__file__).resolve().parents[1] / "jobs"
CLI = JOBS / "sync_erp_mirror.py"
NOTEBOOK = JOBS / "sync_erp_mirror_notebook.py"


def load_job() -> Any:
    spec = importlib.util.spec_from_file_location("sync_erp_mirror_window", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


sync = load_job()

TABLE = "cat.silver_erp_ye.stock_snapshot"


class TestTheWindowKeepsSeveralDays:
    def test_it_no_longer_pins_the_single_most_recent_day(self):
        """L'égalité à ``max(...)`` est exactement ce qui bornait la liste à un."""
        clause = sync._recent_snapshots(TABLE, 7)
        assert "snapshot_date = (SELECT max(snapshot_date)" not in clause
        assert clause.startswith("snapshot_date >= ")

    @pytest.mark.parametrize("days", [1, 3, 7, 30])
    def test_it_keeps_the_number_of_days_asked_for(self, days):
        assert f"LIMIT {days}" in sync._recent_snapshots(TABLE, days)

    def test_it_counts_published_days_and_not_calendar_days(self):
        """La source ne publie pas le week-end.

        Une fenêtre en jours de calendrier aurait donné cinq photos une semaine
        et sept la suivante ; c'est une liste de longueur stable qu'on propose.
        """
        clause = sync._recent_snapshots(TABLE, 7)
        assert "DISTINCT snapshot_date" in clause
        assert "ORDER BY d DESC" in clause

    def test_it_reads_the_window_from_the_source_table(self):
        assert f"FROM {TABLE}" in sync._recent_snapshots(TABLE, 7)

    @pytest.mark.parametrize("days", [0, -1])
    def test_a_nonsense_count_still_keeps_one_day(self, days):
        """Zéro viderait le miroir — et le job ne le découvrirait qu'à la fin,
        après avoir lu toute la source, sous la forme d'un « aucune ligne »."""
        assert "LIMIT 1" in sync._recent_snapshots(TABLE, days)


class TestBothSynchronisationsCarryIt:
    """Corriger un seul des deux chemins est le défaut déjà connu ici."""

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_the_window_clause_is_there(self, path):
        source = path.read_text(encoding="utf-8")
        assert "DISTINCT snapshot_date" in source, (
            f"{path.name} ne copie pas de fenêtre de photos : la liste "
            "« Photo du » n'offrira qu'une date."
        )

    @pytest.mark.parametrize("path", [CLI, NOTEBOOK], ids=["cli", "notebook"])
    def test_the_single_day_filter_is_gone(self, path):
        source = path.read_text(encoding="utf-8")
        assert not re.search(
            r"snapshot_date = \(SELECT max\(snapshot_date\)\s*"
            r"(?:\"\s*\n\s*f\")?FROM \{stock_fqn\}",
            source,
        ), f"{path.name} épingle encore la seule photo la plus récente."

    def test_the_cli_takes_the_count_as_an_argument(self):
        """Sept convient à la reprise du lundi ; un inventaire étalé sur deux
        semaines demandera plus, et cela ne doit pas demander un correctif."""
        assert '"--stock-days"' in CLI.read_text(encoding="utf-8")

    def test_the_notebook_takes_it_as_a_widget(self):
        source = NOTEBOOK.read_text(encoding="utf-8")
        assert 'dbutils.widgets.text("stock_days"' in source

    def test_the_widget_is_actually_read_back(self):
        """Un widget déclaré et jamais relu se règle sans effet.

        `conf` énumère les noms qu'il relit : c'est cette liste-là qu'il faut
        regarder, et pas la présence du nom quelque part dans le fichier — la
        déclaration du widget le porte déjà.
        """
        source = NOTEBOOK.read_text(encoding="utf-8")
        block = re.search(r"conf = \{name: [^(]*\((.*?)\)\}", source, re.S)
        assert block, "la lecture des widgets a changé de forme"
        assert '"stock_days"' in block.group(1)

    def test_the_window_is_what_the_notebook_actually_copies(self):
        """Déclarée sans être passée à la lecture, la fenêtre ne servirait à rien."""
        assert "where=stock_where" in NOTEBOOK.read_text(encoding="utf-8")

    def test_the_window_is_what_the_cli_actually_copies(self):
        source = CLI.read_text(encoding="utf-8")
        assert "where=_recent_snapshots(stock_fqn, args.stock_days)" in source
