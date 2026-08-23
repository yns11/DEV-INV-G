"""Un fichier trop gros est refusé avant d'avoir été lu, pas après.

Le plafond existait — ``INV_MAX_UPLOAD_BYTES`` — mais il était consulté sur
``len(payload)``, c'est-à-dire une fois le fichier entier chargé en mémoire. Le
refus arrivait donc après le dommage : sur un conteneur de quelques gigaoctets
partagé entre plusieurs utilisateurs, un dépôt de trois gigaoctets se payait sur
les requêtes des voisins avant qu'on ne lui dise non.

Et il n'était consulté que sur la route d'import. Le scan d'une pile de
feuilles, la réconciliation entre campagnes et les pièces jointes de l'assistant
lisaient sans rien regarder du tout.

Ces contrôles portent sur la lecture elle-même : combien d'octets ont été tirés
de la source avant le refus.
"""

from __future__ import annotations

import asyncio

import pytest

from inventory.api.uploads import read_upload
from inventory.errors import ValidationError


class Upload:
    """Une source qui compte ce qu'on lui a effectivement pris.

    C'est le point du test : un fichier de dix mégaoctets refusé à un mégaoctet
    n'a coûté qu'un mégaoctet. La doublure le mesure au lieu de le supposer.
    """

    def __init__(self, size: int, *, filename: str = "gros.xlsx") -> None:
        self.size = size
        self.filename = filename
        self.content_type = "application/vnd.ms-excel"
        self.served = 0

    async def read(self, n: int = -1) -> bytes:
        remaining = self.size - self.served
        if remaining <= 0:
            return b""
        take = remaining if n < 0 else min(n, remaining)
        self.served += take
        return b"x" * take


def run(coro):
    return asyncio.run(coro)


class TestWhatFitsGoesThrough:
    def test_a_small_file_comes_back_whole(self):
        upload = Upload(1024)
        assert len(run(read_upload(upload, ceiling=4096))) == 1024

    def test_an_empty_file_comes_back_empty(self):
        """Le vide se refuse ailleurs, avec un message qui parle du contenu."""
        assert run(read_upload(Upload(0), ceiling=4096)) == b""

    def test_a_file_exactly_at_the_ceiling_passes(self):
        """Le plafond est atteignable, pas un seuil d'alarme."""
        ceiling = 3 * (1 << 20)
        assert len(run(read_upload(Upload(ceiling), ceiling=ceiling))) == ceiling


class TestWhatDoesNotFitIsRefusedEarly:
    def test_one_byte_over_the_ceiling_is_refused(self):
        ceiling = 1 << 20
        with pytest.raises(ValidationError):
            run(read_upload(Upload(ceiling + 1), ceiling=ceiling))

    def test_the_refusal_costs_a_chunk_not_a_file(self):
        """Le cœur de la correction, et la seule chose que `len()` ne voit pas."""
        ceiling = 1 << 20
        upload = Upload(200 * (1 << 20))  # 200 Mio déposés par erreur
        with pytest.raises(ValidationError):
            run(read_upload(upload, ceiling=ceiling))
        assert upload.served <= ceiling + (1 << 20), (
            f"{upload.served} octets lus pour un plafond de {ceiling} : "
            "le fichier a été lu en entier avant d'être refusé"
        )

    def test_the_refusal_names_what_was_refused(self):
        """« Le fichier » et « Le scan » ne se cherchent pas au même endroit."""
        with pytest.raises(ValidationError) as raised:
            run(read_upload(Upload(2 << 20), what="Le scan", ceiling=1 << 20))
        assert str(raised.value).startswith("Le scan")


class TestHowASizeIsSaid:
    """« 3000.0 Mo » ne se lit pas ; « 3.00 Go » se lit.

    Contrôlé sur la fonction plutôt qu'en faisant réellement transiter trois
    gigaoctets : la mise en forme est ce qui est en jeu, et un test qui alloue
    trois gigaoctets pour vérifier deux lettres est un test qu'on finit par
    désactiver.
    """

    def test_megabytes_below_a_gigabyte(self):
        from inventory.api.uploads import _human

        assert _human(64 * 1024 * 1024) == "67.1 Mo"

    def test_gigabytes_above(self):
        from inventory.api.uploads import _human

        assert _human(3 << 30) == "3.22 Go"

    def test_the_ceiling_appears_in_the_refusal_in_that_form(self):
        with pytest.raises(ValidationError) as raised:
            run(read_upload(Upload(2 << 20), ceiling=1 << 20))
        assert "1.0 Mo" in str(raised.value)


class TestTheDefaultCeilingIsTheConfiguredOne:
    def test_without_an_override_the_setting_applies(self, monkeypatch):
        from inventory.config import get_settings

        monkeypatch.setenv("INV_MAX_UPLOAD_BYTES", str(1 << 20))
        get_settings.cache_clear()
        try:
            with pytest.raises(ValidationError):
                run(read_upload(Upload(2 << 20)))
            assert len(run(read_upload(Upload(1000)))) == 1000
        finally:
            monkeypatch.delenv("INV_MAX_UPLOAD_BYTES", raising=False)
            get_settings.cache_clear()


class TestEveryUploadRouteGoesThroughIt:
    """Le plafond ne protégeait qu'une route sur quatre.

    Ce contrôle lit le code des routeurs. C'est inhabituel, et c'est justifié
    ici : ajouter une cinquième route de téléversement qui appelle
    ``await file.read()`` ramènerait le défaut sans qu'aucun test fonctionnel ne
    s'en aperçoive — la faute n'est visible qu'en mémoire, sous charge.
    """

    ROUTERS = ("data", "generic", "stock_flow", "assistant")

    def source(self, name: str) -> str:
        import importlib
        import inspect

        module = importlib.import_module(f"inventory.api.routers.{name}")
        return inspect.getsource(module)

    @pytest.mark.parametrize("name", ROUTERS)
    def test_no_router_reads_an_upload_unbounded(self, name):
        source = self.source(name)
        assert "await file.read()" not in source
        assert "await upload.read()" not in source

    @pytest.mark.parametrize("name", ROUTERS)
    def test_every_router_that_takes_a_file_uses_the_helper(self, name):
        source = self.source(name)
        if "UploadFile" not in source:
            pytest.skip(f"{name} ne reçoit aucun fichier")
        assert "read_upload(" in source
