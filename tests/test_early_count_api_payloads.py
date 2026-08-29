"""Les charges utiles des comptages avancés, confrontées à leur déclaration.

Trois routes renvoyaient un `model_dump` de modèle de domaine — donc des clés
en `snake_case` — à un modèle de réponse qui ne déclare que des alias en
`camelCase`. Pydantic exigeait l'alias, ne le trouvait pas, et FastAPI levait
`ResponseValidationError` : **500 sur `GET /early-counts/journals` dès le
premier journal importé**.

Aucun contrôle ne l'a vu, et la raison mérite d'être écrite. La confrontation de
`test_api_contract.py` fait exactement ce travail, mais elle **saute** quand la
route ne renvoie aucune ligne — et la base de contrôle n'avait ni journal ERP,
ni lot, ni dérive. Un contrôle qui s'ignore au lieu d'échouer est la pire des
deux issues ; c'est écrit dans ce fichier-là, et c'est ce qui s'est produit.

D'où celui-ci : il **sème** ce qu'il faut avant de regarder, et il refuse une
liste vide au lieu de s'abstenir.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.postgres

SOL = ("ATP", "SOL")


@pytest.fixture(scope="module")
def client():
    if not os.environ.get("PGHOST"):
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
    from fastapi.testclient import TestClient

    from inventory.api import create_app

    with TestClient(create_app()) as running:
        yield running


@pytest.fixture(scope="module")
def seeded(client) -> str:
    """Une campagne qui porte un journal ERP, un lot et une dérive.

    Semée par les dépôts sur la base que l'application vient de migrer : ce
    sont les mêmes lignes que l'ERP produirait, et c'est leur traversée de la
    route qui est en cause, pas leur origine.
    """
    from inventory.config import get_settings
    from inventory.db.engine import Database
    from inventory.db.repositories.erp_journal import (
        EarlyCountDriftRepository,
        ErpJournalRepository,
        LabelDecisionRepository,
    )
    from inventory.db.repositories.journal import JournalRepository
    from inventory.domain.enums import JournalKind, LabelResolution
    from inventory.domain.models import (
        EarlyCountDrift,
        ErpJournalLine,
        LabelDecision,
        LocationKey,
    )

    db = Database(get_settings())
    campaign_id = str(uuid.uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO campaign (id, code, label, count_date, created_by, status) "
            "VALUES (%s, %s, '', current_date, 'test', 'COUNTING')",
            (campaign_id, f"INV-PAYLOAD-{campaign_id[:8]}"),
        )

    key = LocationKey(warehouse_id=SOL[0], location_id=SOL[1])
    journals = ErpJournalRepository(db)
    journal_id = journals.upsert_journal(
        campaign_id,
        journal_number="NPEM-521215",
        kind=JournalKind.INVE,
        description="Inventaire par étiquette",
        erp_posted=True,
    )
    journals.replace_lines(
        campaign_id,
        journal_id,
        [
            ErpJournalLine(
                id="",
                erp_journal_id=journal_id,
                campaign_id=campaign_id,
                warehouse_id=SOL[0],
                location_id=SOL[1],
                item_number="MASS-1",
                erp_line_number=1,
                qty_on_hand=Decimal(10),
                qty_counted=Decimal(12),
            )
        ],
    )
    JournalRepository(db).ensure_journals(campaign_id, [key])
    journals.set_scope(campaign_id, journal_id, [key], actor="test")

    LabelDecisionRepository(db).decide(
        LabelDecision(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            label_id="001609231",
            item_number="MASS-1",
            decision=LabelResolution.RECOUNT,
            sealed_warehouse_id=SOL[0],
            sealed_location_id=SOL[1],
            other_warehouse_id="ATP",
            other_location_id="QUAI EXP",
        )
    )

    EarlyCountDriftRepository(db).replace(
        campaign_id,
        [
            EarlyCountDrift(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                erp_journal_id=journal_id,
                warehouse_id=SOL[0],
                location_id=SOL[1],
                item_number="MASS-1",
                qty_erp_t0=Decimal(10),
                qty_physical_t0=Decimal(12),
                qty_erp_j=Decimal(9),
                drift_value=Decimal("-12.00"),
                is_material=True,
            )
        ],
    )
    db.close()
    return campaign_id


def _rows(client, campaign_id: str, path: str) -> list[dict]:
    """La réponse d'une route, refusée si elle est vide.

    Une liste vide traverse n'importe quelle déclaration : c'est précisément
    ainsi que la panne est passée. Ici, pas de ligne, pas de contrôle — donc
    échec, pas abstention.
    """
    response = client.get(f"/api/campaigns/{campaign_id}/early-counts/{path}")
    assert response.status_code == 200, response.text[:600]
    rows = response.json()
    assert rows, f"{path} n'a rien renvoyé : le semis n'a pas pris"
    return rows


class TestTheDeclarationMatchesWhatComesBack:
    def test_the_erp_journals(self, client, seeded):
        from inventory.api.responses import ErpJournalResponse

        row = _rows(client, seeded, "journals")[0]
        ErpJournalResponse.model_validate(row)

    def test_the_locations_to_rescan(self, client, seeded):
        from inventory.api.responses import RescanLocation

        row = _rows(client, seeded, "to-rescan")[0]
        RescanLocation.model_validate(row)

    def test_the_drifts(self, client, seeded):
        from inventory.api.responses import DriftResponse

        row = _rows(client, seeded, "drifts")[0]
        DriftResponse.model_validate(row)


class TestTheScreenFindsTheKeysItReads:
    """La validation dit que la forme est licite, pas que l'écran s'y retrouve.

    L'interface lit `journalNumber`, `lineCount`, `isSealed`, `driftQty`. Une
    réponse restée en `snake_case` validerait si les alias devenaient
    facultatifs, et l'écran afficherait des cases vides sans que rien n'échoue.
    """

    @pytest.mark.parametrize(
        "path,field",
        [
            ("journals", "journalNumber"),
            ("journals", "lineCount"),
            ("journals", "erpPosted"),
            ("journals", "scopeDeclared"),
            ("journals", "countedOn"),
            ("journals", "isSealed"),
            ("to-rescan", "warehouseId"),
            ("to-rescan", "labels"),
            ("drifts", "driftQty"),
            ("drifts", "qtyErpJ"),
            ("drifts", "blocksAnalysis"),
        ],
    )
    def test_this_key_is_there(self, client, seeded, path, field):
        assert field in _rows(client, seeded, path)[0]
