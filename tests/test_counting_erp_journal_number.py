"""De quel journal ERP vient le comptage d'un emplacement.

C'est la question du suivi d'avancement le jour J — « quels emplacements ont
été comptés, et par quel document ? » — et l'application avait déjà la place
pour y répondre : une colonne « N° ERP » dans la grille des journaux, la même
dans l'export Excel, le même champ dans le contexte de l'assistant.

Les trois lisaient ``count_journal.journal_number``, que **rien n'écrit
jamais** : un journal de comptage naît d'un emplacement, pas d'un document, et
le numéro arrive plus tard, avec les lignes. Les trois affichaient donc du vide,
et aucun contrôle ne s'en plaignait — une colonne vide est une colonne valide.

Ces contrôles portent sur la dérivation, une seule pour les trois lecteurs.
"""

from __future__ import annotations

from decimal import Decimal

from inventory.domain.models import CountJournalLine, erp_journal_numbers


def _line(number: str, item: str = "MASS-1") -> CountJournalLine:
    return CountJournalLine(
        id="l", journal_id="j", campaign_id="c", item_number=item,
        qty_imported=Decimal(1), erp_journal_number=number,
    )


class TestReadingTheNumberFromTheLines:
    def test_one_journal_gives_one_number(self):
        assert erp_journal_numbers([_line("NPEM-521215")]) == ["NPEM-521215"]

    def test_the_same_number_is_not_repeated_per_line(self):
        """Un emplacement porte des centaines de lignes du même journal."""
        assert erp_journal_numbers(
            [_line("NPEM-521215", "A"), _line("NPEM-521215", "B")]
        ) == ["NPEM-521215"]

    def test_two_journals_are_both_named(self):
        """N'en garder qu'un cacherait que deux documents ont alimenté l'emplacement."""
        assert erp_journal_numbers(
            [_line("NPEM-522821"), _line("NPEM-521215")]
        ) == ["NPEM-521215", "NPEM-522821"]

    def test_a_line_without_a_number_adds_nothing(self):
        """Une quantité saisie à la main ne vient d'aucun document ERP."""
        assert erp_journal_numbers([_line(""), _line("NPEM-521215")]) == [
            "NPEM-521215"
        ]

    def test_no_line_means_no_number_not_an_empty_string(self):
        """Un emplacement pas encore compté n'a pas de numéro : la liste est vide."""
        assert erp_journal_numbers([]) == []
