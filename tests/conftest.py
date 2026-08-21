"""Test configuration.

The domain and ingest layers are pure: they need no database, no warehouse and
no Databricks workspace. That is the whole point of the layering, and it is why
this suite runs in under a second.
"""

import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def with_access(ctx, *, managers=()):
    """Greffe les vraies règles d'accès sur un contexte factice.

    Les doublures de cette suite sont des ``SimpleNamespace`` : elles ne portent
    que ce dont le test a besoin. Or ``role``, ``require_write`` et
    ``require_owner`` sont précisément ce que la barrière d'identité a de
    délicat — les réécrire ici en reproduirait la logique dans le test, et le
    test cesserait de vérifier le code livré pour vérifier sa propre copie.

    On y branche donc les méthodes réelles de :class:`ServiceContext`, avec le
    strict nécessaire pour qu'elles tournent : la liste des gestionnaires et le
    dictionnaire de mémoïsation.
    """
    from types import SimpleNamespace

    from inventory.services.context import ServiceContext

    if not hasattr(ctx, "referentials"):
        ctx.referentials = SimpleNamespace()
    if not hasattr(ctx.referentials, "list_managers"):
        ctx.referentials.list_managers = lambda cid: list(managers)
    ctx._roles = {}
    ctx.role = lambda campaign: ServiceContext.role(ctx, campaign)
    ctx.permissions = lambda campaign: ServiceContext.permissions(ctx, campaign)
    ctx.require_write = lambda campaign: ServiceContext.require_write(ctx, campaign)
    ctx.require_owner = lambda campaign, action: ServiceContext.require_owner(
        ctx, campaign, action
    )
    # `guard` est la porte que tout passe : la stubber reviendrait à retirer du
    # test la seule chose qu'on veut y voir tenir.
    ctx.guard = lambda campaign, aspect: ServiceContext.guard(ctx, campaign, aspect)
    return ctx
