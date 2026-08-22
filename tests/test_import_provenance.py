"""Un chargement, un identifiant de lot. Le même partout.

Deux imports gravent un identifiant de lot **dans les lignes qu'ils écrivent** :
le stock ERP et l'écart backflush. Le stock tirait son identifiant, en marquait
les lignes de ``book_stock``… puis appelait ``imports.create()``, qui en tirait
un autre pour la ligne d'``import_batch``. Deux identifiants pour un seul
chargement, et aucune jointure possible entre les deux : « d'où vient cette
quantité » n'avait pas de réponse, alors que c'est le seul travail de cette
table. Le backflush faisait pire — il gravait un identifiant sans jamais écrire
la ligne d'historique correspondante : le chargement n'apparaissait nulle part,
et la pièce archivée n'était rattachée à rien.

Ces contrôles n'ouvrent aucune base : ils observent, au niveau du service, quel
identifiant part vers les lignes et quel identifiant part vers l'historique, et
exigent que ce soit le même.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.db.repositories import ImportBatchRepository
from inventory.domain.enums import CampaignStatus
from inventory.domain.models import BackflushLine, BookStockLine, Campaign
from inventory.ingest.parser import ParseResult

CAMPAIGN = Campaign(
    id="camp-1", code="INV-2026", label="Inventaire",
    count_date="2026-09-01", status=CampaignStatus.COUNTING,
    created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
)


class Recorder:
    """Retient l'identifiant confié aux lignes et celui confié à l'historique."""

    def __init__(self) -> None:
        self.stamped: list[str] = []
        self.recorded: list[str | None] = []

    def stamp(self, *a, batch_id: str, **k) -> int:
        self.stamped.append(batch_id)
        return 1

    def create(self, *, batch_id: str | None = None, **kw) -> str:
        self.recorded.append(batch_id)
        return batch_id or "id-tiré-par-le-dépôt"


# --------------------------------------------------------------------------- #
# Le dépôt accepte l'identifiant imposé
# --------------------------------------------------------------------------- #

class TestLeDepotAccepteUnIdentifiantImpose:
    """Sans cela, l'appelant ne peut pas imposer le sien — c'était le défaut."""

    def _spy(self):
        repo = ImportBatchRepository.__new__(ImportBatchRepository)
        seen: list[tuple[str, tuple[Any, ...]]] = []
        repo._execute = lambda q, p=(), *, conn=None: (  # type: ignore[method-assign]
            seen.append((q, tuple(p))) or 1
        )
        return repo, seen

    def test_lidentifiant_transmis_est_celui_ecrit(self):
        repo, seen = self._spy()
        returned = repo.create(
            campaign_id="camp-1", target="book_stock", filename="stock.csv",
            content_hash="abc", storage_path=None, rows_received=1,
            rows_accepted=1, rows_rejected=0, report={}, imported_by="a",
            batch_id="lot-imposé",
        )
        assert returned == "lot-imposé"
        assert seen[0][1][0] == "lot-imposé"

    def test_sans_identifiant_le_depot_en_tire_un(self):
        """Les imports qui ne marquent pas leurs lignes gardent l'ancien geste."""
        repo, seen = self._spy()
        returned = repo.create(
            campaign_id="camp-1", target="items", filename="articles.csv",
            content_hash="abc", storage_path=None, rows_received=1,
            rows_accepted=1, rows_rejected=0, report={}, imported_by="a",
        )
        assert returned
        assert seen[0][1][0] == returned

    def test_deux_appels_sans_identifiant_ne_se_confondent_pas(self):
        repo, _ = self._spy()
        common: dict[str, Any] = {
            "campaign_id": "camp-1", "target": "items", "filename": "f.csv",
            "content_hash": "abc", "storage_path": None, "rows_received": 1,
            "rows_accepted": 1, "rows_rejected": 0, "report": {},
            "imported_by": "a",
        }
        assert repo.create(**common) != repo.create(**common)


# --------------------------------------------------------------------------- #
# Le stock ERP
# --------------------------------------------------------------------------- #

def book_stock_service(monkeypatch):
    from inventory.services import import_service as module

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    recorder = Recorder()

    ctx.book_stock = SimpleNamespace(replace=recorder.stamp)
    ctx.imports = SimpleNamespace(create=recorder.create)
    ctx.referentials = SimpleNamespace(
        items_by_number=lambda cid: {},
        locations_by_key=lambda cid: {},
        upsert_warehouses=lambda w, *, actor, conn=None: len(list(w)),
        upsert_locations=lambda l, *, actor, conn=None: len(list(l)),
    )
    ctx.journals = SimpleNamespace(
        ensure_journals=lambda cid, keys, *, kinds, actor, conn=None: len(keys),
    )
    ctx.record = lambda **kw: "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=0, book_stock_frozen=False
    )
    ctx.settings = SimpleNamespace(generic_key=None)
    with_access(ctx)

    service = module.ImportService(ctx)
    # L'archivage et la lecture du fichier ne sont pas le sujet ici : ce test
    # porte sur l'identifiant, et le faire passer par un vrai CSV n'y ajouterait
    # que du bruit.
    service._archive = lambda *a, **k: "/Volumes/x/stock.csv"  # type: ignore[method-assign]
    return service, recorder, module


class TestLeStockErp:
    def test_les_lignes_et_lhistorique_portent_le_meme_lot(self, monkeypatch):
        service, recorder, module = book_stock_service(monkeypatch)
        rows = [{
            "item_number": "P-1", "warehouse_id": "B06", "location_id": "VRAC",
            "qty": "10", "unit": "PCE",
        }]
        monkeypatch.setattr(
            service, "parse",
            lambda target, **kw: (
                None,
                ParseResult(contract_key=target, rows=rows, rows_received=1),
            ),
        )
        monkeypatch.setattr(
            module, "map_book_stock",
            lambda cid, rows, *, items: (
                [BookStockLine(
                    campaign_id=cid, item_number="P-1",
                    warehouse_id="B06", location_id="VRAC", qty=Decimal("10"),
                )],
                [],
            ),
        )
        outcome = service.import_book_stock(CAMPAIGN, payload=b"x", filename="s.csv")

        assert recorder.stamped, "aucune ligne de stock marquée"
        assert recorder.recorded, "aucune ligne d'historique écrite"
        assert recorder.stamped == recorder.recorded, (
            "les lignes de stock et l'historique portent deux lots différents"
        )
        assert outcome.batch_id == recorder.stamped[0], (
            "le rapport renvoie un lot que l'historique ne connaît pas"
        )


# --------------------------------------------------------------------------- #
# L'écart backflush
# --------------------------------------------------------------------------- #

def backflush_service(monkeypatch):
    from inventory.services import import_service as module

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    recorder = Recorder()

    ctx.backflush = SimpleNamespace(replace=recorder.stamp)
    ctx.imports = SimpleNamespace(create=recorder.create)
    ctx.referentials = SimpleNamespace(items_in_scope=lambda cid: {})
    ctx.record = lambda **kw: "evt"
    ctx.forget_progress = lambda cid=None: None
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
    )
    with_access(ctx)

    service = module.ImportService(ctx)
    service._archive = lambda *a, **k: "/Volumes/x/backflush.csv"  # type: ignore[method-assign]
    return service, recorder, module


class TestLEcartBackflush:
    def test_le_chargement_apparait_dans_lhistorique(self, monkeypatch):
        """Il n'y apparaissait pas du tout : aucune ligne n'était écrite."""
        service, recorder, module = backflush_service(monkeypatch)
        monkeypatch.setattr(
            service, "parse",
            lambda target, **kw: (
                None,
                ParseResult(contract_key=target, rows=[{"item_number": "P-1"}], rows_received=1),
            ),
        )
        monkeypatch.setattr(
            module, "map_backflush",
            lambda cid, rows, *, period_start, period_end, items: (
                [BackflushLine(
                    campaign_id=cid, item_number="P-1",
                    period_start=period_start, period_end=period_end,
                )],
                [],
            ),
        )
        outcome = service.import_backflush(
            CAMPAIGN,
            period_start=dt.date(2026, 8, 3),
            period_end=dt.date(2026, 8, 31),
            payload=b"x",
            filename="bf.csv",
        )
        assert recorder.recorded, "le chargement n'apparaît nulle part"
        assert recorder.stamped == recorder.recorded
        assert outcome.batch_id == recorder.stamped[0]


# --------------------------------------------------------------------------- #
# Garde-fou : la doublure distingue bien deux identifiants
# --------------------------------------------------------------------------- #

def test_la_doublure_verrait_la_difference():
    """Sans cela, les égalités ci-dessus passeraient sur deux listes vides."""
    recorder = Recorder()
    recorder.stamp(batch_id="lot-A")
    recorder.create(batch_id="lot-B")
    assert recorder.stamped != recorder.recorded

    vide = Recorder()
    assert vide.stamped == vide.recorded == []


def test_un_appel_sans_identifiant_est_visible_comme_tel():
    """C'était exactement l'ancien comportement : ``create()`` sans ``batch_id``."""
    recorder = Recorder()
    recorder.stamp(batch_id="lot-A")
    recorder.create()
    assert recorder.recorded == [None]
    assert recorder.stamped != recorder.recorded


@pytest.mark.parametrize("service_builder", ["book_stock", "backflush"])
def test_aucun_import_marqueur_ne_laisse_le_depot_tirer_son_propre_lot(
    monkeypatch, service_builder
):
    """La régression exacte : un ``create()`` sans ``batch_id`` la ramènerait."""
    if service_builder == "book_stock":
        service, recorder, module = book_stock_service(monkeypatch)
        monkeypatch.setattr(
            service, "parse",
            lambda target, **kw: (
                None,
                ParseResult(contract_key=target, rows=[{"item_number": "P-1"}], rows_received=1),
            ),
        )
        monkeypatch.setattr(
            module, "map_book_stock",
            lambda cid, rows, *, items: (
                [BookStockLine(
                    campaign_id=cid, item_number="P-1",
                    warehouse_id="B06", location_id="VRAC", qty=Decimal("10"),
                )],
                [],
            ),
        )
        service.import_book_stock(CAMPAIGN, payload=b"x", filename="s.csv")
    else:
        service, recorder, module = backflush_service(monkeypatch)
        monkeypatch.setattr(
            service, "parse",
            lambda target, **kw: (
                None,
                ParseResult(contract_key=target, rows=[{"item_number": "P-1"}], rows_received=1),
            ),
        )
        monkeypatch.setattr(
            module, "map_backflush",
            lambda cid, rows, *, period_start, period_end, items: (
                [BackflushLine(
                    campaign_id=cid, item_number="P-1",
                    period_start=period_start, period_end=period_end,
                )],
                [],
            ),
        )
        service.import_backflush(
            CAMPAIGN,
            period_start=dt.date(2026, 8, 3),
            period_end=dt.date(2026, 8, 31),
            payload=b"x",
            filename="bf.csv",
        )
    assert None not in recorder.recorded, (
        "l'historique laisse le dépôt tirer son propre identifiant"
    )
