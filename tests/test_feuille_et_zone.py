"""Ce qu'on pose sur une feuille, ce qui s'imprime, et le nom de la zone.

Cinq demandes qui portent toutes sur le même document — la feuille de comptage
B06VRAC — et qui ont en commun de coûter du papier ou du temps au préparateur :

* on ne pouvait ajouter à l'aperçu que des intertitres et des lignes vides, pas
  une **référence** ; ajouter un article oublié obligeait à quitter la feuille ;
* une section d'en-cours vide s'imprimait quand même, bandeau et cases
  comprises, sur des zones qui n'ont ni WIP ni WIP assemblé ;
* les lignes libres de fin de section prenaient plus de place qu'elles n'en
  valent ;
* une zone mal nommée ne se corrigeait qu'en la supprimant — donc en perdant ses
  feuilles ;
* une désignation d'atelier se faisait couper à vingt-neuf caractères.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.printing import BLANK_ROWS_PER_SECTION, PrintMode
from inventory.errors import ConflictError, NotFoundError, ValidationError
from inventory.reporting.exports import (
    _NAME_COLUMN_POINTS,
    _NAME_MAX_CHARS,
    _fit,
    _has_articles,
    printed_sections,
)

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend" / "src"


def source(relative: str) -> str:
    return (FRONT / relative).read_text()


# --------------------------------------------------------------------------- #
# 1. Les lignes libres de fin de section
# --------------------------------------------------------------------------- #

class TestLesLignesLibres:
    def test_quatre_au_bord_de_ligne_et_deux_en_wip(self):
        """Demandé, et mesuré sur les feuilles réelles : cinq et trois, c'était
        une section de plus qui débordait sur un second feuillet."""
        assert BLANK_ROWS_PER_SECTION == {"LINE_SIDE": 4, "WIP": 2, "WIP_OK": 2}

    def test_le_releve_nen_porte_aucune(self):
        """Un enregistrement ne doit pas porter d'invitation à écrire plus."""
        from inventory.reporting.exports import _blank_rows_for

        for section in BLANK_ROWS_PER_SECTION:
            assert _blank_rows_for(
                section, mode=PrintMode.FILLED, requested=0
            ) == 0


# --------------------------------------------------------------------------- #
# 2. Une section d'en-cours vide ne s'imprime pas
# --------------------------------------------------------------------------- #

def _line(number: str, section: str, **kw: Any) -> dict[str, Any]:
    return {"item_number": number, "name": f"Article {number}",
            "section": section, "unit": "PCE", "qty": 0, **kw}


def _sections(lines: list[dict[str, Any]], *, mode: PrintMode = PrintMode.LIST):
    """Les sections qui sortiraient de l'imprimante, pour ces lignes."""
    by_section: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_section.setdefault(str(line.get("section", "LINE_SIDE")), []).append(line)
    return [
        section
        for section, _, _ in printed_sections(by_section, mode=mode, blank_lines=0)
    ]


class TestLesSectionsVides:
    def test_une_section_sans_article_nen_est_pas_une(self):
        assert _has_articles([]) is False
        assert _has_articles([
            {"line_kind": "SUBSECTION", "label": "Stock B15", "item_number": ""},
            {"line_kind": "SPACER", "item_number": ""},
        ]) is False
        assert _has_articles([_line("P-1", "WIP")]) is True

    def test_le_bandeau_wip_disparait_quand_il_ny_a_rien_a_compter(self):
        """Deux bandeaux et leurs cases vides sur un tiers de la page, sous
        lesquels la zone n'a rien : c'est du papier, et une invitation à écrire
        dans une section qui n'existe pas ici."""
        assert _sections([_line("P-1", "LINE_SIDE")]) == ["LINE_SIDE"]

    def test_elle_reste_des_qu_un_article_y_figure(self):
        assert _sections([
            _line("P-1", "LINE_SIDE"), _line("P-2", "WIP_OK"),
        ]) == ["LINE_SIDE", "WIP_OK"]

    def test_un_intertitre_seul_ne_la_fait_pas_imprimer(self):
        """L'intertitre est de la mise en page : il ne fait compter personne."""
        assert _sections([
            _line("P-1", "LINE_SIDE"),
            {"item_number": "", "section": "WIP", "line_kind": "SUBSECTION",
             "label": "Établi 3", "unit": "", "qty": None},
        ]) == ["LINE_SIDE"]

    def test_le_bord_de_ligne_simprime_toujours(self):
        """Ses lignes libres sont l'endroit où l'on note une référence que
        personne n'avait listée — et c'est sur une feuille courte qu'on en a le
        plus besoin."""
        assert _sections([_line("P-2", "WIP_OK")]) == ["LINE_SIDE", "WIP_OK"]

    def test_les_trois_restent_quand_les_trois_portent_quelque_chose(self):
        assert _sections([
            _line("P-1", "LINE_SIDE"), _line("P-2", "WIP"), _line("P-3", "WIP_OK"),
        ]) == ["LINE_SIDE", "WIP", "WIP_OK"]

    def test_une_feuille_vierge_ne_porte_que_le_bord_de_ligne(self):
        """Le compteur y écrit référence et quantité : une seule grille."""
        by_section: dict[str, list[dict[str, Any]]] = {}
        sections = printed_sections(
            by_section, mode=PrintMode.BLANK, blank_lines=40
        )
        assert [s for s, _, _ in sections] == ["LINE_SIDE"]
        assert sections[0][2] == 40

    def test_un_releve_sans_ligne_dans_une_section_ne_limprime_pas(self):
        """Un enregistrement n'a pas de lignes libres : une section vide y
        disparaît des trois, bord de ligne compris."""
        assert _sections(
            [_line("P-1", "LINE_SIDE")], mode=PrintMode.FILLED
        ) == ["LINE_SIDE"]
        assert _sections([], mode=PrintMode.FILLED) == []


# --------------------------------------------------------------------------- #
# 3. La désignation tient sur 41 caractères — et dans la case
# --------------------------------------------------------------------------- #

class TestLaDesignationImprimee:
    def test_quarante_et_un_caracteres(self):
        assert _NAME_MAX_CHARS == 41

    def test_une_designation_reelle_de_41_passe_en_entier(self):
        name = "CARTER ARRIERE M3 GEN2 REF LONGUE ALU 41x"
        assert len(name) == 41
        assert _fit(name, chars=41, points=_NAME_COLUMN_POINTS) == name

    def test_au_dela_elle_est_coupee_et_le_dit(self):
        out = _fit("A" * 60, chars=41, points=_NAME_COLUMN_POINTS)
        assert out.endswith("…")
        assert len(out) <= 41

    def test_une_chaine_large_est_rognee_plutot_que_de_deborder(self):
        """Quarante et un « M » occuperaient 290 points dans une colonne qui en
        offre 227. La hauteur de rang étant imposée, le texte passerait à la
        ligne et déborderait sur les lignes suivantes jusqu'au pied de page.
        """
        from reportlab.pdfbase.pdfmetrics import stringWidth

        for sample in ("M" * 41, "W" * 60, "Ø" * 45):
            out = _fit(sample, chars=41, points=_NAME_COLUMN_POINTS)
            assert stringWidth(out, "Helvetica", 8.5) <= _NAME_COLUMN_POINTS, sample

    def test_le_releve_avec_provenance_garde_sa_borne_etroite(self):
        """Là, deux colonnes de plus se partagent la largeur, et les lignes
        s'agrandissent : la troncature n'a pas la même contrainte."""
        from inventory.reporting.exports import _NAME_MAX_CHARS_WITH_SOURCES

        assert _NAME_MAX_CHARS_WITH_SOURCES < _NAME_MAX_CHARS


# --------------------------------------------------------------------------- #
# 4. Renommer une zone
# --------------------------------------------------------------------------- #

def _zone(zone_id: str, code: str, **kw: Any):
    from inventory.domain.models import Zone

    return Zone(id=zone_id, campaign_id="c", code=code, **kw)


def _service(zones: list[Any]):
    from contextlib import contextmanager

    from inventory.services.generic_service import GenericService

    written: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    @contextmanager
    def transaction():
        yield None

    ctx = cast(Any, SimpleNamespace(
        actor="alice",
        guard=lambda campaign, aspect: None,
        db=SimpleNamespace(transaction=transaction),
        record=lambda **kw: events.append(kw),
        sheets=SimpleNamespace(
            list_zones=lambda cid, **kw: zones,
            rename_zone=lambda cid, zid, **kw: written.append({"id": zid, **kw}),
        ),
    ))
    campaign = SimpleNamespace(id="c", code="INV-1")
    return GenericService(ctx), campaign, written, events


class TestRenommerUneZone:
    def test_le_code_change(self):
        service, campaign, written, _ = _service([_zone("z1", "B15")])
        renamed = service.rename_zone(campaign, "z1", code="B15 EST")
        assert renamed.code == "B15 EST"
        assert written[0]["code"] == "B15 EST"

    def test_le_libelle_et_le_secteur_restent_si_on_nen_parle_pas(self):
        """L'écran qui ne propose que le code ne doit pas effacer les deux
        autres en passant."""
        service, campaign, written, _ = _service([
            _zone("z1", "B15", label="Bord de ligne 15", sector="Assemblage")
        ])
        service.rename_zone(campaign, "z1", code="B15 EST")
        assert written[0]["label"] == "Bord de ligne 15"
        assert written[0]["sector"] == "Assemblage"

    def test_le_code_est_normalise_comme_a_la_creation(self):
        """Deux zones ne doivent pas devenir distinctes par leur seule casse."""
        service, campaign, _written, _ = _service([_zone("z1", "B15")])
        assert service.rename_zone(campaign, "z1", code="b15 est").code == "B15 EST"

    def test_un_code_deja_pris_est_refuse_en_le_nommant(self):
        service, campaign, written, _ = _service([
            _zone("z1", "B15"), _zone("z2", "B06 EST"),
        ])
        with pytest.raises(ConflictError) as caught:
            service.rename_zone(campaign, "z1", code="B06 EST")
        assert "B06 EST" in str(caught.value)
        assert written == []

    def test_garder_son_propre_code_nest_pas_un_conflit(self):
        """Corriger le seul libellé passe par ici, et ne doit pas buter."""
        service, campaign, written, _ = _service([_zone("z1", "B15")])
        service.rename_zone(campaign, "z1", code="B15", label="Bord de ligne")
        assert written[0]["label"] == "Bord de ligne"

    def test_un_code_vide_est_refuse(self):
        service, campaign, written, _ = _service([_zone("z1", "B15")])
        with pytest.raises(ValidationError):
            service.rename_zone(campaign, "z1", code="   ")
        assert written == []

    def test_une_zone_inconnue_est_refusee(self):
        service, campaign, _, _ = _service([_zone("z1", "B15")])
        with pytest.raises(NotFoundError):
            service.rename_zone(campaign, "z9", code="B20")

    def test_ne_rien_changer_nécrit_rien(self):
        """Une trace d'audit par ouverture de fenêtre noierait les vraies."""
        service, campaign, written, events = _service([_zone("z1", "B15")])
        service.rename_zone(campaign, "z1", code="B15")
        assert written == []
        assert events == []

    def test_le_renommage_est_trace_avec_les_deux_noms(self):
        service, campaign, _, events = _service([_zone("z1", "B15")])
        service.rename_zone(campaign, "z1", code="B15 EST")
        assert events[0]["before"]["code"] == "B15"
        assert events[0]["after"]["code"] == "B15 EST"
        assert "B15 EST" in events[0]["summary"]

    def test_rien_dautre_que_la_zone_nest_touche(self):
        """Feuilles, lignes et comptages tiennent à l'identifiant, pas au code.

        C'est ce qui rend l'opération sûre — et c'est ce qui doit rester vrai :
        un renommage qui irait réécrire les feuilles serait une suppression
        déguisée.
        """
        import inspect

        from inventory.services import generic_service

        body = inspect.getsource(generic_service.GenericService.rename_zone)
        for forbidden in ("replace_sheet_lines", "ensure_sheets", "delete_zones"):
            assert forbidden not in body


class TestLaRouteEtLEcran:
    """Un service que rien n'appelle n'existe pas — la panne habituelle ici."""

    def test_la_route_existe(self, monkeypatch):
        from inventory.api import app as module
        from inventory.config import get_settings

        monkeypatch.setenv("INV_ENV", "local")
        get_settings.cache_clear()
        paths = {
            (route.path, method)
            for route in module.create_app().routes
            for method in getattr(route, "methods", ())
        }
        assert (
            "/api/campaigns/{campaign_id}/generic/zones/{zone_id}/rename", "POST"
        ) in paths

    def test_le_client_la_connait(self):
        api = source("lib/api.ts")
        assert "renameZone" in api
        assert "/generic/zones/${zoneId}/rename" in api

    def test_la_grille_des_zones_lappelle(self):
        screen = source("features/zones.tsx")
        assert "function RenameZoneModal" in screen
        assert "api.renameZone(campaignId, zone.id, form)" in screen
        assert "setRenaming(row)" in screen

    def test_elle_annonce_la_seule_consequence(self):
        """L'import reconnaît une zone à son code : un fichier qui porte encore
        l'ancien en créera une seconde. Le taire ferait découvrir un doublon."""
        screen = source("features/zones.tsx")
        modal = screen[screen.index("function RenameZoneModal"):]
        assert "créera une seconde zone" in modal


# --------------------------------------------------------------------------- #
# 5. Poser des articles depuis l'aperçu de la feuille
# --------------------------------------------------------------------------- #

class TestPoserDesArticlesDepuisLApercu:
    def test_lapercu_ouvre_un_selecteur(self):
        screen = source("features/generic.layout.tsx")
        assert "ItemPicker" in screen
        assert "Ajouter des articles" in screen

    def test_il_insere_a_lendroit_choisi_et_dans_la_bonne_section(self):
        """La section décide de la règle de consolidation : une référence posée
        dans la mauvaise serait éclatée en nomenclature, ou pas."""
        screen = source("features/generic.layout.tsx")
        assert "{ index: number; section: string }" in screen
        assert "const insertItems" in screen
        insert = screen[screen.index("const insertItems"):]
        assert "section: at.section" in insert[:900]
        assert "line_kind: 'ARTICLE'" in insert[:900]

    def test_rien_nest_ecrit_avant_enregistrer(self):
        """Le geste se défait comme les autres, par « Annuler les modifications »."""
        screen = source("features/generic.layout.tsx")
        insert = screen[screen.index("const insertItems"):]
        assert "edit(next)" in insert[:1200]
        assert "mutate" not in insert[:1200]

    def test_le_selecteur_cherche_cote_serveur(self):
        """Le référentiel fait quelques milliers de lignes : le charger en
        entier dans la fenêtre serait le charger pour rien."""
        picker = source("components/ItemPicker.tsx")
        assert "api.items(campaignId, { search: search.trim()" in picker

    def test_il_en_prend_plusieurs_dun_coup(self):
        picker = source("components/ItemPicker.tsx")
        assert "onPick: (items: PickedItem[]) => void" in picker

    def test_ce_qui_est_coche_survit_a_un_changement_de_recherche(self):
        """Sinon, chercher la troisième référence fait perdre les deux
        premières — et personne ne s'en aperçoit avant d'avoir cliqué."""
        picker = source("components/ItemPicker.tsx")
        assert "chosen.size > 0 && (" in picker
        assert "chip--active" in picker

    def test_il_dit_ce_quil_ne_montre_pas(self):
        picker = source("components/ItemPicker.tsx")
        assert "affinez la recherche" in picker
