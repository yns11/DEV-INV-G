"""Sans identité vérifiée, on refuse — on n'invente pas un utilisateur.

Databricks Apps termine l'authentification au proxy et transmet l'identité de
l'appelant dans un en-tête. Le comportement précédent, faute d'en-tête en
environnement déployé, retombait sur `unknown@unauthenticated` **et laissait la
requête écrire sous ce nom**. Une application joignable hors du proxy, ou un
proxy mal configuré, créait donc et modifiait des données sous une identité
générique que le journal d'audit enregistrait comme n'importe quelle autre.

Une campagne d'inventaire est un dossier opposable : « on ne sait pas qui » n'y
est pas une identité, et le refus doit tomber à la porte.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.api.deps import get_current_user
from inventory.errors import UnauthenticatedError


def request(path: str = "/api/campaigns") -> Any:
    return cast(Any, SimpleNamespace(url=SimpleNamespace(path=path)))


def as_env(monkeypatch, env: str) -> None:
    from inventory import config

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(env=env)
    )
    monkeypatch.setattr(
        "inventory.api.deps.get_settings", lambda: SimpleNamespace(env=env)
    )


class TestAForwardedIdentityIsUsed:
    def test_the_email_header_wins(self, monkeypatch):
        as_env(monkeypatch, "prod")
        assert get_current_user(request(), "Alice@Usine.COM", None, None) == (
            "alice@usine.com"
        )

    def test_the_preferred_username_is_the_fallback(self, monkeypatch):
        as_env(monkeypatch, "prod")
        assert get_current_user(request(), None, "bob@usine", None) == "bob@usine"

    def test_the_user_header_is_the_last_resort(self, monkeypatch):
        as_env(monkeypatch, "prod")
        assert get_current_user(request(), None, None, "carol@usine") == "carol@usine"


class TestNoIdentityDeployed:
    """Le cœur du correctif."""

    @pytest.mark.parametrize("env", ["dev", "prod"])
    def test_it_is_refused_rather_than_invented(self, monkeypatch, env):
        as_env(monkeypatch, env)
        with pytest.raises(UnauthenticatedError):
            get_current_user(request(), None, None, None)

    def test_the_refusal_is_a_401_not_a_403(self, monkeypatch):
        """403 dit « je sais qui vous êtes, vous n'avez pas le droit » et
        enverrait chercher une habilitation manquante ; ici c'est le proxy
        d'authentification qui est hors circuit."""
        as_env(monkeypatch, "prod")
        with pytest.raises(UnauthenticatedError) as caught:
            get_current_user(request(), None, None, None)
        assert caught.value.status_code == 401

    def test_the_message_names_the_cause(self, monkeypatch):
        as_env(monkeypatch, "prod")
        with pytest.raises(UnauthenticatedError) as caught:
            get_current_user(request(), None, None, None)
        assert "proxy" in str(caught.value)

    def test_an_empty_header_is_no_identity_at_all(self, monkeypatch):
        """Un en-tête présent mais vide est le cas d'un proxy qui transmet la
        clé sans la valeur — plus discret qu'une absence, et tout aussi faux."""
        as_env(monkeypatch, "prod")
        with pytest.raises(UnauthenticatedError):
            get_current_user(request(), "", "", "")


class TestLocalDevelopment:
    """Hors plateforme il n'y a pas de proxy : le repli existe, et il est nommé.

    Il est conditionné à `INV_ENV=local`. Un déploiement mal configuré ne peut
    donc pas hériter du confort du poste de développement.
    """

    def test_a_local_run_gets_a_clearly_named_user(self, monkeypatch):
        as_env(monkeypatch, "local")
        assert get_current_user(request(), None, None, None) == "local@dev"

    def test_a_real_identity_still_wins_locally(self, monkeypatch):
        as_env(monkeypatch, "local")
        assert get_current_user(request(), "dev@usine", None, None) == "dev@usine"
