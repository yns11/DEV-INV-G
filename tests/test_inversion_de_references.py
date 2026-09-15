"""Deux écarts qui s'annulent sur un même produit : une erreur, pas deux.

Le cas
------
Deux pièces d'un même assemblage se ressemblent, voisinent sur la même étagère,
et l'une est comptée à la place de l'autre. L'inventaire rend un excédent franc
sur la première et un manque du même ordre sur la seconde. Séparément — et c'est
ainsi que tous les écrans les montrent — ce sont deux anomalies à investiguer.
Ensemble, c'est une seule erreur, et elle se corrige de son bureau.

Pourquoi un calcul et non une question au modèle
-----------------------------------------------
C'est une soustraction : elle a une réponse exacte. La faire chercher au modèle
dans quarante lignes de JSON revenait à tirer au sort qu'il la trouve, et à ne
jamais pouvoir dire, sur une campagne donnée, si la signature était là ou non.
Calculée ici, elle se vérifie sur des chiffres écrits à la main.

Ce que ces contrôles délimitent
-------------------------------
Les trois conditions, et ce que chacune retire. C'est un **indice**, pas une
conclusion : deux écarts qui se compensent peuvent être deux erreurs
indépendantes tombant du bon côté, et rien ici ne corrige une quantité ni ne
pose une cause.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from inventory.domain.inversion import OFFSET_TOLERANCE, offsetting_groups

MOTEUR, AUTRE = "MOTEUR M3", "MOTEUR M5"


def line(item_number: str, qty: str | int):
    """Une ligne d'écart réduite à ce que le rapprochement lit."""
    return SimpleNamespace(item_number=item_number, variance_qty=Decimal(str(qty)))


PRODUITS = {"STATOR": MOTEUR, "ROTOR": MOTEUR, "VIS": AUTRE, "ECROU": AUTRE}


class TestLaSignature:
    def test_un_plus_et_un_moins_du_meme_ordre(self):
        groupes = offsetting_groups([line("STATOR", 40), line("ROTOR", -40)], PRODUITS)

        assert [g.product for g in groupes] == [MOTEUR]
        assert groupes[0].gross_qty == Decimal(80)
        assert groupes[0].net_qty == Decimal(0)

    def test_les_references_sont_rendues_du_plus_gros_au_plus_petit(self):
        """C'est celle qui porte le plus gros écart qu'on va voir en premier."""
        groupes = offsetting_groups(
            [line("STATOR", 40), line("ROTOR", -41), line("VIS", 0)],
            {"STATOR": MOTEUR, "ROTOR": MOTEUR, "VIS": MOTEUR},
        )

        assert groupes[0].item_numbers == ("ROTOR", "STATOR")

    def test_une_compensation_imparfaite_compte_encore(self):
        """Une inversion réelle s'accompagne souvent d'un écart vrai sur l'une
        des deux références : exiger zéro n'aurait signalé que le cas d'école."""
        groupes = offsetting_groups([line("STATOR", 40), line("ROTOR", -36)], PRODUITS)

        assert len(groupes) == 1
        assert groupes[0].ratio <= OFFSET_TOLERANCE

    def test_le_taux_de_compensation_se_lit_a_l_endroit(self):
        """Un pour une compensation parfaite : c'est le chiffre qui part dans le
        dossier, et le lire à l'envers ferait passer le cas le plus net pour le
        moins intéressant."""
        groupes = offsetting_groups([line("STATOR", 40), line("ROTOR", -40)], PRODUITS)

        assert groupes[0].as_dict()["tauxCompensation"] == 1.0


class TestCeQuiNEnEstPas:
    def test_une_seule_reference_ne_se_compense_avec_rien(self):
        groupes = offsetting_groups([line("STATOR", 40)], PRODUITS)

        assert groupes == []

    def test_deux_manques_ne_s_annulent_pas(self):
        """Et c'est la tolérance seule qui les écarte.

        Des quantités de même signe donnent un net égal au brut, donc un rapport
        de 1 : aucune garde sur les signes n'est nécessaire, et celle qui avait
        été écrite ne pouvait jamais rien retirer. Ce contrôle reste, parce que
        l'énoncé — deux manques ne sont pas une inversion — doit tenir quelle que
        soit la façon dont il est obtenu.
        """
        groupes = offsetting_groups([line("STATOR", -40), line("ROTOR", -38)], PRODUITS)

        assert groupes == []

    def test_un_solde_trop_gros_devant_le_brut_n_est_pas_une_compensation(self):
        """Deux écarts de sens opposés mais d'ordres différents se croisent trop
        souvent par hasard : l'indice deviendrait du bruit."""
        groupes = offsetting_groups([line("STATOR", 100), line("ROTOR", -5)], PRODUITS)

        assert groupes == []

    def test_deux_produits_differents_ne_se_rapprochent_pas(self):
        """C'est tout l'objet de la découpe : hors d'un même assemblage, un plus
        et un moins n'ont aucune raison d'être la même erreur."""
        groupes = offsetting_groups([line("STATOR", 40), line("VIS", -40)], PRODUITS)

        assert groupes == []

    def test_une_reference_sans_produit_est_ignoree(self):
        groupes = offsetting_groups(
            [line("STATOR", 40), line("INCONNUE", -40)], {"STATOR": MOTEUR}
        )

        assert groupes == []

    def test_un_produit_vide_ne_regroupe_pas_les_orphelines(self):
        """Sinon toutes les références non rattachées formeraient un seul
        pseudo-produit, et le premier indice rendu serait un artefact."""
        groupes = offsetting_groups(
            [line("A", 40), line("B", -40)], {"A": "", "B": "   "}
        )

        assert groupes == []

    def test_un_ecart_nul_ne_compte_pas_comme_une_reference(self):
        groupes = offsetting_groups(
            [line("STATOR", 40), line("ROTOR", 0)],
            {"STATOR": MOTEUR, "ROTOR": MOTEUR},
        )

        assert groupes == []


class TestLOrdreDeLecture:
    def test_le_plus_gros_mouvement_vient_en_tete(self):
        """Le dossier envoyé au modèle a une taille limitée : si quelque chose
        doit être coupé, ce n'est pas le cas le plus lourd."""
        lignes = [
            line("STATOR", 10), line("ROTOR", -10),
            line("VIS", 500), line("ECROU", -500),
        ]

        groupes = offsetting_groups(lignes, PRODUITS)

        assert [g.product for g in groupes] == [AUTRE, MOTEUR]
