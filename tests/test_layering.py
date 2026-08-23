"""API → service → domaine → dépôt, sans raccourci.

Onze opérations vivaient dans les routeurs. Chacune y faisait le travail d'un
service — garder la phase, comparer avec l'existant, écrire, enregistrer
l'audit — dans une fonction dont la signature parlait de requête HTTP.

Trois conséquences, et aucune n'est théorique.

**Un contrôle passait par le transport.** Vérifier qu'une exclusion posée sur un
lot refuse une référence inconnue demandait de construire un contrat Pydantic,
pour une règle qui tient en trois lignes. Les contrôles écrits ainsi vérifient
le statut HTTP, rarement la règle.

**La règle n'était appelable que par un navigateur.** L'assistant, un job, une
reprise en lot : tout ce qui n'entre pas par HTTP devait la réimplémenter, ou
passer outre — et écrire sans audit.

**Un contrôle d'accès était dans une route.** ``sheet.campaign_id !=
campaign.id`` — la barrière qui empêche de lire le scan d'une autre campagne —
était écrit dans le routeur des pièces justificatives. Il y était juste, et
pourtant appliqué seulement par les appelants qui passent par là.

Ce module est ce qui empêche la couche de se reformer. Il ne vérifie pas des
noms de fichiers : il lit les arbres syntaxiques et cherche les gestes qui la
percent — un routeur qui touche un dépôt, un routeur qui traverse un service
pour atteindre le sien, un routeur qui pose lui-même l'audit ou la garde.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTERS = ROOT / "app" / "inventory" / "api" / "routers"

#: Les dépôts exposés par le contexte de service. Les nommer un par un plutôt
#: que de chercher « repository » attrape ce qui compte : ce sont ces attributs
#: que les routes utilisaient.
REPOSITORIES = (
    "adjustments", "analysis", "audit", "backflush", "book_stock", "campaigns",
    "consolidation", "evidence", "imports", "journals", "referentials",
    "scan_jobs", "sheets", "stock_flow",
)

ROUTER_FILES = sorted(p for p in ROUTERS.glob("*.py") if p.name != "__init__.py")


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def attribute_chains(node: ast.AST) -> list[str]:
    """Toutes les chaînes ``a.b.c`` du module, en texte.

    Comparer sur le texte de la chaîne plutôt que sur le nœud permet de dire
    « ``ctx.sheets`` » et « ``service.ctx`` » dans la même règle, qui est bien
    la même règle : atteindre une couche en sautant celle du dessus.
    """
    return [
        ast.unparse(sub) for sub in ast.walk(node) if isinstance(sub, ast.Attribute)
    ]


# --------------------------------------------------------------------------- #
# Aucun routeur ne touche un dépôt
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", ROUTER_FILES, ids=lambda p: p.name)
class TestNoRouterReachesTheStorage:
    def test_it_never_reads_a_repository_from_the_context(self, path):
        """`ctx.referentials.list_items(...)` était le geste courant."""
        offenders = [
            chain for chain in attribute_chains(tree(path))
            if any(chain.startswith(f"ctx.{name}.") for name in REPOSITORIES)
        ]
        assert offenders == [], f"{path.name} : {offenders}"

    def test_it_never_goes_through_a_service_to_reach_one(self, path):
        """`service.ctx.referentials...` est pire : le service est là, et la
        route passe à côté pour atteindre ce qu'il protège."""
        offenders = [
            chain for chain in attribute_chains(tree(path))
            if chain.startswith("service.ctx.")
        ]
        assert offenders == [], f"{path.name} : {offenders}"

    def test_it_imports_no_repository_class(self, path):
        """Importer un dépôt dans un routeur est la préparation du reste."""
        imported = {
            alias.name
            for node in ast.walk(tree(path))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not {n for n in imported if n.endswith("Repository")}

    def test_it_writes_no_audit_event_itself(self, path):
        """L'audit se pose là où l'écriture se décide, pas là où elle s'annonce.

        Une route qui enregistre l'événement le fait pour son propre chemin ;
        le même geste appelé autrement n'écrit alors rien du tout.
        """
        offenders = [c for c in attribute_chains(tree(path)) if c == "ctx.record"]
        assert offenders == [], f"{path.name} : {offenders}"

    def test_it_runs_no_phase_guard_itself(self, path):
        """`ctx.guard(campaign, ...)` dans une route : la même règle, ailleurs."""
        offenders = [c for c in attribute_chains(tree(path)) if c == "ctx.guard"]
        assert offenders == [], f"{path.name} : {offenders}"


# --------------------------------------------------------------------------- #
# Ce que le routeur garde
# --------------------------------------------------------------------------- #

class TestWhatStaysAtTheDoor:
    """La sérialisation n'est pas de la règle métier.

    ``float(qty_per)`` existe parce que JSON n'a pas de décimal, pas parce que
    l'inventaire l'exige. La faire descendre dans le service obligerait celui-ci
    à connaître la forme de l'écran — et un service qui rend déjà du JSON ne se
    réutilise que par ce qui produit du JSON, c'est-à-dire par un navigateur.
    """

    def test_the_article_route_still_serialises(self):
        source = (ROUTERS / "data.py").read_text()
        assert "float(item.std_price)" in source

    def test_the_service_returns_domain_objects(self):
        """`-> list[Item]`, pas `-> list[dict]` : c'est la différence."""
        from inventory.services.referential_service import ReferentialService

        annotation = ReferentialService.list_items.__annotations__["return"]
        assert annotation == "list[Item]"


class TestTheRulesAreCallableWithoutHttp:
    """Le contrôle qui prouve le gain : ces règles s'appellent sans application."""

    def test_an_empty_selection_is_refused_without_a_router(self):
        from types import SimpleNamespace

        from inventory.errors import ValidationError
        from inventory.services.referential_service import ReferentialService

        ctx = SimpleNamespace(
            actor="testeur",
            guard=lambda campaign, aspect: None,
            record=lambda **kw: None,
            referentials=SimpleNamespace(items_by_number=lambda cid: {}),
        )
        service = ReferentialService(ctx)
        with pytest.raises(ValidationError):
            service.set_item_exclusions(SimpleNamespace(id="c1"), [], [])

    def test_the_campaign_barrier_on_evidence_is_a_service_rule(self):
        """Elle était dans une route. Elle se vérifie maintenant sans route."""
        from types import SimpleNamespace

        from inventory.errors import NotFoundError
        from inventory.services.evidence_service import EvidenceService

        ctx = SimpleNamespace(
            sheets=SimpleNamespace(
                get_sheet=lambda sid: SimpleNamespace(
                    campaign_id="une-autre", evidence_path="/vol/x.pdf"
                )
            )
        )
        with pytest.raises(NotFoundError):
            EvidenceService(ctx).of_sheet(SimpleNamespace(id="camp-1"), "sheet-9")

    def test_a_sheet_of_this_campaign_is_served(self):
        """Le témoin : sans lui, une barrière qui refuse tout passerait aussi."""
        from types import SimpleNamespace

        from inventory.services.evidence_service import EvidenceService

        ctx = SimpleNamespace(
            sheets=SimpleNamespace(
                get_sheet=lambda sid: SimpleNamespace(
                    campaign_id="camp-1", evidence_path="/vol/scan-42.pdf"
                )
            ),
            evidence=SimpleNamespace(get=lambda path: b"%PDF"),
        )
        found = EvidenceService(ctx).of_sheet(SimpleNamespace(id="camp-1"), "sheet-9")
        assert found.filename == "scan-42.pdf"
        assert found.content == b"%PDF"

    def test_the_volume_path_never_leaves_the_service(self):
        """Le renvoyer en ferait une adresse que quelqu'un fabriquerait à la main."""
        from dataclasses import fields

        from inventory.services.evidence_service import ArchivedEvidence

        assert {f.name for f in fields(ArchivedEvidence)} == {"content", "filename"}

    def test_the_import_history_drops_the_path_in_the_service(self):
        """Le retirer à l'affichage laisserait le prochain appelant le sortir."""
        from types import SimpleNamespace

        from inventory.services.campaign_service import CampaignService

        ctx = SimpleNamespace(
            imports=SimpleNamespace(
                list=lambda cid, *, limit: [
                    {"id": "b1", "filename": "a.csv", "storage_path": "/vol/a.csv"}
                ]
            )
        )
        (row,) = CampaignService(ctx).import_history(SimpleNamespace(id="c1"))
        assert "storage_path" not in row
        assert row["archived"] is True

    def test_an_unarchived_load_says_so_rather_than_omitting_the_key(self):
        from types import SimpleNamespace

        from inventory.services.campaign_service import CampaignService

        ctx = SimpleNamespace(
            imports=SimpleNamespace(
                list=lambda cid, *, limit: [
                    {"id": "b1", "filename": "collage", "storage_path": None}
                ]
            )
        )
        (row,) = CampaignService(ctx).import_history(SimpleNamespace(id="c1"))
        assert row["archived"] is False


class TestEveryServiceIsReachableAsADependency:
    """Un service sans dépendance est un service qu'aucune route ne peut
    utiliser — donc la prochaine route refera le travail sur place."""

    @pytest.mark.parametrize(
        "name", ["referential_service", "evidence_service", "campaign_service"]
    )
    def test_the_dependency_exists(self, name):
        from inventory.api import deps

        assert callable(getattr(deps, name))

    @pytest.mark.parametrize(
        "name", ["ReferentialService", "EvidenceService", "AnalysisService"]
    )
    def test_the_service_is_exported(self, name):
        import inventory.services as services

        assert name in services.__all__
