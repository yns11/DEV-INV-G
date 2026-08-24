"""Une opération saisie ou scannée devient une quantité — si la campagne le veut.

L'évaluateur a ses propres contrôles ; ce qui se vérifie ici est la **chaîne** :
le réglage porté par la campagne, la quantité qui atterrit dans la ligne, et le
texte d'origine conservé à côté d'elle.

C'est la moitié qui manquait la dernière fois qu'une fonctionnalité de ce dépôt
n'a jamais fonctionné : les deux bouts étaient corrects et rien ne vérifiait
qu'ils se parlaient.

**Le texte d'origine est ce qui sépare cette fonctionnalité d'une
calculatrice.** Sans lui, « 151 » calculé et « 151 » tapé sont identiques en
base, et le comptage cesse d'être recomptable — on ne peut plus s'apercevoir
qu'une palette n'en contenait que quarante-six.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_transactions

from inventory.domain.enums import CampaignStatus, CountSection
from inventory.domain.models import Campaign, CampaignConfig, CountSheetLine
from inventory.errors import ValidationError


def campaign(*, allow_formulas: bool, status=CampaignStatus.COUNTING) -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date=dt.date(2026, 9, 1), status=status,
        created_by="chef@usine",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        config=CampaignConfig(allow_formulas=allow_formulas),
    )


def service(*, existing: list[CountSheetLine] | None = None, allow_negative=False):
    """Le vrai service, avec le strict nécessaire autour et les lignes écrites
    récupérables : c'est ce qui atterrit en base qui est en question."""
    from inventory.services.generic_service import GenericService

    written: list[CountSheetLine] = []
    sheet = SimpleNamespace(
        id="sheet-1", campaign_id="camp-1", zone_id="zone-1", version=1,
    )
    zone = SimpleNamespace(
        id="zone-1", code="Z1", allow_negative=allow_negative, passes=1,
    )
    ctx = cast(Any, SimpleNamespace(
        actor="chef@usine",
        request_id="req-1",
        # La matrice de gel a ses propres contrôles ; ce qui est en question ici
        # est la conversion des quantités.
        guard=lambda campaign, aspect: None,
        record=lambda **kw: None,
        sheets=SimpleNamespace(
            get_sheet=lambda sid: sheet,
            list_zones=lambda cid: [zone],
            list_sheet_lines=lambda sid: list(existing or []),
            upsert_sheet_lines=lambda lines, actor, conn=None: (
                written.extend(lines) or len(lines)
            ),
            replace_sheet_lines=lambda sid, lines, actor, conn=None: (
                written.extend(lines) or len(lines)
            ),
        ),
    ))
    with_transactions(ctx)
    instance = GenericService(ctx)
    instance.refresh_arbitrations = (  # type: ignore[method-assign]
        lambda campaign, zone_id: None
    )
    return instance, written


def row(qty: Any, *, line_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "item_number": "P-00001", "section": "LINE_SIDE", "qty": qty, "unit": "PCE",
    }
    if line_id:
        out["id"] = line_id
    return out


class TestASheetEntry:
    def test_une_operation_devient_sa_valeur(self):
        instance, written = service()

        instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
            row("3*48+7")
        ])

        assert written[0].qty_manual == Decimal("151.000000")

    def test_le_texte_d_origine_est_conserve(self):
        """C'est ce qui rend le comptage recomptable six mois plus tard."""
        instance, written = service()

        instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
            row("3*48+7")
        ])

        assert written[0].qty_formula == "3*48+7"

    def test_un_nombre_ne_laisse_pas_de_formule_derriere_lui(self):
        instance, written = service()

        instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
            row("151")
        ])

        assert written[0].qty_manual == Decimal("151.000000")
        assert written[0].qty_formula == ""

    def test_le_reglage_eteint_refuse_en_nommant_la_reference_et_le_reglage(self):
        """Deux choses à dire : quelle ligne, et quoi faire. Sur une feuille de
        cent lignes, ni l'une ni l'autre ne se devine."""
        instance, written = service()

        with pytest.raises(ValidationError) as refusal:
            instance.upsert_sheet_lines(campaign(allow_formulas=False), "sheet-1", [
                row("3*48+7")
            ])

        message = str(refusal.value)
        assert "P-00001" in message
        assert "Accepter des formules dans les comptages" in message
        assert written == [], "rien ne doit être écrit quand la ligne est refusée"

    def test_le_reglage_eteint_laisse_passer_les_nombres(self):
        instance, written = service()

        instance.upsert_sheet_lines(campaign(allow_formulas=False), "sheet-1", [
            row("151")
        ])

        assert written[0].qty_manual == Decimal("151.000000")

    def test_une_case_vide_reste_vide(self):
        """Vide ≠ zéro, quel que soit le réglage."""
        instance, written = service()

        instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
            row("")
        ])

        assert written[0].qty_manual is None
        assert written[0].qty_formula == ""

    def test_un_texte_qui_n_est_pas_une_quantite_est_refuse_en_le_citant(self):
        instance, _ = service()

        with pytest.raises(ValidationError) as refusal:
            instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
                row("douze")
            ])

        assert "douze" in str(refusal.value)

    def test_la_garde_des_quantites_negatives_s_applique_au_resultat(self):
        """Sinon « 10-14 » contournerait une règle que « -4 » respecte."""
        instance, _ = service(allow_negative=False)

        with pytest.raises(ValidationError) as refusal:
            instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
                row("10-14")
            ])

        assert "négative" in str(refusal.value)

    def test_une_zone_qui_les_autorise_accepte_le_resultat_negatif(self):
        instance, written = service(allow_negative=True)

        instance.upsert_sheet_lines(campaign(allow_formulas=True), "sheet-1", [
            row("10-14")
        ])

        assert written[0].qty_manual == Decimal("-4.000000")


class TestWhatCountsAsAChangedQuantity:
    """La garde de phase se décide avant que l'opération soit évaluée.

    Une campagne dont les comptages sont gelés doit répondre « c'est gelé »
    plutôt que de discuter la syntaxe de ce qu'on tente d'y écrire.
    """

    def previous(self, qty: str | None, formula: str = "") -> CountSheetLine:
        return CountSheetLine(
            id="ligne-1", sheet_id="sheet-1", campaign_id="camp-1",
            item_number="P-00001", section=CountSection.LINE_SIDE,
            qty_manual=None if qty is None else Decimal(qty),
            qty_formula=formula,
        )

    def guards(self, rows, existing) -> list[str]:
        """Les aspects que le service a fait vérifier."""
        from inventory.services.generic_service import GenericService

        asked: list[str] = []
        instance, _ = service(existing=existing)
        instance.ctx.guard = lambda campaign, aspect: asked.append(aspect)
        GenericService.upsert_sheet_lines(
            instance, campaign(allow_formulas=True), "sheet-1", rows
        )
        return asked

    def test_ecrire_une_operation_est_un_comptage(self):
        assert "count_entries" in self.guards(
            [row("3*48+7", line_id="ligne-1")], [self.previous(None)]
        )

    def test_renvoyer_la_meme_operation_n_en_est_pas_un(self):
        """L'écran de préparation renvoie ce qu'on lui a donné : traiter cet
        écho comme un comptage gèlerait la liste qu'on est en train de bâtir."""
        assert "count_entries" not in self.guards(
            [row("3*48+7", line_id="ligne-1")],
            [self.previous("151", formula="3*48+7")],
        )

    def test_changer_l_operation_en_est_un(self):
        assert "count_entries" in self.guards(
            [row("3*48+9", line_id="ligne-1")],
            [self.previous("151", formula="3*48+7")],
        )

    def test_effacer_une_quantite_en_est_un(self):
        assert "count_entries" in self.guards(
            [row("", line_id="ligne-1")], [self.previous("151")]
        )

    def test_renvoyer_une_case_deja_vide_n_en_est_pas_un(self):
        assert "count_entries" not in self.guards(
            [row("", line_id="ligne-1")], [self.previous(None)]
        )


class TestAScannedSheet:
    """Le modèle rend la case telle qu'il l'a lue ; le réglage décide."""

    def clean(self, value: Any, *, allow: bool):
        from inventory.ai.sheet_extraction import _clean_qty

        return _clean_qty(value, allow_formulas=allow)

    def test_une_operation_lue_devient_sa_valeur_et_garde_son_texte(self):
        assert self.clean("3*48+7", allow=True) == (Decimal("151"), "3*48+7")

    def test_un_nombre_lu_ne_laisse_pas_de_formule(self):
        assert self.clean(151, allow=True) == (Decimal("151"), "")
        assert self.clean("151", allow=True) == (Decimal("151"), "")

    def test_le_reglage_eteint_rend_une_case_vide_et_ne_leve_pas(self):
        """Une lecture ne refuse jamais : lever ferait échouer les cent autres
        lignes de la feuille pour une case que quelqu'un ira remplir à
        l'écran."""
        assert self.clean("3*48+7", allow=False) == (None, "")

    def test_une_operation_illisible_rend_une_case_vide(self):
        """Comme un gribouillis : ce n'est pas au modèle de faire échouer la
        feuille sur ce qu'il n'a pas su lire."""
        assert self.clean("3*+", allow=True) == (None, "")
        assert self.clean("douze", allow=True) == (None, "")

    @pytest.mark.parametrize("blank", [None, "", "null", "none", "-", "  "])
    def test_une_case_vide_le_reste(self, blank):
        assert self.clean(blank, allow=True) == (None, "")

    def test_une_tentative_d_injection_rend_une_case_vide(self):
        """Le texte vient d'un modèle qui a lu une image : il n'est pas plus
        digne de confiance qu'un formulaire."""
        assert self.clean("__import__('os').system('ls')", allow=True) == (None, "")
