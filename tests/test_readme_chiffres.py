"""Le README annonce ses chiffres à trois endroits, et ils doivent s'accorder.

Le nombre de contrôles est écrit trois fois : dans l'arborescence du dépôt
(« tests/ 2800 contrôles ; 273 exigent un PostgreSQL »), et deux fois dans la
section Développement, en commentaire de ``make test`` et de la commande
``vitest``. Rien ne les reliait. Le bilan d'un lot de travail a donc mis à jour
le premier et laissé les deux autres décrire un dépôt qui n'existait plus :
2604 contrôles Python, 171 ignorés, 459 côté navigateur.

Un chiffre faux dans un README ne casse rien, et c'est bien le problème — il
survit d'autant plus longtemps. Ce contrôle ne vérifie pas que les chiffres
sont **justes** : compter la collecte depuis un contrôle serait lent et
circulaire. Il vérifie qu'ils sont **cohérents entre eux**, ce qui est
exactement la forme qu'a prise l'oubli : une mise à jour partielle.
"""

from __future__ import annotations

import re
from pathlib import Path

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")

#: « tests/ 2800 contrôles ; 273 exigent un PostgreSQL, ignorés sinon »
ARBORESCENCE = re.compile(r"^tests/\s+(\d+) contrôles ; (\d+) exigent un PostgreSQL", re.M)

#: « make test  # 2800 contrôles ; 273 ignorés sans PostgreSQL »
MAKE_TEST = re.compile(r"^make test\s+#\s*(\d+) contrôles ; (\d+) ignorés sans PostgreSQL", re.M)

#: « npm --prefix frontend run test  # 482 contrôles navigateur »
VITEST = re.compile(r"^npm --prefix frontend run test\s+#\s*(\d+) contrôles navigateur", re.M)


def uniques(motif: re.Pattern[str]) -> tuple[str, ...]:
    trouvés = motif.findall(README)
    assert trouvés, f"le README n'annonce plus ses chiffres sous la forme {motif.pattern!r}"
    return trouvés[0] if isinstance(trouvés[0], tuple) else (trouvés[0],)


class TestLesDeuxAnnoncesPythonSAccordent:
    def test_le_meme_nombre_de_controles(self):
        assert uniques(ARBORESCENCE)[0] == uniques(MAKE_TEST)[0]

    def test_le_meme_nombre_dignores_sans_postgres(self):
        assert uniques(ARBORESCENCE)[1] == uniques(MAKE_TEST)[1]


class TestChaqueAnnonceExiste:
    def test_larborescence_chiffre_les_controles(self):
        assert uniques(ARBORESCENCE)

    def test_la_section_developpement_chiffre_make_test(self):
        assert uniques(MAKE_TEST)

    def test_la_section_developpement_chiffre_le_banc_navigateur(self):
        assert uniques(VITEST)


class TestLesChiffresSontPlausibles:
    """Un dépôt qui a 2800 contrôles n'en annonce pas 26, ni 280 000.

    Sans cela, remplacer les trois chiffres par la même faute les rendrait
    cohérents et le contrôle passerait.
    """

    def test_les_controles_python_se_comptent_par_milliers(self):
        assert 1000 <= int(uniques(ARBORESCENCE)[0]) <= 9999

    def test_les_ignores_sont_une_part_des_controles(self):
        total, ignorés = (int(n) for n in uniques(ARBORESCENCE))
        assert 0 < ignorés < total

    def test_le_banc_navigateur_se_compte_par_centaines(self):
        assert 100 <= int(uniques(VITEST)[0]) <= 9999
