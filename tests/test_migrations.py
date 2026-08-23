"""Les migrations déjà livrées ne changent plus.

Un renommage de vocabulaire passé sur tout le dépôt a touché deux fichiers de
migration déjà appliqués en production. Rien ne l'a signalé ici : les fichiers ne
sont exécutés par aucun test, seul un commentaire changeait, et la relecture d'un
`git status` de vingt-cinq lignes n'a pas accroché. En production, le contrôle
d'empreinte a fait ce pour quoi il existe — il a refusé — mais il refuse *toutes*
les migrations suivantes avec, si bien qu'une correction de commentaire a bloqué
l'arrivée d'une colonne trois versions plus tard, et le lien entre les deux n'a
sauté aux yeux de personne.

Ces empreintes sont donc épinglées ici. Modifier une migration livrée casse ce
test, à l'endroit et au moment où c'est encore gratuit.

**Ajouter** une migration : ajoutez sa ligne. **Modifier** une migration livrée :
n'ajustez pas la ligne, écrivez-en une nouvelle.
"""

from __future__ import annotations

import hashlib

import pytest

from inventory.db.migrations import MIGRATIONS_DIR, discover

#: sha256 du contenu normalisé (fins de ligne ramenées à LF).
SHIPPED = {
    "001_initial_schema": "9c3d075773af",
    "002_zone_passes": "07fc551cb0ae",
    "003_managers": "e5d10b5d0273",
    "004_zone_negative_quantities": "604bde656ca7",
    "005_erp_mirror": "a0a9eba8628e",
    "006_erp_mirror_grants": "fccbea4b4991",
    "007_bom_status": "8fa704d7ae3d",
    "008_backflush": "e265ddaa359f",
    "009_stock_flow": "734f4e0f2141",
    "010_stock_flow_source": "98022ebe48d6",
    "011_erp_movements_mirror": "1e7a0f545b8a",
    "012_erp_movements_table": "1ba205c32173",
    "013_erp_stock_snapshot": "45d3eb84d4d5",
    "014_campaign_owner_backfill": "d1f41cef0270",
    "015_scan_job": "20ed77f608f7",
    "016_scan_job_sheet": "461bab2d5e5b",
    "017_zone_closure": "67ae24533ced",
    "018_campaign_scoped_keys": "1d70d899edc1",
    "019_sheet_evidence_fingerprint": "9477b2463139",
    "020_audit_truncate_guard": "7ddfcb890f32",
    "021_campaign_published_at": "84865dcef4f3",
}


def fingerprint(path) -> str:
    normalised = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalised).hexdigest()[:12]


class TestShippedMigrationsAreFrozen:
    @pytest.mark.parametrize("version,expected", sorted(SHIPPED.items()))
    def test_the_file_still_matches_what_was_applied(self, version, expected):
        path = MIGRATIONS_DIR / f"{version}.sql"
        assert path.exists(), f"{version} a disparu"
        assert fingerprint(path) == expected, (
            f"{version} a changé. Une migration appliquée ne se modifie pas : "
            "écrivez-en une nouvelle. Si le fichier est neuf, ajoutez sa ligne "
            "à SHIPPED."
        )

    def test_every_migration_on_disk_is_pinned(self):
        """Une migration non épinglée serait libre de dériver ensuite."""
        assert {version for version, _ in discover()} == set(SHIPPED)


class TestTheFingerprintIgnoresLineEndings:
    """Le même fichier déployé depuis Windows et depuis Linux est le même.

    L'empreinte portait sur les octets bruts : une copie de travail en CRLF
    produisait une autre valeur, et le runner l'aurait déclarée « modifiée après
    application » alors que rien de ce que Postgres exécute n'avait bougé.
    """

    def test_crlf_and_lf_give_the_same_fingerprint(self, tmp_path):
        from inventory.db.migrations import _checksum

        unix = tmp_path / "u.sql"
        unix.write_bytes(b"SELECT 1;\nSELECT 2;\n")
        windows = tmp_path / "w.sql"
        windows.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
        assert _checksum(unix) == _checksum(windows)

    def test_a_real_change_still_changes_it(self, tmp_path):
        """La tolérance s'arrête aux fins de ligne."""
        from inventory.db.migrations import _checksum

        one = tmp_path / "1.sql"
        one.write_bytes(b"SELECT 1;\n")
        two = tmp_path / "2.sql"
        two.write_bytes(b"SELECT 2;\n")
        assert _checksum(one) != _checksum(two)

    def test_even_a_comment_counts_as_a_change(self, tmp_path):
        """C'est précisément ce qui est arrivé, et ça doit rester détecté.

        Un commentaire ne change pas ce que fait la migration, mais accepter
        qu'il bouge reviendrait à accepter que le fichier bouge — et la garantie
        ne tiendrait plus qu'à la bonne foi de la relecture.
        """
        from inventory.db.migrations import _checksum

        before = tmp_path / "a.sql"
        before.write_bytes(b"-- stock livre\nSELECT 1;\n")
        after = tmp_path / "b.sql"
        after.write_bytes(b"-- stock ERP\nSELECT 1;\n")
        assert _checksum(before) != _checksum(after)
