"""The campaign assistant: a question in French, an answer from the figures.

This service assembles the material the model is allowed to see, and how much
of it depends on the active profile (see :mod:`inventory.ai.assistant`):

* ``none`` — nothing. The question travels alone.
* ``digest`` — the twenty figures a human would gather before answering: where
  the campaign is, what has been counted, where the money is, what is blocking.
* ``full`` — the whole dossier, ranked lists included, for questions the
  headline figures do not settle.

Whatever the profile, two things hold: the model has no database handle and no
tools, and nothing it says is written anywhere. Only the amount of context
changes — never what the assistant is able to do to the campaign.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..ai.assistant import (
    AssistantProfile,
    Attachment,
    CampaignAssistant,
    profile_for,
)
from ..domain.enums import AuditAction
from ..domain.models import Campaign
from ..domain.workflow import mutability_of
from .analysis_service import AnalysisService
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["AssistantService", "MAX_ATTACHMENTS", "MAX_ATTACHMENT_BYTES"]

#: A question is a question, not a bulk import. Files beyond these limits belong
#: in the import screen, where they get validated line by line.
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024

#: How many rows of each ranked list travel with the question. Enough to answer
#: "where is the money?", short enough that the digest stays readable.
_TOP_VARIANCES = 15
_TOP_ZONES = 40

#: In the extended profile the ranked lists stop being a sample.
_FULL_VARIANCES = 300
_FULL_ROWS = 300


class AssistantService:
    """Answers questions about one campaign."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def ask(
        self,
        campaign: Campaign,
        *,
        question: str,
        history: Sequence[dict[str, str]] = (),
        attachments: Sequence[Attachment] = (),
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Answer *question* about *campaign*, in the requested profile.

        Read-only by construction whatever the profile: nothing here writes a
        quantity, a cause or a status. The only trace left is the audit line
        saying a question was asked, and under which framing — an answer that
        turns out to be wrong needs to be findable, *and* attributable to the
        configuration that produced it.
        """
        if len(attachments) > MAX_ATTACHMENTS:
            from ..errors import ValidationError

            raise ValidationError(
                f"{MAX_ATTACHMENTS} pièces jointes au maximum par question."
            )

        active = profile_for(profile)
        context = self.context(campaign, profile=active)
        answer = CampaignAssistant().ask(
            question=question,
            context=context,
            history=history,
            attachments=attachments,
            profile=active,
        )
        self.ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.EXPORT,
            entity_type="assistant",
            summary=(
                f"Question à l'assistant [{active.key}] : {question[:120]}"
            ),
        )
        return answer.as_dict()

    # ------------------------------------------------------------- the digest

    def context(
        self, campaign: Campaign, *, profile: AssistantProfile | None = None
    ) -> dict[str, Any]:
        """The campaign material handed to the model, sized by the profile.

        Exposed as its own method so the screen can show exactly what the model
        sees. An assistant whose inputs are inspectable is one people can
        calibrate their trust in; a black box is one they either over-trust or
        stop using.

        ``none`` ships nothing — the open profile, where the question travels
        alone. ``digest`` ships the twenty figures that answer most questions.
        ``full`` adds the long tail: every variance, every zone, the cause
        breakdown and the transfer analysis.
        """
        active = profile or profile_for(None)
        if active.context == "none":
            return {}

        ctx = self.ctx
        analysis = AnalysisService(ctx)
        has_book_stock = campaign.book_stock_frozen_at is not None
        full = active.context == "full"

        digest: dict[str, Any] = {
            "campagne": {
                "code": campaign.code,
                "libellé": campaign.label,
                "phase": str(campaign.status),
                "dateDeComptage": campaign.count_date,
                "stockErpGeléLe": campaign.book_stock_frozen_at,
                "dupliquéeDe": campaign.cloned_from_code,
                "modifiableActuellement": mutability_of(campaign.status).as_dict(),
            },
            "avancement": self._progress(campaign),
            "zones": self._zones(campaign, full=full),
            "gestionnaires": self._managers(campaign),
            "référentiel": self._referential(campaign),
            "journauxDeComptage": self._journals(campaign),
            "provenanceEtHistorique": self._history(campaign),
            "seuilsDeMatérialité": [
                {
                    "typeArticle": str(t.item_type),
                    "valeurAbsolueEur": float(t.value_abs_eur),
                    "écartRelatifQté": float(t.qty_relative),
                }
                for t in campaign.thresholds
            ],
        }

        if not has_book_stock:
            # Saying *why* a block is missing is what stops the model from
            # answering "je ne sais pas" to a question the phase simply has no
            # answer to yet.
            digest["écarts"] = {
                "disponible": False,
                "raison": (
                    "Le stock ERP n'est pas encore gelé : aucun écart n'est "
                    "calculable à ce stade de la campagne."
                ),
            }
            return digest

        kpis = analysis.kpis(campaign)
        # Ratios are ``None`` when their base is zero, deliberately: "not
        # computable" and "zero per cent" are different answers, and coercing
        # the first into the second is how a model states a 0 % reliability on a
        # campaign nobody has counted yet.
        digest["indicateurs"] = {
            "stockErpValeurEur": float(kpis.book_value),
            "stockErpQté": float(kpis.book_qty),
            "écartNetValeurEur": float(kpis.net_variance_value),
            "écartBrutValeurEur": float(kpis.gross_variance_value),
            "fiabilitéBrute": _num(kpis.gross_reliability_value),
            "fiabilitéNette": _num(kpis.net_reliability_value),
            "ira": _num(kpis.ira),
            "lignesAnalysées": kpis.line_count,
            "lignesAuDelàDesSeuils": kpis.material_line_count,
            "comptéesSansStockErp": kpis.counted_only_count,
            "jamaisComptées": kpis.book_only_count,
            "écartRésiduelEur": float(kpis.residual_value),
        }
        digest["plusGrosÉcarts"] = [
            {
                "article": v["itemNumber"],
                "désignation": v["name"],
                "type": v["itemType"],
                "écartQté": v["varianceQty"],
                "écartValeurEur": v["varianceValue"],
                "cause": v["causeCode"],
                "commentaire": v["comment"],
            }
            for v in analysis.top_variances(
                campaign, limit=_FULL_VARIANCES if full else _TOP_VARIANCES
            )
        ]
        digest["contrôles"] = analysis.controls(campaign)

        if full:
            # The long tail: what a human would go and look up before answering
            # a question the headline figures do not settle.
            digest["écartsParEntrepôt"] = analysis.aggregate(
                campaign, "warehouse", limit=200
            )
            digest["écartsParEmplacement"] = analysis.aggregate(
                campaign, "location", limit=200
            )
            digest["répartitionDesCauses"] = analysis.cause_split(campaign)
            digest["perteOuTransfert"] = analysis.transfers(campaign)
            digest["ajustementsPostés"] = self._adjustments(campaign)
            digest["wip"] = self._wip(campaign)
            digest["comparaisonCampagnePrécédente"] = self._comparison(campaign)

        return digest

    # ------------------------------------------------------------------ parts

    def _progress(self, campaign: Campaign) -> dict[str, Any]:
        ctx = self.ctx
        journals = ctx.journals.progress(campaign.id)
        return {
            "journaux": {
                "total": journals.get("total", 0),
                "terminés": journals.get("complete", 0),
                "enCours": journals.get("running", 0),
                "enAttente": journals.get("pending", 0),
            },
            "articlesAuRéférentiel": len(ctx.referentials.list_items(campaign.id)),
            "lignesDeStockErp": len(ctx.book_stock.list(campaign.id)),
        }

    def _referential(self, campaign: Campaign) -> dict[str, Any]:
        """The shape of the referential, not its contents.

        Thousands of article rows would drown everything else and answer no
        question anybody actually asks. What is useful is the distribution — how
        many articles per type, per programme, per category — because that is
        what a question like "sommes-nous exposés surtout sur le M3 ?" needs.
        """
        ctx = self.ctx
        items = ctx.referentials.list_items(campaign.id)
        links = ctx.referentials.list_bom_links(campaign.id)
        return {
            "articles": len(items),
            "parType": _tally([str(i.item_type) for i in items]),
            "parProgramme": _tally([i.program or "—" for i in items]),
            "parCatégorie": _tally([i.category or "—" for i in items], limit=25),
            "parProvenance": _tally([str(i.source) for i in items]),
            "articlesSansPrixStandard": sum(1 for i in items if not i.std_price),
            "articlesExclus": sum(1 for i in items if i.exclusions),
            "nomenclatures": {
                "liens": len(links),
                "assemblages": len({l.parent_item for l in links}),
                "composants": len({l.child_item for l in links}),
            },
        }

    def _journals(self, campaign: Campaign) -> list[dict[str, Any]]:
        """The counting journals, one line each — the operational state of the day."""
        ctx = self.ctx
        lines = ctx.journals.lines_by_journal(campaign.id)
        return [
            {
                "entrepôt": j.warehouse_id,
                "emplacement": j.location_id,
                "type": str(j.kind),
                "statut": str(j.status),
                "numéroErp": j.journal_number,
                "lignes": len(lines.get(j.id, [])),
                "quantitéComptée": float(sum(l.qty for l in lines.get(j.id, []))),
                "postéLe": j.posted_at,
            }
            for j in ctx.journals.list(campaign.id)
        ]

    def _adjustments(self, campaign: Campaign) -> list[dict[str, Any]]:
        return [
            {
                "article": a.item_number,
                "entrepôt": a.warehouse_id,
                "emplacement": a.location_id,
                "nature": str(a.kind),
                "quantité": float(a.qty),
                "valeurEur": float(a.value),
                "date": a.physical_date,
                "cause": a.reason_code,
                "commentaire": a.comment,
            }
            for a in self.ctx.adjustments.list(campaign.id, limit=_FULL_ROWS)
        ]

    def _wip(self, campaign: Campaign) -> list[dict[str, Any]]:
        """WIP exploded through the bill of materials, as the consolidation left it."""
        return [
            {
                "zone": r.get("zone_code"),
                "assemblage": r.get("parent_item"),
                "quantitéAssemblage": _num(r.get("parent_qty")),
                "composant": r.get("child_item"),
                "quantitéComposant": _num(r.get("child_qty")),
                "profondeur": r.get("depth"),
            }
            for r in self.ctx.consolidation.wip_breakdown(campaign.id)[:_FULL_ROWS]
        ]

    def _comparison(self, campaign: Campaign) -> dict[str, Any] | None:
        """The previous campaign, when this one was duplicated from it.

        A variance is far easier to judge against the last count than in the
        absolute, and the link is already recorded — not offering it would leave
        the model to answer "is that a lot?" with nothing to compare to.
        """
        if not campaign.cloned_from_code:
            return None
        previous = next(
            (
                c for c in self.ctx.campaigns.list(limit=200)
                if c.code == campaign.cloned_from_code
            ),
            None,
        )
        if previous is None:
            return None
        try:
            from .analysis_service import AnalysisService

            return AnalysisService(self.ctx).compare(campaign, previous.id)
        except Exception as exc:  # a missing comparison must not sink the answer
            log.warning("Comparaison de campagne indisponible : %s", exc)
            return None

    def _history(self, campaign: Campaign) -> dict[str, Any]:
        """Where the campaign's data came from, and what has been done to it."""
        ctx = self.ctx
        return {
            "imports": [
                {
                    "grille": b.get("target"),
                    "origine": b.get("filename"),
                    "lignesAcceptées": b.get("rows_accepted"),
                    "lignesRejetées": b.get("rows_rejected"),
                    "par": b.get("imported_by"),
                    "le": b.get("imported_at"),
                }
                for b in ctx.imports.list(campaign.id, limit=40)
            ],
            "dernièresActions": [
                {
                    "action": str(e.action),
                    "objet": e.entity_type,
                    "résumé": e.summary,
                    "par": e.actor,
                    "le": e.at,
                }
                for e in ctx.audit.list(campaign.id, limit=60)
            ],
        }

    def _zones(self, campaign: Campaign, *, full: bool) -> list[dict[str, Any]]:
        from .generic_service import GenericService

        zones = GenericService(self.ctx).list_zones(campaign)
        return [
            {
                "zone": z["code"],
                "libellé": z.get("label"),
                "secteur": z.get("sector"),
                "statut": z["status"],
                "comptagesPrévus": z.get("passes"),
                "saisieLibre": z.get("free_entry"),
                "gestionnaire": z.get("manager_code"),
                "arbitragesEnAttente": z.get("pendingArbitrations", 0),
                "feuilles": [
                    {
                        "comptage": s.get("pass_no"),
                        "lignes": s.get("lineCount", 0),
                        "lignesComptées": s.get("countedLines", 0),
                        "statut": s.get("status"),
                    }
                    for s in z.get("sheets", [])
                ],
            }
            for z in (zones if full else zones[:_TOP_ZONES])
        ]

    def _managers(self, campaign: Campaign) -> list[dict[str, Any]]:
        from .manager_service import ManagerService

        overview = ManagerService(self.ctx).overview(campaign)
        return [
            {
                "code": m.get("code"),
                "libellé": m.get("label"),
                "zones": m.get("zoneCount", 0),
                "journaux": m.get("journalCount", 0),
            }
            for m in overview.get("managers", [])
        ]


def _num(value: Any) -> float | None:
    """A ratio the domain left undefined stays undefined."""
    return None if value is None else float(value)


def _tally(values: Sequence[str], *, limit: int = 0) -> dict[str, int]:
    """Count occurrences, largest first — a distribution beats a raw list."""
    from collections import Counter

    counts = Counter(values).most_common(limit or None)
    return dict(counts)
