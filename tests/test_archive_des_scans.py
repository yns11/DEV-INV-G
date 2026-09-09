"""L'archive des scans se consulte, et pas seulement s'alimente.

Ce qui existait
--------------
Chaque scan déposé est archivé **avant** d'être lu — c'est la pièce qui
justifie les quantités, et sans elle une valeur contestée six mois plus tard
n'a plus rien derrière elle, la feuille manuscrite étant repartie à l'atelier
puis à la benne. Le fichier se téléchargeait déjà, par une route qui applique la
barrière de campagne.

Ce qui manquait
---------------
**Rien ne permettait de savoir ce que l'archive contenait.** Le téléchargement
demande l'identifiant de la feuille qui porte la pièce : il fallait donc déjà
savoir où regarder pour regarder. Une archive qu'on ne peut pas énumérer ne se
contrôle pas — et c'est pourtant tout son objet.

C'est le défaut habituel de ce dépôt sous une forme de plus : la colonne
existait, la route existait, le client portait même l'adresse — et aucun écran
ne l'appelait.

Ce que ces contrôles tiennent
-----------------------------
* **Une ligne par document, pas par feuille.** Une pile déposée d'un coup est un
  seul document, et les dix feuilles qu'on y a lues pointent dessus. Les lister
  une par une rendrait dix fois le même PDF sous dix noms, et laisserait croire
  à dix originaux.
* **Ce que la liste porte suffit à décider d'ouvrir**, et pas davantage : le
  poids sans les octets — une pile de deux cents pages en pèse trente
  mégaoctets.
* **Le chemin de stockage ne sort pas.** Il n'a rien à faire dans une réponse,
  et le laisser sortir en ferait une adresse que quelqu'un fabriquerait à la
  main.
* **L'écran l'appelle vraiment**, ce qui est la moitié qui manquait.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

import pytest
from tests.conftest import screen_source
from tests.early_count_db import disposable_database, make_campaign

from inventory.db import new_id
from inventory.db.repositories import SheetRepository
from inventory.domain.enums import SheetPass
from inventory.domain.models import Zone
from inventory.evidence import EvidenceStore

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_archive_scans") as database:
        yield database


def _chemin(code: str, name: str, content: bytes, at: dt.datetime) -> str:
    """Le chemin qu'un dépôt de scan produit, composé par le code qui le compose.

    Pas un chemin écrit à la main : c'est ``path_for`` qui décide de la forme, et
    c'est elle que la liste relit. Deux formats écrits à deux endroits finiraient
    par diverger, et la divergence ne se verrait que sur une date manquante.
    """
    from inventory.config import get_settings

    store = EvidenceStore(get_settings())
    return store.path_for(
        campaign_code=code, kind="scans", filename=name,
        at=at, digest=hashlib.sha256(content).hexdigest(),
    )


#: Le contenu des deux pièces, et leur instant de dépôt.
PILE = b"%PDF-pile" + b"x" * 4000
SEULE = b"%PDF-seule"
LE_SOIR = dt.datetime(2026, 6, 29, 18, 30, tzinfo=dt.UTC)
LE_LENDEMAIN = dt.datetime(2026, 6, 30, 9, 15, tzinfo=dt.UTC)


@pytest.fixture
def campagne(db):
    """Une campagne avec deux pièces : une pile de deux feuilles, et une seule.

    C'est la configuration réelle : on scanne une pile la veille au soir, et une
    feuille isolée le lendemain parce qu'elle est revenue en retard.

    Quatre zones, et chacune pour une raison :

    * **A et C** sont couvertes par la pile — sans quoi « une ligne par
      document » resterait vrai sans qu'aucun regroupement n'ait lieu ;
    * **B** est rescannée seule, et quitte donc la pile ;
    * **D** n'a jamais été scannée : c'est une zone comptée à la main, et elle
      n'a rien à faire dans l'archive.
    """
    code = f"ARC-{uuid.uuid4().hex[:8]}"
    campaign_id = make_campaign(db, code)
    sheets = SheetRepository(db)

    zones = {}
    for zone_code, order in (
        ("ZONE-A", 1), ("ZONE-B", 2), ("ZONE-C", 3), ("ZONE-D", 4)
    ):
        zone = sheets.create_zone(
            Zone(id=new_id(), campaign_id=campaign_id, code=zone_code,
                 display_order=order),
            actor="alice",
        )
        sheets.ensure_sheets(campaign_id, zone.id, [SheetPass.PASS_1], actor="alice")
        zones[zone_code] = zone

    par_zone = {s.zone_id: s for s in sheets.list_sheets(campaign_id)}
    pile = _chemin(code, "pile-du-soir.pdf", PILE, LE_SOIR)
    seule = _chemin(code, "zone-b-retard.pdf", SEULE, LE_LENDEMAIN)

    with db.transaction() as conn:
        # La pile couvre les deux feuilles : c'est le dépôt groupé de la veille.
        conn.execute(
            "UPDATE count_sheet SET evidence_path = %s, evidence_sha256 = %s, "
            "evidence_bytes = %s, evidence_mime = %s WHERE campaign_id = %s",
            (pile, hashlib.sha256(PILE).hexdigest(), len(PILE),
             "application/pdf", campaign_id),
        )
        # Puis la feuille de ZONE-B est rescannée seule et pointe sur sa pièce.
        conn.execute(
            "UPDATE count_sheet SET evidence_path = %s, evidence_sha256 = %s, "
            "evidence_bytes = %s WHERE id = %s",
            (seule, hashlib.sha256(SEULE).hexdigest(), len(SEULE),
             par_zone[zones["ZONE-B"].id].id),
        )
        # Et ZONE-D n'a jamais vu de scanner : comptée à la main, à la main.
        conn.execute(
            "UPDATE count_sheet SET evidence_path = '', evidence_sha256 = NULL, "
            "evidence_bytes = NULL, evidence_mime = NULL WHERE id = %s",
            (par_zone[zones["ZONE-D"].id].id,),
        )
    return campaign_id, code, sheets


def _lignes(campagne) -> list[dict[str, Any]]:
    """La liste telle que le service la rend — chemin réel, pas un stub."""
    from inventory.config import get_settings
    from inventory.services.context import ServiceContext
    from inventory.services.evidence_service import EvidenceService

    campaign_id, _code, sheets = campagne
    ctx = ServiceContext(actor="test", db=sheets.db, settings=get_settings())
    return EvidenceService(ctx).scans(ctx.campaigns.get(campaign_id))


class TestUneLigneParDocument:
    def test_les_deux_pieces_sont_listees(self, campagne):
        noms = {row["filename"] for row in _lignes(campagne)}
        assert noms == {"pile-du-soir.pdf", "zone-b-retard.pdf"}

    def test_une_feuille_jamais_scannee_n_entre_pas_dans_l_archive(self, campagne):
        """Sinon l'archive porterait une pièce vide par zone comptée à la main.

        Ce serait pire qu'inutile : l'onglet où l'on vient vérifier que tout est
        justifié dirait « quatre pièces » là où il y en a deux.
        """
        lignes = _lignes(campagne)
        assert len(lignes) == 2
        assert all(row["filename"] for row in lignes)
        assert all(row["sha256"] for row in lignes)

    def test_la_pile_n_apparait_qu_une_fois(self, campagne):
        """Deux feuilles, un document : c'est bien lui qui les justifie.

        Le contrôle ne vaut que parce que la pile en couvre réellement deux —
        voir la fixture. Sans regroupement, cette ligne-ci en verrait deux.
        """
        lignes = [r for r in _lignes(campagne) if r["filename"] == "pile-du-soir.pdf"]
        assert len(lignes) == 1
        assert lignes[0]["sheetCount"] == 2

    def test_elle_nomme_les_feuilles_qu_elle_justifie(self, campagne):
        """La feuille rescannée seule a quitté la pile : elle n'y figure plus."""
        [pile] = [r for r in _lignes(campagne) if r["filename"] == "pile-du-soir.pdf"]
        assert pile["sheets"] == ["ZONE-A — n°1", "ZONE-C — n°1"]
        assert pile["sheetCount"] == 2

    def test_la_plus_recente_vient_en_premier(self, campagne):
        """Ce qu'on cherche dans une archive est presque toujours le dernier dépôt."""
        lignes = _lignes(campagne)
        assert [r["archivedAt"] for r in lignes] == sorted(
            (r["archivedAt"] for r in lignes), reverse=True
        )


class TestCeQueLaLignePorte:
    def test_de_quoi_decider_d_ouvrir(self, campagne):
        [pile] = [r for r in _lignes(campagne) if r["filename"] == "pile-du-soir.pdf"]
        assert pile["sizeBytes"] == len(PILE)
        assert len(pile["sha256"]) == 64
        assert pile["mime"] == "application/pdf"

    def test_et_de_quoi_le_telecharger(self, campagne):
        """L'adresse passe par une feuille, donc par la barrière de campagne.

        N'importe laquelle des feuilles couvertes fait l'affaire — c'est le même
        fichier — et le service en retient une, pour que deux appels rendent la
        même adresse.
        """
        campaign_id, _code, sheets = campagne
        connues = {s.id for s in sheets.list_sheets(campaign_id)}
        for row in _lignes(campagne):
            assert row["sheetId"] in connues, row["filename"]

    def test_les_octets_ne_sont_pas_rapatries(self, campagne):
        """Une pile de deux cents pages pèse trente mégaoctets.

        Les charger pour afficher un tableau ferait de l'ouverture de l'onglet
        un téléchargement que personne n'a demandé.
        """
        for row in _lignes(campagne):
            assert "content" not in row

    def test_le_chemin_de_stockage_ne_sort_pas(self, campagne):
        """Du jargon exposé, et une adresse que rien n'oblige à rester juste."""
        for row in _lignes(campagne):
            assert not any(
                isinstance(value, str) and value.startswith("/Volumes/")
                for value in row.values()
            ), row


class TestLArchiveResteFermeeAuxAutresCampagnes:
    def test_une_campagne_voisine_ne_voit_rien(self, db, campagne):
        """La pièce est rangée sous le code de sa campagne, et lu par lui seul."""
        from inventory.config import get_settings
        from inventory.services.context import ServiceContext
        from inventory.services.evidence_service import EvidenceService

        _campaign_id, _code, _sheets = campagne
        voisine = make_campaign(db, f"ARC-{uuid.uuid4().hex[:8]}")
        ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        assert EvidenceService(ctx).scans(ctx.campaigns.get(voisine)) == []

    def test_les_pieces_d_import_ne_s_y_melangent_pas(self, campagne):
        """Elles ont leur propre onglet, et ce ne sont pas des scans.

        La liste part des feuilles : le fichier d'un chargement n'y est rattaché
        nulle part, et ne peut donc pas s'y glisser.
        """
        assert {r["filename"] for r in _lignes(campagne)} == {
            "pile-du-soir.pdf", "zone-b-retard.pdf",
        }


class TestLEcranAppelleCeQuiExiste:
    """La moitié qui manquait : l'adresse était écrite, personne ne l'appelait.

    C'est le défaut habituel de ce dépôt, et un contrôle qui ne jugerait que le
    serveur passerait dessus une fois de plus.
    """

    def source(self) -> str:
        return screen_source("features/Audit.tsx")

    def test_l_onglet_existe(self):
        """L'entrée d'onglet, et non le titre de la carte qui porte le même mot.

        Chercher « Scans archivés » seul passerait sur un écran dont l'onglet a
        disparu et dont la carte, devenue inatteignable, garde son titre.
        """
        source = self.source()
        assert "id: 'scans'" in source
        assert "Scans archivés" in source

    def test_il_demande_la_liste(self):
        assert "api.archivedScans" in self.source()

    def test_et_il_telecharge_par_la_feuille(self):
        """Un second chemin de téléchargement devrait réappliquer la barrière."""
        assert "downloads.sheetEvidence" in self.source()

    def test_le_client_porte_bien_les_deux(self):
        client = screen_source("lib/api.ts")
        assert "archivedScans" in client
        assert "sheetEvidence" in client


class TestLeLecteurDeCheminNeDeriveDeCeluiQuiLEcrit:
    """La forme du chemin est écrite à un endroit et relue à un autre.

    C'est exactement la configuration où deux copies d'une même règle finissent
    par diverger — et la divergence ne se verrait ici que par une colonne « date
    de dépôt » silencieusement vide. Le contrôle fait donc l'aller-retour :
    ``path_for`` compose, ``deposited_at`` et ``readable_name`` relisent, et ce
    qui sort doit être ce qui est entré.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "pile-du-soir.pdf",
            "scan 12 juin.pdf",
            "FEUILLE_B06 (2).PNG",
            "relevé accentué.pdf",
        ],
    )
    def test_l_aller_retour_rend_la_date_et_le_nom(self, name):
        from inventory.evidence import deposited_at, readable_name, safe_name

        at = dt.datetime(2026, 6, 30, 18, 30, 15, tzinfo=dt.UTC)
        path = _chemin("INV-2026-06", name, b"x", at)

        assert deposited_at(path) == at
        # Le nom traverse `safe_name`, qui remplace ce qu'un chemin ne peut pas
        # porter — un espace, un accent, une parenthèse. Ce qu'on relit est donc
        # ce nom-là, entier : ni l'horodatage, ni l'empreinte, ni une troncature.
        assert readable_name(path) == safe_name(name)

    def test_un_chemin_d_avant_ce_format_ne_fait_pas_tomber_l_ecran(self):
        """Une base porte les pièces des campagnes passées.

        Un chemin qu'on ne sait plus lire doit rendre une liste incomplète, et
        surtout pas un écran en erreur : c'est précisément l'écran où l'on vient
        vérifier que l'archive tient.
        """
        from inventory.evidence import deposited_at, readable_name

        for ancien in ("/Volumes/x/INV/scans/pile.pdf", "scan.pdf", ""):
            assert deposited_at(ancien) is None
            assert readable_name(ancien) == ancien.rsplit("/", 1)[-1]
