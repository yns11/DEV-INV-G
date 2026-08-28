"""Les décisions que portent les modèles des comptages avancés.

Chaque contrôle ici épingle un choix qui, pris autrement, produirait une erreur
silencieuse : un identifiant normalisé comme une clé métier, une référence
absente confondue avec une référence nulle, une sous-phase stockée deux fois.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from inventory.domain.enums import (
    CampaignStatus,
    CountingStage,
    DriftResolution,
    JournalKind,
)
from inventory.domain.models import (
    Campaign,
    CampaignConfig,
    CountJournal,
    CountJournalLine,
    EarlyCountBatch,
    EarlyCountDrift,
    ErpJournal,
    ErpJournalLine,
    LocationKey,
)


def _campaign(**kwargs) -> Campaign:
    base = {
        "id": "c1",
        "code": "INV-2026-06",
        "label": "Juin",
        "count_date": dt.date(2026, 6, 13),
        "created_by": "test",
        "created_at": dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    }
    return Campaign(**{**base, **kwargs})


def _erp_line(**kwargs) -> ErpJournalLine:
    base = {
        "id": "l1",
        "erp_journal_id": "j1",
        "campaign_id": "c1",
        "warehouse_id": "ATP",
        "location_id": "SOL",
        "item_number": "MASS-1",
    }
    return ErpJournalLine(**{**base, **kwargs})


class TestTheBufferIsConfigured:
    """`INV / 01` est une donnée de campagne, pas une constante dans le code."""

    def test_the_default_names_the_erp_buffer(self):
        assert CampaignConfig().buffer_key == LocationKey(
            warehouse_id="INV", location_id="01"
        )

    def test_it_can_be_moved_without_touching_the_code(self):
        config = CampaignConfig(buffer_warehouse="tampon", buffer_location="zz")
        assert config.buffer_key == LocationKey(
            warehouse_id="TAMPON", location_id="ZZ"
        )

    def test_it_is_normalised_like_any_other_location(self):
        """Sinon « inv » et « INV » désigneraient deux emplacements différents."""
        assert CampaignConfig(buffer_warehouse="  inv ").buffer_warehouse == "INV"

    def test_the_buffer_is_not_the_generic_location(self):
        config = CampaignConfig()
        assert config.buffer_key != config.generic_key


class TestTheCountingStageIsDerived:
    """La sous-phase se déduit du jalon : deux stockages finiraient par diverger."""

    def test_outside_counting_the_question_does_not_arise(self):
        for status in (CampaignStatus.PREPARATION, CampaignStatus.ANALYSIS,
                       CampaignStatus.CLOSED):
            assert _campaign(status=status).counting_stage is CountingStage.NOT_COUNTING

    def test_counting_without_the_milestone_is_the_early_stage(self):
        campaign = _campaign(status=CampaignStatus.COUNTING)
        assert campaign.counting_stage is CountingStage.EARLY

    def test_the_milestone_opens_the_general_count(self):
        campaign = _campaign(
            status=CampaignStatus.COUNTING,
            general_count_opened_at=dt.datetime(2026, 6, 13, 6, tzinfo=dt.UTC),
        )
        assert campaign.counting_stage is CountingStage.GENERAL

    def test_the_milestone_alone_does_not_make_a_campaign_count(self):
        """Une campagne close garde son jalon : il ne doit pas la rouvrir."""
        campaign = _campaign(
            status=CampaignStatus.CLOSED,
            general_count_opened_at=dt.datetime(2026, 6, 13, 6, tzinfo=dt.UTC),
        )
        assert campaign.counting_stage is CountingStage.NOT_COUNTING


class TestAnIdentifierIsTransportedNotNormalised:
    """« 001609231 » perd trois caractères dès qu'on le traite comme un nombre.

    Et les clés métier de l'application, elles, sont majusculées et recollées :
    appliquer ce traitement à une étiquette la rendrait introuvable dans l'ERP.
    """

    def test_leading_zeros_survive(self):
        assert _erp_line(label_id="001609231").label_id == "001609231"

    def test_the_case_is_left_alone(self):
        line = _erp_line(label_id="ab00cd", serial_number="t12611100220")
        assert line.label_id == "ab00cd"
        assert line.serial_number == "t12611100220"

    def test_inner_spaces_are_left_alone(self):
        """Recoller les espaces est bon pour « PAL B2S  01 », pas pour une étiquette."""
        assert _erp_line(label_id="00 16  09").label_id == "00 16  09"

    def test_only_the_surrounding_whitespace_goes(self):
        assert _erp_line(serial_number="  T126  ").serial_number == "T126"

    def test_a_missing_identifier_is_the_empty_string_not_none(self):
        assert _erp_line(label_id=None).label_id == ""

    def test_the_location_keys_are_still_normalised(self):
        """La règle change pour les identifiants, pas pour les clés métier."""
        line = _erp_line(warehouse_id=" atp ", location_id="stk  p fi")
        assert line.warehouse_id == "ATP"
        assert line.location_id == "STK P FI"


class TestALineVarianceIsNotAnAnomaly:
    """Un moins ici et un plus là-bas, c'est une pièce qui a bougé."""

    def test_a_departure_is_negative(self):
        assert _erp_line(qty_on_hand=1, qty_counted=0).variance_qty == Decimal("-1")

    def test_an_arrival_is_positive(self):
        assert _erp_line(qty_on_hand=0, qty_counted=1).variance_qty == Decimal("1")

    def test_a_matching_line_has_no_variance(self):
        assert _erp_line(qty_on_hand=52800, qty_counted=52800).variance_qty == 0


class TestTheScopeIsDeclaredNotDeduced:
    def test_a_journal_without_a_declared_scope_says_so(self):
        journal = ErpJournal(id="j1", campaign_id="c1", journal_number="NPEM-1")
        assert journal.scope_declared is False

    def test_a_journal_may_cover_several_locations_of_one_warehouse(self):
        journal = ErpJournal(
            id="j1", campaign_id="c1", journal_number="NPEM-1",
            kind=JournalKind.INVE,
            scope=[
                LocationKey(warehouse_id="ATP", location_id="SOL"),
                LocationKey(warehouse_id="ATP", location_id="STK P FI"),
            ],
            scope_declared_at=dt.datetime(2026, 6, 11, tzinfo=dt.UTC),
        )
        assert journal.scope_declared is True
        assert journal.warehouses == {"ATP"}
        assert journal.covers(LocationKey(warehouse_id="ATP", location_id="SOL"))

    def test_a_location_outside_the_scope_is_not_covered(self):
        """C'est ce qui distingue une ligne comptée d'une ligne de passage."""
        journal = ErpJournal(
            id="j1", campaign_id="c1", journal_number="NPEM-1",
            scope=[LocationKey(warehouse_id="ATP", location_id="SOL")],
        )
        assert not journal.covers(
            LocationKey(warehouse_id="QUAL", location_id="APQP C0")
        )


class TestAnAbsentReferenceIsNotAZeroReference:
    """`None` dit « l'ERP n'en sait rien », `0` dit « l'ERP annonce zéro »."""

    def _line(self, **kwargs) -> CountJournalLine:
        base = {"id": "l1", "journal_id": "j1", "campaign_id": "c1",
                "item_number": "MASS-1"}
        return CountJournalLine(**{**base, **kwargs})

    def test_by_default_there_is_no_reference(self):
        assert self._line().qty_on_hand is None

    def test_zero_is_kept_as_zero(self):
        line = self._line(qty_on_hand=0)
        assert line.qty_on_hand == 0
        assert line.qty_on_hand is not None

    def test_an_empty_string_means_absent(self):
        """Une cellule vide d'export n'annonce pas zéro."""
        assert self._line(qty_on_hand="").qty_on_hand is None


class TestSealing:
    def test_a_journal_is_open_until_it_is_sealed(self):
        journal = CountJournal(
            id="j1", campaign_id="c1", warehouse_id="ATP", location_id="SOL"
        )
        assert journal.is_sealed is False

    def test_sealing_is_the_presence_of_a_date(self):
        journal = CountJournal(
            id="j1", campaign_id="c1", warehouse_id="ATP", location_id="SOL",
            sealed_at=dt.datetime(2026, 6, 11, 17, tzinfo=dt.UTC), sealed_by="alice",
        )
        assert journal.is_sealed is True

    def test_a_batch_is_closed_and_sealed_separately(self):
        """Clore et sceller sont deux gestes : on clôt pour arrêter d'ajouter,
        on scelle pour arrêter de modifier."""
        batch = EarlyCountBatch(
            id="b1", campaign_id="c1", code="lot-j2",
            closed_at=dt.datetime(2026, 6, 11, 16, tzinfo=dt.UTC),
        )
        assert batch.is_closed is True
        assert batch.is_sealed is False


class TestTheDrift:
    def _drift(self, **kwargs) -> EarlyCountDrift:
        base = {
            "id": "d1", "campaign_id": "c1", "warehouse_id": "ATP",
            "location_id": "SOL", "item_number": "MASS-1",
        }
        return EarlyCountDrift(**{**base, **kwargs})

    def test_the_nominal_case_is_a_null_drift(self):
        """Le balisage a tenu : l'ERP du jour J vaut le physique posté."""
        drift = self._drift(qty_erp_t0=10, qty_physical_t0=12, qty_erp_j=12)
        assert drift.drift_qty == 0

    def test_the_drift_is_measured_against_the_physical_not_the_reference(self):
        """Contre `ERP@T0`, cette dérive vaudrait 2 : c'est l'écart d'inventaire,
        pas une dérive, et les confondre ferait crier au scellement rompu sur
        chaque emplacement qui a un écart."""
        drift = self._drift(qty_erp_t0=10, qty_physical_t0=12, qty_erp_j=12)
        assert drift.qty_erp_j - drift.qty_erp_t0 == 2
        assert drift.drift_qty == 0

    def test_a_movement_after_sealing_shows_up(self):
        drift = self._drift(qty_erp_t0=10, qty_physical_t0=12, qty_erp_j=9)
        assert drift.drift_qty == Decimal("-3")

    def test_an_immaterial_drift_does_not_block(self):
        drift = self._drift(qty_physical_t0=12, qty_erp_j=9, is_material=False)
        assert drift.blocks_analysis is False

    def test_a_material_drift_without_a_resolution_blocks(self):
        drift = self._drift(qty_physical_t0=12, qty_erp_j=9, is_material=True)
        assert drift.is_resolved is False
        assert drift.blocks_analysis is True

    @pytest.mark.parametrize(
        "resolution", [DriftResolution.KEEP_EARLY, DriftResolution.RECOUNT]
    )
    def test_either_resolution_unblocks(self, resolution):
        drift = self._drift(
            qty_physical_t0=12, qty_erp_j=9, is_material=True, resolution=resolution
        )
        assert drift.blocks_analysis is False

    def test_there_are_exactly_two_resolutions(self):
        """« Rejouer le postage » et « ajuster » ont été retirés, et pour de
        bonnes raisons : on ne scelle qu'un journal posté, et un mouvement réel
        se saisit par le mécanisme d'ajustement."""
        assert set(DriftResolution) == {
            DriftResolution.KEEP_EARLY, DriftResolution.RECOUNT
        }
