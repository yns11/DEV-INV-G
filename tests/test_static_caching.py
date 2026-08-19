"""Ce que le navigateur a le droit de garder, et pour combien de temps.

« J'ai redéployé et rien n'a changé » a deux causes qui, depuis un navigateur,
sont indiscernables : ou bien le déploiement n'a pas emporté la nouvelle
interface, ou bien le navigateur ressert une coquille en cache qui pointe vers
l'ancien paquet. La deuxième était possible ici — ``index.html`` partait sans
aucune directive de cache, ce qui autorise un navigateur à le garder « au
jugé », typiquement un dixième de son âge.

La règle tient en une phrase : **ce qui porte une empreinte dans son nom se
garde un an, le reste ne se garde pas.** Les paquets construits par Vite
changent de nom à chaque changement de contenu, donc les garder est sans
risque ; la coquille garde son nom pour toujours et change à chaque
déploiement, donc la garder est exactement le risque.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from inventory.api.app import _cache_headers, _frontend_state

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache, must-revalidate"


def policy(name: str) -> str:
    return _cache_headers(Path(name))["Cache-Control"]


class TestAHashedNameMayBeKeptForever:
    @pytest.mark.parametrize(
        "name",
        [
            "index-D5wpFZpw.js",
            "react-BmsQZosA.js",
            "index-D5wpFZpw.css",
            "logo-sombre-CgTFvk9R.png",
            "logo-BPQ0O_BV.svg",
        ],
    )
    def test_it_is_immutable(self, name):
        assert policy(name) == IMMUTABLE


class TestEverythingElseMustBeRevalidated:
    def test_the_shell_above_all(self):
        """C'est lui qui nomme les paquets : périmé, il en sert d'anciens."""
        assert policy("index.html") == REVALIDATE

    @pytest.mark.parametrize(
        "name", ["favicon.ico", "manifest.webmanifest", "robots.txt", "logo.svg"]
    )
    def test_and_anything_without_a_fingerprint(self, name):
        assert policy(name) == REVALIDATE

    def test_a_short_suffix_is_not_a_fingerprint(self):
        """« vendor-min.js » n'est pas une empreinte : huit caractères au moins."""
        assert policy("vendor-min.js") == REVALIDATE


class TestTheDeployedBuildIsIdentifiable:
    """Pour que « mon déploiement est-il arrivé ? » se règle par un appel.

    Le nom du paquet *est* l'identité de la construction : Vite le dérive du
    contenu. Le comparer à celui d'``app/static/`` après un build répond à la
    question sans avoir à interpréter ce qu'affiche un navigateur.
    """

    def test_it_names_the_bundle_the_container_holds(self):
        state = _frontend_state()
        assert set(state) >= {"bundle", "builtAt", "assets"}
        if state["bundle"] is not None:
            assert state["bundle"].endswith(".js")
            assert "index-" in state["bundle"]

    def test_it_never_raises_when_nothing_was_built(self, monkeypatch, tmp_path):
        """C'est la charge utile qu'on lit *parce que* quelque chose ne va pas."""
        import inventory.api.app as module

        monkeypatch.setattr(module, "STATIC_DIR", tmp_path / "absent")
        assert module._frontend_state() == {
            "bundle": None, "builtAt": None, "assets": 0,
        }
