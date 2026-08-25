"""Preparation controls on the GENERIQUE zones."""

from __future__ import annotations

import datetime as dt
import itertools

from inventory.domain.controls import check_zones
from inventory.domain.enums import SheetPass
from inventory.domain.models import CountSheet, CountSheetLine, Zone

_ids = itertools.count(1)
next_id = lambda: f"id-{next(_ids)}"


def zone(**kwargs) -> Zone:
    return Zone(id=next_id(), campaign_id="c", code=kwargs.pop("code", "Z1"), **kwargs)


def sheets_of(z: Zone, *passes: SheetPass) -> list[CountSheet]:
    return [
        CountSheet(id=next_id(), campaign_id="c", zone_id=z.id, pass_no=p)
        for p in passes
    ]


def line(sheet_id: str) -> CountSheetLine:
    return CountSheetLine(
        id=next_id(), sheet_id=sheet_id, campaign_id="c", item_number="P-1"
    )


class TestMissingArticleList:
    def test_a_zone_with_no_pre_printed_line_is_flagged(self):
        z = zone()
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1,
                                                           SheetPass.PASS_2))
        assert [f.code for f in findings] == ["ZONE_WITHOUT_LINES"]

    def test_a_free_entry_zone_is_not(self):
        """A deliberately blank sheet is not an unprepared one."""
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1,
                                                           SheetPass.PASS_2))
        assert findings == []

    def test_a_zone_carrying_lines_is_not(self):
        z = zone()
        sheets = sheets_of(z, SheetPass.PASS_1, SheetPass.PASS_2)
        findings = check_zones(
            zones=[z], sheets=sheets, lines_by_sheet={sheets[0].id: [line(sheets[0].id)]}
        )
        assert findings == []


class TestMissingSheets:
    def test_a_two_pass_zone_missing_its_second_sheet_is_flagged(self):
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1))
        assert [f.code for f in findings] == ["ZONE_MISSING_SHEET"]

    def test_a_single_pass_zone_with_one_sheet_is_complete(self):
        z = zone(passes=1, free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1))
        assert findings == []

    def test_a_zone_with_no_sheet_at_all_is_flagged(self):
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=[])
        assert [f.code for f in findings] == ["ZONE_MISSING_SHEET"]


class TestAnExcludedArticleCountedOnASheet:
    """La contrepartie de son retrait des écarts.

    Un article exclu du périmètre ne produit aucun écart : c'est ce que
    l'exclusion veut dire, et c'est ce que `build_variances` applique. Mais
    quelqu'un a bel et bien compté cette zone, et sa quantité existe. La faire
    disparaître du calcul **sans le dire** serait la troncature muette que ce
    projet refuse partout ailleurs : ou bien l'exclusion est une erreur et il
    faut la lever, ou bien c'est le comptage, et il faut retirer la ligne — mais
    les deux se décident en le sachant.

    Le contrôle jumeau existait pour les **journaux** depuis toujours ; les
    feuilles GENERIQUE ne l'avaient pas, alors que ce sont elles qu'on remplit à
    la main, et donc elles qui portent le plus volontiers un article que le
    référentiel a mis hors périmètre depuis.
    """

    def items(self, *excluded: str):
        from inventory.domain.enums import ExclusionScope
        from inventory.domain.models import Item

        return {
            n: Item(
                campaign_id="c", item_number=n, std_price="10",
                exclusions={ExclusionScope.ALL} if n in excluded else set(),
            )
            for n in ("P-1", "P-2", *excluded)
        }

    def counted_line(self, sheet_id: str, item: str, qty="33980") -> CountSheetLine:
        return CountSheetLine(
            id=next_id(), sheet_id=sheet_id, campaign_id="c",
            item_number=item, qty_manual=qty,
        )

    def run(self, *, item: str, excluded: tuple[str, ...] = (), qty="33980"):
        z = zone(code="B12")
        sheet = sheets_of(z, SheetPass.PASS_1)[0]
        return check_zones(
            zones=[z],
            sheets=[sheet],
            lines_by_sheet={sheet.id: [self.counted_line(sheet.id, item, qty)]},
            items=self.items(*excluded),
        )

    def codes(self, findings) -> list[str]:
        return [f.code for f in findings]

    def test_il_est_signale(self):
        assert "EXCLUDED_ITEM_COUNTED" in self.codes(
            self.run(item="X-1", excluded=("X-1",))
        )

    def test_un_article_du_perimetre_ne_l_est_pas(self):
        assert "EXCLUDED_ITEM_COUNTED" not in self.codes(self.run(item="P-1"))

    def test_le_constat_nomme_la_reference_et_la_zone(self):
        """« Un article exclu a été compté quelque part » n'aide personne."""
        finding = next(
            f for f in self.run(item="X-1", excluded=("X-1",))
            if f.code == "EXCLUDED_ITEM_COUNTED"
        )

        assert finding.item_number == "X-1"
        assert "B12" in finding.message

    def test_il_dit_les_deux_gestes_possibles(self):
        """L'exclusion peut être l'erreur, le comptage aussi."""
        finding = next(
            f for f in self.run(item="X-1", excluded=("X-1",))
            if f.code == "EXCLUDED_ITEM_COUNTED"
        )

        assert "grille Articles" in finding.message
        assert "retirez la ligne" in finding.message
        assert "aucun écart" in finding.message

    def test_une_ligne_pre_imprimee_non_comptee_ne_dit_rien(self):
        """La feuille peut lister un article exclu sans qu'on l'ait compté :
        c'est une liste préparée avant l'exclusion, pas une quantité perdue."""
        z = zone(code="B12")
        sheet = sheets_of(z, SheetPass.PASS_1)[0]

        findings = check_zones(
            zones=[z], sheets=[sheet],
            lines_by_sheet={sheet.id: [
                CountSheetLine(id=next_id(), sheet_id=sheet.id, campaign_id="c",
                               item_number="X-1")
            ]},
            items=self.items("X-1"),
        )

        assert "EXCLUDED_ITEM_COUNTED" not in self.codes(findings)

    def test_le_meme_article_sur_deux_passages_ne_fait_qu_un_constat(self):
        """C'est un seul fait à trancher. Deux lignes pousseraient le reste des
        constats hors de l'écran, sur une zone à double comptage."""
        z = zone(code="B12")
        first, second = sheets_of(z, SheetPass.PASS_1, SheetPass.PASS_2)

        findings = check_zones(
            zones=[z], sheets=[first, second],
            lines_by_sheet={
                first.id: [self.counted_line(first.id, "X-1")],
                second.id: [self.counted_line(second.id, "X-1")],
            },
            items=self.items("X-1"),
        )

        assert self.codes(findings).count("EXCLUDED_ITEM_COUNTED") == 1

    def test_sans_referentiel_les_autres_controles_tournent_quand_meme(self):
        """Un appelant qui n'a pas chargé les articles ne doit pas payer leur
        chargement pour un troisième constat."""
        z = zone(code="B12")
        sheet = sheets_of(z, SheetPass.PASS_1)[0]

        findings = check_zones(
            zones=[z], sheets=[sheet],
            lines_by_sheet={sheet.id: [self.counted_line(sheet.id, "X-1")]},
        )

        assert "EXCLUDED_ITEM_COUNTED" not in self.codes(findings)


class TestTheSheetControlIsReachedFromTheScreen:
    """Le contrôle existe-t-il *dans l'écran*, ou seulement dans ce module ?

    Écrit après coup, parce que la vérification par mutation l'a réclamé :
    cesser de passer le référentiel à `check_zones` depuis le service ne faisait
    tomber aucun contrôle. Le constat aurait été parfaitement écrit et jamais
    émis — et comme il est la contrepartie du retrait des articles exclus du
    calcul des écarts, sa disparition aurait rendu ce retrait silencieux.

    C'est le troisième défaut de cette forme dans ce dépôt. Un contrôle qui
    n'atteint pas l'écran est un contrôle qui n'existe pas.
    """

    def findings(self, *, items, lines):
        from types import SimpleNamespace
        from typing import Any, cast

        from inventory.domain.enums import CampaignStatus
        from inventory.domain.models import Campaign
        from inventory.services.analysis_service import AnalysisService

        z = zone(code="B12")
        sheet = sheets_of(z, SheetPass.PASS_1)[0]
        ctx = cast(Any, SimpleNamespace(
            referentials=SimpleNamespace(
                items_by_number=lambda cid: items,
                list_bom_links=lambda cid: [],
            ),
            imports=SimpleNamespace(latest_per_target=lambda cid: []),
            sheets=SimpleNamespace(
                list_zones=lambda cid: [z],
                list_sheets=lambda cid: [sheet],
                lines_by_sheet=lambda cid: {sheet.id: lines(sheet.id)},
            ),
            book_stock=SimpleNamespace(list=lambda cid: []),
        ))
        campaign = Campaign(
            id="c", code="INV-2026", label="Inventaire",
            count_date="2026-09-01", status=CampaignStatus.COUNTING,
            created_by="chef@usine",
            created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        )
        return AnalysisService(ctx)._all_findings(campaign)

    def test_l_ecran_signale_un_article_exclu_compte(self):
        from inventory.domain.enums import ExclusionScope
        from inventory.domain.models import Item

        items = {
            "X-1": Item(
                campaign_id="c", item_number="X-1", std_price="10",
                exclusions={ExclusionScope.ALL},
            )
        }
        findings = self.findings(
            items=items,
            lines=lambda sid: [
                CountSheetLine(id=next_id(), sheet_id=sid, campaign_id="c",
                               item_number="X-1", qty_manual="33980")
            ],
        )

        assert [f.item_number for f in findings if f.code == "EXCLUDED_ITEM_COUNTED"] == [
            "X-1"
        ]

    def test_et_ne_dit_rien_d_un_article_du_perimetre(self):
        from inventory.domain.models import Item

        items = {"P-1": Item(campaign_id="c", item_number="P-1", std_price="10")}
        findings = self.findings(
            items=items,
            lines=lambda sid: [
                CountSheetLine(id=next_id(), sheet_id=sid, campaign_id="c",
                               item_number="P-1", qty_manual="12")
            ],
        )

        assert not [f for f in findings if f.code == "EXCLUDED_ITEM_COUNTED"]
