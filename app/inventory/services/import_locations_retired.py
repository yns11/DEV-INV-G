"""Ce que devient un emplacement absent du nouveau stock ERP.

Un chargement de stock ERP **remplace** la photographie. Les emplacements que la
nouvelle ne liste plus ne disparaissent pas pour autant : certains sont des
restes qu'on nettoie, d'autres portent déjà du travail qu'on ne jette pas sur un
rechargement. Trancher entre les deux est une question à soi, et elle vivait au
milieu de l'importeur du stock — qui a bien assez à faire.

La règle, en une phrase : **un journal qu'on a ouvert est du travail**, et son
emplacement reste ; un journal que personne n'a touché est un reste, et il part
avec le sien.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.enums import LocationStatus
from ..domain.models import Campaign, LocationKey
from ..ingest import RowError
from .context import ServiceContext
from .import_batches import ImportOutcome

__all__ = ["retire_stale_locations"]


def retire_stale_locations(
    ctx: ServiceContext,
    campaign: Campaign,
    stale: Sequence[LocationKey],
    *,
    outcome: ImportOutcome,
    conn: Any,
) -> tuple[int, set[LocationKey]]:
    """Close the locations a new ERP snapshot no longer knows about.

    Returns how many journals were removed, and the locations kept back.

    A journal nobody has opened is a leftover and goes with its location. A
    journal that carries a line, or that somebody has already posted, is
    *work*: reloading the snapshot is not a decision to throw it away. Those
    locations stay active and the import says so — an emplacement counted
    under a snapshot that no longer lists it is exactly the sort of thing
    that has to be looked at, not cleaned up in silence.
    """
    if not stale:
        return 0, set()

    untouched = ctx.journals.untouched_journal_keys(campaign.id, stale, conn=conn)
    existing_journals = ctx.journals.journal_keys(campaign.id, stale, conn=conn)
    kept = {
        k for k in stale
        if (k.warehouse_id, k.location_id) in existing_journals - untouched
    }
    # GENERIQUE ne porte pas de ligne de journal : son comptage vit dans les
    # feuilles. Le juger sur ses lignes de journal le déclarerait vierge
    # alors qu'une zone entière y a été comptée, et le rechargement d'un
    # snapshot emporterait tout ce travail sans le dire.
    generic = campaign.config.generic_key
    if generic in stale and ctx.sheets.count_counted_lines(campaign.id, conn=conn):
        kept.add(generic)
    removable = [
        k for k in stale
        if (k.warehouse_id, k.location_id) in untouched and k not in kept
    ]

    removed = ctx.journals.delete_journals_for_locations(
        campaign.id, removable, conn=conn
    )
    # L'emplacement suit son journal : le désactiver alors qu'un comptage y
    # est encore ouvert le ferait disparaître des écrans où ce comptage doit
    # rester visible.
    closing = [k for k in stale if k not in kept]
    if closing:
        ctx.referentials.set_location_status(
            campaign.id, closing, LocationStatus.DISABLED,
            actor=ctx.actor, conn=conn,
        )

    outcome.details["locationsRetired"] = len(closing)
    outcome.details["journalsRemoved"] = removed
    if kept:
        outcome.details["locationsKept"] = sorted(
            f"{k.warehouse_id} / {k.location_id}" for k in kept
        )[:50]
        outcome.warnings.append(
            RowError(
                line=0,
                column="",
                value="",
                message=(
                    f"{len(kept)} emplacement(s) absents du nouveau stock ERP "
                    "portent déjà un comptage : leur journal est conservé. "
                    "Vérifiez-les avant la clôture."
                ),
            )
        )
    return removed, kept

