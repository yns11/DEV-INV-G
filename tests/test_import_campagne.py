"""Reprendre une grille d'une autre campagne.

Le besoin est banal et revenait à chaque campagne : le référentiel articles
d'un trimestre est celui du suivant à quelques lignes près, un stock ERP de
contrôle se rejoue, et les journaux de comptage avancés d'une campagne annulée
n'ont aucune raison d'être ressaisis. La duplication de campagne couvre le cas
où l'on repart de zéro — elle crée une campagne. Elle ne couvre pas celui, bien
plus fréquent, où la campagne existe déjà et où il ne manque qu'une grille.

Ce que ce module tient
----------------------
**Une seule définition de « ce qu'une campagne sait ressortir ».** L'export
Excel d'une grille et sa reprise depuis une autre campagne répondent à la même
question ; deux listes auraient fini par répondre différemment — une grille
exportable qu'on ne saurait pas relire, ou une route qui accepte ce que le
service refuse.

**Ce n'est pas une porte dérobée.** Les lignes rentrent par le même point que
le fichier et le collage : un article absent du référentiel reste une erreur de
ligne, un chargement qui remplace refuse toujours d'écrire un ensemble amputé,
et une source plus grande que le plafond est refusée en le disant plutôt que
tronquée.
"""

from __future__ import annotations

import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from early_count_db import disposable_database

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Une seule définition, deux lecteurs
# --------------------------------------------------------------------------- #

class TestLesDeuxLecturesPartagentLeurDefinition:
    def test_le_rapport_ne_recopie_pas_la_liste(self):
        """`_grid_rows` du rapport délègue, il ne redéfinit pas.

        Deux copies auraient fini par diverger sur ce qu'une campagne sait
        ressortir, et la divergence ne se serait vue que le jour où une grille
        s'exporte sans se relire.
        """
        source = (
            ROOT / "app" / "inventory" / "services" / "report_service.py"
        ).read_text(encoding="utf-8")
        body = source[source.index("def _grid_rows("):]
        body = body[: body.index("def _kpi_rows(")]
        assert "campaign_grid_rows(ctx, campaign, key)" in body
        assert "case \"items\"" not in body, "la définition a été recopiée"

    def test_la_route_ne_recopie_pas_la_liste_non_plus(self):
        source = (
            ROOT / "app" / "inventory" / "api" / "routers" / "data.py"
        ).read_text(encoding="utf-8")
        assert "CAMPAIGN_TARGETS = CAMPAIGN_SOURCE_GRIDS" in source

    def test_chaque_grille_annoncee_sait_repondre(self):
        """Une grille de la liste qui ne rendrait rien serait pire qu'absente :
        l'écran l'offrirait, et l'import rapporterait zéro ligne sans erreur."""
        from inventory.services.campaign_source import SUPPORTED, grid_rows

        tree = ast.parse(
            (
                ROOT / "app" / "inventory" / "services" / "campaign_source.py"
            ).read_text(encoding="utf-8")
        )
        handled: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Match):
                for case in node.cases:
                    pattern = case.pattern
                    if isinstance(pattern, ast.MatchValue) and isinstance(
                        pattern.value, ast.Constant
                    ):
                        handled.add(str(pattern.value.value))
        assert handled >= SUPPORTED, sorted(SUPPORTED - handled)
        assert callable(grid_rows)

    def test_chaque_grille_annoncee_a_un_contrat(self):
        from inventory.ingest import get_contract
        from inventory.services.campaign_source import SUPPORTED

        for key in sorted(SUPPORTED):
            assert get_contract(key).fields, key

    def test_les_journaux_de_comptage_avances_en_sont(self):
        """C'est la grille que l'exploitation a nommée en premier."""
        from inventory.services.campaign_source import SUPPORTED

        assert "count_journal_lines" in SUPPORTED

    def test_lecran_offre_exactement_ce_que_le_serveur_accepte(self):
        """Un bouton offert sur une grille que le serveur refuse est une
        impasse ; une grille reprenable sans bouton est une fonction que
        personne ne peut atteindre. C'est la forme même du défaut récurrent de
        ce dépôt — « ça existe, mais ce n'est pas branché »."""
        import re

        from inventory.services.campaign_source import SUPPORTED

        panel = (
            ROOT / "frontend" / "src" / "components" / "ImportPanel.tsx"
        ).read_text(encoding="utf-8")
        block = panel[panel.index("const CAMPAIGN_TARGETS = ["):]
        block = block[: block.index("]")]
        assert set(re.findall(r"'([a-z_]+)'", block)) == set(SUPPORTED)

    def test_chaque_grille_reprenable_sait_se_compter(self):
        """Sans requête de comptage, la fenêtre annonce « 0 ligne » sur toutes
        les campagnes — donc aucune n'est choisissable, alors qu'elles portent
        de quoi remplir."""
        from inventory.db.repositories.campaign import CampaignRepository
        from inventory.services.campaign_source import SUPPORTED

        assert set(CampaignRepository._GRID_TABLES) >= SUPPORTED

    def test_le_mode_est_declare_comme_les_autres(self):
        from inventory.services.import_parsing import InputMode

        assert "campaign" in InputMode.__args__  # type: ignore[attr-defined]


class TestLesColonnesSontCellesDuContrat:
    """Une ligne rendue dans le désordre se relit en silence, et de travers.

    Le nom des colonnes vient du contrat — la même source que les en-têtes du
    classeur exporté — et non d'une liste recopiée à côté.
    """

    def test_les_cles_viennent_du_contrat(self):
        source = (
            ROOT / "app" / "inventory" / "services" / "campaign_source.py"
        ).read_text(encoding="utf-8")
        assert "get_contract(key).fields" in source

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("items", 12),
            ("book_stock", 6),
            ("count_sheets", 5),
            ("count_journal_lines", 17),
        ],
    )
    def test_chaque_ligne_porte_autant_de_valeurs_que_le_contrat_a_de_colonnes(
        self, key: str, expected: int
    ):
        """Sans cela, `zip` tronque en silence et les dernières colonnes
        disparaissent — l'exclusion d'un article, le type d'un journal."""
        from inventory.ingest import get_contract

        assert len(get_contract(key).fields) == expected


# --------------------------------------------------------------------------- #
# Ce que le mode refuse
# --------------------------------------------------------------------------- #

class TestCeQuiEstRefuse:
    def _parser(self, rows_by_grid=None, source=None, limit=100_000):
        from types import SimpleNamespace
        from typing import Any, cast

        from inventory.services.import_parsing import ImportParser

        ctx = cast(Any, SimpleNamespace(
            actor="chef@usine",
            settings=SimpleNamespace(max_import_rows=limit, max_upload_bytes=10_000_000),
            campaigns=SimpleNamespace(get=lambda cid: source),
        ))
        return ImportParser(ctx)

    def test_sans_campagne_source_designee(self):
        from inventory.errors import ValidationError

        with pytest.raises(ValidationError, match="source"):
            self._parser().parse("items", mode="campaign")

    def test_une_grille_qui_ne_se_reprend_pas(self):
        from inventory.errors import ValidationError

        with pytest.raises(ValidationError, match="autre campagne"):
            self._parser().parse(
                "backflush", mode="campaign", source_campaign_id="c-2"
            )

    def test_une_campagne_source_introuvable(self):
        from inventory.errors import NotFoundError

        with pytest.raises(NotFoundError):
            self._parser(source=None).parse(
                "items", mode="campaign", source_campaign_id="c-inconnue"
            )

    def test_la_campagne_courante_est_refusee_par_la_route(self):
        """Se reprendre soi-même ne veut rien dire, et un remplacement
        s'effacerait avant de se réécrire."""
        source = (
            ROOT / "app" / "inventory" / "api" / "routers" / "data.py"
        ).read_text(encoding="utf-8")
        assert "source_campaign_id == campaign.id" in source

    def test_une_source_au_dela_du_plafond_est_refusee_en_le_disant(self):
        """Même règle que les lectures ERP : jamais de troncature en silence.

        Une grille amputée, c'est un comptage contre un référentiel incomplet,
        et l'écart qui en sort n'est l'écart de rien.
        """
        source = (
            ROOT / "app" / "inventory" / "services" / "import_parsing.py"
        ).read_text(encoding="utf-8")
        body = source[source.index("def _read_campaign"):]
        body = body[: body.index("def _read_erp")]
        assert "len(rows) > limit" in body
        assert "plafond" in body


# --------------------------------------------------------------------------- #
# De bout en bout, sur une vraie base
# --------------------------------------------------------------------------- #

@pytest.mark.postgres
class TestSurUneVraieBase:
    """Le contrôle qui compte : deux campagnes, et la grille qui passe.

    Les précédents portent sur la forme ; celui-ci porte sur ce qu'un écran
    obtiendrait.
    """

    @pytest.fixture
    def bench(self):
        from inventory.config import get_settings
        from inventory.domain.models import Item
        from inventory.services import CampaignService, ImportService, ServiceContext

        with disposable_database("inventaire_import_campagne") as database:
            ctx = ServiceContext(actor="local@dev", db=database, settings=get_settings())
            campaigns = CampaignService(ctx)
            stamp = dt.datetime.now(dt.UTC).strftime("%H%M%S%f")
            source = campaigns.create(
                code=f"SRC-{stamp}", label="Source", count_date=dt.date(2026, 6, 30)
            )
            ctx.referentials.upsert_items(
                [
                    Item(campaign_id=source.id, item_number="P-00001", name="VIS M6",
                         std_price=Decimal("2.5"), unit="PCE"),
                    Item(campaign_id=source.id, item_number="P-00002", name="ROTOR",
                         std_price=Decimal("50"), exclusions={"GENERIC"}),
                ],
                actor=ctx.actor,
            )
            target = campaigns.create(
                code=f"DST-{stamp}", label="Cible", count_date=dt.date(2026, 9, 30)
            )
            yield ctx, ImportService(ctx), source, target

    def test_lessai_a_blanc_annonce_ce_qui_passera(self, bench):
        _ctx, imports, source, _target = bench
        preview = imports.preview(
            "items", mode="campaign", source_campaign_id=source.id
        )
        assert preview["rowsAccepted"] == 2
        assert preview["rowsRejected"] == 0
        # L'échantillon est ce que l'écran montre avant d'écrire quoi que ce soit.
        assert {row["item_number"] for row in preview["sample"]} == {
            "P-00001", "P-00002",
        }

    def test_les_articles_arrivent_dans_la_cible(self, bench):
        ctx, imports, source, target = bench
        outcome = imports.import_items(
            target, mode="campaign", source_campaign_id=source.id
        )
        assert outcome.rows_accepted == 2
        assert {i.item_number for i in ctx.referentials.list_items(target.id)} == {
            "P-00001", "P-00002",
        }

    def test_le_prix_et_lunite_suivent(self, bench):
        """Une reprise qui perdrait le prix standard rendrait tous les écarts de
        la campagne faux, et sans rien signaler : c'est la seule base de
        valorisation."""
        ctx, imports, source, target = bench
        imports.import_items(target, mode="campaign", source_campaign_id=source.id)
        by_number = {i.item_number: i for i in ctx.referentials.list_items(target.id)}
        assert by_number["P-00001"].std_price == Decimal("2.50")
        assert by_number["P-00001"].unit == "PCE"
        assert by_number["P-00001"].name == "VIS M6"

    def test_lexclusion_suit_aussi(self, bench):
        """C'est une décision de campagne, et la reprendre est bien l'intention
        de qui recopie un référentiel — la retaper sur quatre mille lignes ne
        l'est pas."""
        ctx, imports, source, target = bench
        imports.import_items(target, mode="campaign", source_campaign_id=source.id)
        rotor = next(
            i for i in ctx.referentials.list_items(target.id)
            if i.item_number == "P-00002"
        )
        assert rotor.excluded_from_generic

    def test_la_source_nest_pas_touchee(self, bench):
        """Une reprise lit ; elle n'écrit que dans la campagne de l'URL."""
        ctx, imports, source, target = bench
        imports.import_items(target, mode="campaign", source_campaign_id=source.id)
        assert len(ctx.referentials.list_items(source.id)) == 2

    def test_le_decompte_dit_ou_sont_les_donnees(self, bench):
        """Le chiffre qui fait choisir : sans lui, on désigne une campagne au
        jugé et on découvre après coup qu'elle ne portait rien."""
        ctx, _imports, source, target = bench
        from inventory.services.campaign_source import candidates

        rows = candidates(ctx, target, "items")
        mine = next(r for r in rows if r["id"] == source.id)
        assert mine["rows"] == 2
        assert mine["code"] == source.code

    def test_la_campagne_courante_nest_pas_proposee(self, bench):
        """Se reprendre soi-même ne veut rien dire, et un remplacement
        s'effacerait avant de se réécrire."""
        ctx, _imports, _source, target = bench
        from inventory.services.campaign_source import candidates

        assert all(r["id"] != target.id for r in candidates(ctx, target, "items"))

    def test_une_campagne_sans_rien_se_voit_a_zero(self, bench):
        """Grisée plutôt que masquée : « elle n'a rien » est une réponse, et la
        cacher ferait chercher une campagne qu'on croit avoir oubliée."""
        ctx, _imports, source, _target = bench
        from inventory.services.campaign_source import candidates

        rows = candidates(ctx, source, "adjustments")
        assert rows and all(r["rows"] == 0 for r in rows)

    def test_la_provenance_est_tracee(self, bench):
        """« D'où vient cette quantité » doit avoir une réponse six mois plus
        tard, et « d'une autre campagne » en est une."""
        ctx, imports, source, target = bench
        imports.import_items(target, mode="campaign", source_campaign_id=source.id)
        batches = ctx.imports.list(target.id)
        assert batches, "aucun lot enregistré"
        assert any(b["target"] == "items" for b in batches)
