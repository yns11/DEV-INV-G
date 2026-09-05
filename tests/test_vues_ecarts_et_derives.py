"""Trois vues qui montraient autre chose que ce qu'elles annonçaient.

Chacune répondait à une question voisine de celle qu'on lui posait, et chaque
fois le chiffre affiché était juste — c'est ce qui les rendait difficiles à
voir.

**Les dérives.** La vue listait toutes les lignes confrontées, dont l'immense
majorité à zéro : c'est le cas *normal*, l'emplacement était balisé et poster
son journal a réaligné l'ERP. Les quelques lignes qui demandent une décision se
perdaient au milieu des autres.

**L'arbitrage.** Il listait aussi les lignes en accord, sur lesquelles il n'y a
rien à trancher.

**La décomposition du journal consolidé.** Elle listait une ligne *par feuille*
— donc deux fois la même quantité sur une zone à double comptage — et affichait
les deux chiffres bruts là où la consolidation n'en retient qu'un. Et le total
d'une ligne du journal GENERIQUE ouvrait la décomposition du stock compté de
toute la campagne : les treize pièces d'un autre emplacement s'y affichaient à
côté des soixante mille de GENERIQUE, alors que le journal, lui, ne les compte
pas.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from conftest import with_transactions

from inventory.domain.enums import (
    CampaignStatus,
    CountSection,
    ExclusionScope,
    ItemType,
    SheetPass,
)
from inventory.domain.models import (
    ArbitrationLine,
    Campaign,
    CountSheet,
    CountSheetLine,
    EarlyCountDrift,
    Item,
    Zone,
)

CAMPAIGN = Campaign(
    id="camp-1", code="INV-2026-06", label="Inventaire",
    count_date=dt.date(2026, 6, 30), status=CampaignStatus.COUNTING,
    created_by="chef@usine", created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
)


# --------------------------------------------------------------------------- #
# 1. Les dérives nulles ne sont pas une information
# --------------------------------------------------------------------------- #

def drift(item: str, erp_j: str, physical: str) -> EarlyCountDrift:
    return EarlyCountDrift(
        id=f"d-{item}", campaign_id="camp-1", warehouse_id="ATP",
        location_id="SOL", item_number=item,
        qty_erp_t0=Decimal(physical), qty_physical_t0=Decimal(physical),
        qty_erp_j=Decimal(erp_j),
    )


class TestLaVueDerivesNeMontreQueCeQuiADerive:
    def _service(self, drifts):
        from inventory.services.drift_service import DriftService

        ctx = cast(Any, SimpleNamespace(
            actor="chef@usine",
            drifts=SimpleNamespace(list=lambda cid: list(drifts)),
        ))
        return DriftService(ctx)

    def test_une_derive_nulle_est_ecartee(self):
        """C'est le cas normal, donc l'absence d'information."""
        service = self._service([drift("P-1", "100", "100")])
        assert service.list_drifts("camp-1") == []

    def test_une_derive_non_nulle_reste(self):
        service = self._service([drift("P-2", "97", "100")])
        rows = service.list_drifts("camp-1")
        assert [r.item_number for r in rows] == ["P-2"]

    def test_une_derive_negative_reste_aussi(self):
        """Le signe ne dit pas si la ligne mérite un regard, seul l'écart le dit."""
        service = self._service([drift("P-3", "104", "100")])
        assert [r.item_number for r in service.list_drifts("camp-1")] == ["P-3"]

    def test_le_calcul_les_conserve_toutes(self):
        """La ligne à zéro est la trace que la confrontation a bien eu lieu ;
        c'est la *vue* qui la tait, pas le dépôt."""
        from inventory.services.drift_service import DriftService

        source = __import__("inspect").getsource(DriftService.list_drifts)
        assert "ctx.drifts.list" in source


# --------------------------------------------------------------------------- #
# 2. L'arbitrage ne liste que les désaccords, et sait les valider
# --------------------------------------------------------------------------- #

def arbitration(
    item: str, q1: str, q2: str, *, decided: bool = False, aid: str | None = None
) -> ArbitrationLine:
    return ArbitrationLine(
        id=aid or f"a-{item}", campaign_id="camp-1", zone_id="z-1",
        item_number=item, section=CountSection.LINE_SIDE,
        qty_pass_1=Decimal(q1), qty_pass_2=Decimal(q2),
        qty_arbitrated=Decimal(q1) if decided else None,
        decided_at=dt.datetime(2026, 6, 30, tzinfo=dt.UTC) if decided else None,
        decided_by="alice" if decided else None,
    )


def arbitration_service(lines, decided: list[tuple[str, Decimal, str]]):
    from inventory.services.arbitration_service import ArbitrationService

    zone = Zone(id="z-1", campaign_id="camp-1", code="PREPA STACK", passes=2)
    ctx = cast(Any, SimpleNamespace(
        actor="chef@usine",
        request_id="req-1",
        guard=lambda campaign, aspect: None,
        record=lambda **kw: None,
        referentials=SimpleNamespace(items_by_number=lambda cid: {
            "VIS": Item(campaign_id="camp-1", item_number="VIS", name="Vis M6",
                        std_price=Decimal("2")),
            "ROTOR": Item(campaign_id="camp-1", item_number="ROTOR", name="Rotor",
                          std_price=Decimal("50")),
        }),
        sheets=SimpleNamespace(
            list_zones=lambda cid, **kw: [zone],
            list_arbitrations=lambda cid, **kw: list(lines),
            decide_arbitration=lambda aid, qty, *, actor, comment="": decided.append(
                (aid, qty, comment)
            ),
        ),
    ))
    with_transactions(ctx)
    return ArbitrationService(ctx)


class TestLaVueArbitrageNeListeQueLesDesaccords:
    def test_les_lignes_en_accord_sortent(self):
        service = arbitration_service(
            [arbitration("VIS", "100", "100"), arbitration("ROTOR", "50", "60")], []
        )
        rows = service.list(CAMPAIGN, "z-1", divergent_only=True)
        assert [r["item_number"] for r in rows] == ["ROTOR"]

    def test_sans_le_drapeau_tout_reste_lisible(self):
        """La liste complète garde un usage — un export, une relecture."""
        service = arbitration_service(
            [arbitration("VIS", "100", "100"), arbitration("ROTOR", "50", "60")], []
        )
        assert len(service.list(CAMPAIGN, "z-1")) == 2

    def test_un_desaccord_deja_tranche_reste_affiche(self):
        """Il diverge toujours : le masquer ferait disparaître la décision de
        l'écran qui la porte."""
        service = arbitration_service([arbitration("ROTOR", "50", "60", decided=True)], [])
        rows = service.list(CAMPAIGN, "z-1", divergent_only=True)
        assert len(rows) == 1 and rows[0]["needsDecision"] is False

    def test_chaque_ligne_porte_sa_zone(self):
        """La vue peut couvrir toute la campagne ; sans la zone, une référence
        comptée dans quatre aires donne quatre lignes indiscernables."""
        service = arbitration_service([arbitration("ROTOR", "50", "60")], [])
        assert service.list(CAMPAIGN, "z-1")[0]["zoneCode"] == "PREPA STACK"


class TestValiderToutValideCeQuiEstAffiche:
    """Le défaut : « Valider tout » comptait comme non tranchées des lignes
    qui portaient un chiffre sous les yeux de l'utilisateur.

    Le serveur allait rechercher la quantité lui-même — le comptage n°2, ou la
    proposition enregistrée. Une quantité tapée dans le champ, ou posée là par
    un bouton de remplissage local, n'existait pas de son côté.
    """

    def test_les_quantites_de_lecran_sont_celles_qui_sont_ecrites(self):
        decided: list[tuple[str, Decimal, str]] = []
        service = arbitration_service(
            [arbitration("VIS", "10", "12"), arbitration("ROTOR", "50", "60")], decided
        )
        out = service.decide_many(
            CAMPAIGN, "z-1",
            {"a-VIS": Decimal("11"), "a-ROTOR": Decimal("55")},
        )
        assert out == {"decided": 2, "skipped": 0}
        assert {(aid, qty) for aid, qty, _ in decided} == {
            ("a-VIS", Decimal("11")), ("a-ROTOR", Decimal("55")),
        }

    def test_une_ligne_sans_quantite_reste_ouverte_et_le_compte_le_dit(self):
        decided: list[tuple[str, Decimal, str]] = []
        service = arbitration_service(
            [arbitration("VIS", "10", "12"), arbitration("ROTOR", "50", "60")], decided
        )
        out = service.decide_many(CAMPAIGN, "z-1", {"a-VIS": Decimal("11")})
        assert out == {"decided": 1, "skipped": 1}

    def test_une_ligne_deja_tranchee_nest_pas_retouchee(self):
        """Un lot ne défait pas un jugement pris une par une."""
        decided: list[tuple[str, Decimal, str]] = []
        service = arbitration_service(
            [arbitration("ROTOR", "50", "60", decided=True)], decided
        )
        out = service.decide_many(CAMPAIGN, "z-1", {"a-ROTOR": Decimal("99")})
        assert decided == []
        assert out == {"decided": 0, "skipped": 0}

    def test_une_ligne_en_accord_nappelle_aucune_decision(self):
        decided: list[tuple[str, Decimal, str]] = []
        service = arbitration_service([arbitration("VIS", "100", "100")], decided)
        assert service.decide_many(CAMPAIGN, "z-1", {}) == {"decided": 0, "skipped": 0}
        assert decided == []

    def test_une_quantite_negative_est_refusee_comme_ligne_a_ligne(self):
        decided: list[tuple[str, Decimal, str]] = []
        service = arbitration_service([arbitration("ROTOR", "50", "60")], decided)
        out = service.decide_many(CAMPAIGN, "z-1", {"a-ROTOR": Decimal("-1")})
        assert out == {"decided": 0, "skipped": 1}
        assert decided == []

    def test_un_arbitrage_dune_autre_zone_est_refuse(self):
        """Les identifiants arrivent dans le corps ; c'est ici qu'on vérifie
        qu'ils désignent bien la zone de l'URL."""
        from inventory.errors import NotFoundError

        service = arbitration_service([arbitration("ROTOR", "50", "60")], [])
        with pytest.raises(NotFoundError):
            service.decide_many(CAMPAIGN, "z-1", {"a-ETRANGER": Decimal("1")})


# --------------------------------------------------------------------------- #
# 3. La décomposition du journal consolidé
# --------------------------------------------------------------------------- #

def analysis_service(*, zone_lines, items=None, wip_rows=()):
    """Un service d'analyse avec une zone à deux passages et rien d'autre."""
    from inventory.services.analysis_service import AnalysisService

    zone = Zone(id="z-1", campaign_id="camp-1", code="B15", label="Stock B15", passes=2)
    sheets = [
        CountSheet(id="s-1", campaign_id="camp-1", zone_id="z-1",
                   pass_no=SheetPass.PASS_1),
        CountSheet(id="s-2", campaign_id="camp-1", zone_id="z-1",
                   pass_no=SheetPass.PASS_2),
    ]
    ctx = cast(Any, SimpleNamespace(
        actor="chef@usine",
        referentials=SimpleNamespace(items_by_number=lambda cid: items or {}),
        # Un autre emplacement porte aussi la référence : c'est lui qui
        # s'affichait à tort dans la décomposition du journal consolidé, et
        # sans lui la mutation « rouvrir la décomposition de toute la
        # campagne » ne changerait rien de visible.
        journals=SimpleNamespace(counted_quantities=lambda cid: [
            {"item_number": "VIS", "warehouse_id": "ATP",
             "location_id": "SF2", "qty": Decimal("13")},
        ]),
        sheets=SimpleNamespace(
            list_zones=lambda cid, **kw: [zone],
            list_sheets=lambda cid, **kw: sheets,
            lines_by_sheet=lambda cid, **kw: zone_lines,
            list_arbitrations=lambda cid, **kw: [],
        ),
        consolidation=SimpleNamespace(
            wip_breakdown=lambda cid, child_item=None: list(wip_rows)
        ),
    ))
    with_transactions(ctx)
    return AnalysisService(ctx)


def sheet_line(sheet_id: str, item: str, qty: str, section=CountSection.LINE_SIDE):
    return CountSheetLine(
        id=f"{sheet_id}-{item}", sheet_id=sheet_id, campaign_id="camp-1",
        item_number=item, section=section, qty_manual=Decimal(qty),
    )


class TestLaDecompositionEstParZone:
    def test_les_deux_passages_ne_font_quune_ligne(self):
        """C'est la même quantité, comptée deux fois par deux équipes : la
        décomposition en montrait deux, et leur somme n'était le total de rien."""
        service = analysis_service(zone_lines={
            "s-1": [sheet_line("s-1", "VIS", "60050")],
            "s-2": [sheet_line("s-2", "VIS", "60050")],
        })
        rows = service._sheet_rows(CAMPAIGN, "VIS", "LINE_SIDE")
        assert len(rows) == 1
        assert rows[0]["qty"] == 60050.0
        assert rows[0]["origin"] == "Stock B15"

    def test_un_desaccord_montre_la_quantite_retenue(self):
        """Le second passage est le plus tardif ; c'est lui qui est retenu tant
        qu'un arbitrage n'a pas dit autre chose."""
        service = analysis_service(zone_lines={
            "s-1": [sheet_line("s-1", "VIS", "100")],
            "s-2": [sheet_line("s-2", "VIS", "90")],
        })
        rows = service._sheet_rows(CAMPAIGN, "VIS", "LINE_SIDE")
        assert [r["qty"] for r in rows] == [90.0]

    def test_un_desaccord_non_tranche_est_annonce(self):
        service = analysis_service(zone_lines={
            "s-1": [sheet_line("s-1", "VIS", "100")],
            "s-2": [sheet_line("s-2", "VIS", "90")],
        })
        rows = service._sheet_rows(CAMPAIGN, "VIS", "LINE_SIDE")
        assert "arbitrage en attente" in rows[0]["detail"]

    def test_la_section_demandee_est_la_seule_lue(self):
        service = analysis_service(zone_lines={
            "s-1": [
                sheet_line("s-1", "VIS", "10"),
                sheet_line("s-1", "VIS", "3", CountSection.WIP_OK),
            ],
            "s-2": [
                sheet_line("s-2", "VIS", "10"),
                sheet_line("s-2", "VIS", "3", CountSection.WIP_OK),
            ],
        })
        assert [r["qty"] for r in service._sheet_rows(CAMPAIGN, "VIS", "WIP_OK")] == [3.0]


class TestLeTotalConsolideNeCompteQueGenerique:
    LINES: ClassVar[dict] = {
        "s-1": [sheet_line("s-1", "VIS", "60050")],
        "s-2": [sheet_line("s-2", "VIS", "60050")],
    }

    def test_seules_les_zones_generique_apparaissent(self):
        """Les treize pièces d'un autre emplacement ne sont pas dans le journal
        consolidé ; les afficher dans sa décomposition expliquait un chiffre par
        des quantités qui n'y sont pas.

        Par ``breakdown()`` et non par la fonction interne : c'est le chemin que
        l'écran emprunte, et brancher l'aspect sur la mauvaise fonction est
        exactement la faute que ce contrôle doit voir.
        """
        service = analysis_service(
            zone_lines=self.LINES,
            items={"VIS": Item(campaign_id="camp-1", item_number="VIS", name="Vis",
                               std_price=Decimal("2"))},
        )
        out = service.breakdown(CAMPAIGN, "VIS", "generic")
        assert {r["locationId"] for r in out["rows"]} == {"GENERIQUE"}
        assert out["total"] == 60050.0

    def test_laspect_quantite_comptee_garde_sa_portee(self):
        """C'est le total du journal consolidé qui n'avait rien à faire là ;
        « quantité comptée », lui, doit continuer de couvrir la campagne."""
        from inventory.services.analysis_service import AnalysisService

        source = __import__("inspect").getsource(AnalysisService.breakdown)
        assert '"counted": self._counted_rows' in source
        assert '"generic": self._generic_rows' in source

    def test_la_section_est_dite_dans_le_detail(self):
        service = analysis_service(zone_lines=self.LINES)
        rows = service._generic_rows(CAMPAIGN, "VIS")
        assert "Bord de ligne" in rows[0]["detail"]

    def test_un_article_exclu_du_perimetre_generique_ne_rend_rien(self):
        service = analysis_service(
            zone_lines=self.LINES,
            items={"VIS": Item(
                campaign_id="camp-1", item_number="VIS", name="Vis",
                exclusions={ExclusionScope.GENERIC},
            )},
        )
        assert service._generic_rows(CAMPAIGN, "VIS") == []

    def test_un_produit_fini_nentre_que_par_la_porte_du_wip(self):
        """Compté en bord de ligne, il compterait une deuxième fois ce que ses
        composants comptent déjà — la consolidation l'écarte, la décomposition
        doit l'écarter aussi."""
        service = analysis_service(
            zone_lines=self.LINES,
            items={"VIS": Item(
                campaign_id="camp-1", item_number="VIS", name="Moteur",
                item_type=ItemType.FINISHED,
            )},
        )
        assert service._generic_rows(CAMPAIGN, "VIS") == []

    def test_leclatement_du_wip_est_repris(self):
        service = analysis_service(
            zone_lines={"s-1": [], "s-2": []},
            wip_rows=[{
                "parent_item": "SF-10", "zone_code": "B15",
                "parent_qty": 5, "qty_per": 4, "child_qty": 20,
            }],
        )
        rows = service._generic_rows(CAMPAIGN, "VIS")
        assert [r["qty"] for r in rows] == [20.0]
        assert rows[0]["origin"] == "SF-10"


class TestLAspectEstDeclare:
    def test_le_serveur_le_connait(self):
        from inventory.services import AnalysisService

        assert "generic" in AnalysisService.BREAKDOWN_ASPECTS

    def test_lecran_du_journal_consolide_louvre(self):
        """« Exists but not wired » : l'aspect a existé sans être atteignable."""
        from pathlib import Path

        screen = (
            Path(__file__).resolve().parent.parent
            / "frontend" / "src" / "features" / "generic.consolidation.tsx"
        ).read_text(encoding="utf-8")
        assert "aspect: 'generic'" in screen
        assert "aspect: 'counted'" not in screen
