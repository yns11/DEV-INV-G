"""Les deux voies de lecture reçoivent les mêmes réglages.

Une feuille se scanne de deux façons : seule, depuis la feuille ouverte — et
par pile, cent feuilles en un dépôt. La seconde est la voie normale ; la
première sert à vérifier ou à rattraper.

Elles construisent chacune leur appel au modèle, et l'une avait oublié un
réglage : `allow_formulas`. « 3*48+7 » — trois palettes de quarante-huit et un
fond de bac — était donc lu et évalué sur une feuille seule, et rendu comme une
**case vide** sur la même feuille passée dans une pile. Sans erreur, sans
avertissement, et sans que rien ne distingue ensuite ce cas d'une ligne que
personne n'avait comptée.

Ces contrôles portent sur les deux appels ensemble : c'est leur *divergence*
qui est le défaut, pas la valeur d'un réglage en particulier. Un réglage ajouté
demain à une seule des deux voies fera échouer le dernier.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from test_scan_pipeline import counting_sheet, multi_scan_service, one_sheet_bench

from inventory.ai.sheet_extraction import ExtractionResult, PageRouting


class _Recorder:
    """Un extracteur qui note ce qu'on lui passe, et rend une lecture vide."""

    seen: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def route_pages(self, **kwargs: Any) -> PageRouting:
        return PageRouting(pages_by_sheet={"s-0": [0]})

    def expected_from_items(self, lines, items):
        return list(lines)

    def extract(self, **kwargs: Any) -> ExtractionResult:
        _Recorder.seen.append(kwargs)
        return ExtractionResult(pages=1, tokens_used=10)

    extract_free_entry = extract


@pytest.fixture
def recorded(monkeypatch):
    """Rend `{voie: kwargs}` pour les deux chemins de lecture."""
    import inventory.ai as ai_module

    _Recorder.seen = []
    out: dict[str, dict[str, Any]] = {}

    service, campaign = one_sheet_bench(monkeypatch)
    monkeypatch.setattr(ai_module, "SheetExtractor", _Recorder)
    service.extract_from_scan(
        campaign, "s-1", payload=b"x", filename="scan.png", content_type="image/png",
    )
    out["feuille"] = _Recorder.seen[-1]

    _Recorder.seen = []
    service, campaign, _w, _r = multi_scan_service(
        monkeypatch,
        routing=PageRouting(pages_by_sheet={"s-0": [0]}),
        results={"s-0": ExtractionResult(pages=1, tokens_used=10)},
    )
    monkeypatch.setattr(ai_module, "SheetExtractor", _Recorder)
    service.extract_from_multi_scan(
        campaign, payload=counting_sheet(), filename="pile.pdf",
        content_type="application/pdf",
    )
    out["pile"] = _Recorder.seen[-1]
    return out


class TestLeReglageDesFormulesTraverseLesDeuxVoies:
    def test_la_feuille_seule_le_transmet(self, recorded):
        assert "allow_formulas" in recorded["feuille"]

    def test_la_pile_aussi(self, recorded):
        """Le défaut : la clé manquait, `False` s'appliquait par défaut, et
        toute opération écrite sur la feuille devenait une case vide."""
        assert "allow_formulas" in recorded["pile"]

    def test_et_les_deux_disent_la_même_chose(self, recorded):
        assert recorded["pile"]["allow_formulas"] == recorded["feuille"]["allow_formulas"]


class TestAucunAutreReglageNeDivergE:
    """Le contrôle qui vaut mieux que le précédent.

    Nommer `allow_formulas` ferme le défaut du jour ; comparer les deux jeux de
    clés ferme la *famille* — le prochain réglage ajouté à une seule des deux
    voies échouera ici, avant d'être livré.

    Les clés propres à chaque voie sont énumérées : l'image et la feuille
    diffèrent par construction, tout le reste doit coïncider.
    """

    #: Ce qu'une voie a légitimement de particulier.
    PER_PATH: ClassVar[set[str]] = {
        "images", "sheet_id", "zone_label", "pass_no", "image_mime",
    }

    def test_les_deux_appels_portent_les_mêmes_reglages(self, recorded):
        feuille = set(recorded["feuille"]) - self.PER_PATH
        pile = set(recorded["pile"]) - self.PER_PATH
        assert feuille == pile, (
            "un réglage n'est passé qu'à l'une des deux voies de lecture : "
            f"seule la feuille → {sorted(feuille - pile)} ; "
            f"seule la pile → {sorted(pile - feuille)}"
        )
