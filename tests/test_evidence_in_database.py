"""L'archive de secours : les pièces justificatives dans la base.

Le volume Unity Catalog est le bon endroit pour une pièce justificative — il se
parcourt depuis l'espace de travail, sans requête SQL et sans l'application. Y
écrire demande trois privilèges au service principal de l'application, et le
premier, ``USE CATALOG``, ne s'accorde que par un détenteur de ``MANAGE`` sur le
catalogue, c'est-à-dire son propriétaire. Sur un catalogue partagé, ce
propriétaire peut être injoignable. L'inventaire, lui, garde sa date.

``INV_EVIDENCE_STORE=lakebase`` archive alors dans le schéma de l'application,
qu'elle possède et où elle écrit déjà tout le reste : **aucun administrateur
n'est impliqué**. C'est le renversement que ``INV_ERP_SOURCE=mirror`` a fait
pour le référentiel, appliqué aux pièces.

Trois propriétés portent tout le reste, et ce sont celles que ces contrôles
tiennent :

1. **La garantie ne bouge pas.** Un scan est archivé avant que ses quantités
   soient écrites, ou l'opération est refusée. Une archive de secours qui
   perdrait cette propriété ne serait pas une solution, seulement un
   renoncement déguisé.
2. **La relecture s'aiguille sur le chemin, jamais sur la configuration.** Une
   base d'inventaire porte les pièces des campagnes passées, déposées sous le
   réglage d'alors. Router sur le réglage du jour rendrait introuvable, à
   l'instant d'une bascule, tout ce qui est dans l'autre archive — et une pièce
   qu'on ne retrouve plus n'en est plus une.
3. **Le refus du volume nomme cette issue.** L'exploitant qui bute sur
   ``USE CATALOG`` lit le message d'erreur, pas la documentation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from types import SimpleNamespace
from typing import Any

import pytest

from inventory.config import Settings
from inventory.errors import NotFoundError, UpstreamError
from inventory.evidence import LAKEBASE_ROOT, EvidenceStore, archive_advice

AT = dt.datetime(2026, 9, 1, 6, 30, 15, tzinfo=dt.UTC)


def octets(texte: str) -> bytes:
    """Le texte en UTF-8 — un littéral ``b"…"`` n'accepte pas les accents."""
    return texte.encode()


class FakeBlobs:
    """Le contrat de ``EvidenceBlobRepository``, et rien de plus.

    ``put`` reproduit le ``ON CONFLICT (path) DO NOTHING`` de la migration 022 :
    un chemin déjà pris n'est pas une erreur, parce que le chemin porte
    l'empreinte du contenu et qu'un chemin repris l'est donc par un fichier
    identique. Une doublure qui lèverait ici décrirait une table que la
    migration ne crée pas.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.fail = fail
        self.boom = "UndefinedTable: relation « evidence_blob » n'existe pas"
        self.deleted: list[str] = []

    def put(self, **row: Any) -> bool:
        if self.fail:
            raise RuntimeError(self.boom)
        if row["path"] in self.rows:
            return False
        self.rows[row["path"]] = row
        return True

    def get(self, path: str) -> bytes | None:
        row = self.rows.get(path)
        return None if row is None else row["content"]

    def delete(self, path: str) -> int:
        self.deleted.append(path)
        return 1 if self.rows.pop(path, None) else 0


class FakeFiles:
    """Un volume qui note ce qu'on lui demande. Il ne doit rien recevoir."""

    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}
        self.calls = 0

    def upload(self, path: str, contents: Any, overwrite: bool = False) -> None:
        self.calls += 1
        self.stored[path] = contents.read()

    def delete(self, path: str) -> None:
        self.stored.pop(path, None)

    def download(self, path: str) -> Any:
        if path not in self.stored:
            raise RuntimeError("NOT_FOUND")
        return SimpleNamespace(contents=SimpleNamespace(read=lambda: self.stored[path]))


def store(
    *, where: str = "lakebase", blobs_fail: bool = False, **overrides: Any
) -> tuple[EvidenceStore, FakeBlobs, FakeFiles]:
    """Un magasin réglé sur l'une ou l'autre archive, avec les deux doublures.

    Les deux sont branchées quel que soit le réglage : c'est ce qui permet de
    vérifier non seulement que la pièce arrive où elle doit, mais qu'elle
    **n'arrive pas** ailleurs.
    """
    blobs, files = FakeBlobs(fail=blobs_fail), FakeFiles()
    settings = Settings(
        INV_EVIDENCE_STORE=where,
        INV_UC_CATALOG=overrides.pop("catalog", "cat"),
        INV_UC_SCHEMA="inventory",
        INV_UC_VOLUME="inventory_evidence",
        DATABRICKS_CLIENT_ID="sp-1234",
        PGHOST=overrides.pop("pg_host", "lakebase.example"),
        PGDATABASE=overrides.pop("pg_database", "inventaire"),
        PGUSER=overrides.pop("pg_user", "app"),
    )
    engine = EvidenceStore(settings, client=SimpleNamespace(files=files), blobs=blobs)
    return engine, blobs, files


class TestWhereThePieceActuallyGoes:
    def test_in_database_mode_nothing_reaches_the_volume(self):
        """La bascule n'est pas une préférence : le volume n'est plus touché.

        Un magasin qui déposerait des deux côtés « pour ne rien perdre »
        échouerait sur le mur qu'on contourne, et rendrait la bascule inutile.
        """
        engine, blobs, files = store()

        archived = engine.put(
            b"scan", campaign_code="GEN2", kind="scans",
            filename="feuille.pdf", at=AT, required=True,
        )

        assert files.calls == 0
        assert archived is not None
        assert list(blobs.rows) == [archived.path]

    def test_the_path_says_which_archive_holds_it(self):
        engine, _, _ = store()

        archived = engine.put(
            b"scan", campaign_code="GEN2", kind="scans",
            filename="feuille.pdf", at=AT,
        )

        assert archived is not None
        assert archived.path.startswith(f"{LAKEBASE_ROOT}/")
        assert "/Volumes/" not in archived.path

    def test_the_shape_of_the_path_is_the_one_of_the_volume(self):
        """Campagne, nature, horodatage, empreinte, nom — dans cet ordre.

        Ce n'est pas de la coquetterie : c'est ce qui garde un ``SELECT path``
        lisible par un humain, et ce qui rendra possible de ressortir les pièces
        vers le volume le jour où le grant arrive.
        """
        engine, _, _ = store()
        digest = hashlib.sha256(b"scan").hexdigest()

        archived = engine.put(
            b"scan", campaign_code="GEN2", kind="scans",
            filename="feuille.pdf", at=AT,
        )

        assert archived is not None
        assert archived.path == (
            f"{LAKEBASE_ROOT}/GEN2/scans/20260901T063015-{digest[:8]}-feuille.pdf"
        )

    def test_what_is_written_beside_the_content_identifies_it(self):
        """L'empreinte et le type sont écrits, pas seulement les octets.

        L'empreinte répond à la seule question d'un contrôle — le fichier relu
        est-il celui que le modèle a lu — et le type est ce qui fait qu'un PDF
        s'ouvre au lieu d'être téléchargé sans nom.
        """
        engine, blobs, _ = store()

        archived = engine.put(
            b"scan", campaign_code="GEN2", kind="scans",
            filename="feuille.pdf", at=AT,
        )

        assert archived is not None
        row = blobs.rows[archived.path]
        assert row["sha256"] == hashlib.sha256(b"scan").hexdigest()
        assert row["mime"] == "application/pdf"
        assert row["campaign_code"] == "GEN2"
        assert row["kind"] == "scans"
        assert row["content"] == b"scan"

    def test_in_volume_mode_the_table_stays_empty(self):
        """Le dépôt est passé au magasin quel que soit le réglage.

        S'il n'était consulté que par le réglage, une inversion de la condition
        écrirait des deux côtés sans que rien ne le dise.
        """
        engine, blobs, files = store(where="volume")

        archived = engine.put(
            b"scan", campaign_code="GEN2", kind="scans",
            filename="feuille.pdf", at=AT, required=True,
        )

        assert blobs.rows == {}
        assert archived is not None
        assert archived.path in files.stored


class TestTheGuaranteeIsUnchanged:
    """Une archive de secours qui perdrait la garantie n'en serait pas une."""

    def test_a_required_scan_is_refused_when_the_table_fails(self):
        engine, _, _ = store(blobs_fail=True)

        with pytest.raises(UpstreamError) as refusal:
            engine.put(
                b"scan", campaign_code="GEN2", kind="scans",
                filename="feuille.pdf", at=AT, required=True,
            )

        assert "n'a pas pu être archivée" in str(refusal.value)

    def test_an_import_still_survives_a_failing_table(self):
        """Non bloquant reste non bloquant : deux cent mille lignes valent plus
        qu'une pièce jointe, et l'écran distingue déjà « pas de pièce »."""
        engine, _, _ = store(blobs_fail=True)

        assert engine.put(
            b"export", campaign_code="GEN2", kind="imports", filename="erp.csv"
        ) is None

    def test_an_unreachable_database_is_not_a_configured_archive(self):
        """Sans base, le mode « lakebase » n'archive rien — et le dit.

        En mode volume, ce sont les trois variables du volume qui répondent ;
        ici la question n'a plus de sens, et y répondre « oui » ferait croire à
        une archive qui n'existe pas.
        """
        engine, _, _ = store(pg_host="")

        assert engine.available is False
        with pytest.raises(UpstreamError) as refusal:
            engine.put(
                b"scan", campaign_code="GEN2", kind="scans",
                filename="feuille.pdf", required=True,
            )
        assert "base de l'application est joignable" in str(refusal.value)

    def test_the_volume_variables_no_longer_decide(self):
        """Un volume non déclaré n'empêche pas l'archivage en base."""
        engine, _, _ = store(catalog="")

        assert engine.available is True

    def test_redepositing_the_same_file_is_not_a_failure(self):
        """Le chemin porte l'empreinte : un chemin repris l'est à l'identique.

        C'est le cas nominal d'un re-scan après une erreur ailleurs. Le refuser
        ferait échouer une opération dont la pièce est déjà là, et c'est la
        bonne.
        """
        engine, blobs, _ = store()
        kwargs = {"campaign_code": "GEN2", "kind": "scans",
                  "filename": "f.pdf", "at": AT}

        first = engine.put(b"scan", required=True, **kwargs)
        second = engine.put(b"scan", required=True, **kwargs)

        assert first == second
        assert len(blobs.rows) == 1


class TestRereadingFollowsThePathNotTheSetting:
    """Une bascule ne rend illisible rien de ce qui la précède."""

    def test_a_volume_path_is_read_from_the_volume_in_database_mode(self):
        engine, _, files = store()
        path = "/Volumes/cat/inventory/inventory_evidence/GEN1/scans/vieux.pdf"
        files.stored[path] = octets("la feuille de la campagne d'avant")

        assert engine.get(path) == octets("la feuille de la campagne d'avant")

    def test_a_database_path_is_read_from_the_table_in_volume_mode(self):
        engine, blobs, _ = store(where="volume")
        path = f"{LAKEBASE_ROOT}/GEN1/scans/vieux.pdf"
        blobs.rows[path] = {"content": octets("la feuille gardée en base")}

        assert engine.get(path) == octets("la feuille gardée en base")

    def test_a_missing_row_reads_as_a_missing_piece(self):
        engine, _, _ = store()

        with pytest.raises(NotFoundError):
            engine.get(f"{LAKEBASE_ROOT}/GEN2/scans/jamais-deposee.pdf")

    @pytest.mark.parametrize(
        "path",
        [
            "lakebase:/../etc/passwd",
            "/Volumes/autre/inventory/inventory_evidence/x.pdf",
            "lakebasement:/GEN2/scans/x.pdf",
            "/etc/passwd",
        ],
    )
    def test_a_path_outside_both_archives_is_refused(self, path):
        """Le chemin vient d'une colonne texte, et transite par une URL.

        Élargir la barrière à deux racines ne doit pas revenir à l'ouvrir : ce
        qui est accepté est le volume **de cette application** et sa table de
        pièces, rien d'autre.
        """
        engine, _, _ = store()

        with pytest.raises(NotFoundError):
            engine.get(path)


class TestTheProbeAnswersForBothArchives:
    def test_it_writes_and_takes_back_its_diagnostic_row(self):
        """Écrire est la seule question qui pose vraiment la question.

        Et une sonde qui laisserait sa ligne derrière elle salirait la table
        qu'elle vérifie.
        """
        engine, blobs, _ = store()

        answer = engine.probe()

        assert answer["ok"] is True
        assert answer["configured"] is True
        assert answer["path"] == f"{LAKEBASE_ROOT}/_diagnostic/ecriture.probe"
        assert blobs.deleted == [answer["path"]]
        assert blobs.rows == {}

    def test_a_failing_table_is_reported_without_a_single_grant(self):
        """Conseiller un GRANT ici enverrait chercher un droit qui n'existe pas.

        La base de l'application n'a demandé de privilège à personne : c'est
        tout l'intérêt de cette archive, et le message doit le refléter.
        """
        engine, _, _ = store(blobs_fail=True)

        answer = engine.probe()

        assert answer["ok"] is False
        assert answer["configured"] is True
        assert "GRANT" not in answer["detail"]
        assert "evidence_blob" in answer["detail"]

    def test_an_unconfigured_database_names_the_database(self):
        engine, _, _ = store(pg_host="")

        answer = engine.probe()

        assert answer["configured"] is False
        assert "PGHOST" in answer["detail"]
        assert "INV_UC_CATALOG" not in answer["detail"]


class TestTheRefusalOnTheVolumeNamesTheWayOut:
    """L'exploitant bloqué lit le message d'erreur, pas la documentation."""

    def test_a_permission_refusal_offers_the_database(self):
        advice = archive_advice(
            RuntimeError("PERMISSION_DENIED: User does not have USE CATALOG"),
            path="/Volumes/cat/inventory/inventory_evidence/GEN2/scans/f.pdf",
            principal="sp-1234",
        )

        assert "INV_EVIDENCE_STORE=lakebase" in advice
        # Sans cette phrase, la sortie de secours ressemble à une perte : ce qui
        # est déjà dans le volume y reste, et reste lisible.
        assert "reste lisible" in advice

    def test_it_still_names_the_three_grants_first(self):
        """La sortie de secours est un second choix, pas le conseil principal.

        Le volume se parcourt depuis l'espace de travail ; la table non. Qui
        peut obtenir les trois GRANT doit les obtenir.
        """
        advice = archive_advice(
            RuntimeError("PERMISSION_DENIED: User does not have USE CATALOG"),
            path="/Volumes/cat/inventory/inventory_evidence/GEN2/scans/f.pdf",
            principal="sp-1234",
        )

        assert advice.index("GRANT USE CATALOG") < advice.index("INV_EVIDENCE_STORE")

    def test_a_database_path_never_advises_a_grant(self):
        advice = archive_advice(
            RuntimeError("UndefinedTable: relation « evidence_blob » n'existe pas"),
            path=f"{LAKEBASE_ROOT}/GEN2/scans/f.pdf",
            principal="sp-1234",
        )

        assert "GRANT" not in advice
        assert "USE CATALOG" not in advice
        assert "022" in advice


# --------------------------------------------------------------------------- #
# Contre une vraie base
# --------------------------------------------------------------------------- #

@pytest.mark.postgres
class TestAgainstRealPostgres:
    """Ce qu'une doublure ne peut pas certifier.

    La leçon a été payée une fois : la doublure de l'API Files acceptait des
    octets là où le SDK exige un flux, et elle a donc certifié pendant toute la
    vie de la fonctionnalité un appel qui ne pouvait pas aboutir — aucune pièce
    n'avait jamais été archivée. Le stockage binaire et le ``ON CONFLICT`` sont
    exactement du même ordre : ce qui est en jeu est le comportement du moteur.

    Sans PostgreSQL joignable, ces contrôles sont ignorés plutôt que faussement
    verts ; ceux du dessus, qui portent sur l'aiguillage, tournent toujours.
    """

    @pytest.fixture
    def repository(self):
        if not os.environ.get("PGHOST"):
            pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
        from inventory.config import get_settings
        from inventory.db.engine import Database
        from inventory.db.migrations import apply_all
        from inventory.db.repositories import EvidenceBlobRepository

        get_settings.cache_clear()
        try:
            db = Database(get_settings())
            if not db.ping():
                pytest.skip("PostgreSQL injoignable")
        except Exception as exc:  # pragma: no cover - dépend de l'infrastructure
            pytest.skip(f"PostgreSQL injoignable : {exc}")
        apply_all(db)
        return EvidenceBlobRepository(db)

    @staticmethod
    def _row(path: str, content: bytes) -> dict[str, Any]:
        return {
            "path": path, "campaign_code": "TEST", "kind": "scans",
            "filename": "f.pdf", "mime": "application/pdf",
            "sha256": hashlib.sha256(content).hexdigest(), "content": content,
        }

    def test_bytes_survive_the_round_trip_unchanged(self, repository):
        """Un PDF n'est pas du texte : zéros, octets hauts, tout doit revenir.

        Une colonne ``TEXT`` aurait « marché » sur du contenu ASCII et cassé sur
        le premier vrai scan.
        """
        content = bytes(range(256)) * 8 + b"\x00\x00%PDF-1.7\xff\xfe"
        path = f"{LAKEBASE_ROOT}/TEST/scans/octets-{content[-4:].hex()}.pdf"
        repository.delete(path)

        repository.put(**self._row(path, content))

        assert repository.get(path) == content
        repository.delete(path)

    def test_the_same_path_twice_is_accepted_and_keeps_the_first(self, repository):
        content = b"la feuille"
        path = f"{LAKEBASE_ROOT}/TEST/scans/conflit.pdf"
        repository.delete(path)

        assert repository.put(**self._row(path, content)) is True
        assert repository.put(**self._row(path, content)) is False
        assert repository.get(path) == content
        repository.delete(path)

    def test_an_absent_path_reads_as_nothing(self, repository):
        assert repository.get(f"{LAKEBASE_ROOT}/TEST/scans/jamais.pdf") is None

    def test_a_deposited_piece_reads_back_through_the_store(self, repository):
        """Bout en bout : le magasin dépose, le magasin relit, sans volume.

        Les deux moitiés vérifiées séparément peuvent tenir sans que la chaîne
        tienne — c'est précisément ce qui s'était produit.
        """
        settings = Settings(
            INV_EVIDENCE_STORE="lakebase",
            PGHOST=os.environ["PGHOST"], PGDATABASE=os.environ.get("PGDATABASE", ""),
            PGUSER=os.environ.get("PGUSER", ""),
        )
        engine = EvidenceStore(settings, blobs=repository)
        content = b"%PDF-1.7\x00 relev\xc3\xa9 manuscrit"

        archived = engine.put(
            content, campaign_code="TEST", kind="scans",
            filename="relevé n°4.pdf", at=AT, required=True,
        )

        assert archived is not None
        assert engine.get(archived.path) == content
        assert archived.sha256 == hashlib.sha256(content).hexdigest()
        repository.delete(archived.path)
