"""Le stock ERP se charge, quoi que le référentiel en pense.

Le fichier ERP couvre toute l'usine. La campagne, elle, choisit son périmètre :
un programme parti du site, une gamme après-vente comptée ailleurs, des
emballages que personne ne pèse — tout cela s'exclut sur la grille Articles, et
c'est une décision, prise avant le chargement. Le référentiel, de son côté,
avance à son rythme : une référence créée la semaine dernière dans l'ERP n'y est
pas encore.

Le mappeur faisait des deux cas une **erreur de ligne**. Or le stock ERP
remplace l'ensemble existant, et un chargement qui remplace refuse d'écrire dès
qu'une ligne est rejetée — à raison : il laisserait un ensemble amputé qui se
présente comme complet. Les deux règles se composaient en un piège :

    Le stock ERP remplace l'ensemble existant, et 1558 ligne(s) sur 1598 ont
    été refusées. L'écriture est annulée.

Plus le périmètre était restreint ou le référentiel en retard, moins le stock
était chargeable — et le seul geste proposé, « corrigez le fichier », portait
sur le seul document qui n'avait rien de faux.

Aucun des deux n'est une erreur de fichier. Les lignes sont **écartées**,
comptées et dites. Ce qui reste refusé est ce que le fichier a réellement de
faux : une quantité illisible, une colonne obligatoire absente.

Les deux cas restent **distincts**, parce qu'ils ne se corrigent pas au même
endroit — l'un sur la grille Articles, l'autre en complétant le référentiel — et
parce que l'un est voulu quand l'autre est un manque. C'est cette distinction
que la vue Contrôles relit ensuite.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from conftest import with_access, with_transactions

from inventory.domain.controls import check_stock_import
from inventory.domain.enums import CampaignStatus, ControlSeverity, ExclusionScope
from inventory.domain.models import BookStockLine, Campaign, Item
from inventory.ingest.mappers import map_book_stock
from inventory.ingest.parser import ParseResult, RowError


def campaign() -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date="2026-09-01", status=CampaignStatus.COUNTING,
        created_by="chef@usine",
        created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


def referential(*numbers: str, excluded: tuple[str, ...] = ()) -> dict[str, Item]:
    return {
        n: Item(
            campaign_id="camp-1", item_number=n, std_price="10",
            exclusions={ExclusionScope.ALL} if n in excluded else set(),
        )
        for n in numbers
    }


def stock_rows(*numbers: str) -> list[dict[str, Any]]:
    return [
        {"item_number": n, "warehouse_id": "B06", "location_id": "VRAC", "qty": "10"}
        for n in numbers
    ]


# --------------------------------------------------------------------------- #
# Le mappeur
# --------------------------------------------------------------------------- #

class TestTheMapperRefusesOnlyWhatTheFileGotWrong:
    def test_l_article_hors_perimetre_est_ecarte(self):
        lines, errors, out_of_scope, unknown = map_book_stock(
            "camp-1", stock_rows("P-1", "P-2"),
            items=referential("P-1", "P-2", excluded=("P-2",)),
        )

        assert [line.item_number for line in lines] == ["P-1"]
        assert errors == []
        assert [w.value for w in out_of_scope] == ["P-2"]
        assert unknown == []

    def test_l_article_inconnu_est_ecarte_aussi(self):
        """Il ne peut pas entrer — sans article, ni désignation, ni prix, ni
        matérialité — mais il n'annule plus le chargement des autres."""
        lines, errors, out_of_scope, unknown = map_book_stock(
            "camp-1", stock_rows("P-1", "INCONNU"), items=referential("P-1"),
        )

        assert [line.item_number for line in lines] == ["P-1"]
        assert errors == []
        assert out_of_scope == []
        assert [w.value for w in unknown] == ["INCONNU"]

    def test_les_deux_causes_ne_se_confondent_pas(self):
        """Elles ne se corrigent pas au même endroit : les confondre enverrait
        la moitié des cas au mauvais écran."""
        _, errors, out_of_scope, unknown = map_book_stock(
            "camp-1", stock_rows("P-1", "P-2", "INCONNU"),
            items=referential("P-1", "P-2", excluded=("P-2",)),
        )

        assert errors == []
        assert [w.value for w in out_of_scope] == ["P-2"]
        assert [w.value for w in unknown] == ["INCONNU"]

    def test_une_quantite_illisible_reste_un_refus(self):
        """Là, « corrigez le fichier » est le bon conseil.

        Sans ce contrôle, « rien ne refuse plus rien » passerait aussi.
        """
        rows = [
            *stock_rows("P-1"),
            {"item_number": "P-1", "warehouse_id": "B06",
             "location_id": "L2", "qty": "douze"},
        ]

        lines, errors, out_of_scope, unknown = map_book_stock(
            "camp-1", rows, items=referential("P-1")
        )

        assert len(errors) == 1
        assert len(lines) == 1
        assert (out_of_scope, unknown) == ([], [])

    def test_un_perimetre_presque_entierement_exclu_charge_quand_meme(self):
        """Le cas rencontré : quarante lignes gardées sur mille six cents."""
        gardees = [f"P-{n}" for n in range(40)]
        exclues = [f"X-{n}" for n in range(1558)]
        items = referential(*gardees, *exclues, excluded=tuple(exclues))

        lines, errors, out_of_scope, _ = map_book_stock(
            "camp-1", stock_rows(*gardees, *exclues), items=items
        )

        assert len(lines) == 40
        assert errors == [], "aucun refus : rien n'annule l'écriture"
        assert len(out_of_scope) == 1558

    def test_un_referentiel_entierement_en_retard_charge_ce_qu_il_connait(self):
        """Le pendant du précédent, côté référence inconnue."""
        connues = [f"P-{n}" for n in range(40)]
        nouvelles = [f"N-{n}" for n in range(1558)]

        lines, errors, _, unknown = map_book_stock(
            "camp-1", stock_rows(*connues, *nouvelles),
            items=referential(*connues),
        )

        assert len(lines) == 40
        assert errors == []
        assert len(unknown) == 1558

    def test_l_ecart_ne_reste_pas_a_charge_de_l_article_exclu(self):
        """Son stock n'entre pas : l'inventaire ne le compte pas, et un stock
        ERP sans comptage produirait un écart égal à la totalité du stock."""
        lines, _, _, _ = map_book_stock(
            "camp-1", stock_rows("P-2"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert lines == []

    def test_chaque_message_dit_le_geste_qui_inclurait_la_ligne(self):
        _, _, out_of_scope, unknown = map_book_stock(
            "camp-1", stock_rows("P-2", "INCONNU"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert "hors du périmètre" in out_of_scope[0].message
        assert "grille Articles" in out_of_scope[0].message
        assert "absent du référentiel" in unknown[0].message
        assert "rechargez" in unknown[0].message

    def test_la_ligne_ecartee_porte_sa_reference(self):
        """« 1558 lignes écartées » sans savoir lesquelles n'aide personne."""
        _, _, out_of_scope, _ = map_book_stock(
            "camp-1", stock_rows("P-2"),
            items=referential("P-2", excluded=("P-2",)),
        )

        assert out_of_scope[0].value == "P-2"
        assert out_of_scope[0].column == "item_number"

    def test_la_reference_signalee_est_celle_du_referentiel(self):
        """Normalisée, pas telle que le fichier l'a écrite.

        Le contrôle liste ces valeurs et quelqu'un les cherchera dans la grille
        Articles. « p-2 » et « P-2 » y sont la même référence ; deux constats
        pour une seule ligne manquante feraient chercher deux fois.
        """
        rows = [
            {"item_number": " p-2 ", "warehouse_id": "B06",
             "location_id": "VRAC", "qty": "10"}
        ]

        _, _, _, unknown = map_book_stock("camp-1", rows, items=referential("P-1"))

        assert [w.value for w in unknown] == ["P-2"]


# --------------------------------------------------------------------------- #
# Le service
# --------------------------------------------------------------------------- #

def import_service(monkeypatch, *, rows, items):
    """Un service dont la lecture est dictée, et le mappage bien réel."""
    from inventory.services import import_service as module

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    written: list[int] = []
    ctx.book_stock = SimpleNamespace(
        replace=lambda cid, lines, **k: written.append(len(lines)) or len(lines)
    )
    ctx.referentials = SimpleNamespace(
        items_by_number=lambda cid: items,
        locations_by_key=lambda cid: {},
        upsert_warehouses=lambda w, *, actor, conn=None: len(list(w)),
        upsert_locations=lambda l, *, actor, conn=None: len(list(l)),
    )
    ctx.journals = SimpleNamespace(
        ensure_journals=lambda cid, keys, *, kinds, actor, conn=None: len(keys),
    )
    ctx.imports = SimpleNamespace(create=lambda **k: k.get("batch_id") or "lot")
    ctx.record = lambda **kw: "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=0, book_stock_frozen=False
    )
    with_access(ctx)

    service = module.ImportService(ctx)
    service.batches.archive = lambda *a, **k: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        service.parser, "parse",
        lambda t, **kw: (
            None,
            ParseResult(contract_key=t, rows=rows, rows_received=len(rows)),
        ),
    )
    return service, written


class TestTheSnapshotLoadsAndSaysWhatItLeftOut:
    def run(self, monkeypatch, *, kept, excluded=(), unknown=()):
        items = referential(*kept, *excluded, excluded=tuple(excluded))
        service, written = import_service(
            monkeypatch, rows=stock_rows(*kept, *excluded, *unknown), items=items
        )
        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="stock.csv"
        )
        return outcome, written

    def test_le_stock_du_perimetre_est_ecrit(self, monkeypatch):
        _, written = self.run(monkeypatch, kept=["P-1"], excluded=["X-1", "X-2"])

        assert written == [1], "l'écriture est annulée alors qu'elle ne doit pas l'être"

    def test_rien_n_est_compte_comme_refuse(self, monkeypatch):
        """C'est le décompte des refus qui déclenchait l'annulation."""
        outcome, _ = self.run(
            monkeypatch, kept=["P-1"], excluded=["X-1"], unknown=["N-1"]
        )

        assert outcome.rows_rejected == 0
        assert outcome.ok

    def test_les_lignes_ecartees_sont_dites(self, monkeypatch):
        """Écarter en silence serait la troncature muette que ce projet refuse."""
        outcome, _ = self.run(
            monkeypatch, kept=["P-1"], excluded=["X-1", "X-2"], unknown=["N-1"]
        )

        assert outcome.details["outOfScopeLines"] == 2
        assert outcome.details["unknownLines"] == 1
        assert len(outcome.warnings) == 3

    def test_les_articles_sont_comptes_a_part_des_lignes(self, monkeypatch):
        """Un article hors périmètre présent sur dix emplacements fait dix
        lignes et un article : les deux chiffres ne disent pas la même chose."""
        items = referential("P-1", "X-1", excluded=("X-1",))
        rows = stock_rows("P-1") + [
            {"item_number": "X-1", "warehouse_id": "B06",
             "location_id": f"L{n}", "qty": "5"}
            for n in range(10)
        ]
        service, _ = import_service(monkeypatch, rows=rows, items=items)

        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="stock.csv"
        )

        assert outcome.details["outOfScopeLines"] == 10
        assert outcome.details["outOfScopeItems"] == 1

    def test_les_references_manquantes_sont_nommees_pas_seulement_comptees(
        self, monkeypatch
    ):
        """C'est ce que la vue Contrôles relit. Un compte seul n'y suffit pas :
        personne ne peut compléter un référentiel à partir d'un nombre."""
        outcome, _ = self.run(monkeypatch, kept=["P-1"], unknown=["N-2", "N-1"])

        assert outcome.details["unknownItemNumbers"] == ["N-1", "N-2"]
        assert outcome.details["outOfScopeItemNumbers"] == []

    def test_la_liste_nommee_est_bornee_mais_le_total_ne_l_est_pas(
        self, monkeypatch
    ):
        """Un fichier ERP contre un référentiel vide en produirait des dizaines
        de milliers : le rapport deviendrait une copie du fichier."""
        from inventory.services.import_service import UNKNOWN_ITEMS_KEPT

        trop = [f"N-{n:05d}" for n in range(UNKNOWN_ITEMS_KEPT + 25)]
        outcome, _ = self.run(monkeypatch, kept=["P-1"], unknown=trop)

        assert len(outcome.details["unknownItemNumbers"]) == UNKNOWN_ITEMS_KEPT
        assert outcome.details["unknownItems"] == len(trop)

    def test_un_fichier_entierement_hors_perimetre_n_ecrit_rien(self, monkeypatch):
        """Et le dit, plutôt que de laisser croire à un chargement réussi."""
        outcome, written = self.run(monkeypatch, kept=[], excluded=["X-1"])

        assert written == []
        assert outcome.details["outOfScopeLines"] == 1

    def test_sans_exclusion_le_decompte_est_zero(self, monkeypatch):
        outcome, written = self.run(monkeypatch, kept=["P-1", "P-2"])

        assert written == [2]
        assert outcome.details["outOfScopeLines"] == 0
        assert outcome.details["unknownLines"] == 0
        assert outcome.warnings == []

    def test_un_article_inconnu_n_annule_plus_l_ecriture(self, monkeypatch):
        """Le cas demandé : charger ce qui est connu, signaler le reste.

        La garde du remplacement n'a pas bougé — elle vise toujours les vraies
        erreurs de fichier — mais une référence que le référentiel ignore n'en
        est plus une.
        """
        service, written = import_service(
            monkeypatch, rows=stock_rows("P-1", "INCONNU"),
            items=referential("P-1"),
        )

        outcome = service.import_book_stock(
            campaign(), payload=b"x", filename="s.csv"
        )

        assert written == [1]
        assert outcome.ok
        assert outcome.details["unknownItemNumbers"] == ["INCONNU"]


# --------------------------------------------------------------------------- #
# Ce que la vue Contrôles en dit
# --------------------------------------------------------------------------- #

class TestTheControlSaysWhatWasLeftOut:
    """Le rapport d'import disparaît quand on quitte l'écran ; le constat, non.

    C'est toute la raison de ce contrôle : sans lui, « importe les connues et
    signale le reste » se réduirait à un bandeau qu'on voit une fois.
    """

    def test_sans_chargement_aucun_constat(self):
        assert check_stock_import(report=None) == []
        assert check_stock_import(report={}) == []

    def test_un_rapport_d_avant_ce_decoupage_ne_dit_rien_plutot_que_de_casser(self):
        """Les campagnes en cours portent des rapports sans ces clés."""
        assert check_stock_import(report={"rowsReceived": 12}) == []

    def test_chaque_reference_inconnue_a_son_constat(self):
        findings = check_stock_import(report={
            "unknownItemNumbers": ["N-1", "N-2"],
            "unknownItems": 2, "unknownLines": 3,
        })

        assert [f.item_number for f in findings] == ["N-1", "N-2"]
        assert {f.code for f in findings} == {"BOOK_STOCK_UNKNOWN_ITEM"}
        assert all(f.severity is ControlSeverity.WARNING for f in findings)

    def test_le_constat_dit_ce_qu_il_faut_faire(self):
        findings = check_stock_import(report={
            "unknownItemNumbers": ["N-1"], "unknownItems": 1, "unknownLines": 1,
        })

        assert "référentiel articles" in findings[0].message
        assert "rechargez" in findings[0].message

    def test_l_article_exclu_est_signale_sans_reproche(self):
        """C'est une décision : la ranger en avertissement ferait passer pour
        un défaut ce que quelqu'un a délibérément choisi."""
        findings = check_stock_import(report={
            "outOfScopeItemNumbers": ["X-1"],
            "outOfScopeItems": 1, "outOfScopeLines": 4,
        })

        assert [f.code for f in findings] == ["BOOK_STOCK_OUT_OF_SCOPE"]
        assert findings[0].severity is ControlSeverity.INFO

    def test_les_deux_motifs_cohabitent_sans_se_melanger(self):
        findings = check_stock_import(report={
            "unknownItemNumbers": ["N-1"], "unknownItems": 1, "unknownLines": 1,
            "outOfScopeItemNumbers": ["X-1"], "outOfScopeItems": 1,
            "outOfScopeLines": 1,
        })

        by_code = {f.code: f.item_number for f in findings}
        assert by_code == {
            "BOOK_STOCK_UNKNOWN_ITEM": "N-1",
            "BOOK_STOCK_OUT_OF_SCOPE": "X-1",
        }

    def test_une_liste_tronquee_ne_se_lit_pas_comme_complete(self):
        """Sans cette ligne, deux cents références nommées sur douze mille
        laisseraient croire que le référentiel n'en manque que deux cents."""
        findings = check_stock_import(report={
            "unknownItemNumbers": ["N-1", "N-2"],
            "unknownItems": 12_000, "unknownLines": 30_000,
        })

        summary = findings[-1]
        assert summary.item_number == ""
        assert "12000 références au total" in summary.message
        assert summary.context["total"] == 12_000

    def test_une_liste_complete_n_ajoute_pas_de_resume(self):
        findings = check_stock_import(report={
            "unknownItemNumbers": ["N-1", "N-2"],
            "unknownItems": 2, "unknownLines": 2,
        })

        assert len(findings) == 2


def test_la_ligne_ecartee_n_est_pas_une_erreur_de_ligne():
    """Le type est le même — `RowError` — mais la liste ne l'est pas.

    C'est cette distinction, et elle seule, qui empêche la garde du
    remplacement de se déclencher.
    """
    _, errors, out_of_scope, unknown = map_book_stock(
        "camp-1", stock_rows("P-2", "INCONNU"),
        items=referential("P-2", excluded=("P-2",)),
    )

    assert isinstance(out_of_scope[0], RowError)
    assert isinstance(unknown[0], RowError)
    assert errors == []


def test_la_ligne_hors_perimetre_n_apparait_pas_dans_le_stock_ecrit(monkeypatch):
    """Le bout qui compte vraiment : ce qui atterrit en base."""
    items = referential("P-1", "X-1", excluded=("X-1",))
    service, _ = import_service(
        monkeypatch, rows=stock_rows("P-1", "X-1", "INCONNU"), items=items
    )
    ecrit: list[BookStockLine] = []
    service.ctx.book_stock = SimpleNamespace(
        replace=lambda cid, lines, **k: ecrit.extend(lines) or len(lines)
    )

    service.import_book_stock(campaign(), payload=b"x", filename="stock.csv")

    assert [line.item_number for line in ecrit] == ["P-1"]
    assert all(line.qty == Decimal("10.000000") for line in ecrit)


class TestTheControlIsActuallyWiredIn:
    """Le contrôle existe-t-il *dans l'écran*, ou seulement dans son module ?

    Écrit après coup, parce que la vérification par mutation l'a réclamé :
    débrancher entièrement l'appel dans `_all_findings` ne faisait tomber aucun
    contrôle. Une fonction parfaite que rien n'appelle est le défaut que ce
    projet a déjà payé une fois — l'archivage des pièces n'avait jamais abouti,
    et tout ce qui l'entourait était vert.
    """

    def analysis(self, *, report=None, targets=("book_stock",)):
        from inventory.services.analysis_service import AnalysisService

        rows = [
            {"target": t, "report": report if t == "book_stock" else {}}
            for t in targets
        ]
        ctx = SimpleNamespace(
            referentials=SimpleNamespace(
                items_by_number=lambda cid: {},
                list_bom_links=lambda cid: [],
            ),
            imports=SimpleNamespace(latest_per_target=lambda cid: rows),
            sheets=SimpleNamespace(list_zones=lambda cid: []),
            book_stock=SimpleNamespace(list=lambda cid: []),
        )
        return AnalysisService(cast(Any, ctx))

    def test_l_ecran_des_controles_montre_les_references_ecartees(self):
        findings = self.analysis(report={
            "unknownItemNumbers": ["N-1"], "unknownItems": 1, "unknownLines": 1,
        })._all_findings(campaign())

        assert [f.item_number for f in findings if f.code == "BOOK_STOCK_UNKNOWN_ITEM"] == ["N-1"]

    def test_sans_chargement_de_stock_l_ecran_ne_dit_rien_de_plus(self):
        findings = self.analysis(report=None, targets=("items",))._all_findings(
            campaign()
        )

        assert findings == []

    def test_le_constat_survit_a_un_stock_entierement_ecarte(self):
        """Le cas où il compte le plus : aucune ligne chargée, donc aucun des
        contrôles qui lisent le stock ne s'exécute. Sans celui-ci, l'écran
        n'aurait rien à dire d'un chargement qui n'a rien chargé."""
        findings = self.analysis(report={
            "unknownItemNumbers": ["N-1", "N-2"], "unknownItems": 2,
            "unknownLines": 2,
        })._all_findings(campaign())

        assert len(findings) == 2
