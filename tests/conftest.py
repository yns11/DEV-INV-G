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


class FakeTransactions:
    """Une doublure de ``Database`` qui ne retient que l'ouverture et la clôture.

    Une commande métier qui écrit dans plusieurs tables doit le faire dans une
    seule transaction, sinon un incident au milieu laisse la moitié du geste
    derrière lui — une zone sans ses feuilles, une saisie sans sa trace. Les
    doublures de cette suite n'ont pas de base ; ce qu'on peut malgré tout
    observer, c'est *quand* la transaction était ouverte, et c'est exactement la
    question posée.

    ``opened`` compte les transactions ouvertes sur la durée du test, ``depth``
    dit si l'une est ouverte à l'instant, et ``connection`` est le jeton que les
    dépôts doivent recevoir en ``conn=``. Les dépôts factices appellent
    :meth:`note` à chaque écriture ; ``writes`` en garde la profondeur.
    """

    connection = "connexion-de-test"

    def __init__(self) -> None:
        self.opened = 0
        self.depth = 0
        self.writes: dict[str, int] = {}

    def note(self, what: str) -> None:
        """Enregistre qu'une écriture a eu lieu, et à quelle profondeur."""
        self.writes[what] = self.depth

    def all_writes_inside_one_transaction(self) -> bool:
        """Vrai si des écritures ont eu lieu, toutes dans une transaction."""
        return bool(self.writes) and all(d == 1 for d in self.writes.values())

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _open():
            self.opened += 1
            self.depth += 1
            try:
                yield self.connection
            finally:
                self.depth -= 1

        return _open()


def with_transactions(ctx):
    """Greffe une base factice observable sur un contexte de test."""
    ctx.db = FakeTransactions()
    return ctx.db


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
