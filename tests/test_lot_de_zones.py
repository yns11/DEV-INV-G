"""Créer un lot de zones d'un bloc collé, et régler leurs lignes vierges.

Une campagne réelle compte quarante à soixante zones, et la liste existe déjà :
dans un tableur, dans le compte rendu de la campagne précédente, sur le plan de
l'atelier. Les recréer une par une dans une fenêtre modale, c'est quarante
allers-retours pour recopier ce qu'on a sous les yeux.

Trois choses tiennent cette création, et chacune qui lâche coûte cher :

* **tout ou rien** — un lot à moitié créé laisse un état que personne n'a voulu
  et que rien ne dit comment défaire ; c'est pourquoi la validation précède la
  première écriture ;
* **les feuilles suivent** — une zone sans feuille de comptage ne s'imprime pas
  et ne se compte pas, et rien à l'écran ne le dit ;
* **le refus nomme** — « le collage est invalide » sur un bloc de soixante
  lignes oblige à les relire une par une.

Le nombre de lignes vierges par section fait partie du même geste : c'est une
propriété de ce qu'on va compter là-bas, pas une décision qu'on reprend à chaque
sortie d'imprimante.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign, Zone
from inventory.errors import (
    ConflictError,
    InventoryError,
    NotFoundError,
    ValidationError,
)
from inventory.services.zone_service import MAX_BULK_ZONES, ZoneService

OWNER = "alice@usine"

#: La connexion distribuée par la transaction de test. Objet identifiable :
#: c'est ce qui permet de vérifier que toutes les écritures passent par la
#: *même* — un lot à moitié écrit est précisément ce qu'on refuse.
CONN = object()


def campaign(status: CampaignStatus = CampaignStatus.PREPARATION) -> Campaign:
    return Campaign(
        id="camp-1",
        code="INV-2026-06",
        label="Inventaire général",
        count_date="2026-06-13",
        status=status,
        created_by=OWNER,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


def zone(zone_id: str, code: str, **kwargs) -> Zone:
    return Zone(id=zone_id, campaign_id="camp-1", code=code, label=code, **kwargs)


def service(
    *, zones: tuple[Zone, ...] = (), actor: str = OWNER
) -> tuple[ZoneService, dict[str, list]]:
    """Le service, et le journal de ce qu'il a fait."""
    log: dict[str, list] = {
        "created": [], "sheets": [], "events": [], "conns": [], "blank_rows": [],
    }

    @contextmanager
    def transaction():
        yield CONN

    def create_zone(z, *, actor, conn=None):
        log["conns"].append(conn)
        log["created"].append(z)
        return z

    def ensure_sheets(campaign_id, zone_id, passes, *, actor, conn=None):
        log["sheets"].append((zone_id, tuple(passes)))
        return []

    def set_blank_rows(campaign_id, zone_id, rows, *, actor, conn=None):
        log["conns"].append(conn)
        log["blank_rows"].append((campaign_id, zone_id, dict(rows)))

    ctx = SimpleNamespace(
        actor=actor,
        # Le séquencement est traversé, pas contourné : une campagne qui porte
        # déjà des articles laisse passer l'aspect « zones ».
        progress=lambda c: SimpleNamespace(
            items=10, zones=len(zones), book_stock_lines=0, book_stock_frozen=False
        ),
        db=SimpleNamespace(transaction=transaction),
        sheets=SimpleNamespace(
            list_zones=lambda cid: list(zones),
            create_zone=create_zone,
            ensure_sheets=ensure_sheets,
            set_blank_rows=set_blank_rows,
        ),
        record=lambda **kw: log["events"].append(kw) or "evt",
        forget_progress=lambda cid: None,
    )
    with_access(ctx)
    return ZoneService(cast(Any, ctx)), log


class TestUnBlocDonneAutantDeZonesQueDeLignes:
    def test_le_code_suffit(self):
        svc, log = service()
        created = svc.create_zones(
            campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-B"}]
        )
        assert [z.code for z in created] == ["ZONE-A", "ZONE-B"]
        assert [z.code for z in log["created"]] == ["ZONE-A", "ZONE-B"]

    def test_les_feuilles_suivent_chaque_zone(self):
        """Une zone sans feuille ne s'imprime pas et ne se compte pas.

        C'est le branchement qu'une création groupée écrite à part oublierait :
        la zone apparaît dans la liste, et rien ne dit qu'elle est inerte.
        """
        svc, log = service()
        svc.create_zones(campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-B"}])
        assert [zone_id for zone_id, _ in log["sheets"]] == [
            z.id for z in log["created"]
        ]
        assert all(passes for _, passes in log["sheets"])

    def test_toutes_les_ecritures_partagent_une_transaction(self):
        svc, log = service()
        svc.create_zones(campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-B"}])
        assert log["conns"] == [CONN, CONN]

    def test_chaque_zone_laisse_sa_trace(self):
        svc, log = service()
        svc.create_zones(campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-B"}])
        assert len(log["events"]) == 2
        assert {e["entity_type"] for e in log["events"]} == {"zone"}

    def test_l_ordre_d_affichage_continue_celui_des_zones_existantes(self):
        """Sinon le lot se range avant tout ce qui était déjà là."""
        svc, _ = service(zones=(zone("z-1", "ZONE-0", display_order=7),))
        created = svc.create_zones(
            campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-B"}]
        )
        assert [z.display_order for z in created] == [8, 9]

    def test_les_lignes_vierges_du_collage_arrivent_sur_la_zone(self):
        svc, _ = service()
        created = svc.create_zones(
            campaign(),
            [{"code": "ZONE-A", "blank_rows": {"LINE_SIDE": 40, "WIP": 10}}],
        )
        assert created[0].blank_rows == {"LINE_SIDE": 40, "WIP": 10}


class TestToutOuRien:
    def test_un_code_manquant_arrete_tout_le_lot(self):
        svc, log = service()
        with pytest.raises(ValidationError):
            svc.create_zones(campaign(), [{"code": "ZONE-A"}, {"code": ""}])
        assert log["created"] == [] and log["events"] == []

    def test_le_refus_nomme_la_ligne_fautive(self):
        """Sur un bloc de soixante lignes, « invalide » oblige à tout relire."""
        svc, _ = service()
        with pytest.raises(ValidationError) as raised:
            svc.create_zones(
                campaign(),
                [
                    {"code": "ZONE-A"},
                    {"code": "ZONE-B"},
                    {"code": "ZONE-C", "blank_rows": {"WIP": 500}},
                ],
            )
        assert "ligne 3" in str(raised.value)
        assert "WIP" in str(raised.value)

    def test_un_doublon_avec_une_zone_existante_arrete_tout(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(ConflictError) as raised:
            svc.create_zones(campaign(), [{"code": "ZONE-B"}, {"code": "ZONE-A"}])
        assert "ZONE-A" in str(raised.value)
        assert log["created"] == []

    def test_un_doublon_interne_au_collage_arrete_tout(self):
        """Deux fois la même ligne dans le tableur est l'erreur la plus banale."""
        svc, log = service()
        with pytest.raises(ConflictError):
            svc.create_zones(campaign(), [{"code": "ZONE-A"}, {"code": "ZONE-A"}])
        assert log["created"] == []

    def test_un_nombre_de_lignes_hors_bornes_arrete_tout(self):
        svc, log = service()
        with pytest.raises(ValidationError):
            svc.create_zones(
                campaign(), [{"code": "ZONE-A", "blank_rows": {"LINE_SIDE": 500}}]
            )
        assert log["created"] == []

    def test_un_bloc_vide_est_refuse(self):
        svc, _ = service()
        with pytest.raises(ValidationError):
            svc.create_zones(campaign(), [])

    def test_au_dela_du_plafond_le_collage_est_refuse(self):
        """Un collage de dix mille lignes est une erreur de manipulation.

        Le refuser d'emblée coûte un message ; l'accepter coûte une transaction
        qui tient la table `zone` pendant que quelqu'un se demande pourquoi.
        """
        svc, log = service()
        specs = [{"code": f"Z-{n:04d}"} for n in range(MAX_BULK_ZONES + 1)]
        with pytest.raises(ValidationError):
            svc.create_zones(campaign(), specs)
        assert log["created"] == []


class TestPreparationSeulement:
    @pytest.mark.parametrize(
        "status",
        [CampaignStatus.ANALYSIS, CampaignStatus.CLOSED],
    )
    def test_le_lot_est_refuse_hors_des_phases_qui_ouvrent_les_zones(
        self, status: CampaignStatus
    ):
        svc, log = service()
        with pytest.raises(InventoryError):
            svc.create_zones(campaign(status), [{"code": "ZONE-A"}])
        assert log["created"] == []

    @pytest.mark.parametrize(
        "status",
        [CampaignStatus.ANALYSIS, CampaignStatus.CLOSED],
    )
    def test_les_lignes_vierges_le_sont_aussi(self, status: CampaignStatus):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(InventoryError):
            svc.set_blank_rows(campaign(status), "z-1", {"WIP": 10})
        assert log["blank_rows"] == []


class TestLesLignesViergesDUneZone:
    def test_elles_s_enregistrent(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        updated = svc.set_blank_rows(campaign(), "z-1", {"WIP": 10, "WIP_OK": 4})
        assert updated.blank_rows == {"WIP": 10, "WIP_OK": 4}
        assert log["blank_rows"] == [("camp-1", "z-1", {"WIP": 10, "WIP_OK": 4})]

    def test_un_zero_ne_se_stocke_pas(self):
        """« Ne s'imprime pas » et « rien de déclaré » sont le même état.

        En garder deux écritures ferait diverger deux lectures — celle de la
        grille et celle du générateur de PDF.
        """
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        updated = svc.set_blank_rows(campaign(), "z-1", {"LINE_SIDE": 0, "WIP": 10})
        assert updated.blank_rows == {"WIP": 10}
        assert log["blank_rows"] == [("camp-1", "z-1", {"WIP": 10})]

    def test_le_reglage_remplace_et_ne_complete_pas(self):
        """Un dictionnaire fusionné ne permettrait jamais de retirer une section."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A", blank_rows={"LINE_SIDE": 40, "WIP": 10}),)
        )
        updated = svc.set_blank_rows(campaign(), "z-1", {"WIP": 10})
        assert updated.blank_rows == {"WIP": 10}
        assert log["blank_rows"] == [("camp-1", "z-1", {"WIP": 10})]

    def test_tout_effacer_est_permis(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A", blank_rows={"WIP": 10}),))
        assert svc.set_blank_rows(campaign(), "z-1", {}).blank_rows == {}
        assert log["blank_rows"] == [("camp-1", "z-1", {})]

    @pytest.mark.parametrize(
        "rows",
        [
            {"WIP": 121},
            {"WIP": -1},
            {"SECTION_INCONNUE": 10},
            {"WIP": "beaucoup"},
        ],
    )
    def test_ce_qui_n_est_pas_un_nombre_de_lignes_est_refuse(self, rows):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(ValidationError):
            svc.set_blank_rows(campaign(), "z-1", rows)
        assert log["blank_rows"] == []

    def test_la_zone_est_resolue_contre_la_campagne(self):
        """L'identifiant vient d'une requête : rien d'autre ne l'y attache."""
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(NotFoundError):
            svc.set_blank_rows(campaign(), "z-de-quelqu-un-d-autre", {"WIP": 10})
        assert log["blank_rows"] == []

    def test_le_changement_laisse_sa_trace_avec_l_etat_precedent(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A", blank_rows={"WIP": 4}),))
        svc.set_blank_rows(campaign(), "z-1", {"WIP": 10})
        assert len(log["events"]) == 1
        assert log["events"][0]["before"] == {"blankRows": {"WIP": 4}}
        assert log["events"][0]["after"] == {"blankRows": {"WIP": 10}}
