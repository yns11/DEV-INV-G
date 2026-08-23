"""Qui a le droit d'écrire sur une campagne.

Jusqu'ici l'application n'avait qu'une seule barrière devant une écriture : la
**phase**. Ce qu'un statut gèle, personne ne le modifie ; ce qu'il laisse ouvert,
n'importe quel utilisateur connecté le modifiait. Sur un site où tout le monde
peut ouvrir l'application, cela veut dire que n'importe qui pouvait recharger le
référentiel d'une campagne qu'il ne pilote pas, la veille du comptage.

Une seconde barrière s'ajoute donc, orthogonale à la première : l'**identité**.

    peut écrire  =  la phase l'autorise  ET  l'acteur est propriétaire
                                             ou gestionnaire déclaré

Les deux se cumulent et ne se remplacent pas. Un propriétaire n'écrit pas dans
une campagne clôturée ; un lecteur n'écrit nulle part, même en préparation.

**Trois rôles, et ce qui les sépare.** Le propriétaire est celui qui a créé la
campagne. Les gestionnaires sont ceux qu'il a déclarés, par leur identité, dans
l'écran Gestion. Tous les autres lisent.

**Deux actions restent au seul propriétaire**, et pour la même raison dans les
deux cas — elles portent sur la campagne, pas sur ses données :

*Déclarer les gestionnaires.* Un gestionnaire qui pourrait en déclarer d'autres
s'accorderait à lui-même le droit d'en accorder : la liste ne voudrait plus rien
dire, et rien n'empêcherait d'en retirer le propriétaire.

*Supprimer la campagne.* C'était déjà la règle, et pour un motif qui n'a pas
changé : une campagne qui disparaît sous les pieds de celui qui la mène est un
accident bien pire qu'une campagne qui traîne une semaine de trop.

Le reste — charger, compter, ajuster, analyser, changer de phase — appartient
aux gestionnaires autant qu'au propriétaire. Le passage en comptage se fait à
six heures du matin le jour J, et exiger que le propriétaire soit devant son
écran à ce moment-là ferait de cette règle une gêne plutôt qu'une garantie.

Ce module est pur : il ne connaît ni la base ni HTTP, et se teste avec trois
objets construits à la main.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from .models import Campaign, Manager
from .workflow import Editable

__all__ = ["Role", "role_of", "restrict"]


class Role(StrEnum):
    """Ce qu'un utilisateur est vis-à-vis d'**une** campagne donnée.

    Pas un rôle global : la même personne est propriétaire de sa campagne et
    simple lectrice de celle du trimestre précédent.
    """

    OWNER = "OWNER"
    MANAGER = "MANAGER"
    READER = "READER"

    @property
    def may_write(self) -> bool:
        """Si ce rôle peut modifier les données de la campagne."""
        return self is not Role.READER

    @property
    def is_owner(self) -> bool:
        return self is Role.OWNER


def _identity(value: str | None) -> str:
    """La forme sous laquelle une identité se compare.

    Le proxy transmet l'adresse telle que l'annuaire la porte, et la casse y
    varie d'un utilisateur à l'autre. `Manager.actor` est déjà normalisé à la
    saisie ; `campaign.created_by` l'est à la création. Normaliser une
    troisième fois ici ne coûte rien et couvre les lignes écrites avant que ce
    soit le cas.
    """
    return (value or "").strip().lower()


def role_of(
    actor: str | None, campaign: Campaign, managers: Iterable[Manager] = ()
) -> Role:
    """Le rôle de *actor* sur *campaign*.

    ``managers`` est la liste enregistrée de la campagne. Un gestionnaire
    **désactivé** ne compte pas : le rendre inactif est précisément la façon de
    retirer quelqu'un sans effacer la trace de son passage, et l'écran présente
    l'interrupteur comme tel.

    Une campagne sans propriétaire enregistré — il n'y en a que d'avant que
    l'identité soit tracée — laisse le rôle se jouer sur les seuls
    gestionnaires. Traiter l'absence comme un accord général rouvrirait
    justement ce que cette règle ferme.
    """
    who = _identity(actor)
    if not who:
        return Role.READER
    if who == _identity(campaign.created_by):
        return Role.OWNER
    if any(m.active and _identity(m.actor) == who for m in managers):
        return Role.MANAGER
    return Role.READER


#: Aucun aspect modifiable. Sert de réponse à un lecteur : l'interface lit la
#: même matrice que le serveur, donc rendre tout faux suffit à désactiver
#: chaque bouton sans qu'un seul écran ait à connaître la notion de rôle.
_NOTHING = Editable(**dict.fromkeys(Editable.__slots__, False))


def restrict(editable: Editable, role: Role) -> Editable:
    """Ce que *role* peut réellement modifier, la phase étant déjà appliquée.

    L'intersection des deux barrières, en un seul objet. C'est lui que l'API
    renvoie sous ``permissions``, ce qui fait qu'un lecteur voit exactement ce
    que voit quelqu'un devant une campagne clôturée : tout est grisé, et pour
    une raison que l'écran affiche.
    """
    return editable if role.may_write else _NOTHING
