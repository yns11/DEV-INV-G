"""Cinquante constats identiques ne sont pas cinquante informations.

Un contrôle qui se déclenche sur cinquante articles écrivait cinquante lignes,
qui remplissaient l'écran et poussaient dehors les cinq autres contrôles — dont
le bloquant. Le regroupement rend au lecteur la question qu'il se pose en
premier : *quels* contrôles ont parlé, et *combien de fois*.
"""

from __future__ import annotations

from inventory.domain.controls import CONTROL_LABELS, group_findings
from inventory.domain.enums import ControlSeverity
from inventory.domain.models import ControlFinding


def finding(code: str, *, item: str = "", severity=ControlSeverity.WARNING):
    return ControlFinding(
        code=code, severity=severity, message=f"{item or code} : constat", item_number=item
    )


class TestGrouping:
    def test_one_entry_per_control_whatever_the_number_of_articles(self):
        groups = group_findings(
            [finding("ASSEMBLY_WITHOUT_BOM", item=f"P-{n}") for n in range(50)]
        )
        assert len(groups) == 1
        assert groups[0].count == 50

    def test_the_group_keeps_every_occurrence(self):
        """C'est ce que « voir plus » ouvre : rien ne doit s'y perdre."""
        groups = group_findings(
            [finding("BOM_CYCLE", item="A"), finding("BOM_CYCLE", item="B")]
        )
        assert [f.item_number for f in groups[0].findings] == ["A", "B"]

    def test_two_controls_stay_two_groups(self):
        groups = group_findings(
            [finding("ASSEMBLY_WITHOUT_BOM"), finding("ITEMS_WITHOUT_PRICE")]
        )
        assert {g.code for g in groups} == {
            "ASSEMBLY_WITHOUT_BOM", "ITEMS_WITHOUT_PRICE"
        }

    def test_grouping_is_by_code_not_by_message(self):
        """Les messages diffèrent — chacun nomme son article. C'est le sujet."""
        groups = group_findings(
            [finding("BOM_PARENT_UNKNOWN", item="A"), finding("BOM_PARENT_UNKNOWN", item="B")]
        )
        assert len(groups) == 1

    def test_nothing_in_gives_nothing_out(self):
        assert group_findings([]) == []


class TestOrdering:
    def test_blockers_come_first_however_few(self):
        """Un bloquant enterré sous trente avertissements est un bloquant manqué."""
        groups = group_findings(
            [finding("ITEMS_WITHOUT_PRICE") for _ in range(30)]
            + [finding("BOM_CYCLE", severity=ControlSeverity.BLOCKER)]
        )
        assert groups[0].code == "BOM_CYCLE"

    def test_at_equal_severity_the_loudest_control_leads(self):
        groups = group_findings(
            [finding("ASSEMBLY_WITHOUT_BOM") for _ in range(3)]
            + [finding("UNIT_MISMATCH") for _ in range(7)]
        )
        assert [g.code for g in groups] == ["UNIT_MISMATCH", "ASSEMBLY_WITHOUT_BOM"]


class TestSeverityOfAGroup:
    def test_a_group_carries_the_worst_of_its_occurrences(self):
        """Sinon un cas grave se rangerait sous l'étiquette du cas bénin."""
        groups = group_findings([
            finding("UNIT_MISMATCH", severity=ControlSeverity.WARNING),
            finding("UNIT_MISMATCH", severity=ControlSeverity.BLOCKER),
        ])
        assert groups[0].severity is ControlSeverity.BLOCKER


class TestTheLabels:
    def test_every_control_the_engine_can_emit_has_a_french_title(self):
        """Un code brut à l'écran, c'est le contrôle qui n'explique rien."""
        import re
        from pathlib import Path

        import inventory.domain.controls as controls

        source = Path(controls.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r'code="([A-Z_0-9]+)"', source))
        assert emitted, "aucun code trouvé — la lecture du module a échoué"
        assert emitted <= set(CONTROL_LABELS), (
            "contrôles sans libellé : "
            f"{sorted(emitted - set(CONTROL_LABELS))}"
        )

    def test_no_label_is_left_behind_for_a_control_that_no_longer_exists(self):
        import re
        from pathlib import Path

        import inventory.domain.controls as controls

        source = Path(controls.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r'code="([A-Z_0-9]+)"', source))
        assert set(CONTROL_LABELS) <= emitted, (
            f"libellés orphelins : {sorted(set(CONTROL_LABELS) - emitted)}"
        )

    def test_an_unknown_code_falls_back_to_itself_rather_than_disappearing(self):
        groups = group_findings([finding("CONTROLE_TOUT_NEUF")])
        assert groups[0].label == "CONTROLE_TOUT_NEUF"


class TestTheWireShape:
    def test_the_group_travels_without_repeating_its_occurrences(self):
        """Elles voyagent déjà à plat ; deux copies finiraient par diverger."""
        group = group_findings([finding("BOM_CYCLE", item="A")])[0]
        payload = group.to_summary()
        assert payload == {
            "code": "BOM_CYCLE",
            "label": CONTROL_LABELS["BOM_CYCLE"],
            "severity": "WARNING",
            "count": 1,
        }
