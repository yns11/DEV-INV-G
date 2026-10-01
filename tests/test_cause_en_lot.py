"""La cause se pose là où on regarde les chiffres, et sur un lot.

Ce qui manquait
---------------
La cause se décidait dans un seul écran, avec une liste déroulante par ligne et
aucun commentaire. Or c'est en regardant les chiffres qu'on sait quoi écrire — le
stock ERP, le compté, la décomposition qu'on vient d'ouvrir. Faire le tour par un
autre écran pour noter ce qu'on vient de comprendre est le meilleur moyen de ne
pas le noter.

Et la fin d'analyse se fait en lot : vingt lignes, même cause. Vingt appels
donneraient vingt transactions, vingt entrées d'audit et un échec possible au
douzième — la moitié du lot posée, l'autre non, et rien pour dire où.

La règle du commentaire vide, et pourquoi elle s'inverse
-------------------------------------------------------
Sur **une** ligne, le formulaire montre le commentaire courant : l'effacer est un
geste, et l'enregistrement écrit ce qui est à l'écran. Sur un **lot**, le
formulaire s'ouvre à blanc — il ne peut pas montrer vingt commentaires différents
— donc son champ vide veut dire « je n'en parle pas ». C'est la forme du
formulaire qui dicte la règle, et ces contrôles tiennent les deux.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign, Item
from inventory.errors import FrozenError

pytestmark = pytest.mark.postgres

A, B, C = "P-A", "P-B", "P-C"
TOUTES = (A, B, C)

#: Deux codes du référentiel réel. Les causes sont une clé étrangère : inventer
#: « VOL » aurait fait tomber ces contrôles sur une contrainte de base plutôt
#: que sur ce qu'ils vérifient.
SAISIE, AUTRE = "1", "99"


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_causes") as database:
        yield database


def _campaign(campaign_id: str, status: CampaignStatus) -> Campaign:
    return Campaign(
        id=campaign_id,
        code=f"CA-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 9, 12),
        status=status,
        created_by="test",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def campaign(db):
    campaign_id = make_campaign(db, f"CA-{uuid.uuid4().hex[:8]}")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE campaign SET status='ANALYSIS' WHERE id=%s", (campaign_id,)
        )
    return _campaign(campaign_id, CampaignStatus.ANALYSIS)


@pytest.fixture
def ctx(db, campaign):
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    context = ServiceContext(actor="test", db=db, settings=get_settings())
    context.referentials.upsert_items(
        [
            Item(campaign_id=campaign.id, item_number=n, name=n, std_price=Decimal(1))
            for n in TOUTES
        ],
        actor="test",
    )
    return context


@pytest.fixture
def service(ctx):
    from inventory.services.cause_service import CauseService

    return CauseService(ctx)


def _posees(ctx, campaign: Campaign) -> dict[str, tuple[str | None, str]]:
    return {
        a.item_number: (a.cause_code, a.comment)
        for a in ctx.analysis.list_analyses(campaign.id)
    }


class TestUneLigne:
    def test_la_cause_et_le_commentaire_s_enregistrent_ensemble(
        self, service, ctx, campaign
    ):
        service.save(
            campaign, item_number=A, cause_code=SAISIE, comment="palette en zone B"
        )

        assert _posees(ctx, campaign) == {A: (SAISIE, "palette en zone B")}

    def test_le_formulaire_fait_foi_commentaire_vidé_compris(
        self, service, ctx, campaign
    ):
        """Le champ montrait sa valeur : l'effacer est un geste, pas un oubli."""
        service.save(campaign, item_number=A, cause_code=SAISIE, comment="à revoir")
        service.save(campaign, item_number=A, cause_code=SAISIE, comment="")

        assert _posees(ctx, campaign) == {A: (SAISIE, "")}


class TestUnLot:
    def test_la_même_cause_sur_plusieurs_lignes(self, service, ctx, campaign):
        service.save_many(
            campaign, item_numbers=[A, B], cause_code=AUTRE, comment="magasin ouvert"
        )

        assert _posees(ctx, campaign) == {
            A: (AUTRE, "magasin ouvert"),
            B: (AUTRE, "magasin ouvert"),
        }

    def test_un_commentaire_vide_ne_vide_rien(self, service, ctx, campaign):
        """La règle inverse de celle d'une ligne, et c'est la forme du formulaire
        qui la dicte : à blanc, il ne montre pas ce qu'il remplacerait."""
        service.save(campaign, item_number=A, cause_code=SAISIE, comment="constat A")
        service.save_many(campaign, item_numbers=[A, B], cause_code=AUTRE, comment="")

        posees = _posees(ctx, campaign)
        assert posees[A] == (AUTRE, "constat A")
        assert posees[B] == (AUTRE, "")

    def test_il_ne_touche_pas_aux_lignes_hors_du_lot(self, service, ctx, campaign):
        service.save(campaign, item_number=C, cause_code=SAISIE, comment="intacte")
        service.save_many(campaign, item_numbers=[A, B], cause_code=AUTRE)

        assert _posees(ctx, campaign)[C] == (SAISIE, "intacte")

    def test_une_reference_repetee_ne_compte_qu_une_fois(self, service, campaign):
        assert service.save_many(campaign, item_numbers=[A, A, B], cause_code=AUTRE) == 2

    def test_un_lot_vide_n_ecrit_rien(self, service, ctx, campaign):
        assert service.save_many(campaign, item_numbers=[], cause_code=AUTRE) == 0
        assert _posees(ctx, campaign) == {}

    def test_la_proposition_du_modele_survit_a_l_affectation(
        self, service, ctx, campaign
    ):
        """Sans elle, plus moyen de savoir, plus tard, si la décision suivait la
        proposition ou s'en écartait — c'est-à-dire de juger le modèle."""
        ctx.analysis.save_ai_suggestions(campaign.id, [(A, SAISIE, 0.9, "parce que")])
        service.save_many(campaign, item_numbers=[A], cause_code=AUTRE)

        analyse = next(
            a for a in ctx.analysis.list_analyses(campaign.id) if a.item_number == A
        )
        assert analyse.cause_code == AUTRE
        assert analyse.ai_suggested_cause == SAISIE
        assert analyse.ai_rationale == "parce que"

    def test_un_echec_au_milieu_ne_laisse_rien(self, ctx, service, campaign, monkeypatch):
        """L'atomicité, vérifiée plutôt que promise en commentaire.

        C'est toute la raison d'être de la route de lot : vingt appels séparés
        auraient laissé la moitié du travail posé au douzième échec, sans rien
        pour dire où ça s'est arrêté. Le vérifier demande de faire échouer
        l'écriture en plein milieu, ce qu'aucune donnée d'entrée ne provoque.
        """
        vrai = ctx.analysis.upsert_analysis
        appels = {"n": 0}

        def casse(analysis, **kwargs):
            appels["n"] += 1
            if appels["n"] == 2:
                raise RuntimeError("la base lâche au milieu du lot")
            return vrai(analysis, **kwargs)

        monkeypatch.setattr(ctx.analysis, "upsert_analysis", casse)

        with pytest.raises(RuntimeError):
            service.save_many(campaign, item_numbers=[A, B, C], cause_code=AUTRE)

        assert _posees(ctx, campaign) == {}, (
            "la première ligne a été écrite et la transaction ne l'a pas reprise"
        )

    def test_le_lot_laisse_une_seule_trace_d_audit(self, ctx, service, campaign):
        """Vingt lignes d'audit pour un geste rendraient le journal illisible le
        jour où on le relit pour comprendre une décision."""
        service.save_many(campaign, item_numbers=[A, B, C], cause_code=AUTRE)

        traces = ctx.audit.list(campaign.id, entity_type="variance_analysis")
        assert len(traces) == 1
        assert "3 écart(s)" in traces[0].summary


class TestLaGarde:
    @pytest.mark.parametrize(
        "statut", [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS]
    )
    def test_la_cause_s_affecte_des_le_comptage(self, service, ctx, campaign, statut):
        """Le jour J, la cause est sous les yeux de celui qui compte.

        La garde passe par le domaine, mais c'est ici qu'on vérifie qu'elle
        atteint bien l'écriture : ouvrir l'aspect sans que le service la laisse
        passer aurait rendu une porte peinte à l'écran.
        """
        en_cours = _campaign(campaign.id, statut)

        service.save(en_cours, item_number=A, cause_code=SAISIE, comment="allée 3")
        service.save_many(en_cours, item_numbers=[B, C], cause_code=AUTRE)

        posees = _posees(ctx, campaign)
        assert posees[A] == (SAISIE, "allée 3")
        assert posees[B][0] == AUTRE and posees[C][0] == AUTRE

    def test_mais_pas_avant_qu_il_y_ait_un_ecart_a_commenter(self, service, campaign):
        """La borne basse : en préparation, il n'y a rien à expliquer."""
        with pytest.raises(FrozenError):
            service.save(
                _campaign(campaign.id, CampaignStatus.PREPARATION),
                item_number=A,
                cause_code=AUTRE,
            )

    def test_le_lot_se_ferme_a_la_cloture(self, service, campaign):
        with pytest.raises(FrozenError):
            service.save_many(
                _campaign(campaign.id, CampaignStatus.CLOSED),
                item_numbers=[A],
                cause_code=AUTRE,
            )

    def test_et_une_ligne_seule_aussi(self, service, campaign):
        with pytest.raises(FrozenError):
            service.save(
                _campaign(campaign.id, CampaignStatus.CLOSED),
                item_number=A,
                cause_code=AUTRE,
            )

    def test_la_route_du_lot_existe(self):
        """La classe de défaut de ce dépôt : un service écrit et que rien
        n'atteint."""
        from inventory.api.app import create_app

        routes = {
            (r.path, m)
            for r in create_app().routes
            if getattr(r, "methods", None)
            for m in r.methods
        }
        assert (
            "/api/campaigns/{campaign_id}/analysis/variances/causes", "POST"
        ) in routes
