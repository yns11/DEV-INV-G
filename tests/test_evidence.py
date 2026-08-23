"""L'archivage des pièces justificatives dans le volume Unity Catalog.

Ce que l'inventaire produit de plus fragile n'est pas le chiffre : c'est ce qui
le justifie. Un écart signé se défend six mois plus tard avec la feuille
manuscrite qui l'a produit, ou ne se défend pas — et le conteneur qui a reçu
cette feuille a disparu depuis longtemps.

Le dossier de campagne portait déjà les deux colonnes, ``import_batch.
storage_path`` et ``count_sheet.evidence_path``, et la documentation affirmait
que les preuves partaient dans un volume. Elles restaient vides : rien ne
déposait jamais de fichier. Ces tests fixent ce que le branchement fait, et
surtout ce qu'il ne fait pas.

**Il n'échoue jamais à la place de ce qu'il accompagne.** C'est la décision
structurante. Un import de deux cent mille lignes qu'on perdrait parce que le
volume est injoignable coûterait infiniment plus cher que l'absence d'une pièce
jointe, laquelle se voit et se rattrape. L'archivage se tait donc et renvoie
``None`` ; la colonne reste nulle ; l'écran dit « pas de pièce » plutôt que de
mentir.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.config import Settings
from inventory.errors import NotFoundError, UpstreamError
from inventory.evidence import EvidenceStore, safe_name

AT = dt.datetime(2026, 9, 1, 6, 30, 15, tzinfo=dt.UTC)


class FakeFiles:
    """Le strict nécessaire de l'API Files du SDK."""

    def __init__(self, *, fail: bool = False) -> None:
        self.stored: dict[str, bytes] = {}
        self.fail = fail
        #: Le drapeau reçu à chaque dépôt. Le volume réel refuse un chemin déjà
        #: pris quand il vaut ``False`` ; c'est cette garantie que le magasin
        #: exige désormais, et un test la lit ici.
        self.overwrites: list[bool] = []

    def upload(self, path: str, contents: bytes, overwrite: bool = False) -> None:
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED: WRITE VOLUME")
        self.overwrites.append(overwrite)
        if path in self.stored and not overwrite:
            raise RuntimeError(f"ALREADY EXISTS: {path}")
        self.stored[path] = contents

    def download(self, path: str) -> Any:
        if path not in self.stored:
            raise RuntimeError("NOT_FOUND")
        return SimpleNamespace(contents=_Reader(self.stored[path]))


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def store(*, fail: bool = False, **overrides: Any) -> tuple[EvidenceStore, FakeFiles]:
    files = FakeFiles(fail=fail)
    settings = Settings(
        INV_UC_CATALOG=overrides.pop("catalog", "cat"),
        INV_UC_SCHEMA=overrides.pop("schema", "inventory"),
        INV_UC_VOLUME=overrides.pop("volume", "inventory_evidence"),
    )
    return EvidenceStore(settings, client=SimpleNamespace(files=files)), files


class TestTheNameOfAFileBecomesASegmentOfPath:
    """Un nom saisi par un humain ne va pas tel quel dans un chemin."""

    def test_accents_are_flattened(self):
        assert safe_name("relevé n°4.pdf") == "releve-n4.pdf"

    def test_a_slash_cannot_change_the_folder(self):
        """Sinon le fichier part ailleurs que là où le chemin enregistré le dit."""
        assert "/" not in safe_name("Inventaire T3 / atelier")

    def test_a_name_made_only_of_separators_falls_back(self):
        assert safe_name("///", fallback="scan") == "scan"

    def test_a_very_long_name_is_cut(self):
        """Un scanner produit des noms de cent cinquante caractères."""
        assert len(safe_name("a" * 300)) == 60

    def test_an_ordinary_name_is_left_alone(self):
        assert safe_name("stock_2026-09-01.xlsx") == "stock_2026-09-01.xlsx"


class TestWhereAPieceLands:
    def test_the_path_carries_campaign_kind_moment_and_fingerprint(self):
        s, _ = store()
        assert s.path_for(
            campaign_code="INV-2026-T3", kind="scans", filename="feuille.pdf",
            at=AT, digest="abcdef1234567890",
        ) == (
            "/Volumes/cat/inventory/inventory_evidence/INV-2026-T3/scans/"
            "20260901T063015-abcdef12-feuille.pdf"
        )

    def test_the_stamp_comes_first_so_the_folder_sorts_chronologically(self):
        """C'est l'ordre dans lequel on cherche une pièce."""
        s, _ = store()
        early, late = (
            s.path_for(campaign_code="C", kind="imports", filename="a.xlsx", at=moment)
            for moment in (AT, AT + dt.timedelta(hours=3))
        )
        assert sorted((late, early)) == [early, late]

    def test_two_files_of_the_same_campaign_share_a_folder(self):
        s, _ = store()
        paths = [
            s.path_for(campaign_code="C", kind=kind, filename="f", at=AT)
            for kind in ("imports", "scans")
        ]
        assert all(p.startswith("/Volumes/cat/inventory/inventory_evidence/C/")
                   for p in paths)


class TestDepositing:
    def test_a_file_is_written_where_the_path_says(self):
        s, files = store()
        archived = s.put(b"contenu", campaign_code="C", kind="imports",
                         filename="stock.xlsx", at=AT)
        assert archived is not None
        assert files.stored[archived.path] == b"contenu"

    def test_the_path_returned_is_the_one_stored(self):
        s, files = store()
        archived = s.put(b"x", campaign_code="C", kind="imports", filename="f.xlsx")
        assert archived is not None
        assert list(files.stored) == [archived.path]

    def test_an_empty_payload_writes_nothing(self):
        """Il n'y a rien à justifier avec un fichier vide."""
        s, files = store()
        assert s.put(b"", campaign_code="C", kind="imports", filename="f") is None
        assert files.stored == {}


class TestTwoPiecesNeverCollide:
    """Le défaut : horodatage à la seconde + nom, déposés en écrasement.

    Deux feuilles envoyées ensemble, ou un re-scan après correction, portent le
    nom que le scanner leur a donné — souvent le même. Dans la même seconde,
    les deux chemins étaient identiques, et le second effaçait le premier : la
    feuille dont la base gardait le chemin pointait alors sur l'image d'une
    autre. Un contrôle six mois plus tard aurait relu la mauvaise pièce sans
    que rien ne le signale.
    """

    def test_two_different_files_of_the_same_name_and_second_coexist(self):
        s, files = store()
        first = s.put(b"la feuille de Z1", campaign_code="C", kind="scans",
                      filename="scan.pdf", at=AT)
        second = s.put(b"la feuille de Z2", campaign_code="C", kind="scans",
                       filename="scan.pdf", at=AT)
        assert first is not None and second is not None
        assert first.path != second.path
        assert files.stored[first.path] == b"la feuille de Z1"
        assert files.stored[second.path] == b"la feuille de Z2"

    def test_the_same_file_twice_lands_on_the_same_path(self):
        """Deux dépôts identiques ne doivent pas encombrer le volume."""
        s, files = store()
        once = s.put(b"identique", campaign_code="C", kind="scans",
                     filename="scan.pdf", at=AT)
        twice = s.put(b"identique", campaign_code="C", kind="scans",
                      filename="scan.pdf", at=AT)
        assert once is not None and twice is not None
        assert once.path == twice.path
        assert len(files.stored) == 1

    def test_nothing_is_ever_deposited_in_overwrite_mode(self):
        """La propriété tient parce que rien n'écrase : c'est vérifié ici."""
        s, files = store()
        s.put(b"x", campaign_code="C", kind="scans", filename="f.pdf", at=AT)
        assert files.overwrites == [False]


class TestWhatIsKnownAboutAPiece:
    """Le chemin seul ne dit pas si le fichier relu est celui qui a été lu.

    Un volume se modifie depuis l'espace de travail. L'empreinte est ce qui
    permet, six mois plus tard, de répondre autrement que par la confiance.
    """

    def test_the_fingerprint_is_the_sha256_of_the_content(self):
        import hashlib

        s, _ = store()
        archived = s.put(b"contenu", campaign_code="C", kind="imports",
                         filename="f.xlsx")
        assert archived is not None
        assert archived.sha256 == hashlib.sha256(b"contenu").hexdigest()

    def test_the_size_is_the_size(self):
        s, _ = store()
        archived = s.put(b"12345", campaign_code="C", kind="imports", filename="f")
        assert archived is not None
        assert archived.size == 5

    def test_the_type_comes_from_the_name(self):
        s, _ = store()
        archived = s.put(b"x", campaign_code="C", kind="scans", filename="p.png")
        assert archived is not None
        assert archived.mime == "image/png"

    def test_an_unknown_extension_does_not_invent_a_type(self):
        s, _ = store()
        archived = s.put(b"x", campaign_code="C", kind="scans", filename="p.zzz")
        assert archived is not None
        assert archived.mime == "application/octet-stream"

    def test_the_path_begins_with_the_fingerprint_that_is_returned(self):
        """Les deux doivent parler du même fichier, sinon aucun ne sert."""
        s, _ = store()
        archived = s.put(b"contenu", campaign_code="C", kind="imports",
                         filename="f.xlsx", at=AT)
        assert archived is not None
        assert f"-{archived.sha256[:8]}-" in archived.path


class TestWhenArchivingCannotHappen:
    """La décision structurante : se taire, jamais faire échouer l'appelant."""

    def test_an_unconfigured_volume_is_silent(self):
        s, files = store(volume="")
        assert s.available is False
        assert s.put(b"x", campaign_code="C", kind="imports", filename="f") is None
        assert files.stored == {}

    def test_a_refused_write_is_silent_too(self):
        """Droit manquant, volume plein, API en panne : même issue."""
        s, _ = store(fail=True)
        assert s.put(b"x", campaign_code="C", kind="imports", filename="f") is None

    def test_the_refusal_is_traced_even_though_it_is_swallowed(self, caplog):
        """Silencieux pour l'appelant ne veut pas dire invisible pour l'exploitant."""
        s, _ = store(fail=True)
        with caplog.at_level("WARNING"):
            s.put(b"x", campaign_code="C", kind="imports", filename="f")
        assert "PERMISSION_DENIED" in caplog.text


class TestWhenTheArchiveIsNotOptional:
    """Le silence était la règle partout, y compris là où il ne peut pas l'être.

    Un export ERP se relit dans l'ERP : ne pas l'archiver est un incident
    d'exploitation. Une feuille manuscrite, non : le papier repart dans
    l'atelier et finit à la benne. Écrire les quantités que le modèle y a lues
    en sachant que l'image n'a pas été archivée fabriquerait un comptage que
    personne ne pourra jamais vérifier — et c'est très exactement ce que
    l'application existe pour empêcher.
    """

    def test_a_refused_write_stops_the_operation(self):
        s, _ = store(fail=True)
        with pytest.raises(UpstreamError):
            s.put(b"x", campaign_code="C", kind="scans", filename="f.pdf",
                  required=True)

    def test_an_unconfigured_volume_stops_it_too(self):
        """Une archive non déclarée est une archive absente, pas une dispense."""
        s, _ = store(volume="")
        with pytest.raises(UpstreamError):
            s.put(b"x", campaign_code="C", kind="scans", filename="f.pdf",
                  required=True)

    def test_an_empty_file_stops_it_as_well(self):
        s, _ = store()
        with pytest.raises(UpstreamError):
            s.put(b"", campaign_code="C", kind="scans", filename="f.pdf",
                  required=True)

    def test_the_refusal_says_what_to_do(self):
        s, _ = store(volume="")
        with pytest.raises(UpstreamError) as raised:
            s.put(b"x", campaign_code="C", kind="scans", filename="f.pdf",
                  required=True)
        assert "volume Unity Catalog" in str(raised.value)

    def test_the_refusal_is_traced_as_an_error_not_a_warning(self, caplog):
        s, _ = store(fail=True)
        with caplog.at_level("ERROR"), pytest.raises(UpstreamError):
            s.put(b"x", campaign_code="C", kind="scans", filename="f.pdf",
                  required=True)
        assert "PERMISSION_DENIED" in caplog.text

    def test_a_file_already_there_is_not_a_failure(self):
        """Le même contenu déposé deux fois : la pièce est là, et c'est la bonne."""
        s, files = store()
        first = s.put(b"identique", campaign_code="C", kind="scans",
                      filename="f.pdf", at=AT, required=True)
        again = s.put(b"identique", campaign_code="C", kind="scans",
                      filename="f.pdf", at=AT, required=True)
        assert first is not None and again is not None
        assert first.path == again.path
        assert len(files.stored) == 1

    def test_without_the_flag_nothing_changes(self):
        """Le régime par défaut reste celui que le module défend depuis toujours."""
        s, _ = store(fail=True)
        assert s.put(b"x", campaign_code="C", kind="imports", filename="f") is None


class TestRereading:
    def test_a_piece_comes_back_byte_for_byte(self):
        s, _ = store()
        archived = s.put(b"\x89PNG\r\n scan", campaign_code="C", kind="scans",
                         filename="p.png")
        assert archived is not None
        assert s.get(cast(str, archived.path)) == b"\x89PNG\r\n scan"

    def test_a_missing_piece_says_so_instead_of_returning_nothing(self):
        """L'utilisateur a cliqué sur « pièce jointe » et attend un fichier."""
        s, _ = store()
        with pytest.raises(NotFoundError):
            s.get("/Volumes/cat/inventory/inventory_evidence/C/scans/absent.png")

    @pytest.mark.parametrize("path", [
        "/etc/passwd",
        "/Volumes/autre/schema/volume/piece.pdf",
        "/Volumes/cat/inventory/inventory_evidence/../../secret",
    ])
    def test_a_path_outside_the_volume_is_refused(self, path):
        """Le chemin vient de la base, mais il transite par une URL.

        Une colonne texte n'est pas une garantie, et le seul endroit où cette
        vérification ne coûte rien est juste avant la lecture.
        """
        s, _ = store()
        with pytest.raises(NotFoundError):
            s.get(path)


# --------------------------------------------------------------------------- #
# Le branchement : qui archive, et qui ne doit surtout pas
# --------------------------------------------------------------------------- #

class TestWhichPathsArchive:
    """Une pièce n'a de sens que là où il y a un original à conserver.

    La règle a déjà été appliquée à moitié une fois dans ce dossier — les deux
    colonnes existaient, rien ne les remplissait. Ces contrôles lisent le source
    des services plutôt que de refaire un import complet : ce qui se perd, c'est
    un appel oublié dans un chemin sur huit, et c'est cela qu'ils regardent.
    """

    def importers(self) -> dict[str, str]:
        """Le corps de chaque importeur public, par cible."""
        import ast
        from pathlib import Path

        import inventory

        path = Path(inventory.__file__).parent / "services" / "import_service.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        out: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("import_"):
                out[node.name] = ast.get_source_segment(source, node) or ""
        return out

    def test_every_importer_was_found(self):
        """Sinon le contrôle ci-dessous passerait sur une liste vide."""
        assert len(self.importers()) >= 8

    def test_every_importer_archives_its_file(self):
        guilty = [
            name for name, body in self.importers().items()
            if "self.batches.archive(" not in body
        ]
        assert not guilty, (
            "Ces importeurs ne déposent jamais leur fichier dans le volume : "
            + ", ".join(sorted(guilty))
            + ". Ajoutez `outcome.storage_path = self.batches.archive(campaign, "
            "\"<cible>\", kwargs)` après la construction de l'issue."
        )

    def test_no_recording_hard_codes_the_absence_of_a_piece(self):
        """`storage_path=None` en dur est la forme qu'avait le défaut d'origine."""
        from pathlib import Path

        import inventory

        source = (
            Path(inventory.__file__).parent / "services" / "import_service.py"
        ).read_text(encoding="utf-8")
        assert "storage_path=None" not in source


class TestWhatIsNotWorthArchiving:
    """Tout ce qui entre n'est pas un original."""

    def service(self, **settings: Any):
        from inventory.services.import_service import ImportService

        s, files = store(**settings)
        ctx = SimpleNamespace(evidence=s)
        return ImportService(cast(Any, ctx)), files

    def campaign(self):
        return cast(Any, SimpleNamespace(id="camp-1", code="INV-2026-T3"))

    def test_a_pasted_block_has_no_original_to_keep(self):
        """Le texte collé est déjà dans les lignes chargées."""
        svc, files = self.service()
        assert svc.batches.archive(
            self.campaign(), "items", {"mode": "paste", "text": "A\t1"}
        ) is None
        assert files.stored == {}

    def test_an_erp_read_is_replayed_by_its_query_not_by_a_file(self):
        svc, files = self.service()
        assert svc.batches.archive(self.campaign(), "items", {"mode": "erp"}) is None
        assert files.stored == {}

    def test_an_uploaded_file_is_kept(self):
        svc, files = self.service()
        path = svc.batches.archive(
            self.campaign(), "items",
            {"mode": "file", "payload": b"xlsx", "filename": "articles.xlsx"},
        )
        assert path is not None
        assert files.stored[path] == b"xlsx"

    def test_the_piece_is_filed_under_the_campaign_code(self):
        """Un humain qui parcourt le volume cherche par campagne, pas par UUID."""
        svc, _ = self.service()
        path = svc.batches.archive(
            self.campaign(), "items",
            {"mode": "file", "payload": b"x", "filename": "a.xlsx"},
        )
        assert "/INV-2026-T3/items/" in cast(str, path)


# --------------------------------------------------------------------------- #
# Le nom du fichier, dans l'en-tête HTTP
# --------------------------------------------------------------------------- #

class TestTheNameThatTravelsInTheHeader:
    """Un en-tête HTTP se transporte en latin-1, pas en UTF-8.

    Les rapports portaient déjà des noms accentués, mais l'application les
    fabriquait elle-même. Une pièce justificative arrive avec le nom que lui a
    donné le copieur ou la personne qui l'a enregistrée : n'importe lequel.

    Deux issues, découvertes en rejouant un chargement de bout en bout.
    « scan №4.pdf » n'a pas de représentation latin-1, donc l'encodage de la
    réponse échouait — un téléchargement transformé en erreur serveur par le
    seul nom du fichier. Et un guillemet fermait la valeur de l'en-tête, ce qui
    laissait y écrire ce qu'on voulait.
    """

    def header(self, filename: str) -> str:
        from inventory.api.downloads import attachment

        return attachment(b"x", filename, "text/csv").headers["content-disposition"]

    @pytest.mark.parametrize("filename", [
        "articles réf n°1.csv",
        "scan №4.pdf",
        "关于库存.xlsx",
        "relevé 🧾.pdf",
    ])
    def test_any_name_survives_the_encoding_of_the_response(self, filename):
        """C'est le serveur qui encode l'en-tête ; il ne doit pas y échouer."""
        self.header(filename).encode("latin-1")

    @pytest.mark.parametrize("filename", [
        'feuille"; attachment; x=".pdf',
        "ligne\r\nX-Injecte: oui.pdf",
    ])
    def test_a_name_cannot_write_its_own_header(self, filename):
        """Le nom vient du fichier téléversé : il n'est pas de confiance."""
        header = self.header(filename)
        quoted = header.split('filename="', 1)[1].split('"', 1)[0]
        assert '"' not in quoted
        assert "\r" not in quoted and "\n" not in quoted

    def test_the_real_name_is_still_carried_intact(self):
        """Le repli ASCII est un secours, pas ce que le navigateur retient."""
        assert "filename*=UTF-8''%C3%A9carts.xlsx" in self.header("écarts.xlsx")

    def test_a_name_that_folds_to_nothing_still_has_a_fallback(self):
        """Sinon l'en-tête porterait `filename=""`, que certains clients refusent.

        Un nom entièrement idéographique ne laisse rien après le repli ASCII —
        contrairement à « № », que la décomposition Unicode rend en « No ».
        """
        assert 'filename="fichier"' in self.header("关于库存")


class TestWhatTheSheetActuallyStores:
    """Le service transmet l'empreinte ; encore faut-il que la requête l'écrive.

    Les contrôles du pipeline de scan doublent le dépôt : ils voient ce que le
    service *transmet*, jamais ce que Postgres *reçoit*. Une colonne oubliée
    dans le constructeur de requête leur échapperait entièrement — c'est déjà
    arrivé, sur une autre colonne, et seule la base réelle l'avait vu.
    """

    def _spy(self):
        from inventory.db.repositories import SheetRepository

        repo = SheetRepository.__new__(SheetRepository)
        seen: list[tuple[str, tuple[Any, ...]]] = []
        repo._execute = lambda q, p=(), *, conn=None: (  # type: ignore[method-assign]
            seen.append((" ".join(q.split()), tuple(p))) or 1
        )
        return repo, seen

    def test_the_three_columns_reach_the_statement(self):
        repo, seen = self._spy()
        repo.update_sheet(
            "camp-1", "s-1",
            evidence_path="/Volumes/x/scan.pdf",
            evidence_sha256="d" * 64,
            evidence_bytes=4096,
            evidence_mime="application/pdf",
            actor="chef@usine",
        )
        query, params = seen[-1]
        for column in ("evidence_path", "evidence_sha256", "evidence_bytes",
                       "evidence_mime"):
            assert f"{column} = %s" in query, f"{column} absent de la requête"
        assert "d" * 64 in params
        assert 4096 in params
        assert "application/pdf" in params

    def test_a_sheet_without_a_scan_writes_none_of_them(self):
        """Une correction de nom de compteur ne doit pas toucher la pièce."""
        repo, seen = self._spy()
        repo.update_sheet("camp-1", "s-1", counter_name="Alice", actor="a")
        query, _ = seen[-1]
        assert "evidence" not in query
