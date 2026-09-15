"""Le TypeScript vient du serveur, et cesse de le paraphraser.

Le défaut
---------
``types.ts`` décrivait à la main la forme de chaque réponse : soixante-cinq
interfaces recopiées d'un langage à l'autre, qu'aucun mécanisme ne rapprochait
de ce que le backend produit. Renommer un champ côté serveur laissait la
déclaration TypeScript intacte — le compilateur restait content, l'écran
affichait ``undefined``, et rien n'échouait avant la production.

Pourquoi le générateur ne suffisait pas
---------------------------------------
L'audit demandait un client généré depuis l'OpenAPI. Le schéma ne s'y prêtait
pas : les cent dix-sept routes annoncent ``-> dict[str, Any]``, et l'OpenAPI ne
portait donc aucune information de champ. Générer aurait produit
``Record<string, unknown>`` partout, c'est-à-dire **moins** que ce qui était
écrit à la main. Déclarer ce que l'API renvoie était le travail ; générer n'en
est que la conséquence.

Les trois choses que ces contrôles tiennent
-------------------------------------------
1. **La déclaration dit vrai.** Chaque modèle est confronté à une charge utile
   réellement produite par l'API sur une base réelle. Un modèle qui se contente
   d'exister ne vaut rien : c'est la confrontation qui a déjà corrigé quatre
   déclarations — ``where`` nullable, ``default`` entier ou booléen, et les deux
   ``ratio`` absents quand il n'y a rien à compter.

2. **Rien n'est filtré.** ``responses=`` documente ; ``response_model=``
   sérialise **à travers** le modèle et supprime en silence toute clé non
   déclarée. Sur une API dont les charges utiles sont assemblées à la main dans
   les services, une seule omission retirerait un champ que l'écran lit. Un
   contrôle interdit donc la seconde forme sur ces routes.

3. **Le fichier généré est à jour.** Un ``schema.d.ts`` périmé rend exactement
   le service que rendait la déclaration recopiée : il compile, et il ment.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
FRONTEND = ROOT / "frontend"
SCHEMA_TS = FRONTEND / "src" / "lib" / "schema.d.ts"
TYPES_TS = FRONTEND / "src" / "lib" / "types.ts"
DUMP = ROOT / "scripts" / "dump_openapi.py"
RESPONSES = APP / "inventory" / "api" / "responses.py"
ROUTERS = APP / "inventory" / "api" / "routers"
API_APP = APP / "inventory" / "api" / "app.py"


def openapi() -> dict:
    """Le schéma, tel que le script de génération le produit."""
    out = subprocess.run(
        [sys.executable, str(DUMP)], capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "INV_ENV": "local"}, timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


# --------------------------------------------------------------------------- #
# Le schéma porte enfin quelque chose
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def schema() -> dict:
    return openapi()


@pytest.fixture(scope="module")
def client():
    if not os.environ.get("PGHOST"):
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
    from fastapi.testclient import TestClient

    from inventory.api import create_app

    with TestClient(create_app()) as running:
        yield running


@pytest.fixture(scope="module")
def campaign_id(client) -> str:
    """Une campagne **qui porte des données**, pas la première venue.

    La base de contrôle accumule les campagnes créées par le reste de la
    suite : des coquilles sans article ni seuil, plus récentes que celle qui
    est semée. Prendre la première rendait le contrôle dépendant de l'ordre
    d'exécution — et il s'ignorait au lieu d'échouer, ce qui est la pire des
    deux issues.
    """
    items = client.get("/api/campaigns").json().get("items", [])
    if not items:
        pytest.skip("aucune campagne dans la base de contrôle")
    for campaign in items:
        counts = client.get(
            f"/api/campaigns/{campaign['id']}/overview"
        ).json().get("counts", {})
        if counts.get("items"):
            return str(campaign["id"])
    pytest.skip("aucune campagne peuplée dans la base de contrôle")


@pytest.fixture(scope="module")
def fresh_campaign(client) -> str:
    """Une campagne neuve, créée par l'API et retirée ensuite.

    Ce que la base contient déjà ne dit rien de ce qu'une campagne reçoit à sa
    création — les seuils par défaut, par exemple, que seul le service pose.
    """
    created = client.post("/api/campaigns", json={
        "code": "INV-CONTRAT-NEUVE",
        "label": "Contrôle du contrat",
        "countDate": "2026-12-30",
    })
    assert created.status_code == 201, created.text[:300]
    identifier = str(created.json()["id"])
    yield identifier
    client.delete(f"/api/campaigns/{identifier}")


class TestTheSchemaSaysWhatComesBack:
    def declared(self, schema: dict) -> list[str]:
        """Les routes dont la réponse est une forme nommée, pas « un objet »."""
        found = []
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                answers = {
                    code: body
                    for code, body in operation.get("responses", {}).items()
                    if code in ("200", "201")
                }
                if "#/components/schemas/" in json.dumps(answers):
                    found.append(f"{method.upper()} {path}")
        return found

    def test_some_routes_declare_their_answer(self, schema):
        """Avant, aucune : cent treize chemins en `{"type": "object"}`."""
        assert len(self.declared(schema)) >= 16

    @pytest.mark.parametrize(
        "route",
        [
            "GET /api/health",
            "GET /api/me",
            "GET /api/metrics",
            "GET /api/campaigns",
            "GET /api/campaigns/{campaign_id}",
            "GET /api/campaigns/{campaign_id}/overview",
            "GET /api/campaigns/{campaign_id}/closure-checklist",
            "GET /api/campaigns/{campaign_id}/thresholds",
            "GET /api/contracts",
        ],
    )
    def test_this_route_declares_its_answer(self, schema, route):
        assert route in self.declared(schema)

    def test_a_field_that_always_comes_back_is_not_optional(self, schema):
        """Sinon le client généré teste à chaque lecture un champ jamais absent.

        Un champ à valeur par défaut est *toujours* émis par ``model_dump`` ;
        Pydantic le déclare pourtant facultatif, faute du drapeau qui distingue
        « facultatif à la construction » de « facultatif sur le fil ».
        """
        campaign = schema["components"]["schemas"]["Campaign"]
        assert "closed_at" in campaign["required"]
        assert "config" in campaign["required"]

    @pytest.mark.parametrize(
        "model,field",
        [("ClosureChecklistItem", "state"), ("GridField", "type")],
    )
    def test_an_enumerated_field_is_enumerated(self, schema, model, field):
        """`string` laisserait passer n'importe quelle chaîne jusqu'à l'écran.

        L'un vaut BLOCKING / ATTENTION / DONE, l'autre nomme le type d'une
        colonne importable. Déclarés en chaîne libre, le client généré accepte
        une faute de frappe et l'écran affiche une case vide.
        """
        declared = schema["components"]["schemas"][model]["properties"][field]
        blob = json.dumps(declared)
        assert "$ref" in blob or "enum" in blob, declared

    def test_a_list_says_what_it_contains(self, schema):
        """`items: list[dict]` rendrait `unknown[]` : la page redeviendrait opaque."""
        page = schema["components"]["schemas"]["CampaignPage"]
        items = page["properties"]["items"]
        assert items["items"]["$ref"].endswith("/Campaign"), items


# --------------------------------------------------------------------------- #
# Ce qui est déclaré est ce qui sort vraiment
# --------------------------------------------------------------------------- #

@pytest.mark.postgres
class TestTheDeclarationMatchesTheRealPayload:
    """Le seul contrôle qui donne sa valeur aux autres.

    Un modèle écrit à côté d'une route n'est qu'une opinion. Confronté à ce que
    l'API produit sur une base réelle, il devient une affirmation vérifiable —
    et c'est cette confrontation qui a corrigé quatre déclarations avant même
    d'être livrée.
    """

    def check(self, client, url: str, model) -> None:
        response = client.get(url)
        assert response.status_code == 200, response.text[:400]
        payload = response.json()
        # L'ordre compte : indexer avant de vérifier le vide lève une
        # IndexError qui ressemble à une panne du contrôle plutôt qu'à une
        # absence de données.
        if isinstance(payload, list):
            if not payload:
                pytest.skip(f"{url} n'a renvoyé aucune ligne")
            payload = payload[0]
        model.model_validate(payload)

    def test_health(self, client):
        from inventory.api.responses import HealthResponse

        self.check(client, "/api/health", HealthResponse)

    def test_me(self, client):
        from inventory.api.responses import MeResponse

        self.check(client, "/api/me", MeResponse)

    def test_metrics(self, client):
        from inventory.api.responses import MetricsResponse

        self.check(client, "/api/metrics", MetricsResponse)

    def test_contracts(self, client):
        from inventory.api.responses import GridContractResponse

        self.check(client, "/api/contracts", GridContractResponse)

    def test_the_campaign_page(self, client):
        from inventory.api.responses import CampaignPage

        self.check(client, "/api/campaigns", CampaignPage)

    def test_a_campaign(self, client, campaign_id):
        from inventory.domain.models import Campaign

        self.check(client, f"/api/campaigns/{campaign_id}", Campaign)

    def test_the_overview(self, client, campaign_id):
        from inventory.api.responses import OverviewResponse

        self.check(client, f"/api/campaigns/{campaign_id}/overview", OverviewResponse)

    def test_the_closure_checklist(self, client, campaign_id):
        from inventory.api.responses import ClosureChecklistResponse

        self.check(
            client,
            f"/api/campaigns/{campaign_id}/closure-checklist",
            ClosureChecklistResponse,
        )

    def test_the_thresholds(self, client, fresh_campaign):
        """Sur une campagne neuve : les seuils par défaut sont posés à la
        création, et c'est leur forme qui est déclarée."""
        from inventory.domain.models import Thresholds

        response = client.get(f"/api/campaigns/{fresh_campaign}/thresholds")
        assert response.status_code == 200
        rows = response.json()
        assert rows, "une campagne neuve reçoit ses seuils par défaut"
        for row in rows:
            Thresholds.model_validate(row)

    def test_an_empty_campaign_has_no_ratio_rather_than_zero(self, client):
        """« 0 % fait » et « rien à faire » ne se ressemblent qu'à l'écran.

        Une campagne neuve n'a ni journal ni zone : le ratio est absent, et le
        modèle doit l'accepter. Déclaré obligatoire il passerait sur toute base
        déjà remplie — et échouerait le premier jour d'une campagne.
        """
        from inventory.api.responses import OverviewResponse

        created = client.post("/api/campaigns", json={
            "code": "INV-CONTRAT-VIDE",
            "label": "Contrôle du contrat",
            "countDate": "2026-12-31",
        })
        assert created.status_code == 201, created.text[:300]
        fresh = created.json()["id"]
        try:
            body = client.get(f"/api/campaigns/{fresh}/overview").json()
            assert body["journalProgress"]["ratio"] is None
            assert body["genericProgress"]["ratio"] is None
            OverviewResponse.model_validate(body)
        finally:
            client.delete(f"/api/campaigns/{fresh}")

    def test_nothing_is_dropped_on_the_way_out(self, client, campaign_id):
        """La déclaration documente ; elle ne doit rien retrancher.

        `response_model` supprimerait ici les clés non déclarées. Le contrôle
        compare donc ce que le service a construit à ce qui sort de l'API.
        """
        from inventory.api.deps import campaign_service
        from inventory.services import ServiceContext

        built = campaign_service(
            ServiceContext(actor="local@dev")
        ).overview(campaign_id)
        served = client.get(f"/api/campaigns/{campaign_id}/overview").json()
        assert set(served) == set(built)


# --------------------------------------------------------------------------- #
# La forme qui documente, jamais celle qui filtre
# --------------------------------------------------------------------------- #

class TestNothingFiltersTheResponse:
    def sources(self) -> list[Path]:
        return [API_APP, *sorted(ROUTERS.glob("*.py"))]

    def test_no_route_serialises_through_a_model(self):
        """`response_model=` retirerait en silence toute clé non déclarée.

        Les charges utiles sont assemblées à la main dans les services : une
        seule omission de déclaration ferait disparaître un champ que l'écran
        lit, et seulement sur l'écran concerné.
        """
        for path in self.sources():
            assert "response_model=" not in path.read_text(), path.name

    def test_the_declarations_are_the_documenting_form(self):
        found = sum(
            path.read_text().count('responses={200: {"model"')
            + path.read_text().count('responses={201: {"model"')
            for path in self.sources()
        )
        assert found >= 16, found

    def test_the_payload_base_allows_what_it_does_not_declare(self):
        """Un service qui ajoute une clé doit faire échouer un contrôle, pas
        perdre la clé en vol."""
        from inventory.api.responses import Payload

        assert Payload.model_config.get("extra") == "allow"

    def test_the_payload_base_accepts_the_field_name_too(self):
        """L'entrée est en `snake_case`, la sortie en alias : les deux tiennent.

        Les charges utiles sont assemblées à partir du `model_dump` d'un modèle
        de domaine, qui parle `snake_case`. Sans ce drapeau, Pydantic exige
        l'alias, ne le trouve pas, et FastAPI répond 500 — non pas au premier
        contrôle, mais à la première ligne réellement rendue. C'est exactement
        ce qui est arrivé aux trois routes des comptages avancés, en production.

        La sortie, elle, ne bouge pas : FastAPI sérialise par alias, et l'écran
        lit les mêmes clés qu'avant.
        """
        from inventory.api.responses import Payload

        assert Payload.model_config.get("populate_by_name") is True


# --------------------------------------------------------------------------- #
# Le fichier généré est à jour, et l'interface s'en sert
# --------------------------------------------------------------------------- #

class TestTheGeneratedClient:
    def test_the_schema_file_is_committed(self):
        assert SCHEMA_TS.exists(), "lancer `npm --prefix frontend run generate:api`"

    def test_it_is_marked_as_generated(self):
        assert "auto-generated by openapi-typescript" in SCHEMA_TS.read_text()

    def test_the_generator_is_a_declared_dependency(self):
        package = json.loads((FRONTEND / "package.json").read_text())
        assert "openapi-typescript" in package["devDependencies"]

    def test_there_is_one_command_to_regenerate(self):
        package = json.loads((FRONTEND / "package.json").read_text())
        script = package["scripts"]["generate:api"]
        assert "dump_openapi.py" in script
        assert "openapi-typescript" in script

    @pytest.mark.parametrize(
        "name,alias",
        [
            ("Campaign", "Campaign"),
            ("CampaignPage", "CampaignPage"),
            ("Overview", "OverviewResponse"),
            ("Threshold", "Thresholds"),
            ("GridContract", "GridContractResponse"),
            ("ClosureChecklist", "ClosureChecklistResponse"),
            ("Health", "HealthResponse"),
            ("Me", "MeResponse"),
        ],
    )
    def test_the_interface_reads_the_generated_shape(self, name, alias):
        """Ré-exporté sous le nom de l'écran, mais défini par le serveur."""
        assert f"export type {name} = Schemas['{alias}']" in TYPES_TS.read_text()

    def test_these_shapes_are_no_longer_hand_written(self):
        source = TYPES_TS.read_text()
        for name in ("Campaign", "Overview", "Health", "Me"):
            assert f"export interface {name} {{" not in source, name

    def test_the_committed_schema_matches_the_api(self):
        """Un fichier généré périmé compile, et ment.

        Le contrôle compare les formes nommées : le texte exact du fichier
        dépend de la version du générateur, la liste des schémas non.
        """
        declared = set(openapi()["components"]["schemas"])
        generated = SCHEMA_TS.read_text()
        missing = [
            name for name in declared
            if f"        {name}: " not in generated
            and f"        {name}:" not in generated
        ]
        assert not missing, (
            f"{len(missing)} forme(s) absente(s) du client généré : "
            f"{sorted(missing)[:10]} — lancer "
            "`npm --prefix frontend run generate:api`"
        )


# --------------------------------------------------------------------------- #
# Ce qui reste à faire, dit plutôt que tu
# --------------------------------------------------------------------------- #

class TestTheRemainingSurfaceIsNamed:
    """Une migration à moitié faite doit se voir, pas se deviner.

    La moitié non migrée n'est pas un oubli : ce sont les routes dont la
    charge utile n'est pas encore déclarée, et le compte figure ici pour qu'il
    baisse volontairement plutôt que d'être découvert.
    """

    def undeclared(self) -> int:
        count = 0
        for path in [API_APP, *sorted(ROUTERS.glob("*.py"))]:
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.FunctionDef):
                    continue
                decorators = ast.unparse(ast.Module(node.decorator_list, []))
                if not any(
                    f"@{owner}." in decorators or f"{owner}." in decorators
                    for owner in ("router", "app")
                ):
                    continue
                if "responses=" not in decorators:
                    count += 1
        return count

    def test_the_untyped_surface_only_shrinks(self):
        """Le chiffre est un cliquet : il descend, jamais il ne remonte."""
        assert self.undeclared() <= 106, (
            f"{self.undeclared()} routes ne déclarent pas leur réponse — "
            "en ajouter une sans la déclarer fait remonter le compte."
        )

    def test_the_module_says_which_form_to_use(self):
        """Le prochain qui en déclare une doit trouver la raison sur place."""
        source = RESPONSES.read_text()
        assert "response_model" in source
        assert "sérialise" in source
