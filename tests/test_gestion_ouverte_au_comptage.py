"""La vue Gestion reste modifiable pendant le comptage et pendant l'analyse.

Les gestionnaires et leurs deux périmètres — l'affectation des entrepôts, donc
de leurs journaux, et celle des zones GENERIQUE — partageaient la garde des
seuils. La règle était bonne pour les seuils et fausse pour eux.

Un seuil décide de ce qui sera signalé comme exception : le changer en cours de
route changerait la liste sous les yeux de qui la traite. Il gèle à l'entrée en
comptage, et c'est juste.

Un gestionnaire ne décide de rien. Ce n'est pas une habilitation mais un filtre
— « mon périmètre » — et l'écran le dit en toutes lettres : chacun garde le
droit d'agir partout. Le figer ne protégeait donc aucun chiffre, et fermait
l'écran au seul moment où le personnel bouge vraiment : quelqu'un tombe malade
le matin du jour J, un renfort arrive à midi, un entrepôt apparaît dans un
import de l'après-midi. Le cycle de vie étant strictement en avant, corriger une
adresse e-mail demandait de recréer la campagne.

Ce que ces contrôles fixent, c'est le **branchement** : les trois écritures
passent réellement par la nouvelle garde. La matrice elle-même est épinglée
dans `test_domain_workflow.py` ; répéter ici ce qu'elle dit ne prouverait rien
de plus que sa propre lecture.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign
from inventory.errors import FrozenError

pytestmark = pytest.mark.postgres

#: La campagne est créée par « test » : c'est donc lui le propriétaire, et
#: `save_managers` est réservé au propriétaire.
CHEF = "test"

#: Les trois phases où la vue doit rester ouverte, et celle où elle se ferme.
OUVERTES = (
    CampaignStatus.PREPARATION,
    CampaignStatus.COUNTING,
    CampaignStatus.ANALYSIS,
)


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_gestion_ouverte") as database:
        yield database


@pytest.fixture
def campaign_id(db):
    """Une campagne neuve par contrôle, et aucun nettoyage.

    Ces écritures laissent une trace d'audit, et la migration 020 refuse qu'on
    l'efface — c'est précisément ce qu'elle garantit. Supprimer la campagne
    échouerait donc sur sa clé étrangère. La base est jetable et disparaît avec
    le module : il n'y a rien à ranger.
    """
    return make_campaign(db, f"GES-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def ctx(db):
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext

    return ServiceContext(actor=CHEF, db=db, settings=get_settings())


@pytest.fixture
def service(ctx):
    from inventory.services import ManagerService

    return ManagerService(ctx)


def _campaign(campaign_id: str, status: CampaignStatus) -> Campaign:
    return Campaign(
        id=campaign_id,
        code=f"GES-{campaign_id[:8]}",
        label="",
        count_date=dt.date(2026, 9, 12),
        status=status,
        created_by=CHEF,
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


def _une_zone(ctx, campaign: Campaign) -> str:
    """Une zone à réaffecter, posée directement en base.

    Par le dépôt et non par `ZoneService` : créer une zone exige le référentiel
    articles et se ferme à l'analyse, et ce n'est pas ce qui est en question
    ici. Charger un référentiel pour obtenir une ligne à réaffecter ferait
    dépendre ce contrôle d'une règle voisine, et le ferait tomber le jour où
    elle bouge.
    """
    from inventory.db import new_id
    from inventory.domain.models import Zone

    zone = Zone(
        id=new_id(), campaign_id=campaign.id, code=f"Z-{uuid.uuid4().hex[:6]}"
    )
    ctx.sheets.create_zone(zone, actor=CHEF)
    return zone.id


def _renommer(service, campaign: Campaign, label: str) -> None:
    service.save_managers(
        campaign,
        [{"code": "GESTIONNAIRE_1", "label": label, "actor": "", "display_order": 0}],
    )


class TestLesTroisEcrituresPassentPendantLeComptageEtLAnalyse:
    @pytest.mark.parametrize("status", OUVERTES)
    def test_un_gestionnaire_se_renomme(self, service, campaign_id, status):
        """Un renfort arrive à midi le jour J, et il lui faut un poste."""
        campaign = _campaign(campaign_id, status)
        _renommer(service, campaign, "Renfort après-midi")

        noms = {m.code: m.label for m in service.list_managers(campaign)}
        assert noms["GESTIONNAIRE_1"] == "Renfort après-midi"

    @pytest.mark.parametrize("status", OUVERTES)
    def test_une_identite_se_corrige(self, service, campaign_id, status):
        """Le cas qui a fait remonter le sujet : une adresse e-mail fautive.

        Elle est ce qui résout « Mon périmètre » côté serveur. Fausse, le filtre
        ne rend rien, et il fallait recréer la campagne pour la corriger.
        """
        campaign = _campaign(campaign_id, status)
        service.save_managers(
            campaign,
            [
                {
                    "code": "GESTIONNAIRE_1",
                    "label": "Atelier",
                    "actor": "prenom.nom@usine.fr",
                    "display_order": 0,
                }
            ],
        )

        acteurs = {m.code: m.actor for m in service.list_managers(campaign)}
        assert acteurs["GESTIONNAIRE_1"] == "prenom.nom@usine.fr"

    @pytest.mark.parametrize("status", OUVERTES)
    def test_un_entrepot_se_reaffecte(self, service, campaign_id, status):
        """Un entrepôt découvert par un import de l'après-midi doit trouver preneur."""
        campaign = _campaign(campaign_id, status)
        assert service.assign_warehouses(campaign, {"B06": "GESTIONNAIRE_1"}) == 1

        affecte = {
            w["warehouseId"]: w["managerCode"]
            for w in service.overview(campaign)["warehouses"]
        }
        assert affecte["B06"] == "GESTIONNAIRE_1"

    @pytest.mark.parametrize("status", OUVERTES)
    def test_une_zone_se_reaffecte(self, ctx, service, campaign_id, status):
        """Et en analyse, la zone elle-même est gelée : ce sont deux choses.

        La zone porte des quantités relevées sur le terrain, et son nom, son
        nombre de comptages ou ses lignes ne bougent plus. Le gestionnaire
        n'est qu'un filtre, et répartir l'analyse entre plusieurs personnes est
        précisément ce qu'on fait à ce moment-là.
        """
        campaign = _campaign(campaign_id, status)
        zone_id = _une_zone(ctx, campaign)

        assert service.assign_zones(campaign, [zone_id], "GESTIONNAIRE_1") == 1

        zones = {z.id: z.manager_code for z in ctx.sheets.list_zones(campaign.id)}
        assert zones[zone_id] == "GESTIONNAIRE_1"


class TestLaClotureFermeTout:
    """Le dossier est immuable, et qui a compté quoi en fait partie."""

    def test_un_gestionnaire_ne_se_renomme_plus(self, service, campaign_id):
        campaign = _campaign(campaign_id, CampaignStatus.CLOSED)
        with pytest.raises(FrozenError) as refus:
            _renommer(service, campaign, "Trop tard")
        assert "gestionnaires" in str(refus.value).lower()

    def test_un_entrepot_ne_se_reaffecte_plus(self, service, campaign_id):
        campaign = _campaign(campaign_id, CampaignStatus.CLOSED)
        with pytest.raises(FrozenError):
            service.assign_warehouses(campaign, {"B06": "GESTIONNAIRE_1"})

    def test_une_zone_ne_se_reaffecte_plus(self, ctx, service, campaign_id):
        campaign = _campaign(campaign_id, CampaignStatus.CLOSED)
        zone_id = _une_zone(ctx, campaign)
        with pytest.raises(FrozenError):
            service.assign_zones(campaign, [zone_id], "GESTIONNAIRE_1")


class TestLeRefusResteLisible:
    """Un gel qui s'annonce « managers » ne se lit pas.

    Le libellé de l'aspect existe pour ça, et un aspect neuf sans libellé
    produit un message en anglais technique au milieu d'une phrase française.
    """

    def test_le_message_nomme_l_aspect_en_francais(self, service, campaign_id):
        campaign = _campaign(campaign_id, CampaignStatus.CLOSED)
        with pytest.raises(FrozenError) as refus:
            service.assign_warehouses(campaign, {"B06": "GESTIONNAIRE_1"})
        message = str(refus.value)
        assert "Les gestionnaires et leurs périmètres" in message
        assert "managers" not in message

    def test_et_le_statut_qui_gele(self, service, campaign_id):
        campaign = _campaign(campaign_id, CampaignStatus.CLOSED)
        with pytest.raises(FrozenError) as refus:
            service.assign_warehouses(campaign, {"B06": "GESTIONNAIRE_1"})
        assert "CLÔTURÉE" in str(refus.value)
