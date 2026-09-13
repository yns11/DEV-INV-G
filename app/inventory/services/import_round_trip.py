"""L'aller et le retour doivent coïncider.

L'application calcule une quantité — la consolidation GENERIQUE, une correction
saisie —, l'exploitant la porte dans l'ERP, et l'extraction suivante la
rapporte. Si ce qui revient n'est pas ce qui est parti, quelque chose s'est
passé en chemin, et ce quelque chose mérite d'être lu le jour même.

Ce qui l'a fait écrire
----------------------
Une campagne terrain. La consolidation avait produit 1 336,92 kg de résine ;
l'ERP l'a acceptée telle quelle — sa colonne « Compté » porte bien 1 336,92.
Mais entre l'extraction et l'import, une analyse rapide sous Power Query avait
typé la colonne en entier : 1 337 est revenu. Soixante-quatre quantités pesées
ou mesurées ont été arrondies de la même façon, et l'import les a acceptées
sans un mot. L'écart ne s'est vu que des jours plus tard.

Rien dans la chaîne ne pouvait le dire — sauf l'application, qui tenait les
deux chiffres et ne les a jamais comparés.

Ce que le contrôle ne fait pas
------------------------------
Il ne suppose **rien de la cause**. Arrondi en amont, séparateur décimal perdu,
collage partiel, ligne oubliée, correction faite dans l'ERP et pas ici : les
causes ne se connaissent qu'après, et une règle qui n'en couvrirait qu'une
laisserait passer les autres. Il constate que les deux chiffres diffèrent et
les nomme.

Il ne **corrige rien** non plus. La valeur de l'application prime et reste en
place ; c'est à l'exploitant de décider si c'est le retour ou l'aller qui a
tort. Écraser l'une par l'autre déciderait à sa place, et se tromperait la
moitié du temps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from ..domain.models import CountJournalLine
from ..ingest import RowError

__all__ = ["ROUND_TRIP_REPORTED", "round_trip_findings"]

#: Combien de divergences sont **nommées** dans le rapport d'un lot.
#:
#: Le rapport part en JSONB dans ``import_batch`` et se relit à chaque
#: affichage. Quand le fichier tout entier a dérivé — une colonne retypée en
#: amont, un séparateur décimal perdu — ce sont des centaines de lignes qui
#: divergent, et les lire une à une n'apprend rien de plus que les vingt
#: premières.
#:
#: Le **compte**, lui, n'est jamais tronqué : c'est lui qui distingue la poignée
#: d'accidents de la dérive générale, et c'est la première chose à savoir.
ROUND_TRIP_REPORTED = 20


def round_trip_findings(
    held: Mapping[tuple[str, str], Decimal],
    lines: Sequence[CountJournalLine],
    *,
    reported: int = ROUND_TRIP_REPORTED,
) -> tuple[list[RowError], int]:
    """Ce que l'application tenait, comparé à ce que l'export rapporte.

    :param held: quantité calculée par l'application, par (journal, article).
        Seules les lignes qui en portent une sont comparables : ailleurs, l'ERP
        est la seule source et il n'y a pas d'aller à opposer au retour.
    :param lines: les lignes que l'import s'apprête à écrire.
    :returns: les constats à afficher, et leur **nombre total** — qui n'est pas
        la longueur de la liste dès que celle-ci est tronquée.
    """
    divergences = [
        (line, held[(line.journal_id, line.item_number)])
        for line in lines
        if (line.journal_id, line.item_number) in held
        and held[(line.journal_id, line.item_number)] != line.qty_imported
    ]
    findings = [
        RowError(
            # Zéro, et pas un numéro de ligne : ce constat porte sur la
            # campagne, pas sur une ligne du fichier. L'écran sait ne pas
            # afficher « Ligne 0 », qui enverrait chercher ce qui n'existe pas.
            0, "counted_quantity", line.qty_imported,
            f"{line.item_number} : l'application tient {calculee}, l'export en "
            f"rapporte {line.qty_imported}. La valeur de l'application est "
            "conservée — vérifiez ce qui a changé en chemin.",
        )
        for line, calculee in divergences[:reported]
    ]
    return findings, len(divergences)
