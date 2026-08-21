"""Une clé de grille se recolle et se redécoupe au même endroit.

Plusieurs grilles identifient leurs lignes par deux colonnes : un assemblage et
son composant, un entrepôt et son emplacement. La clé est alors une chaîne, donc
il faut un séparateur — et le seul caractère qu'aucune référence ne porte est le
caractère nul.

Écrit au format brut, il rend le fichier binaire : `grep` cesse d'y chercher,
`git diff` cesse de l'afficher, et un éditeur qui nettoie les caractères de
contrôle le retire sans bruit. C'est ainsi que les deux moitiés de la vue
Préparation ont divergé — la clé assemblée sur une espace, redécoupée sur le
caractère nul. Le composant ressortait indéfini, ``JSON.stringify`` retirait la
clé de l'objet envoyé, et l'activation groupée des nomenclatures était refusée
par la validation d'entrée sans que l'écran puisse dire pourquoi.

Rien n'échouait à la compilation : `childItem!` affirmait au vérificateur de
types ce que le code ne tenait pas. Ces deux contrôles portent donc sur la forme
du source, seule trace que le défaut laissait.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
HELPERS = FRONTEND / "lib" / "rowKey.ts"

SOURCES = sorted(
    p for p in FRONTEND.rglob("*.ts*") if p.is_file()
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_source_file_carries_a_raw_control_byte(path: Path) -> None:
    """Un caractère invisible ne se relit pas, donc ne se vérifie pas.

    Les échappements — ``\\u0000`` — disent la même chose en restant lisibles.
    """
    raw = path.read_bytes()
    found = sorted({byte for byte in raw if byte < 9 or 13 < byte < 32})
    assert not found, (
        f"{path.name} contient des octets de contrôle bruts {found} : "
        "le fichier passe pour binaire et échappe aux outils de relecture. "
        "Écrivez-les en séquence d'échappement."
    )


def test_the_separator_lives_in_exactly_one_module() -> None:
    """Deux définitions, c'est déjà deux occasions de diverger."""
    guilty = [
        path.name
        for path in SOURCES
        if path != HELPERS and "\\u0000" in path.read_text(encoding="utf-8")
    ]
    assert not guilty, (
        "Séparateur de clé redéfini hors de rowKey.ts : "
        + ", ".join(guilty)
        + ". Utilisez `compositeKey` et `splitCompositeKey`."
    )


def test_the_two_halves_are_exported_together() -> None:
    """Séparés, l'un peut être modifié sans que l'autre suive."""
    source = HELPERS.read_text(encoding="utf-8")
    assert "export function compositeKey" in source
    assert "export function splitCompositeKey" in source
