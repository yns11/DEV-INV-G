"""Qui peut écrire sur une campagne, et qui ne fait que la lire.

L'application n'avait qu'une barrière devant une écriture : la **phase**. Ce
qu'un statut gèle, personne ne le modifiait ; ce qu'il laissait ouvert,
n'importe quel utilisateur connecté le modifiait. Sur un site où tout le monde
peut ouvrir l'application, cela voulait dire que n'importe qui pouvait recharger
le référentiel d'une campagne qu'il ne pilote pas, la veille du comptage.

Une seconde barrière s'ajoute, orthogonale : l'**identité**. Les deux se
cumulent — un propriétaire n'écrit pas dans une campagne clôturée, un lecteur
n'écrit nulle part.

Ce que ces tests fixent : les trois rôles et ce qui les sépare, le fait que les
deux barrières se composent au lieu de se remplacer, les deux actions qui
restent au seul propriétaire, et — le plus important pour la suite — qu'aucun
chemin d'écriture ne contourne la règle.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from conftest import with_access

from inventory.domain.access import Role, restrict, role_of
from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign, Manager
from inventory.domain.workflow import mutability_of
from inventory.errors import FrozenError, PermissionDeniedError

CHEF = "chef@usine.fr"
GEST = "gestionnaire@usine.fr"
TIERS = "passant@usine.fr"


def campaign(
    *, created_by: str = CHEF, status: CampaignStatus = CampaignStatus.PREPARATION
) -> Campaign:
    return Campaign(
        id="camp-1",
        code="INV-2026-09",
        label="Inventaire",
        count_date=dt.date(2026, 9, 1),
        status=status,
        created_by=created_by,
        created_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    )


def manager(actor: str, *, active: bool = True) -> Manager:
    return Manager(
        campaign_id="camp-1", code="GESTIONNAIRE_1", actor=actor, active=active
    )


class TestTheThreeRoles:
    def test_the_creator_owns_the_campaign(self):
        assert role_of(CHEF, campaign()) is Role.OWNER

    def test_a_declared_manager_may_write(self):
        assert role_of(GEST, campaign(), [manager(GEST)]) is Role.MANAGER

    def test_anybody_else_only_reads(self):
        assert role_of(TIERS, campaign(), [manager(GEST)]) is Role.READER

    def test_a_deactivated_manager_is_back_to_reading(self):
        """Désactiver est la façon de retirer quelqu'un sans effacer sa trace."""
        assert role_of(GEST, campaign(), [manager(GEST, active=False)]) is Role.READER

    def test_the_case_of_the_address_does_not_decide_a_permission(self):
        """L'annuaire varie ; le droit d'écrire ne peut pas en dépendre."""
        assert role_of("CHEF@Usine.FR", campaign()) is Role.OWNER
        assert role_of("  Gestionnaire@USINE.fr ", campaign(), [manager(GEST)]) is (
            Role.MANAGER
        )

    def test_an_unidentified_caller_reads(self):
        """Déployé sans le proxy, personne ne doit hériter de droits."""
        for nobody in ("", "   ", None):
            assert role_of(nobody, campaign(), [manager(GEST)]) is Role.READER

    def test_an_ownerless_campaign_is_not_up_for_grabs(self):
        """Les campagnes d'avant que l'identité soit tracée n'ouvrent rien."""
        assert role_of(TIERS, campaign(created_by="")) is Role.READER

    def test_an_ownerless_campaign_still_has_its_managers(self):
        assert role_of(GEST, campaign(created_by=""), [manager(GEST)]) is Role.MANAGER


class TestTheTwoBarriersCompose:
    """Elles se cumulent, elles ne se remplacent pas."""

    def test_the_owner_writes_while_the_phase_allows_it(self):
        editable = restrict(mutability_of(CampaignStatus.PREPARATION), Role.OWNER)
        assert editable.items is True

    def test_the_owner_stops_writing_once_the_campaign_is_closed(self):
        """L'identité n'est pas un passe-droit sur le gel."""
        editable = restrict(mutability_of(CampaignStatus.CLOSED), Role.OWNER)
        assert editable.items is False

    def test_a_reader_writes_nothing_even_in_preparation(self):
        editable = restrict(mutability_of(CampaignStatus.PREPARATION), Role.READER)
        assert not any(editable.as_dict().values())

    def test_a_manager_sees_exactly_what_the_phase_allows(self):
        """Un gestionnaire n'a pas moins de droits qu'un propriétaire sur les données."""
        phase = mutability_of(CampaignStatus.COUNTING)
        assert restrict(phase, Role.MANAGER) == restrict(phase, Role.OWNER)


def context(actor: str, *, managers=(), status=CampaignStatus.PREPARATION):
    """Un contexte factice portant les vraies règles d'accès."""
    ctx = SimpleNamespace(
        actor=actor,
        progress=lambda c: SimpleNamespace(
            items=10, zones=1, book_stock_lines=5, book_stock_frozen=False
        ),
    )
    with_access(ctx, managers=managers)
    return cast(Any, ctx), campaign(status=status)


class TestWhatTheGuardRefuses:
    def test_a_reader_is_refused_before_anything_else(self):
        ctx, camp = context(TIERS)
        with pytest.raises(PermissionDeniedError):
            ctx.guard(camp, "items")

    def test_the_refusal_says_who_to_ask(self):
        """« Interdit » sans recours est un cul-de-sac."""
        ctx, camp = context(TIERS)
        with pytest.raises(PermissionDeniedError) as caught:
            ctx.guard(camp, "items")
        assert CHEF in str(caught.value)

    def test_the_owner_passes(self):
        ctx, camp = context(CHEF)
        ctx.guard(camp, "items")

    def test_a_declared_manager_passes(self):
        ctx, camp = context(GEST, managers=[manager(GEST)])
        ctx.guard(camp, "items")

    def test_identity_is_checked_before_the_phase(self):
        """Un lecteur devant un aspect gelé lit le refus le plus actionnable.

        « Vous êtes en lecture seule » se corrige en demandant un droit ;
        « c'est gelé » ne se corrige pas du tout. Les deux sont vrais, un seul
        est utile.
        """
        ctx, camp = context(TIERS, status=CampaignStatus.CLOSED)
        with pytest.raises(PermissionDeniedError):
            ctx.guard(camp, "items")

    def test_the_owner_still_meets_the_phase(self):
        ctx, camp = context(CHEF, status=CampaignStatus.CLOSED)
        with pytest.raises(FrozenError):
            ctx.guard(camp, "items")


class TestWhatOnlyTheOwnerMayDo:
    """Deux actions, et elles portent sur la campagne, pas sur ses données."""

    def test_a_manager_cannot_declare_managers(self):
        """Sinon il s'accorderait le droit d'en accorder."""
        ctx, camp = context(GEST, managers=[manager(GEST)])
        with pytest.raises(PermissionDeniedError):
            ctx.require_owner(camp, "déclarer les gestionnaires")

    def test_a_manager_cannot_delete_the_campaign(self):
        ctx, camp = context(GEST, managers=[manager(GEST)])
        with pytest.raises(PermissionDeniedError):
            ctx.require_owner(camp, "supprimer une campagne")

    def test_the_owner_may(self):
        ctx, camp = context(CHEF)
        ctx.require_owner(camp, "supprimer une campagne")

    def test_the_refusal_names_the_action_and_the_owner(self):
        ctx, camp = context(GEST, managers=[manager(GEST)])
        with pytest.raises(PermissionDeniedError) as caught:
            ctx.require_owner(camp, "déclarer les gestionnaires")
        message = str(caught.value)
        assert "gestionnaires" in message and CHEF in message


class TestTheRoleIsResolvedOncePerCampaign:
    def test_the_managers_are_read_once(self):
        """La garde s'exécute avant chaque écriture, et un import en fait beaucoup."""
        reads = []
        ctx = SimpleNamespace(actor=CHEF)
        ctx.referentials = SimpleNamespace(
            list_managers=lambda cid: reads.append(cid) or []
        )
        with_access(ctx)
        camp = campaign()
        for _ in range(50):
            ctx.role(camp)
        assert len(reads) == 1

    def test_two_campaigns_do_not_share_an_answer(self):
        """Le clonage en lit deux ; une entrée commune ouvrirait l'une sur l'autre."""
        ctx = SimpleNamespace(actor=CHEF)
        ctx.referentials = SimpleNamespace(list_managers=lambda cid: [])
        with_access(ctx)
        mine = campaign()
        theirs = campaign(created_by=TIERS)
        theirs.id = "camp-2"
        assert ctx.role(mine) is Role.OWNER
        assert ctx.role(theirs) is Role.READER


class TestNoWritePathBypassesTheRule:
    """La barrière ne vaut que si tout passe devant.

    Quarante-quatre appels à `guard` existent aujourd'hui, et il s'en ajoutera.
    Une méthode d'écriture qui oublierait la porte ne casserait rien : elle
    marcherait, pour tout le monde, ce qui est précisément le défaut qu'on
    vient de fermer. Ce contrôle lit donc le source des services et refuse la
    forme, plutôt que d'espérer qu'on y pense à chaque fois.

    Les exemptions sont nommées une par une, avec leur raison. Ajouter une
    méthode d'écriture oblige donc soit à la garder, soit à venir écrire ici
    pourquoi elle n'en a pas besoin.
    """

    #: Ce qui ressemble à une écriture dans le source d'une méthode.
    WRITE: ClassVar[tuple[str, ...]] = (
        "upsert", "create", "replace", "delete", "update", "set_", "insert",
        "clear", "mark_", "ensure_", "soft_delete", "post_", "save",
    )
    GATES: ClassVar[tuple[str, ...]] = (
        "ctx.guard(", "ctx.require_write(", "ctx.require_owner(",
    )

    #: Méthode → pourquoi elle n'a pas de porte.
    EXEMPT: ClassVar[dict[str, str]] = {
        "AnalysisService.backflush": "lecture ; « update » vient d'un model_copy",
        "CampaignService.overview": "lecture",
        "CampaignService.create":
            "aucune campagne n'existe encore ; son auteur en devient propriétaire",
        "CampaignService.clone":
            "crée une campagne neuve, dont l'auteur est propriétaire",
        "ServiceContext.require_write": "c'est la porte",
        "ServiceContext.require_owner": "c'est la porte",
        "ServiceContext.forget_progress": "cache de comptages, rien en base",
        "GenericService.delete_sheet_line":
            "délègue à delete_sheet_lines, qui garde",
        "ManagerService.perimeter": "lecture",
        # L'historique d'un import est la **trace** d'une écriture déjà
        # autorisée : les six importeurs franchissent la porte avant de
        # l'appeler. La mettre derrière la porte ferait qu'un import refusé
        # faute de droits ne laisserait aucune trace de la tentative — soit
        # l'inverse de ce qu'un historique sert à établir.
        "ImportBatches.record_batch": "trace d'une écriture déjà autorisée",
    }

    def offenders(self) -> dict[str, str]:
        import ast
        from pathlib import Path

        import inventory

        found: dict[str, str] = {}
        root = Path(inventory.__file__).parent / "services"
        for path in sorted(root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
                for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                    if fn.name.startswith("_"):
                        continue
                    body = ast.get_source_segment(source, fn) or ""
                    writes = any(f".{w}" in body for w in self.WRITE)
                    gated = any(g in body for g in self.GATES)
                    if writes and not gated:
                        found[f"{cls.name}.{fn.name}"] = f"{path.name}:{fn.lineno}"
        return found

    def test_every_ungated_write_is_a_named_exemption(self):
        surprises = {
            name: where
            for name, where in self.offenders().items()
            if name not in self.EXEMPT
        }
        assert not surprises, (
            "Ces méthodes écrivent sans passer par la porte d'accès :\n  "
            + "\n  ".join(f"{n}  ({w})" for n, w in sorted(surprises.items()))
            + "\nAjoutez `ctx.guard(campaign, \"<aspect>\")`, ou — si elle n'en "
            "a réellement pas besoin — inscrivez-la dans EXEMPT avec sa raison."
        )

    def test_the_exemption_list_has_not_gone_stale(self):
        """Une exemption qui ne correspond plus à rien cache la suivante."""
        gone = set(self.EXEMPT) - set(self.offenders())
        assert not gone, (
            "Ces exemptions ne servent plus — la méthode a disparu ou a reçu "
            "sa porte : " + ", ".join(sorted(gone))
        )
