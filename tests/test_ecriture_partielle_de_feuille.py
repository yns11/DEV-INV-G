"""Une écriture qui ne parle pas d'un champ ne l'efface pas.

Le défaut
---------
Réordonner une feuille depuis l'aperçu de mise en page effaçait les quantités
saisies et les commentaires. L'aperçu n'affiche ni les unes ni les autres — il
montre le document : l'ordre des lignes, les intertitres, les sections — et
n'envoyait donc ni ``qty`` ni ``comment``. Le contrat, lui, donnait à ces deux
champs une valeur par défaut, et l'enregistrement écrivait cette valeur : rien.

Un comptage relevé en atelier disparaissait ainsi sans un mot, à l'occasion d'un
geste de mise en page. Le message disait « lignes enregistrées », et c'était
vrai.

La règle
--------
**Un champ absent de la charge utile veut dire « je ne parle pas de ce
champ ».** La désignation le disait déjà et c'est ce qui la protégeait ; la
quantité et le commentaire le disent maintenant aussi.

**Un champ présent et vide veut dire « efface ».** La distinction est réelle et
elle est celle de l'écran : la fenêtre de saisie affiche la colonne comptage,
donc elle en parle — et une case qu'on y vide doit se vider en base. L'aperçu de
mise en page ne l'affiche pas, donc il n'en parle pas.

Où ces contrôles vivent
-----------------------
Sur le chemin **réel** : la charge utile JSON telle que l'écran l'émet, validée
par le contrat, passée à la fonction de route, jusqu'à la ligne écrite. Le
défaut se tenait précisément entre ces trois-là — chacun avait raison de son
côté — et un contrôle qui appellerait le service avec un dictionnaire construit
à la main ne l'aurait jamais vu.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_transactions

from inventory.domain.enums import (
    CampaignStatus,
    CountSection,
    DataSource,
    SheetPass,
)
from inventory.domain.models import Campaign, CountSheetLine, Item

ITEMS = {
    "P-1": Item(campaign_id="camp-1", item_number="P-1", name="CARTER AR",
                std_price="10"),
}

#: La ligne telle qu'elle est en base avant l'enregistrement : comptée à la
#: main, commentée, et nommée par la feuille.
def compte(**over: Any) -> CountSheetLine:
    base: dict[str, Any] = {
        "id": "ligne-1",
        "sheet_id": "sheet-1",
        "campaign_id": "camp-1",
        "item_number": "P-1",
        "section": CountSection.LINE_SIDE,
        "qty_manual": "151",
        "source": DataSource.MANUAL,
        "comment": "bac du fond",
        "name": "CARTER ARRIÈRE M3 GEN2",
        "unit": "PCE",
        "display_order": 0,
    }
    return CountSheetLine(**{**base, **over})


def campagne(status: CampaignStatus = CampaignStatus.COUNTING) -> Campaign:
    """Une campagne en ordre de marche : le séquencement est traversé, pas neutralisé.

    Le stock ERP est gelé, comme il l'est le jour du comptage. Neutraliser la
    garde ferait perdre à ces contrôles la moitié de leur objet : le défaut
    voisin — une mise en page refusée par le gel des comptages — se joue
    précisément là.
    """
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date=dt.date(2026, 9, 1), status=status,
        created_by="chef@usine",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        book_stock_frozen_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


def enregistrer(
    ligne: dict[str, Any],
    *,
    previous: CountSheetLine | None = None,
    status: CampaignStatus = CampaignStatus.COUNTING,
    replace: bool = True,
) -> CountSheetLine:
    """La charge utile passe par le contrat, la route et le service.

    ``ligne`` est le dictionnaire **tel que le navigateur l'émet** : une clé
    absente y est absente pour de bon, et c'est tout l'objet de ces contrôles.
    """
    from inventory.api.routers.generic import upsert_sheet_lines
    from inventory.api.schemas import SheetLinesRequest
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext
    from inventory.services.generic_service import GenericService

    existing = [previous] if previous is not None else []
    written: list[CountSheetLine] = []
    sheet = SimpleNamespace(
        id="sheet-1", campaign_id="camp-1", zone_id="zone-1", version=1,
        # Le passage 2 : le miroir vers l'autre feuille ne se déclenche pas, et
        # ce n'est pas lui qui est en cause ici.
        pass_no=SheetPass.PASS_2,
    )
    zone = SimpleNamespace(
        id="zone-1", code="B06VRAC", allow_negative=False, passes=1,
    )
    ctx = ServiceContext(actor="chef@usine", db=cast(Any, None),
                         settings=get_settings())
    ctx.__dict__["referentials"] = SimpleNamespace(
        items_by_number=lambda cid: ITEMS,
        count_items=lambda cid: len(ITEMS),
    )
    ctx.__dict__["sheets"] = SimpleNamespace(
        get_sheet=lambda sid: sheet,
        list_zones=lambda cid: [zone],
        list_sheets=lambda cid, **kw: [sheet],
        list_sheet_lines=lambda sid: list(existing),
        lines_by_sheet=lambda cid, **kw: {"sheet-1": list(existing)},
        bump_sheet=lambda *a, **kw: None,
        upsert_sheet_lines=lambda lines, actor, conn=None: (
            written.extend(lines) or len(lines)
        ),
        replace_sheet_lines=lambda sid, lines, actor, conn=None: (
            written.extend(lines) or len(lines)
        ),
    )
    ctx.__dict__["book_stock"] = SimpleNamespace(count=lambda cid: 120)
    ctx.__dict__["journals"] = SimpleNamespace(count_lines=lambda cid: 0)
    ctx.record = lambda **kw: None  # type: ignore[method-assign]
    ctx.require_write = lambda campaign: None  # type: ignore[method-assign]
    with_transactions(cast(Any, ctx))

    service = GenericService(ctx)
    payload = SheetLinesRequest.model_validate({"lines": [ligne], "replace": replace})
    upsert_sheet_lines(
        campaign=campagne(status), sheet_id="sheet-1",
        payload=payload, service=service,
    )
    assert len(written) == 1, written
    return written[0]


#: Ce que l'aperçu de mise en page envoie : le document, et rien du comptage.
MISE_EN_PAGE = {
    "id": "ligne-1",
    "itemNumber": "P-1",
    "section": "LINE_SIDE",
    "lineKind": "ARTICLE",
    "label": "",
    "unit": "PCE",
    "displayOrder": 3,
}


class TestReordonnerNEffaceRien:
    """Le défaut signalé, dans les termes où il a été constaté."""

    def test_la_quantite_saisie_survit(self):
        ligne = enregistrer(MISE_EN_PAGE, previous=compte())
        assert ligne.qty_manual == compte().qty_manual

    def test_et_le_commentaire_aussi(self):
        ligne = enregistrer(MISE_EN_PAGE, previous=compte())
        assert ligne.comment == "bac du fond"

    def test_et_la_designation_de_feuille(self):
        """Déjà protégée — le contrôle tient qu'elle le reste."""
        ligne = enregistrer(MISE_EN_PAGE, previous=compte())
        assert ligne.name == "CARTER ARRIÈRE M3 GEN2"

    def test_et_la_provenance_de_la_quantite(self):
        """Une lecture IA relue par personne reste une lecture IA."""
        ligne = enregistrer(
            MISE_EN_PAGE,
            previous=compte(qty_manual=None, qty_imported="48",
                            source=DataSource.SCAN_AI, confidence="0.9"),
        )
        assert ligne.source is DataSource.SCAN_AI
        assert ligne.qty_imported == compte(qty_imported="48").qty_imported

    def test_et_l_operation_ecrite_par_le_compteur(self):
        """« 3*48+7 » est ce qui explique le 151 : le perdre perd l'explication."""
        ligne = enregistrer(
            MISE_EN_PAGE, previous=compte(qty_formula="3*48+7"),
        )
        assert ligne.qty_formula == "3*48+7"

    def test_ce_dont_l_apercu_parle_arrive_bien(self):
        """Le contrôle du contrôle : l'écriture n'est pas devenue sans effet."""
        ligne = enregistrer(MISE_EN_PAGE, previous=compte())
        assert ligne.display_order == 3
        assert ligne.section is CountSection.LINE_SIDE

    def test_reordonner_en_preparation_ne_se_heurte_pas_a_la_garde(self):
        """Une mise en page n'est pas un comptage, et ne s'y gèle pas.

        Sans cette règle, une feuille portant des quantités importées refusait
        d'être réordonnée en préparation : la charge utile sans ``qty`` se
        lisait comme l'effacement de toutes ses quantités.
        """
        ligne = enregistrer(
            MISE_EN_PAGE,
            previous=compte(qty_manual=None, qty_imported="48",
                            source=DataSource.FILE_IMPORT),
            status=CampaignStatus.PREPARATION,
        )
        assert ligne.qty_imported == compte(qty_imported="48").qty_imported


class TestUnChampPresentEtVideEfface:
    """L'autre moitié de la règle, et elle compte autant.

    Sans elle, plus rien ne s'effacerait jamais : vider une case dans la fenêtre
    de saisie n'aurait aucun effet, et le compteur qui corrige une erreur
    verrait l'ancienne valeur revenir au rechargement.
    """

    @pytest.mark.parametrize("vide", [None, ""])
    def test_une_quantite_videe_s_efface(self, vide):
        ligne = enregistrer(
            {**MISE_EN_PAGE, "qty": vide, "comment": "bac du fond"},
            previous=compte(),
        )
        assert ligne.qty_manual is None
        assert ligne.has_entry is False

    def test_un_commentaire_vide_s_efface(self):
        ligne = enregistrer(
            {**MISE_EN_PAGE, "comment": ""}, previous=compte(),
        )
        assert ligne.comment == ""

    def test_une_quantite_changee_s_ecrit(self):
        ligne = enregistrer(
            {**MISE_EN_PAGE, "qty": "42"}, previous=compte(),
        )
        assert ligne.qty_manual == Decimal("42")

    def test_un_zero_est_un_comptage(self):
        """« Bac vide » est un constat, pas une absence de saisie."""
        ligne = enregistrer(
            {**MISE_EN_PAGE, "qty": "0"}, previous=compte(qty_manual=None),
        )
        assert ligne.has_entry is True
        assert ligne.qty_manual == Decimal("0")



#: Les champs dont l'absence a un sens propre, et ce qui les efface.
#:
#: La valeur d'effacement n'est pas la même partout, et ce n'est pas une
#: négligence. La quantité **est** nullable en base : une ligne que personne n'a
#: comptée ne porte aucune quantité, et ``null`` est donc l'effacement. La
#: désignation et le commentaire sont des textes : il n'y a pas de désignation
#: nulle, la chaîne vide *est* l'absence d'écrasement. Écrire la règle par champ
#: plutôt que de supposer « le défaut du contrat efface » est ce qui garde ce
#: contrôle vrai.
EFFACEMENT: dict[str, Any] = {"name": "", "qty": None, "comment": ""}

#: La référence identifie la ligne : elle est obligatoire, jamais absente.
REQUIS = {"item_number"}


def _contrat() -> dict[str, Any]:
    from inventory.api.schemas import SheetLineRow

    return dict(SheetLineRow.model_fields)


#: Une ligne où **tous** les champs du contrat sont dits, par leur nom.
COMPLETE: dict[str, Any] = {
    "id": "ligne-1",
    "item_number": "P-1",
    "section": "WIP",
    "line_kind": "ARTICLE",
    "label": "",
    "name": "CARTER ARRIÈRE M3 GEN2",
    "qty": "42",
    "unit": "KG",
    "comment": "bac du fond",
    "display_order": 3,
}

ORDINAIRES = sorted(set(COMPLETE) - set(EFFACEMENT) - REQUIS)


class TestLAbsenceEtLeDefautNeDivergentQueLaOuOnLeVeut:
    """Le contrôle qui rend le changement de couture sûr — et le garde sûr.

    Le routeur ne complète plus la charge utile avec les valeurs par défaut du
    contrat : il transmet ce que l'écran a dit. C'est ce qui donne un sens à
    l'absence — mais cela ne vaut que si, pour **tous les autres champs**,
    l'absence produit exactement ce que la valeur par défaut produisait. Sinon
    le changement aurait déplacé un défaut ailleurs, en silence, sur des champs
    que personne ne regarde.

    Le contrôle est donc dynamique et à double sens : il énumère le contrat,
    vérifie que l'omission d'un champ ordinaire ne change rien, et vérifie que
    l'omission d'un champ à sens propre change quelque chose. Un champ ajouté
    demain devra passer par ici, et par la question qui va avec : son absence
    veut-elle dire quelque chose ?
    """

    @staticmethod
    def _stored(ligne: dict[str, Any]) -> dict[str, Any]:
        """La ligne écrite, moins ce qui ne peut pas être comparé.

        L'identifiant est tiré au sort quand la charge utile n'en donne pas :
        le comparer opposerait deux tirages, pas deux comportements.
        """
        return enregistrer(ligne, previous=compte()).model_dump(exclude={"id"})

    def test_le_contrat_est_couvert_en_entier(self):
        """Sinon les deux contrôles suivants passeraient à côté d'un champ neuf."""
        manquants = sorted(set(_contrat()) - set(COMPLETE))
        assert manquants == [], (
            f"Le contrat a gagné {manquants} : ajoutez-les à COMPLETE, et rangez "
            "chacun dans EFFACEMENT ou hors d'EFFACEMENT selon que son absence a "
            "un sens propre."
        )
        assert set(EFFACEMENT) | REQUIS <= set(_contrat())

    @pytest.mark.parametrize("field", ORDINAIRES)
    def test_omettre_un_champ_ordinaire_revient_a_envoyer_son_defaut(self, field):
        """Aucun défaut ne s'est déplacé en passant à ``exclude_unset``."""
        sans = {k: v for k, v in COMPLETE.items() if k != field}
        avec = {**COMPLETE, field: _contrat()[field].default}
        assert self._stored(sans) == self._stored(avec), field

    @pytest.mark.parametrize("field", sorted(EFFACEMENT))
    def test_omettre_un_champ_a_sens_propre_ne_l_efface_pas(self, field):
        """L'autre sens : la distinction existe vraiment, elle n'est pas décorative.

        C'est ce contrôle-ci qui tombe si le routeur revient à ``model_dump()``
        sans ``exclude_unset``, ou si le service cesse de regarder la présence
        du champ.
        """
        sans = {k: v for k, v in COMPLETE.items() if k != field}
        efface = {**COMPLETE, field: EFFACEMENT[field]}
        assert self._stored(sans) != self._stored(efface), field

    @pytest.mark.parametrize("field", sorted(EFFACEMENT))
    def test_et_l_effacement_efface_bien(self, field):
        """Le contrôle du contrôle : la valeur déclarée efface vraiment.

        Sans lui, ranger un champ dans EFFACEMENT avec une valeur qui n'efface
        rien ferait passer le contrôle précédent pour une mauvaise raison.
        """
        avant = self._stored(COMPLETE)
        efface = self._stored({**COMPLETE, field: EFFACEMENT[field]})
        assert efface != avant, field
