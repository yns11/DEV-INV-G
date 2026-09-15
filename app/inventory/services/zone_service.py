"""L'administration des zones : ce qu'on va compter, et sous quelle forme.

Ce que ce module a quitté
-------------------------
``generic_service`` réunissait deux vocabulaires. D'un côté **les zones** — les
créer, les renommer, décider du nombre de comptages, des lignes vierges, des
en-têtes imprimés, et les retirer. De l'autre **le contenu des feuilles** — les
lignes, les quantités, la clôture d'une zone par celui qui l'a comptée. Le
premier se règle avant que quiconque tienne un crayon ; le second n'existe que
pendant le comptage, et ne se touche plus après.

Pourquoi celui-là et pas un autre découpage
-------------------------------------------
Parce que c'est la frontière que la campagne elle-même trace. On administre les
zones en préparation, on saisit les feuilles au comptage, et les deux gestes
n'ont jamais lieu le même jour ni par les mêmes personnes. Le collage d'un lot
de quarante zones l'a rendu visible : il n'avait aucune raison de partager un
module avec la saisie d'une quantité relevée en atelier.

Ce que ce module ne fait pas
----------------------------
Il ne lit pas les feuilles et n'écrit aucune quantité. ``list_zones`` est resté
sur :class:`~inventory.services.generic_service.GenericService` pour cette
raison : c'est une lecture d'avancement, pas une administration — elle compte
des lignes saisies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..db import new_id
from ..domain.enums import AuditAction, CampaignStatus, SheetPass, section_of
from ..domain.models import Campaign, Zone
from ..domain.workflow import passes_for
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ServiceContext

#: Combien de zones un seul collage peut créer.
#:
#: Une campagne réelle en compte quarante à soixante ; deux cents laisse la
#: marge d'un site qui découpe plus fin, et arrête le collage accidentel d'un
#: tableur entier avant qu'il ne devienne deux mille zones et quatre mille
#: feuilles à supprimer une par une.
MAX_BULK_ZONES = 200

__all__ = ["ZoneService", "MAX_BULK_ZONES"]


def _first_reason(exc: Exception) -> str:
    """Le premier motif d'un refus de validation, en clair.

    Une erreur Pydantic complète cite le type, l'entrée et une URL : utile pour
    qui écrit du code, illisible pour qui vient de coller quarante lignes et
    veut savoir laquelle est fautive.
    """
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = (errors() or [{}])[0]
        message = str(first.get("msg") or "").removeprefix("Value error, ")
        field = ".".join(str(part) for part in first.get("loc") or ())
        return f"{field} : {message}" if field else message
    return str(exc)


class ZoneService:
    """Créer, régler et retirer les zones d'une campagne."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def create_zone(
        self,
        campaign: Campaign,
        *,
        code: str,
        label: str = "",
        sector: str = "",
        display_order: int = 0,
        passes: int | None = None,
        free_entry: bool = True,
        manager_code: str = "",
        blank_rows: Mapping[str, int] | None = None,
    ) -> Zone:
        """Create a zone and its counting sheets.

        Allowed in both PREPARATION and COUNTING: preparation is precisely when
        one decides what to count, and a physical area nobody had listed is
        routinely discovered on the day of the inventory.

        :param free_entry: this endpoint creates a zone with **no** pre-printed
            article list, which is the definition of a free-entry sheet — the
            counter writes down what they find. Defaulting to ``True`` is what
            keeps the interface from presenting a deliberate blank sheet as an
            unprepared one. Loading a list through the ``count_sheets`` import
            clears the flag.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        existing = {z.code for z in ctx.sheets.list_zones(campaign.id)}
        zone = Zone(
            id=new_id(),
            campaign_id=campaign.id,
            code=code,
            label=label,
            sector=sector,
            display_order=display_order,
            passes=campaign.config.generic_passes if passes is None else passes,
            free_entry=free_entry,
            manager_code=manager_code,
            blank_rows=dict(blank_rows or {}),
        )
        if zone.code in existing:
            raise ConflictError(
                f"Une zone « {zone.code} » existe déjà dans cette campagne.",
                code=zone.code,
            )
        # Une zone sans ses feuilles n'est pas une demi-zone : c'est une zone
        # que rien ne permet de compter, et que l'écran présente pourtant comme
        # prête. Les trois écritures tiennent ou tombent ensemble.
        with ctx.db.transaction() as conn:
            ctx.sheets.create_zone(zone, actor=ctx.actor, conn=conn)
            ctx.sheets.ensure_sheets(
                campaign.id, zone.id, passes_for(zone.passes),
                actor=ctx.actor, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.CREATE,
                entity_type="zone",
                entity_id=zone.id,
                summary=f"Création de la zone {zone.code}",
                after=zone.model_dump(mode="json"),
                conn=conn,
            )
        # A zone is what unlocks the pilotage steps; the counts move with it.
        ctx.forget_progress(campaign.id)
        return zone

    def create_zones(
        self, campaign: Campaign, specs: Sequence[Mapping[str, Any]]
    ) -> list[Zone]:
        """Créer d'un coup toutes les zones d'un bloc collé.

        Une campagne réelle en compte quarante à soixante, chacune avec son
        nombre de lignes par section. Les créer une par une, c'est quarante
        allers-retours dans une fenêtre modale ; le collage depuis le tableur où
        la liste existe déjà est le geste que tout le reste de l'application
        propose, et il manquait ici.

        **Tout ou rien.** Les zones sont validées *avant* la première écriture —
        code manquant, doublon dans le collage, doublon avec une zone existante,
        nombre de lignes hors bornes — et le refus les nomme toutes. Créer les
        trente premières puis s'arrêter sur la trente et unième laisserait un
        état que personne n'a voulu et que rien ne dit comment défaire.

        Une seule transaction, un seul événement d'audit par zone : c'est la
        même écriture que la création à l'unité, faite ensemble.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if not specs:
            raise ValidationError("Aucune zone à créer : le bloc collé est vide.")
        if len(specs) > MAX_BULK_ZONES:
            raise ValidationError(
                f"Trop de zones d'un coup : {len(specs)} pour un maximum de "
                f"{MAX_BULK_ZONES}. Découpez le collage.",
                zones=len(specs),
            )

        existing = {z.code for z in ctx.sheets.list_zones(campaign.id)}
        order = max(
            (z.display_order for z in ctx.sheets.list_zones(campaign.id)), default=0
        )
        zones: list[Zone] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        invalid: list[str] = []
        for index, spec in enumerate(specs, start=1):
            order += 1
            try:
                zone = Zone(
                    id=new_id(),
                    campaign_id=campaign.id,
                    code=str(spec.get("code") or ""),
                    label=str(spec.get("label") or ""),
                    sector=str(spec.get("sector") or ""),
                    display_order=int(spec.get("display_order") or order),
                    passes=(
                        campaign.config.generic_passes
                        if spec.get("passes") in (None, "")
                        else int(spec["passes"])
                    ),
                    free_entry=bool(spec.get("free_entry", True)),
                    manager_code=str(spec.get("manager_code") or ""),
                    blank_rows=dict(spec.get("blank_rows") or {}),
                )
            except (PydanticValidationError, ValueError, TypeError) as exc:
                invalid.append(f"ligne {index} : {_first_reason(exc)}")
                continue
            if zone.code in existing or zone.code in seen:
                duplicates.append(zone.code)
            seen.add(zone.code)
            zones.append(zone)

        if invalid:
            raise ValidationError(
                "Le bloc collé n'est pas exploitable : " + " · ".join(invalid[:10]),
                rows=invalid[:10],
            )
        if duplicates:
            raise ConflictError(
                "Ces codes de zone existent déjà, ou reviennent deux fois dans "
                "le collage : " + ", ".join(sorted(set(duplicates))[:10]),
                codes=sorted(set(duplicates)),
            )

        with ctx.db.transaction() as conn:
            for zone in zones:
                ctx.sheets.create_zone(zone, actor=ctx.actor, conn=conn)
                ctx.sheets.ensure_sheets(
                    campaign.id, zone.id, passes_for(zone.passes),
                    actor=ctx.actor, conn=conn,
                )
                ctx.record(
                    campaign_id=campaign.id,
                    action=AuditAction.CREATE,
                    entity_type="zone",
                    entity_id=zone.id,
                    summary=f"Création de la zone {zone.code} (lot de {len(zones)})",
                    after=zone.model_dump(mode="json"),
                    conn=conn,
                )
        ctx.forget_progress(campaign.id)
        return zones

    def set_blank_rows(
        self, campaign: Campaign, zone_id: str, rows: Mapping[str, int]
    ) -> Zone:
        """Combien de lignes vierges chaque section de cette zone imprime."""
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        zone = zones.get(zone_id)
        if zone is None:
            raise NotFoundError("Zone introuvable dans cette campagne.", zoneId=zone_id)
        try:
            updated = zone.model_copy(update={"blank_rows": {}})
            updated = Zone.model_validate(
                {**updated.model_dump(), "blank_rows": dict(rows or {})}
            )
        except PydanticValidationError as exc:
            raise ValidationError(_first_reason(exc)) from exc

        with ctx.db.transaction() as conn:
            ctx.sheets.set_blank_rows(
                campaign.id, zone_id, updated.blank_rows, actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                entity_id=zone_id,
                summary=f"Lignes vierges de la zone {zone.code}",
                before={"blankRows": zone.blank_rows},
                after={"blankRows": updated.blank_rows},
                conn=conn,
            )
        return updated

    def rename_zone(
        self,
        campaign: Campaign,
        zone_id: str,
        *,
        code: str,
        label: str | None = None,
        sector: str | None = None,
    ) -> Zone:
        """Renommer une zone : son code, son libellé, son secteur.

        Le code d'une zone se décide avant de connaître le terrain, et il se
        révèle faux une fois sur place — « B15 » qui désigne en réalité deux
        aires, un code recopié d'une campagne où l'atelier s'appelait
        autrement. Le seul recours était de supprimer la zone et de la
        recréer, ce qui emporte ses feuilles, donc sa liste d'articles et ses
        quantités.

        **Ce que le renommage ne fait pas** : il ne touche à rien d'autre. Les
        feuilles, les lignes, les comptages et les arbitrages sont rattachés à
        l'identifiant de la zone, jamais à son code — c'est ce qui rend
        l'opération sûre.

        **Ce qu'il faut savoir** : le code est la clé sur laquelle l'import des
        feuilles reconnaît une zone. Recharger ensuite un fichier qui porte
        encore l'ancien code **créera une seconde zone**, au lieu de compléter
        celle-ci. L'écran le dit ; le refuser serait interdire un renommage
        légitime pour un fichier que personne ne rechargera peut-être jamais.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        zone = zones.get(zone_id)
        if zone is None:
            raise NotFoundError("Zone introuvable.", zoneId=zone_id)

        renamed = zone.model_copy(update={
            "code": code,
            "label": zone.label if label is None else label,
            "sector": zone.sector if sector is None else sector,
        })
        # Validé par le modèle, donc normalisé comme à la création : un code
        # saisi en minuscules devient le même code, et deux zones ne peuvent
        # pas se retrouver distinctes par leur seule casse.
        #
        # Le refus du modèle est retraduit : brut, c'est une erreur de contrat
        # Pydantic — « value error, zone code is required » — qui remonterait en
        # 500 à qui appelle le service autrement que par la route, et qui ne dit
        # rien à qui vient d'effacer une case.
        try:
            renamed = Zone.model_validate(renamed.model_dump())
        except PydanticValidationError as error:
            raise ValidationError(
                "Une zone sans code ne se retrouve pas : donnez-lui un nom.",
                code=code,
            ) from error
        taken = {z.code for z in zones.values() if z.id != zone_id}
        if renamed.code in taken:
            raise ConflictError(
                f"Une zone « {renamed.code} » existe déjà dans cette campagne.",
                code=renamed.code,
            )
        if (renamed.code, renamed.label, renamed.sector) == (
            zone.code, zone.label, zone.sector
        ):
            return zone

        with ctx.db.transaction() as conn:
            ctx.sheets.rename_zone(
                campaign.id, zone_id,
                code=renamed.code, label=renamed.label, sector=renamed.sector,
                actor=ctx.actor, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                entity_id=zone_id,
                summary=(
                    f"Zone {zone.code} renommée en {renamed.code}"
                    if renamed.code != zone.code
                    else f"Zone {zone.code} : libellé ou secteur modifié"
                ),
                before={"code": zone.code, "label": zone.label,
                        "sector": zone.sector},
                after={"code": renamed.code, "label": renamed.label,
                       "sector": renamed.sector},
                conn=conn,
            )
        return renamed

    def set_zone_passes(
        self, campaign: Campaign, zone_ids: Sequence[str], passes: int
    ) -> dict[str, Any]:
        """Set how many independent counts a selection of zones requires.

        Dropping to one count **deletes** the second sheet, so the operation is
        refused when that sheet already carries a quantity: bringing a zone back
        to a single count after the fact would erase a real count. The refusal
        names the zones concerned, because "some zone somewhere" is not
        actionable on inventory day.

        Raising back to two recreates the second sheet, empty.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if passes not in (1, 2):
            raise ValidationError(
                "Le nombre de comptages doit être 1 ou 2.", passes=passes
            )
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)
        targets = [z for z in zone_ids if zones[z].passes != passes]
        if not targets:
            return {"updated": 0, "sheetsRemoved": 0, "sheetsCreated": 0}

        removed = created = 0
        if passes == 1:
            counted = ctx.sheets.zones_with_counted_pass(
                campaign.id, targets, SheetPass.PASS_2
            )
            if counted:
                codes = sorted(zones[z].code for z in counted)
                raise ConflictError(
                    "Impossible de ramener à un seul comptage : le comptage n°2 "
                    f"porte déjà des quantités saisies sur {', '.join(codes)}. "
                    "Effacez ces quantités si le second comptage doit être "
                    "abandonné.",
                    zones=codes,
                )

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, targets, actor=ctx.actor, passes=passes, conn=conn
            )
            if passes == 1:
                removed = ctx.sheets.delete_sheets_for_pass(
                    campaign.id, targets, SheetPass.PASS_2, conn=conn
                )
                ctx.arbitrations.delete_arbitrations(campaign.id, targets, conn=conn)
            else:
                for zone_id in targets:
                    created += ctx.sheets.ensure_sheets(
                        campaign.id, zone_id, passes_for(2),
                        actor=ctx.actor, conn=conn,
                    )
                    self._mirror_document(campaign, zone_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) passée(s) à {passes} comptage(s) "
                    f"({removed} feuille(s) supprimée(s), {created} créée(s))."
                ),
                after={
                    "passes": passes,
                    "zones": sorted(zones[z].code for z in targets),
                },
                conn=conn,
            )
        return {"updated": updated, "sheetsRemoved": removed, "sheetsCreated": created}

    def set_zone_negative(
        self, campaign: Campaign, zone_ids: Sequence[str], allowed: bool
    ) -> int:
        """Allow — or forbid again — negative counted quantities on a selection.

        Carried by the zone rather than the sheet: both passes of one area must
        obey the same rule, otherwise the arbitration compares two counts that
        were not allowed the same values.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, list(zone_ids), actor=ctx.actor,
                allow_negative=allowed, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) : quantités négatives "
                    f"{'autorisées' if allowed else 'interdites'}"
                ),
                after={
                    "allowNegative": allowed,
                    "zones": sorted(zones[z].code for z in zone_ids),
                },
                conn=conn,
            )
        return updated

    def set_section_labels(
        self, campaign: Campaign, zone_id: str, labels: dict[str, str]
    ) -> dict[str, str]:
        """Le texte imprimé en tête de chaque section d'une zone.

        Un texte vide **efface** la personnalisation au lieu d'imprimer une
        bannière vide : c'est ce que veut dire un champ qu'on vide, et une
        section sans titre laisserait le compteur sans la règle sous laquelle il
        compte.

        Posé sur la zone et non sur la feuille : les deux passages sont le même
        document imprimé deux fois, et les voir diverger n'aurait aucun sens.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == zone_id), None
        )
        if zone is None:
            raise NotFoundError("Zone introuvable.", zoneId=zone_id)

        kept = {
            str(section_of(code)): text.strip()
            for code, text in labels.items()
            if text.strip()
        }
        ctx.sheets.set_section_labels(
            campaign.id, zone_id, kept, actor=ctx.actor
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="zone",
            entity_id=zone_id,
            summary=(
                f"En-têtes de section de la zone {zone.code} : "
                f"{len(kept)} personnalisé(s)"
            ),
            before={"sectionLabels": zone.section_labels},
            after={"sectionLabels": kept},
        )
        return kept

    def delete_zones(
        self, campaign: Campaign, zone_ids: Sequence[str]
    ) -> dict[str, int]:
        """Retire des zones et leurs feuilles de comptage, une ou tout un lot.

        **Réservé à la préparation**, et c'est plus strict que la matrice de gel
        ne l'exige : les zones restent modifiables en phase de comptage, mais
        leurs feuilles y portent alors des quantités relevées sur le terrain, et
        une feuille supprimée emporte ses lignes — donc un comptage que personne
        ne refera. Préparer du papier est une activité de préparation ; en jeter
        le jour J n'en est pas une.

        **Les feuilles partent avec la zone.** La zone est retirée
        logiquement — son histoire reste au dossier — mais ses feuilles sont
        supprimées pour de bon. Les laisser produirait des feuilles orphelines :
        les listes par zone ne les montreraient plus, la liste à plat de toutes
        les lignes si, et la campagne compterait des articles rattachés à une
        zone qui n'existe plus.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if campaign.status is not CampaignStatus.PREPARATION:
            raise ValidationError(
                "Les zones ne se suppriment qu'en préparation. Depuis le passage "
                "en comptage, leurs feuilles portent des quantités relevées.",
                status=str(campaign.status),
            )

        unique = list(dict.fromkeys(i for i in zone_ids if i))
        if not unique:
            raise ValidationError("Aucune zone transmise.")

        known = {zone.id: zone for zone in ctx.sheets.list_zones(campaign.id)}
        missing = [i for i in unique if i not in known]
        if missing:
            raise ValidationError(
                f"{len(missing)} zone(s) introuvables dans cette campagne, dont "
                f"{missing[0]}.",
                missing=missing[:20],
            )

        doomed = [
            sheet.id
            for sheet in ctx.sheets.list_sheets(campaign.id)
            if sheet.zone_id in known and sheet.zone_id in set(unique)
        ]
        with ctx.db.transaction() as conn:
            sheets = ctx.sheets.delete_sheets(campaign.id, doomed, conn=conn)
            for zone_id in unique:
                ctx.sheets.delete_zone(campaign.id, zone_id, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.DELETE,
                entity_type="zone",
                summary=(
                    f"Suppression de {len(unique)} zone(s) et de leurs "
                    f"{sheets} feuille(s) de comptage"
                ),
                before={"codes": [known[i].code for i in unique][:50]},
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return {"zones": len(unique), "sheets": sheets}
