"""D'où viennent les données, et si elles sont déjà là.

Un import ne se résume pas à écrire des lignes. Il faut pouvoir répondre, six
mois plus tard, à trois questions que personne ne pose avant d'en avoir besoin :

**D'où vient ce chiffre ?** Un fichier a un nom, une lecture ERP a une table et
une date de photo. Laisser la colonne vide pour les lectures ERP rendrait la
moitié de l'historique illisible — ce qui est le seul métier de l'historique.

**Est-ce qu'on l'a déjà chargé ?** Recharger deux fois le même fichier double
les quantités sans rien signaler. L'empreinte du contenu le dit avant l'écriture.

**Peut-on encore le lire ?** Le fichier d'origine est archivé dans le volume
Unity Catalog : c'est ce qui permet de rejouer un import contesté au lieu de
discuter de mémoire.

Extrait de ``ImportService`` : la provenance n'est ni de la lecture ni de
l'écriture. Elle accompagne les six importeurs sans appartenir à aucun, et
c'est la seule partie du service qui parle de volume, d'empreinte et
d'historique.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import (
    DataSource,
)
from ..domain.imports import refuse_partial_write
from ..domain.models import (
    Campaign,
)
from ..errors import ValidationError
from ..ingest import RowError
from .context import ServiceContext


@dataclass(slots=True)
class ImportOutcome:
    """Result of one import, shaped for direct display."""

    target: str
    rows_received: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    errors: list[RowError] = field(default_factory=list)
    warnings: list[RowError] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    batch_id: str | None = None
    #: Chemin du fichier archivé dans le volume, quand il a pu l'être. ``None``
    #: dit « pas de pièce » : collage, lecture ERP, ou archivage indisponible.
    storage_path: str | None = None
    #: Free-form facts the specific import wants to surface (journals created,
    #: locations discovered, …).
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rows_rejected == 0 and not self.missing_columns

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "rowsReceived": self.rows_received,
            "rowsAccepted": self.rows_accepted,
            "rowsRejected": self.rows_rejected,
            "ok": self.ok,
            "errors": [e.as_dict() for e in self.errors[:200]],
            "warnings": [w.as_dict() for w in self.warnings[:200]],
            "truncatedErrors": max(0, len(self.errors) - 200),
            "missingColumns": self.missing_columns,
            "unknownColumns": self.unknown_columns,
            "duplicateKeys": self.duplicate_keys[:50],
            "batchId": self.batch_id,
            "archived": self.storage_path is not None,
            "details": self.details,
        }


class ImportBatches:
    """La provenance et l'idempotence des imports."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def origin_of(self, target: str, kwargs: dict[str, Any]) -> str:
        """What the import history should name as the origin of a batch.

        A file has a filename; an ERP read has a table. Leaving the column blank
        for ERP loads would make half the history unreadable six months later,
        which is the one job that history has.
        """
        if kwargs.get("mode") != "erp":
            return kwargs.get("filename", "")
        settings = self.ctx.settings
        table = {
            "items": settings.erp_items_fqn,
            "book_stock": settings.erp_stock_fqn,
        }.get(target, settings.erp_bom_fqn)
        # Pour le stock, la photo chargée compte plus que la table : deux
        # campagnes lisant la même table à deux jours d'écart ne comparent pas
        # leur comptage au même état du système.
        day = kwargs.get("snapshot_date")
        if target == "book_stock" and day is not None:
            table = f"{table} au {day.isoformat()}"
        if settings.erp_source != "mirror":
            return table
        # Naming the ERP table alone would claim a live read that did not
        # happen. Which copy was loaded, and how old it was, is the whole
        # question six months later.
        from ..ingest.erp import mirror_state

        synced = (mirror_state().get(target) or {}).get("syncedAt")
        return f"{table} (miroir du {synced[:10]})" if synced else f"{table} (miroir)"

    def archive(
        self, campaign: Campaign, target: str, kwargs: dict[str, Any]
    ) -> str | None:
        """Dépose le fichier chargé dans le volume, et renvoie son chemin.

        Appelée **avant** d'ouvrir la transaction : le dépôt part sur le réseau,
        et tenir une transaction ouverte pendant un aller-retour vers le volume
        garderait une connexion du pool immobilisée pour une écriture qui ne la
        concerne pas.

        Seuls les fichiers sont archivés. Un collage n'a pas d'original à
        conserver — le texte collé est déjà dans les lignes chargées — et une
        lecture ERP se rejoue par sa requête, que l'historique nomme déjà.
        """
        payload = kwargs.get("payload")
        if not isinstance(payload, bytes):
            return None
        archived = self.ctx.evidence.put(
            payload,
            campaign_code=campaign.code,
            kind=target,
            filename=kwargs.get("filename") or f"{target}.bin",
        )
        return archived.path if archived else None

    def refuse_if_partial(
        self,
        outcome: ImportOutcome,
        *,
        accepted: int,
        allow_partial: bool,
        what: str,
    ) -> None:
        """Refuse un remplacement amputé, sauf dérogation explicite.

        Appelée **avant** d'ouvrir la transaction : le refus n'a rien à défaire,
        et une transaction ouverte pour être immédiatement annulée immobilise
        une connexion du pool pour rien.

        La dérogation, quand elle est prise, est écrite dans le rapport du lot :
        « 3 997 lignes chargées » ne veut pas dire la même chose selon qu'il y
        en avait 3 997 ou 4 000, et six mois plus tard c'est cette ligne
        d'historique qui répond.
        """
        refusal = refuse_partial_write(
            wholesale=True,
            rejected=outcome.rows_rejected,
            accepted=accepted,
            allow_partial=allow_partial,
            what=what,
            reasons=tuple(e.message for e in outcome.errors),
        )
        if refusal is not None:
            raise ValidationError(
                refusal.message,
                rejected=refusal.rejected,
                accepted=refusal.accepted,
                errors=[e.as_dict() for e in outcome.errors[:50]],
            )
        if allow_partial and outcome.rows_rejected:
            outcome.details["partialAccepted"] = True
            outcome.details["partialRejected"] = outcome.rows_rejected

    def record_batch(
        self,
        campaign_id: str,
        target: str,
        outcome: ImportOutcome,
        *,
        conn: Any = None,
        **kwargs: Any,
    ) -> str:
        """Persist the provenance of one import.

        Call this **after** ``outcome.rows_accepted`` is set: the batch row is
        the permanent record of what a file actually loaded, and a zero there
        would make the import history useless.
        """
        batch_id = self.ctx.imports.create(
            campaign_id=campaign_id,
            target=target,
            filename=self.origin_of(target, kwargs),
            content_hash=_hash_of(kwargs),
            storage_path=outcome.storage_path,
            rows_received=outcome.rows_received,
            rows_accepted=outcome.rows_accepted,
            rows_rejected=outcome.rows_rejected,
            report=outcome.as_dict(),
            imported_by=self.ctx.actor,
            conn=conn,
        )
        # The counts the sequencing guard reads have just changed: a request
        # that loads the referential and then creates the sheets must not be
        # judged on the counts taken before the load.
        self.ctx.forget_progress(campaign_id)
        return batch_id

    def check_duplicate(
        self, campaign_id: str, target: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Warn when the exact same payload was already imported."""
        digest = _hash_of(kwargs)
        if not digest:
            return None
        return self.ctx.imports.find_duplicate(campaign_id, target, digest)


def _source_of(mode: str) -> DataSource:
    """Where a row came from, kept on every article for the rest of the campaign.

    Worth distinguishing: an article read from the ERP and one typed by hand
    carry different confidence, and the analysis screen shows the provenance.
    """
    match mode:
        case "erp":
            return DataSource.ERP_IMPORT
        case "paste" | "rows":
            return DataSource.MANUAL
        case _:
            return DataSource.FILE_IMPORT


def _hash_of(kwargs: dict[str, Any]) -> str:
    payload = kwargs.get("payload")
    if isinstance(payload, bytes):
        return hashlib.sha256(payload).hexdigest()
    text = kwargs.get("text")
    if isinstance(text, str):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ""


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    from decimal import Decimal

    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, (_dt.date, _dt.datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
