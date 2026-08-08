"""Reading a scanned counting sheet with a vision model.

The paper sheets are printed from the campaign itself, so the application
already knows which articles *should* be on each page. That turns a hard OCR
problem into a much easier and far safer one: the model is given the expected
article list and only has to read the handwritten quantity next to each line.

Consequences for reliability:

* an article the model "reads" that was not on the sheet is rejected outright —
  it is a hallucination, not a discovery;
* a blank quantity stays blank (``None``), never becomes ``0``: "not counted"
  and "counted zero" are different facts and only the second one closes a line;
* every extracted value carries a confidence, is stored as ``SCAN_AI`` source,
  and lands in an editable grid. Nothing is posted straight to a journal.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..domain.enums import CountSection, DataSource, legacy_section_alias
from ..domain.models import CountSheetLine, Item, normalise_key
from ..domain.quantities import to_decimal
from ..errors import ValidationError
from .client import LlmClient, get_llm_client

log = logging.getLogger(__name__)

__all__ = [
    "ExpectedLine",
    "ExtractionResult",
    "PageRouting",
    "SheetCandidate",
    "SheetExtractor",
    "render_pdf_pages",
]

#: Confidence below which a value is surfaced to the user as "to be checked".
LOW_CONFIDENCE = 0.75

_SYSTEM_PROMPT = """\
Tu es un opérateur de saisie expert en inventaire industriel. Tu transcris des \
feuilles de comptage papier remplies à la main, dans une usine de moteurs \
électriques.

Règles absolues :
1. Tu transcris UNIQUEMENT ce qui est écrit. Tu ne calcules rien, tu ne \
complètes rien, tu ne corriges rien.
2. Si la case « Comptage » d'une ligne est VIDE, tu renvoies null — jamais 0. \
Une case vide signifie « non compté », ce qui n'est pas la même chose que \
« compté à zéro ».
3. Tu ne renvoies que des références présentes dans la liste attendue fournie. \
Si tu lis une référence absente de cette liste, tu la places dans \
« unexpected » sans l'inventer ni la rapprocher d'une autre.
4. Si un chiffre est ambigu (rature, surcharge, chiffre coupé), tu renvoies ta \
meilleure lecture avec une confiance basse et tu décris le doute.
5. Les quantités sont des nombres. Les séparateurs de milliers (espace, point) \
sont ignorés ; la virgule est un séparateur décimal.

Tu réponds exclusivement en JSON valide, sans texte autour."""

_USER_TEMPLATE = """\
Feuille de comptage : zone « {zone} », comptage n°{pass_no}.

Liste des références attendues sur cette feuille (section entre crochets) :
{expected}

Transcris la feuille scannée et renvoie ce JSON :

{{
  "counter_name": "<nom du compteur lu sur la feuille, ou null>",
  "started_at": "<heure de début lue, format HH:MM, ou null>",
  "ended_at": "<heure de fin lue, format HH:MM, ou null>",
  "lines": [
    {{
      "item_number": "<référence, exactement telle qu'écrite dans la liste attendue>",
      "qty": <nombre ou null>,
      "confidence": <nombre entre 0 et 1>,
      "note": "<doute de lecture, ou chaîne vide>"
    }}
  ],
  "unexpected": [
    {{"text": "<ce que tu as lu>", "qty": <nombre ou null>, "note": "<contexte>"}}
  ]
}}

Renvoie une entrée dans "lines" pour CHAQUE référence attendue, même non comptée \
(qty = null)."""


_FREE_ENTRY_SYSTEM_PROMPT = """\
Tu es un opérateur de saisie expert en inventaire industriel. Tu transcris des \
feuilles de comptage papier remplies à la main, dans une usine de moteurs \
électriques.

Cette feuille est une feuille de SAISIE LIBRE : elle a été imprimée vide, et le \
compteur y a écrit lui-même la référence ET la quantité de chaque ligne.

Règles absolues :
1. Tu transcris UNIQUEMENT ce qui est écrit. Tu ne calcules rien, tu ne \
complètes rien, tu ne corriges rien, et tu n'inventes aucune référence.
2. Si la case « Comptage » d'une ligne est VIDE, tu renvoies null — jamais 0.
3. Tu recopies la référence caractère par caractère, telle qu'elle est écrite, \
même si elle te paraît incomplète ou fautive. La vérification est faite ensuite \
contre le référentiel : une référence que tu « corriges » verse un comptage sur \
le mauvais article.
4. Si un caractère est ambigu (rature, surcharge, chiffre coupé), tu renvoies ta \
meilleure lecture avec une confiance basse et tu décris le doute.
5. Tu ignores les lignes entièrement vides.
6. Les quantités sont des nombres. Les séparateurs de milliers (espace, point) \
sont ignorés ; la virgule est un séparateur décimal.

Tu réponds exclusivement en JSON valide, sans texte autour."""

_FREE_ENTRY_TEMPLATE = """\
Feuille de saisie libre : zone « {zone} », comptage n°{pass_no}.

Aucune liste n'était pré-imprimée : lis chaque ligne écrite à la main.

Renvoie ce JSON :

{{
  "counter_name": "<nom du compteur lu sur la feuille, ou null>",
  "started_at": "<heure de début lue, format HH:MM, ou null>",
  "ended_at": "<heure de fin lue, format HH:MM, ou null>",
  "lines": [
    {{
      "item_number": "<référence exactement telle qu'écrite>",
      "qty": <nombre ou null>,
      "section": "<BDL, WIP ou WIP_OK selon le tableau où figure la ligne>",
      "unit": "<unité lue, ou null>",
      "confidence": <nombre entre 0 et 1>,
      "note": "<doute de lecture, ou chaîne vide>"
    }}
  ]
}}

Renvoie les lignes dans l'ordre où elles apparaissent sur la feuille."""


_ROUTING_SYSTEM_PROMPT = """\
Tu tries des pages scannées de feuilles de comptage d'inventaire. Chaque page \
imprimée porte, en bas à gauche, une ligne d'identité de la forme :

    <CODE CAMPAGNE> · zone <NOM DE ZONE> · comptage n°<1 ou 2> · feuille <identifiant>

Ta seule tâche est de lire cette ligne sur chaque page et de rendre \
l'identifiant de feuille. Tu ne transcris aucune quantité.

Règles absolues :
1. Tu ne rends qu'un identifiant présent dans la liste fournie. Si le pied de \
page est illisible, coupé ou absent, tu rends null : une page mal attribuée \
verse un comptage sur la mauvaise zone, ce qui est pire qu'une page non traitée.
2. Si le pied de page est illisible mais que le titre de la page nomme sans \
ambiguïté une seule zone de la liste et un seul numéro de comptage, tu peux \
t'en servir, avec une confiance basse.

Tu réponds exclusivement en JSON valide, sans texte autour."""

_ROUTING_TEMPLATE = """\
Feuilles attendues dans ce lot :
{candidates}

Pour chacune des {count} pages fournies, dans l'ordre, renvoie ce JSON :

{{
  "pages": [
    {{
      "page": <numéro de page, à partir de 1>,
      "sheet": "<identifiant de feuille de la liste, ou null>",
      "confidence": <nombre entre 0 et 1>,
      "note": "<ce que tu as lu au pied de page, ou la raison du doute>"
    }}
  ]
}}"""


@dataclass(frozen=True, slots=True)
class SheetCandidate:
    """A sheet a scanned page might belong to, as printed in its footer."""

    sheet_id: str
    zone_code: str
    pass_no: int

    @property
    def token(self) -> str:
        """The identifier the printed footer actually carries."""
        return self.sheet_id[:8]


@dataclass(slots=True)
class PageRouting:
    """Which sheet each page of a multi-sheet scan belongs to."""

    #: ``{sheet_id: [0-based page indexes]}``, in reading order.
    pages_by_sheet: dict[str, list[int]] = field(default_factory=dict)
    #: Pages whose footer could not be read — reported, never guessed.
    unrouted: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0


@dataclass(frozen=True, slots=True)
class ExpectedLine:
    """One article pre-printed on the sheet."""

    item_number: str
    name: str
    section: CountSection
    unit: str = "PCE"


@dataclass(slots=True)
class ExtractionResult:
    """Outcome of reading one scanned sheet."""

    lines: list[CountSheetLine] = field(default_factory=list)
    #: Values the model read with low confidence — the user must confirm them.
    low_confidence_items: list[str] = field(default_factory=list)
    #: Text the model read that matches no expected article.
    unexpected: list[dict[str, Any]] = field(default_factory=list)
    #: Expected articles the model returned no reading for.
    missing_items: list[str] = field(default_factory=list)
    counter_name: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    mean_confidence: float | None = None
    pages: int = 0
    tokens_used: int = 0

    def as_report(self) -> dict[str, Any]:
        return {
            "linesExtracted": len(self.lines),
            "counted": sum(1 for l in self.lines if l.is_counted),
            "lowConfidence": self.low_confidence_items,
            "unexpected": self.unexpected,
            "missing": self.missing_items,
            "counterName": self.counter_name,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "meanConfidence": self.mean_confidence,
            # Deliberately *not* "pages": a multi-sheet scan merges this report
            # with the routing, which owns "pages" as the list of page numbers
            # that fed the sheet. Two different shapes under one name is how the
            # screen ended up calling ``join`` on an integer.
            "pagesRead": self.pages,
            "tokensUsed": self.tokens_used,
        }


class SheetExtractor:
    """Turns a scanned counting sheet into editable :class:`CountSheetLine` rows."""

    def __init__(self, client: LlmClient | None = None) -> None:
        self._client = client or get_llm_client()

    def extract(
        self,
        *,
        campaign_id: str,
        sheet_id: str,
        zone_label: str,
        pass_no: int,
        expected: Sequence[ExpectedLine],
        images: Sequence[bytes],
        image_mime: str = "image/png",
        id_factory,
    ) -> ExtractionResult:
        """Read *images* against the *expected* article list.

        :param images: page renders of the scan, in reading order.
        :raises ValidationError: when there is nothing to read against — the
            model must never be asked to invent an article list.
        """
        if not images:
            raise ValidationError("Aucune page à analyser.")
        if not expected:
            raise ValidationError(
                "La feuille ne contient aucune référence attendue. Créez d'abord "
                "les lignes de la feuille (ou dupliquez-les d'une campagne "
                "précédente) avant d'importer un scan."
            )

        expected_by_number = {e.item_number: e for e in expected}
        listing = "\n".join(
            f"- {e.item_number} [{_section_label(e.section)}] {e.name}"[:160]
            for e in expected
        )
        prompt = _USER_TEMPLATE.format(
            zone=zone_label, pass_no=pass_no, expected=listing
        )

        payload, response = self._client.complete_json(
            system=_SYSTEM_PROMPT,
            user=prompt,
            images=images,
            image_mime=image_mime,
            max_tokens=8192,
        )

        result = ExtractionResult(
            counter_name=str(payload.get("counter_name") or "").strip(),
            started_at=_clean_time(payload.get("started_at")),
            ended_at=_clean_time(payload.get("ended_at")),
            pages=len(images),
            tokens_used=response.total_tokens,
        )

        confidences: list[float] = []
        seen: set[str] = set()

        for order, raw in enumerate(payload.get("lines") or []):
            if not isinstance(raw, dict):
                continue
            number = normalise_key(str(raw.get("item_number") or ""))
            expected_line = expected_by_number.get(number)
            if expected_line is None:
                # A reading that matches nothing on the printed sheet is a
                # hallucination: surface it, never accept it as a count.
                result.unexpected.append({
                    "text": str(raw.get("item_number") or ""),
                    "qty": raw.get("qty"),
                    "note": "Référence absente de la liste attendue.",
                })
                continue
            if number in seen:
                continue
            seen.add(number)

            qty = _clean_qty(raw.get("qty"))
            confidence = _clean_confidence(raw.get("confidence"))
            if qty is not None:
                confidences.append(confidence)
                if confidence < LOW_CONFIDENCE:
                    result.low_confidence_items.append(number)

            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=number,
                    section=expected_line.section,
                    qty_imported=qty,
                    qty_manual=None,
                    unit=expected_line.unit,
                    source=DataSource.SCAN_AI,
                    confidence=confidence,
                    comment=str(raw.get("note") or "").strip(),
                    display_order=order,
                )
            )

        for raw in payload.get("unexpected") or []:
            if isinstance(raw, dict):
                result.unexpected.append({
                    "text": str(raw.get("text") or ""),
                    "qty": raw.get("qty"),
                    "note": str(raw.get("note") or ""),
                })

        result.missing_items = sorted(set(expected_by_number) - seen)
        # A missing expected line still gets a row, blank, so the encoder sees
        # it and can type the value instead of discovering the gap at posting.
        for order, number in enumerate(result.missing_items, start=len(result.lines)):
            expected_line = expected_by_number[number]
            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=number,
                    section=expected_line.section,
                    unit=expected_line.unit,
                    source=DataSource.SCAN_AI,
                    confidence=0.0,
                    comment="Non lue sur le scan — à saisir manuellement.",
                    display_order=order,
                    qty_imported=None,
                    qty_manual=None,
                )
            )

        if confidences:
            result.mean_confidence = round(sum(confidences) / len(confidences), 4)
        return result

    def extract_free_entry(
        self,
        *,
        campaign_id: str,
        sheet_id: str,
        zone_label: str,
        pass_no: int,
        known_items: Mapping[str, Any],
        images: Sequence[bytes],
        image_mime: str = "image/png",
        id_factory,
    ) -> ExtractionResult:
        """Read a sheet that was printed empty and filled in by hand.

        There is no expected list to read against — that is what "free entry"
        means — so the guard against invented articles moves one step later: the
        model transcribes whatever reference it sees, and *this* method decides
        whether it exists. A reference the campaign's referential does not know
        is reported, never created. That is the same rule the grid import obeys,
        for the same reason: an article created by a misreading becomes a
        variance line nobody can explain.

        :param known_items: the campaign's article referential, keyed by
            normalised item number.
        """
        if not images:
            raise ValidationError("Aucune page à analyser.")

        payload, response = self._client.complete_json(
            system=_FREE_ENTRY_SYSTEM_PROMPT,
            user=_FREE_ENTRY_TEMPLATE.format(zone=zone_label, pass_no=pass_no),
            images=images,
            image_mime=image_mime,
            max_tokens=8192,
        )

        result = ExtractionResult(
            counter_name=str(payload.get("counter_name") or "").strip(),
            started_at=_clean_time(payload.get("started_at")),
            ended_at=_clean_time(payload.get("ended_at")),
            pages=len(images),
            tokens_used=response.total_tokens,
        )

        confidences: list[float] = []
        seen: set[str] = set()

        for order, raw in enumerate(payload.get("lines") or []):
            if not isinstance(raw, dict):
                continue
            read = str(raw.get("item_number") or "").strip()
            if not read:
                continue
            number = normalise_key(read)
            item = known_items.get(number)
            if item is None:
                result.unexpected.append({
                    "text": read,
                    "qty": raw.get("qty"),
                    "note": "Référence absente du référentiel articles.",
                })
                continue
            if number in seen:
                continue
            seen.add(number)

            qty = _clean_qty(raw.get("qty"))
            confidence = _clean_confidence(raw.get("confidence"))
            if qty is not None:
                confidences.append(confidence)
                if confidence < LOW_CONFIDENCE:
                    result.low_confidence_items.append(number)

            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=number,
                    section=(
                        legacy_section_alias(str(raw.get("section") or ""))
                        or CountSection.LINE_SIDE
                    ),
                    qty_imported=qty,
                    qty_manual=None,
                    unit=str(raw.get("unit") or "") or getattr(item, "unit", "PCE"),
                    source=DataSource.SCAN_AI,
                    confidence=confidence,
                    comment=str(raw.get("note") or "").strip(),
                    display_order=order,
                )
            )

        if confidences:
            result.mean_confidence = round(sum(confidences) / len(confidences), 4)
        return result

    def route_pages(
        self,
        *,
        images: Sequence[bytes],
        candidates: Sequence[SheetCandidate],
        image_mime: str = "image/png",
    ) -> PageRouting:
        """Work out which sheet each page of a multi-sheet scan belongs to.

        The application printed these pages itself, so every one carries its
        sheet's identifier in the footer. Reading that is a far smaller and far
        safer job than guessing from the content — and it is what makes it
        possible to drop a whole stack on the scanner instead of feeding sheets
        one at a time.

        A page whose footer cannot be read is **reported, never guessed**:
        attributing a page to the wrong zone posts a count against stock that was
        never there, which is worse than leaving the page for a human.
        """
        if not images:
            raise ValidationError("Aucune page à analyser.")
        if not candidates:
            raise ValidationError(
                "Aucune feuille de comptage ne peut recevoir ce scan : créez "
                "d'abord les zones et leurs feuilles."
            )

        by_token = {c.token.upper(): c for c in candidates}
        listing = "\n".join(
            f"- {c.token} → zone « {c.zone_code} », comptage n°{c.pass_no}"
            for c in candidates
        )
        payload, response = self._client.complete_json(
            system=_ROUTING_SYSTEM_PROMPT,
            user=_ROUTING_TEMPLATE.format(candidates=listing, count=len(images)),
            images=images,
            image_mime=image_mime,
            max_tokens=4096,
        )

        routing = PageRouting(tokens_used=response.total_tokens)
        seen_pages: set[int] = set()
        for raw in payload.get("pages") or []:
            if not isinstance(raw, dict):
                continue
            try:
                page = int(raw.get("page") or 0) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= page < len(images) or page in seen_pages:
                continue
            seen_pages.add(page)

            token = str(raw.get("sheet") or "").strip().upper()
            candidate = by_token.get(token)
            if candidate is None:
                routing.unrouted.append({
                    "page": page + 1,
                    "read": str(raw.get("sheet") or ""),
                    "note": str(raw.get("note") or "Pied de page illisible."),
                })
                continue
            routing.pages_by_sheet.setdefault(candidate.sheet_id, []).append(page)

        for page in range(len(images)):
            if page not in seen_pages:
                routing.unrouted.append({
                    "page": page + 1,
                    "read": "",
                    "note": "Le modèle n'a rien renvoyé pour cette page.",
                })
        routing.unrouted.sort(key=lambda u: u["page"])
        return routing

    def expected_from_items(
        self, lines: Sequence[CountSheetLine], items: dict[str, Item]
    ) -> list[ExpectedLine]:
        """Build the expected list from a sheet's existing (pre-printed) lines."""
        return [
            ExpectedLine(
                item_number=line.item_number,
                name=(items[line.item_number].name if line.item_number in items else ""),
                section=line.section,
                unit=line.unit,
            )
            for line in lines
        ]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_SECTION_LABELS = {
    CountSection.LINE_SIDE: "bord de ligne",
    CountSection.WIP: "WIP non déclaré",
    CountSection.WIP_OK: "WIP assemblé",
}


def _section_label(section: CountSection) -> str:
    return _SECTION_LABELS.get(section, str(section))


def _clean_qty(value: Any) -> Decimal | None:
    """A blank stays blank. Only a real number becomes a quantity."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "-"):
        return None
    try:
        return to_decimal(value)
    except ValueError:
        return None


def _clean_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(max(confidence, 0.0), 1.0)


def _clean_time(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def render_pdf_pages(
    payload: bytes, *, max_pages: int = 12, dpi: int = 150
) -> list[bytes]:
    """Rasterise a scanned PDF into one PNG per page for the vision model.

    The chat-completions API carries images and only images: a page handed over
    as ``application/pdf`` in an ``image_url`` block is refused, which is why
    scans uploaded as PDF failed while the same page uploaded as a photo went
    through. Splitting the document into single-page PDFs — the previous
    approach — changed nothing, because the payload was still a PDF.

    Rendering is done by ``pypdfium2``: self-contained manylinux wheels with the
    PDFium engine bundled, so it works in a container with no system packages
    and no root, unlike Poppler-based tooling.

    :param max_pages: guard against a 200-page scan blowing the token budget.
    :param dpi: 150 keeps a handwritten quantity legible while staying well
        under the per-request payload budget; 300 doubles the bytes for no
        measurable gain on the counting sheets this reads.
    """
    import pypdfium2

    document = pypdfium2.PdfDocument(payload)
    try:
        total = len(document)
        if total > max_pages:
            log.warning("Scan truncated at %d pages (document has %d)", max_pages, total)
        pages: list[bytes] = []
        for index in range(min(total, max_pages)):
            image = document[index].render(scale=dpi / 72).to_pil()
            buffer = io.BytesIO()
            # PNG rather than JPEG: a counting sheet is line art and text, where
            # JPEG artefacts land exactly on the strokes the model must read.
            image.save(buffer, format="PNG", optimize=True)
            pages.append(buffer.getvalue())
        return pages
    finally:
        document.close()
