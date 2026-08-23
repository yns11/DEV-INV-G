#!/usr/bin/env python3
"""Écrit le schéma OpenAPI de l'application sur la sortie standard.

Sans serveur et sans base : ``create_app`` construit les routes, et le schéma se
déduit des routes seules. C'est ce qui permet de régénérer le client TypeScript
depuis un poste de développement, un runner d'intégration continue ou un
conteneur de build — aucun d'eux n'a de Lakebase à joindre.

    python scripts/dump_openapi.py > openapi.json
    npm --prefix frontend run generate:api
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

# Le schéma ne dépend pas de l'environnement, mais la construction de
# l'application lit la configuration : « local » est celui qui n'exige rien.
os.environ.setdefault("INV_ENV", "local")


def main() -> int:
    from inventory.api import create_app

    schema = create_app().openapi()
    # Indenté et trié : le fichier généré est relu par un humain lors d'une
    # revue, et deux exécutions doivent produire le même texte, faute de quoi
    # le contrôle de fraîcheur signalerait une dérive à chaque lancement.
    json.dump(schema, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
