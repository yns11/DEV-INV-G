"""Un appel lent ne fige plus l'application entière.

FastAPI exécute dans un pool de fils les endpoints déclarés ``def``. Un endpoint
``async``, lui, tourne **sur la boucle** — et tout ce qu'il appelle de synchrone
la bloque.

Cinq endpoints sont ``async`` parce qu'ils reçoivent un fichier, et que lire le
corps d'une requête l'est. Ce qui suivait la lecture n'avait rien de court : un
import de deux cent mille lignes, un aller-retour vers le modèle de vision, une
question à l'assistant. Pendant ces secondes — parfois cette minute — l'unique
boucle d'Uvicorn ne servait plus personne d'autre.

Le jour de l'inventaire, dix personnes travaillent en parallèle. L'une chargeait
son export, les neuf autres voyaient l'application figée, et rien dans les
journaux ne le disait : chaque requête finissait par aboutir, simplement bien
plus tard qu'elle n'aurait dû.

Ce contrôle mesure la propriété plutôt que de la supposer : pendant qu'un appel
lent est en cours, la boucle avance-t-elle encore ?
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import pytest

from inventory.api.uploads import offload

ROUTERS = Path(__file__).resolve().parent.parent / "app" / "inventory" / "api" / "routers"

#: Assez long pour que la boucle ait le temps de tourner plusieurs fois si elle
#: est libre, assez court pour que la suite reste rapide.
SLOW = 0.12


class TestTheLoopKeepsRunning:
    def test_the_loop_advances_while_a_slow_call_is_in_flight(self):
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        async def scenario() -> None:
            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            await offload(lambda: time.sleep(SLOW))
            beat.cancel()

        asyncio.run(scenario())
        assert ticks > 5, (
            f"la boucle n'a battu que {ticks} fois pendant {SLOW}s : "
            "l'appel lent l'a immobilisée"
        )

    def test_the_same_call_without_offload_freezes_it(self):
        """Le témoin : sans le renvoi au pool, la boucle s'arrête bel et bien."""
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        async def scenario() -> None:
            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            time.sleep(SLOW)  # exactement ce que faisaient les endpoints
            beat.cancel()

        asyncio.run(scenario())
        assert ticks <= 2, f"{ticks} battements — le témoin ne témoigne de rien"


class TestWhatOffloadPreserves:
    def test_the_result_comes_back(self):
        assert asyncio.run(offload(lambda: 40 + 2)) == 42

    def test_an_exception_travels_intact(self):
        """La garde de domaine doit lever son refus comme avant le renvoi."""
        from inventory.errors import ValidationError

        async def scenario():
            def refuse():
                raise ValidationError("fichier illisible", line=7)

            await offload(refuse)

        with pytest.raises(ValidationError) as raised:
            asyncio.run(scenario())
        assert raised.value.details["line"] == 7

    def test_it_runs_somewhere_else_than_the_loop(self):
        import threading

        loop_thread = None
        work_thread = None

        async def scenario():
            nonlocal loop_thread, work_thread
            loop_thread = threading.get_ident()
            work_thread = await offload(threading.get_ident)

        asyncio.run(scenario())
        assert loop_thread is not None and work_thread != loop_thread


#: Les noms sous lesquels les routeurs reçoivent leurs services par injection.
SERVICES = ("importer", "service", "jobs")


class TestNoAsyncEndpointBlocksTheLoop:
    """Le contrôle qui survit à l'ajout d'un sixième endpoint.

    Il lit le code des routeurs. C'est inhabituel, et c'est justifié : la faute
    n'est visible qu'en charge, jamais dans un test fonctionnel, et un nouvel
    endpoint `async` la ramènerait sans que rien ne s'en aperçoive.
    """

    def async_endpoints(self):
        found = []
        for path in sorted(ROUTERS.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if any("router." in ast.unparse(d) for d in node.decorator_list):
                    found.append((path.name, node))
        return found

    def test_there_are_async_endpoints_to_check(self):
        """Sans cela, tous les contrôles ci-dessous passeraient sur du vide."""
        assert len(self.async_endpoints()) >= 5

    @pytest.mark.parametrize(
        "name",
        [f"{p.name}:{node.name}"
         for p in sorted(ROUTERS.glob("*.py"))
         for node in ast.parse(p.read_text()).body
         if isinstance(node, ast.AsyncFunctionDef)
         and any("router." in ast.unparse(d) for d in node.decorator_list)],
    )
    def test_no_service_call_stays_on_the_loop(self, name):
        """**Chaque** appel de service, pas au moins un.

        La première version de ce contrôle demandait qu'`offload` figure quelque
        part dans l'endpoint. Un endpoint qui en renvoie trois sur quatre le
        satisfaisait donc, et c'est précisément la forme que prend l'oubli :
        on protège le chemin qu'on a en tête, pas son voisin.
        """
        filename, function = name.split(":", 1)
        node = next(
            n for f, n in self.async_endpoints()
            if f == filename and n.name == function
        )

        # Tout ce qui se trouve, à n'importe quelle profondeur, dans un
        # argument d'`offload(...)` : ce travail-là quitte la boucle.
        offloaded: set[int] = set()
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                continue
            if call.func.id != "offload":
                continue
            for argument in call.args:
                offloaded.update(id(n) for n in ast.walk(argument))

        def hits_a_service(call: ast.Call) -> bool:
            """`service.faire(...)`, mais aussi `getattr(service, nom)(...)`.

            La seconde forme est celle du routeur d'import, qui résout la
            méthode depuis la grille visée. Elle échappait à la détection —
            son `func` n'est pas un attribut mais un appel — et c'est
            exactement l'appel le plus lourd de l'application.
            """
            target = call.func
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                return target.value.id in SERVICES
            if (
                isinstance(target, ast.Call)
                and isinstance(target.func, ast.Name)
                and target.func.id == "getattr"
                and target.args
                and isinstance(target.args[0], ast.Name)
            ):
                return target.args[0].id in SERVICES
            return False

        stranded = [
            ast.unparse(c.func)
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
            and hits_a_service(c)
            and id(c) not in offloaded
        ]
        assert stranded == [], (
            f"{name} appelle {', '.join(stranded)} sur la boucle"
        )

    def test_every_router_that_offloads_imports_the_helper(self):
        for path in sorted(ROUTERS.glob("*.py")):
            source = path.read_text()
            if "offload(" not in source:
                continue
            assert "from ..uploads import offload" in source, path.name
