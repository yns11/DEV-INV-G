"""Aucun référentiel ne se tronque en silence.

Trois listes de référence, trois formes différentes. Les articles paginaient
déjà correctement — total, décalage, plafond — et l'écran disait « 42 000
article(s) — 20 000 affichés ». Les nomenclatures et les lignes de feuilles,
elles, partaient entières : pas de plafond, pas de total, rien.

Le défaut n'est pas seulement le volume. Une liste sans total est
**indistinguable d'une liste complète** : l'écran ne peut pas dire qu'il en
manque, donc il ne le dit pas, et personne ne sait ce qui n'est pas là. Sur une
nomenclature de cinquante mille liens, c'est un appel qui tient une seconde à
lui seul et un navigateur qui trie cinquante mille lignes pour en montrer
trente. Sur les lignes de feuilles — la liste qu'on ouvre justement pour
retrouver une ligne saisie dans la mauvaise zone — c'est pire : la ligne
cherchée peut ne pas être là, sans que rien ne le signale.

``render`` n'est appliqué qu'à la page, et c'est vérifié : une ligne de
nomenclature va chercher deux désignations au référentiel, et payer ce prix sur
des lignes qu'on jette ensuite annulerait le bénéfice de la pagination.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from inventory.api.paging import MAX_PAGE, page

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# La tranche elle-même
# --------------------------------------------------------------------------- #

class TestThePageCarriesWhatTheScreenNeeds:
    def test_it_returns_the_asked_slice(self):
        got = page(list(range(100)), offset=10, limit=5, render=lambda n: {"n": n})
        assert [r["n"] for r in got["rows"]] == [10, 11, 12, 13, 14]

    def test_it_says_how_many_exist(self):
        """Sans lui, une liste tronquée ressemble à une liste complète."""
        got = page(list(range(100)), offset=0, limit=5, render=lambda n: {"n": n})
        assert got["total"] == 100

    def test_the_total_is_not_the_page_size(self):
        """Le témoin : rendre `len(rows_de_la_page)` serait le défaut d'origine."""
        got = page(list(range(100)), offset=0, limit=5, render=lambda n: {"n": n})
        assert got["total"] != len(got["rows"])

    def test_it_echoes_where_it_is(self):
        got = page(list(range(100)), offset=30, limit=5, render=lambda n: {"n": n})
        assert (got["offset"], got["limit"]) == (30, 5)

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        got = page(list(range(10)), offset=500, limit=5, render=lambda n: {"n": n})
        assert got["rows"] == []
        assert got["total"] == 10

    def test_an_empty_list_still_carries_a_total(self):
        assert page([], offset=0, limit=5, render=lambda n: {})["total"] == 0


class TestTheDetailIsPaidOnlyOnThePage:
    def test_render_never_touches_a_row_that_is_not_returned(self):
        """Un lien de nomenclature va chercher deux désignations : les payer sur
        cinquante mille lignes pour en rendre trente annulerait la pagination."""
        seen: list[int] = []

        def render(n: int) -> dict[str, Any]:
            seen.append(n)
            return {"n": n}

        page(list(range(10_000)), offset=0, limit=3, render=render)
        assert seen == [0, 1, 2]

    def test_the_total_is_still_right_although_nothing_was_rendered(self):
        got = page(list(range(10_000)), offset=0, limit=0, render=lambda n: 1 / 0)
        assert got["total"] == 10_000


class TestTheCeiling:
    def test_it_exists_and_is_the_grid_ceiling(self):
        assert MAX_PAGE == 20_000

    def test_the_browser_asks_for_the_same_one(self):
        api = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text()
        assert "export const GRID_ROW_CEILING = 20_000" in api


# --------------------------------------------------------------------------- #
# Les trois routes
# --------------------------------------------------------------------------- #

#: Les listes de référence, et le module qui les sert.
LISTS = [
    ("api/routers/data.py", "list_items"),
    ("api/routers/data.py", "list_boms"),
    ("api/routers/generic.py", "list_all_lines"),
]


def endpoint(relative: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / "app" / "inventory" / relative).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} introuvable dans {relative}")


def parameters(fn: ast.FunctionDef) -> set[str]:
    return {a.arg for a in fn.args.args + fn.args.kwonlyargs}


class TestEveryReferentialListPaginates:
    @pytest.mark.parametrize("relative,name", LISTS)
    def test_it_takes_a_limit_and_an_offset(self, relative, name):
        assert {"limit", "offset"} <= parameters(endpoint(relative, name))

    @pytest.mark.parametrize("relative,name", LISTS)
    def test_the_limit_is_bounded_by_the_shared_ceiling(self, relative, name):
        """Un plafond écrit en dur dans chaque route dérive ; celui-ci est un seul."""
        source = ast.unparse(endpoint(relative, name))
        assert "Query(ge=1, le=MAX_PAGE)" in source

    @pytest.mark.parametrize("relative,name", LISTS)
    def test_a_negative_offset_is_refused_by_the_signature(self, relative, name):
        assert "Query(ge=0)" in ast.unparse(endpoint(relative, name))

    @pytest.mark.parametrize("relative,name", LISTS)
    def test_it_returns_a_page_and_not_a_bare_list(self, relative, name):
        """`-> list[dict]` est exactement la signature qui ne peut pas porter
        de total : c'est la forme d'origine, et sa disparition est la garantie."""
        fn = endpoint(relative, name)
        assert fn.returns is not None
        assert ast.unparse(fn.returns) == "dict[str, Any]"

    @pytest.mark.parametrize("relative,name", LISTS)
    def test_it_builds_that_page_with_the_shared_helper(self, relative, name):
        """Trois façons de composer la même enveloppe, c'est trois occasions
        d'en oublier une clé — et l'écran lit les quatre."""
        assert "return page(" in ast.unparse(endpoint(relative, name))


class TestTheFilteringHappensBeforeTheCount:
    """Un total qui compterait les lignes filtrées annoncerait une suite
    que « charger la suite » ne ramènerait jamais.

    Le filtrage appartient au service ; le routeur ne fait que découper ce
    qu'il reçoit. Ces contrôles lisent donc le service, et leur seule façon de
    passer est que la route ne filtre plus rien elle-même.
    """

    def test_the_bill_filters_in_the_service(self):
        source = ast.unparse(
            endpoint("services/referential_service.py", "list_bom_links")
        )
        assert "if counted:" in source

    def test_the_article_filters_in_the_service(self):
        source = ast.unparse(
            endpoint("services/referential_service.py", "list_items")
        )
        assert "if search:" in source

    @pytest.mark.parametrize("name", ["list_items", "list_boms"])
    def test_the_route_pages_what_the_service_gives_it(self, name):
        """Filtrer des deux côtés est la façon dont les deux divergent."""
        source = ast.unparse(endpoint("api/routers/data.py", name))
        assert "if counted:" not in source
        assert "if search:" not in source


# --------------------------------------------------------------------------- #
# Les nomenclatures, en vrai
# --------------------------------------------------------------------------- #

from test_item_exclusions import CAMPAIGN, context  # noqa: E402

from inventory.api.routers import data  # noqa: E402
from inventory.domain.models import BomLink  # noqa: E402
from inventory.services.referential_service import ReferentialService  # noqa: E402


def links(n: int) -> list[BomLink]:
    return [
        BomLink(campaign_id="camp-1", parent_item=f"A{i}", child_item="C", qty_per=1)
        for i in range(n)
    ]


def bom_route(ctx, **kwargs):
    """La route, servie par un vrai service sur un contexte factice."""
    return data.list_boms(CAMPAIGN, ReferentialService(ctx), **kwargs)


class TestTheBillOfMaterialsIsPaged:
    def test_a_default_call_does_not_return_fifty_thousand_edges(self):
        got = bom_route(context(links=links(6000)))
        assert len(got["rows"]) == 5000
        assert got["total"] == 6000

    def test_the_second_page_carries_the_rest(self):
        got = bom_route(context(links=links(6000)), offset=5000)
        assert len(got["rows"]) == 1000

    def test_the_pages_do_not_overlap(self):
        ctx = context(links=links(30))
        first = bom_route(ctx, limit=10)
        second = bom_route(ctx, limit=10, offset=10)
        assert not {r["parent_item"] for r in first["rows"]} & {
            r["parent_item"] for r in second["rows"]
        }

    def test_the_page_still_carries_the_designations(self):
        """La pagination ne doit pas emporter ce qui rend la liste lisible."""
        got = bom_route(context(links=links(3)), limit=1)
        assert "parentName" in got["rows"][0]
        assert "childName" in got["rows"][0]

    def test_the_total_counts_the_filtered_set(self):
        ctx = context(links=links(50), on_sheets={"A1"})
        got = bom_route(ctx, counted=True)
        assert got["total"] == 1


# --------------------------------------------------------------------------- #
# Ce que le navigateur en fait
# --------------------------------------------------------------------------- #

def frontend(relative: str) -> str:
    return (ROOT / "frontend" / "src" / relative).read_text()


class TestTheClientAsksForAPage:
    def test_the_bill_call_accepts_a_limit(self):
        source = frontend("lib/api.ts")
        block = source[source.index("boms: (") :][:400]
        assert "limit?: number" in block
        assert "offset?: number" in block

    def test_the_sheet_lines_call_accepts_one_too(self):
        source = frontend("lib/api.ts")
        block = source[source.index("sheetLines: (") :][:400]
        assert "limit?: number" in block

    @pytest.mark.parametrize("name", ["boms", "sheetLines"])
    def test_the_answer_is_typed_as_a_page(self, name):
        """`Array<Record<…>>` était le type qui ne pouvait pas porter de total."""
        source = frontend("lib/api.ts")
        block = source[source.index(f"{name}: (") :][:500]
        assert "request<Page>" in block

    def test_the_page_type_names_its_four_keys(self):
        source = frontend("lib/api.ts")
        block = source[source.index("export type Page") :][:300]
        for key in ("total", "offset", "limit", "rows"):
            assert key in block


class TestTheScreensSayWhatTheyAreNotShowing:
    def test_the_bill_grid_reads_the_rows_of_the_page(self):
        assert "links.data?.rows ?? []" in frontend("features/Preparation.tsx")

    def test_the_bill_grid_computes_what_is_missing(self):
        source = frontend("features/Preparation.tsx")
        assert "(links.data?.total ?? rows.length) - rows.length" in source

    def test_the_bill_grid_says_it(self):
        assert "lien(s) non chargé(s)" in frontend("features/Preparation.tsx")

    def test_the_sheet_lines_grid_reads_the_rows_of_the_page(self):
        assert "query.data?.rows ?? []" in frontend("features/Preparation.tsx")

    def test_the_sheet_lines_footer_shows_the_true_total(self):
        """Afficher `rows.length` là où le total existe redirait « 20 000 »
        d'une liste qui en compte soixante mille."""
        source = frontend("features/Preparation.tsx")
        assert "(draft ? rows.length : (query.data?.total ?? 0))" in source

    def test_the_missing_count_ignores_the_draft(self):
        """`rows` porte le brouillon en cours d'édition : une ligne ajoutée à la
        main ferait sinon baisser le nombre de lignes « non chargées »."""
        source = frontend("features/Preparation.tsx")
        assert "const loaded = query.data?.rows.length ?? 0" in source
        assert "(query.data?.total ?? loaded) - loaded" in source

    @pytest.mark.parametrize(
        "call", ["api.boms(campaignId", "api.sheetLines(campaignId"]
    )
    def test_the_screen_asks_for_the_ceiling_rather_than_a_default(self, call):
        """Le défaut du serveur est de cinq mille ; la grille en tient vingt
        mille, et une nomenclature entière est ce qu'on vient y lire."""
        source = frontend("features/Preparation.tsx")
        block = source[source.index(call) :][:300]
        assert "GRID_ROW_CEILING" in block
