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
from inventory.errors import NotFoundError
from inventory.evidence import EvidenceStore, safe_name

AT = dt.datetime(2026, 9, 1, 6, 30, 15, tzinfo=dt.UTC)


class FakeFiles:
    """Le strict nécessaire de l'API Files du SDK."""

    def __init__(self, *, fail: bool = False) -> None:
        self.stored: dict[str, bytes] = {}
        self.fail = fail

    def upload(self, path: str, contents: bytes, overwrite: bool = False) -> None:
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED: WRITE VOLUME")
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
    def test_the_path_carries_campaign_kind_and_moment(self):
        s, _ = store()
        assert s.path_for(
            campaign_code="INV-2026-T3", kind="scans", filename="feuille.pdf", at=AT
        ) == (
            "/Volumes/cat/inventory/inventory_evidence/INV-2026-T3/scans/"
            "20260901T063015-feuille.pdf"
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
        path = s.put(b"contenu", campaign_code="C", kind="imports",
                     filename="stock.xlsx", at=AT)
        assert path is not None
        assert files.stored[path] == b"contenu"

    def test_the_path_returned_is_the_one_stored(self):
        s, files = store()
        path = s.put(b"x", campaign_code="C", kind="imports", filename="f.xlsx")
        assert list(files.stored) == [path]

    def test_an_empty_payload_writes_nothing(self):
        """Il n'y a rien à justifier avec un fichier vide."""
        s, files = store()
        assert s.put(b"", campaign_code="C", kind="imports", filename="f") is None
        assert files.stored == {}


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


class TestRereading:
    def test_a_piece_comes_back_byte_for_byte(self):
        s, _ = store()
        path = s.put(b"\x89PNG\r\n scan", campaign_code="C", kind="scans",
                     filename="p.png")
        assert s.get(cast(str, path)) == b"\x89PNG\r\n scan"

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
            if "self._archive(" not in body
        ]
        assert not guilty, (
            "Ces importeurs ne déposent jamais leur fichier dans le volume : "
            + ", ".join(sorted(guilty))
            + ". Ajoutez `outcome.storage_path = self._archive(campaign, "
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
        assert svc._archive(
            self.campaign(), "items", {"mode": "paste", "text": "A\t1"}
        ) is None
        assert files.stored == {}

    def test_an_erp_read_is_replayed_by_its_query_not_by_a_file(self):
        svc, files = self.service()
        assert svc._archive(self.campaign(), "items", {"mode": "erp"}) is None
        assert files.stored == {}

    def test_an_uploaded_file_is_kept(self):
        svc, files = self.service()
        path = svc._archive(
            self.campaign(), "items",
            {"mode": "file", "payload": b"xlsx", "filename": "articles.xlsx"},
        )
        assert path is not None
        assert files.stored[path] == b"xlsx"

    def test_the_piece_is_filed_under_the_campaign_code(self):
        """Un humain qui parcourt le volume cherche par campagne, pas par UUID."""
        svc, _ = self.service()
        path = svc._archive(
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
