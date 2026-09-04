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
import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Any, TypeVar

from ..domain.enums import (
    CountLineKind,
    CountSection,
    DataSource,
    legacy_section_alias,
)
from ..domain.formula import FormulaError, evaluate, looks_like_formula
from ..domain.models import CountSheetLine, Item, normalise_key
from ..domain.quantities import to_decimal
from ..errors import ValidationError
from .client import LlmClient, get_scan_client

log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

__all__ = [
    "ExpectedLine",
    "ExtractionResult",
    "PageRouting",
    "SheetCandidate",
    "SheetExtractor",
    "footer_strips",
    "page_count",
    "render_pdf_pages",
]

#: Confidence below which a value is surfaced to the user as "to be checked".
LOW_CONFIDENCE = 0.75

#: Combien de lignes une feuille de saisie libre porte au plus, par page. Sert
#: uniquement à dimensionner le budget de sortie : la feuille imprimée en offre
#: une trentaine, quarante laisse la marge d'une écriture serrée.
_FREE_ENTRY_LINES_PER_PAGE = 40

_SYSTEM_PROMPT = """\
Tu es un opérateur de saisie expert en inventaire industriel. Tu transcris des \
feuilles de comptage papier remplies à la main, dans une usine de moteurs \
électriques.

Règles absolues :
1. Tu transcris UNIQUEMENT ce qui est écrit. Tu ne calcules rien, tu ne \
complètes rien, tu ne corriges rien.
2. Si la case « Comptage » d'une ligne est VIDE, tu renvoies null — jamais 0. \
Tu transcris ce qui est écrit : renvoyer 0 ferait passer « je n'ai rien lu » \
pour « le compteur a écrit zéro », et l'encodeur ne saurait plus laquelle des \
deux vérifier sur le papier.
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
      "section": "<la section du tableau où figure cette ligne : {sections}>",
      "qty": <nombre, ou l'opération écrite entre guillemets si la case en \
contient une (ex. "3*48+7"), ou null>,
      "confidence": <nombre entre 0 et 1>,
      "note": "<doute de lecture, ou chaîne vide>"
    }}
  ],
  "unexpected": [
    {{"text": "<ce que tu as lu>", "qty": <nombre ou null>, "note": "<contexte>"}}
  ]
}}

Renvoie une entrée dans "lines" pour CHAQUE ligne de la liste attendue, même non \
comptée (qty = null). Une même référence peut figurer sur DEUX lignes, dans deux \
sections différentes : ce sont deux comptages distincts, et « section » est ce \
qui les sépare."""


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
      "qty": <nombre, ou l'opération écrite entre guillemets si la case en \
contient une (ex. "3*48+7"), ou null>,
      "section": "<BDL, WIP ou WIP_OK selon le tableau où figure la ligne>",
      "unit": "<unité lue, ou null>",
      "confidence": <nombre entre 0 et 1>,
      "note": "<doute de lecture, ou chaîne vide>"
    }}
  ]
}}

Renvoie les lignes dans l'ordre où elles apparaissent sur la feuille."""


_ROUTING_SYSTEM_PROMPT = """\
Tu relèves l'identité de pages scannées de feuilles de comptage d'inventaire.

Chaque image qu'on te donne est la BANDE BASSE d'une page, découpée autour de sa \
ligne d'identité, de la forme :

    <CODE CAMPAGNE> · zone <NOM DE ZONE> · comptage n°<1 ou 2> · feuille <identifiant>

Ta seule tâche est de **recopier** ce que tu lis. Tu ne transcris aucune \
quantité — il n'y en a pas sur ces bandes.

Règles absolues :
1. Tu recopies chaque champ **exactement tel qu'il est écrit**, caractère par \
caractère. Tu ne le complètes pas, tu ne le corriges pas, tu ne le rapproches \
d'aucune liste.
2. Tu ne vérifies **rien**. Savoir si ce que tu as lu correspond à une feuille \
connue n'est pas ton travail : c'est le programme qui s'en charge, et il le fait \
mieux que toi. Une bande parfaitement lisible dont tu croirais l'identifiant \
inconnu se recopie quand même.
3. Un champ que tu ne parviens **pas** à lire vaut null. Un champ lisible ne vaut \
jamais null, même si le reste de la bande est abîmé : chaque champ est relevé \
pour lui-même, et un seul suffit souvent.
4. Tu ne devines pas un caractère douteux : tu recopies ta meilleure lecture. \
Un champ franchement illisible vaut null — c'est plus utile qu'une invention, \
car les trois champs se rattrapent l'un l'autre.

Tu réponds exclusivement en JSON valide, sans texte autour."""

_ROUTING_TEMPLATE = """\
Pour information seulement — les feuilles attendues dans ce lot. Cette liste \
t'aide à lever un doute de lecture ; elle ne te demande **aucune** vérification.
{candidates}

Pour chacune des {count} bandes fournies, dans l'ordre, renvoie **un seul objet \
JSON compact**, sans indentation, sans retour à la ligne et sans commentaire — \
exactement trois champs par bande :

{{"pages":[{{"page":<numéro de bande dans CE lot, à partir de 1>,\
"sheet":"<l'identifiant lu après « feuille », ou null>",\
"zone":"<le nom de zone lu après « zone », ou null>",\
"pass":<le numéro lu après « comptage n° » : 1 ou 2, ou null>}}]}}

N'ajoute aucun autre champ : ni commentaire, ni recopie de la ligne entière, ni \
indice de confiance. Ce que tu écrirais en plus mange le budget de réponse du \
lot, et une réponse coupée fait perdre le routage de toutes ses pages."""


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
    """One article pre-printed on the sheet, **and where it sits on it**.

    La place et l'intertitre voyagent avec la ligne parce que la lecture les
    perdait : le modèle rend les quantités dans l'ordre où il les a vues, ce qui
    n'est pas toujours l'ordre du papier, et la feuille réécrite sortait
    mélangée — les intertitres restant, eux, à leur rang d'origine.
    """

    item_number: str
    name: str
    section: CountSection
    unit: str = "PCE"
    #: Le rang de la ligne dans le document. Rendu tel quel, jamais recalculé.
    display_order: int = 0
    #: L'intertitre sous lequel elle se trouve. Recopié pour la même raison :
    #: la clé d'unicité le porte, et une feuille relue sans lui verrait ses
    #: lignes se dédoublonner entre deux emplacements.
    subsection: str = ""


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
            "counted": sum(1 for l in self.lines if l.has_entry),
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
        self._client = client or get_scan_client()

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
        allow_formulas: bool = False,
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

        # **La clé est le couple (référence, section), pas la référence.** Un
        # même article figure légitimement deux fois sur une feuille — en bord
        # de ligne *et* dans un en-cours — et ce sont deux comptages distincts,
        # posés sur deux tableaux différents du papier. Indexé sur la référence
        # seule, le dictionnaire n'en gardait qu'une : la seconde ligne était
        # perdue à l'écriture, et la quantité relevée sur l'une atterrissait sur
        # la section de l'autre. Rien ne le signalait.
        expected_by_key = {(e.item_number, e.section): e for e in expected}
        sections_by_number: dict[str, list[CountSection]] = {}
        for line in expected:
            known = sections_by_number.setdefault(line.item_number, [])
            if line.section not in known:
                known.append(line.section)

        listing = "\n".join(
            f"- {e.item_number} [{_section_label(e.section)}] {e.name}"[:160]
            for e in expected
        )
        prompt = _USER_TEMPLATE.format(
            zone=zone_label,
            pass_no=pass_no,
            expected=listing,
            sections=", ".join(str(s) for s in CountSection),
        )

        payload, response = self._client.complete_json(
            system=_SYSTEM_PROMPT,
            user=prompt,
            images=images,
            image_mime=image_mime,
            max_tokens=extraction_tokens(len(expected)),
        )

        result = ExtractionResult(
            counter_name=str(payload.get("counter_name") or "").strip(),
            started_at=_clean_time(payload.get("started_at")),
            ended_at=_clean_time(payload.get("ended_at")),
            pages=len(images),
            tokens_used=response.total_tokens,
        )

        confidences: list[float] = []
        seen: set[tuple[str, CountSection]] = set()

        for raw in payload.get("lines") or []:
            if not isinstance(raw, dict):
                continue
            number = normalise_key(str(raw.get("item_number") or ""))
            sections = sections_by_number.get(number)
            if not sections:
                # A reading that matches nothing on the printed sheet is a
                # hallucination: surface it, never accept it as a count.
                result.unexpected.append({
                    "text": str(raw.get("item_number") or ""),
                    "qty": raw.get("qty"),
                    "note": "Référence absente de la liste attendue.",
                })
                continue

            # La section lue ne sert qu'à départager. Une référence qui ne figure
            # qu'une fois sur la feuille n'a rien à départager : exiger d'elle
            # une section correcte ajouterait un mode d'échec au cas courant,
            # pour rien.
            read = legacy_section_alias(str(raw.get("section") or ""))
            if read in sections:
                section = read
            elif len(sections) == 1:
                section = sections[0]
            else:
                # Ambiguë, et sans section exploitable : on ne devine pas. Poser
                # un comptage d'en-cours sur la ligne de bord de ligne fausse
                # deux quantités d'un coup, et rien en aval ne peut le rattraper.
                result.unexpected.append({
                    "text": str(raw.get("item_number") or ""),
                    "qty": raw.get("qty"),
                    "note": (
                        f"{number} figure deux fois sur cette feuille "
                        f"({' et '.join(_section_label(s) for s in sections)}) "
                        "et la section lue est inexploitable. Saisissez la "
                        "quantité à la main sur la bonne ligne."
                    ),
                })
                continue

            key = (number, section)
            if key in seen:
                continue
            seen.add(key)
            expected_line = expected_by_key[key]
            label = _line_label(number, section, ambiguous=len(sections) > 1)

            qty, formula = _clean_qty(
                raw.get("qty"), allow_formulas=allow_formulas
            )
            confidence = _clean_confidence(raw.get("confidence"))
            if qty is not None:
                confidences.append(confidence)
                if confidence < LOW_CONFIDENCE:
                    result.low_confidence_items.append(label)

            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=number,
                    section=section,
                    qty_imported=qty,
                    qty_manual=None,
                    unit=expected_line.unit,
                    source=DataSource.SCAN_AI,
                    confidence=confidence,
                    qty_formula=formula,
                    comment=str(raw.get("note") or "").strip(),
                    # La place de la ligne **sur le papier**, et non le rang de
                    # la lecture. Le modèle rend ce qu'il voit dans l'ordre où
                    # il le voit ; reprendre cet ordre-là réécrivait la feuille
                    # mélangée, sous des intertitres restés à leur place.
                    display_order=expected_line.display_order,
                    subsection=expected_line.subsection,
                )
            )

        for raw in payload.get("unexpected") or []:
            if isinstance(raw, dict):
                result.unexpected.append({
                    "text": str(raw.get("text") or ""),
                    "qty": raw.get("qty"),
                    "note": str(raw.get("note") or ""),
                })

        # Manquantes au sens du *couple* : une feuille portant l'article en bord
        # de ligne et en WIP, lue sur une seule des deux, doit voir l'autre.
        unread = sorted(
            set(expected_by_key) - seen, key=lambda k: (k[0], str(k[1]))
        )
        result.missing_items = [
            _line_label(number, section,
                        ambiguous=len(sections_by_number[number]) > 1)
            for number, section in unread
        ]
        # A missing expected line still gets a row, blank, so the encoder sees
        # it and can type the value instead of discovering the gap at posting.
        # Elle aussi garde sa place : c'est précisément la ligne qu'on va
        # chercher des yeux sur le papier pour la saisir à la main.
        for key in unread:
            expected_line = expected_by_key[key]
            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=expected_line.item_number,
                    section=expected_line.section,
                    unit=expected_line.unit,
                    source=DataSource.SCAN_AI,
                    confidence=0.0,
                    comment="Non lue sur le scan — à saisir manuellement.",
                    display_order=expected_line.display_order,
                    subsection=expected_line.subsection,
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
        allow_formulas: bool = False,
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
            # Aucune liste attendue, donc aucun compte de lignes connu — mais une
            # page A4 lignée n'en porte pas plus d'une quarantaine, et le budget
            # se calcule sur le nombre de pages fournies.
            max_tokens=extraction_tokens(_FREE_ENTRY_LINES_PER_PAGE * len(images)),
        )

        result = ExtractionResult(
            counter_name=str(payload.get("counter_name") or "").strip(),
            started_at=_clean_time(payload.get("started_at")),
            ended_at=_clean_time(payload.get("ended_at")),
            pages=len(images),
            tokens_used=response.total_tokens,
        )

        confidences: list[float] = []
        doubtful: list[tuple[str, CountSection]] = []
        # Le couple, ici aussi : le compteur qui écrit la même référence dans le
        # tableau bord de ligne *et* dans celui des en-cours relève deux
        # quantités, pas une qu'il aurait recopiée deux fois.
        seen: set[tuple[str, CountSection]] = set()

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
            section = (
                legacy_section_alias(str(raw.get("section") or ""))
                or CountSection.LINE_SIDE
            )
            if (number, section) in seen:
                continue
            seen.add((number, section))

            qty, formula = _clean_qty(
                raw.get("qty"), allow_formulas=allow_formulas
            )
            confidence = _clean_confidence(raw.get("confidence"))
            if qty is not None:
                confidences.append(confidence)
                if confidence < LOW_CONFIDENCE:
                    doubtful.append((number, section))

            result.lines.append(
                CountSheetLine(
                    id=id_factory(),
                    sheet_id=sheet_id,
                    campaign_id=campaign_id,
                    item_number=number,
                    section=section,
                    qty_imported=qty,
                    qty_manual=None,
                    unit=str(raw.get("unit") or "") or getattr(item, "unit", "PCE"),
                    source=DataSource.SCAN_AI,
                    confidence=confidence,
                    qty_formula=formula,
                    comment=str(raw.get("note") or "").strip(),
                    display_order=order,
                )
            )

        # Les étiquettes se posent à la fin : « P-1 » suffit tant que la
        # référence n'apparaît qu'une fois, et il faut « P-1 [WIP] » dès
        # qu'elle apparaît deux fois — ce qui ne se sait qu'une fois la feuille
        # entièrement lue.
        multiple = {
            number
            for number, _ in seen
            if sum(1 for n, _ in seen if n == number) > 1
        }
        result.low_confidence_items = [
            _line_label(number, section, ambiguous=number in multiple)
            for number, section in doubtful
        ]

        if confidences:
            result.mean_confidence = round(sum(confidences) / len(confidences), 4)
        return result

    def route_pages(
        self,
        *,
        footers: Sequence[bytes],
        candidates: Sequence[SheetCandidate],
        image_mime: str = "image/png",
        batch_size: int = 12,
        max_workers: int = 4,
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

        **Par lots, en parallèle, et sur les bandes seules.** Un appel unique
        portant deux cents pages entières est une charge utile que l'endpoint
        refuse bien avant que le modèle ait un problème de lecture — et une seule
        réponse tronquée y perdait le routage de toute la pile. Découpé, un lot
        qui échoue n'emporte que ses propres pages : elles deviennent non
        attribuées, avec la raison, et les autres lots aboutissent.

        :param footers: les bandes de pied de page, dans l'ordre des pages
            (:func:`footer_strips`).
        """
        if not footers:
            raise ValidationError("Aucune page à analyser.")
        if not candidates:
            raise ValidationError(
                "Aucune feuille de comptage ne peut recevoir ce scan : créez "
                "d'abord les zones et leurs feuilles."
            )

        # Deux index, parce que le pied de page imprime deux identités : le
        # jeton, et le couple zone + comptage. L'un rattrape l'autre.
        by_token = {c.token.upper(): c for c in candidates}
        by_zone_pass: dict[tuple[str, int], list[SheetCandidate]] = {}
        for candidate in candidates:
            key = (normalise_key(candidate.zone_code), candidate.pass_no)
            by_zone_pass.setdefault(key, []).append(candidate)

        listing = "\n".join(
            f"- {c.token} → zone « {c.zone_code} », comptage n°{c.pass_no}"
            for c in candidates
        )
        # Les lots gardent l'ordre des pages : le décalage ramène chaque numéro
        # relatif au lot vers son numéro dans la pile.
        batches = [
            (offset, list(footers[offset:offset + batch_size]))
            for offset in range(0, len(footers), batch_size)
        ]

        def read(batch: tuple[int, list[bytes]]) -> tuple[int, dict[str, Any], int]:
            offset, strips = batch
            payload, response = self._client.complete_json(
                system=_ROUTING_SYSTEM_PROMPT,
                user=_ROUTING_TEMPLATE.format(candidates=listing, count=len(strips)),
                images=strips,
                image_mime=image_mime,
                max_tokens=_routing_tokens(len(strips)),
            )
            return offset, payload, response.total_tokens

        routing = PageRouting()
        seen_pages: set[int] = set()
        # Un lot qui échoue est **coupé en deux et redemandé**, jusqu'à la page
        # seule. Ce qui rend un lot infaisable — une réponse trop longue, une
        # bande qui fait dérailler le modèle — disparaît presque toujours à la
        # moitié ; condamner les douze pages faisait payer à onze pages nettes
        # le défaut d'une seule, ou d'aucune. Le découpage termine : chaque tour
        # divise, et une page seule qui échoue encore est le seul cas terminal.
        pending = batches
        while pending:
            retry: list[tuple[int, list[bytes]]] = []
            for batch, outcome in zip(
                pending, in_parallel(read, pending, max_workers), strict=True
            ):
                offset, strips = batch
                if isinstance(outcome, BaseException):
                    if len(strips) > 1:
                        middle = len(strips) // 2
                        log.warning(
                            "Routage du lot page %d (%d pages) échoué, "
                            "on recoupe en deux : %s",
                            offset + 1, len(strips), outcome,
                        )
                        retry.append((offset, strips[:middle]))
                        retry.append((offset + middle, strips[middle:]))
                        continue
                    log.warning("Routage de la page %d échoué : %s", offset + 1, outcome)
                    seen_pages.add(offset)
                    routing.unrouted.append({
                        "page": offset + 1,
                        "read": "",
                        "note": f"Routage interrompu : {outcome}",
                    })
                    continue

                _, payload, tokens = outcome
                routing.tokens_used += tokens
                for raw in payload.get("pages") or []:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        page = offset + int(raw.get("page") or 0) - 1
                    except (TypeError, ValueError):
                        continue
                    if not offset <= page < offset + len(strips) or page in seen_pages:
                        continue
                    seen_pages.add(page)

                    candidate, refusal = _resolve_page(raw, by_token, by_zone_pass)
                    if candidate is None:
                        routing.unrouted.append({
                            "page": page + 1,
                            "read": _as_read(raw),
                            "note": refusal,
                        })
                        continue
                    routing.pages_by_sheet.setdefault(
                        candidate.sheet_id, []
                    ).append(page)
            pending = retry

        for page in range(len(footers)):
            if page not in seen_pages:
                routing.unrouted.append({
                    "page": page + 1,
                    "read": "",
                    "note": "Le modèle n'a rien renvoyé pour cette page.",
                })
        routing.unrouted.sort(key=lambda u: u["page"])
        # Les lots arrivent dans l'ordre, mais un modèle peut rendre ses pages
        # dans le désordre : c'est ici que l'ordre de lecture est rétabli, sans
        # quoi les pages d'une feuille partiraient mélangées au modèle.
        for pages in routing.pages_by_sheet.values():
            pages.sort()
        return routing

    def expected_from_items(
        self, lines: Sequence[CountSheetLine], items: dict[str, Item]
    ) -> list[ExpectedLine]:
        """Build the expected list from a sheet's existing (pre-printed) lines.

        Les intertitres et les lignes vides en sont écartés : ils ne portent pas
        d'article, et les annoncer au modèle comme des lignes attendues le
        lancerait à la recherche d'une quantité là où il n'y en a pas — puis
        les compterait parmi les lignes « non lues ».
        """
        return [
            ExpectedLine(
                item_number=line.item_number,
                name=(items[line.item_number].name if line.item_number in items else ""),
                section=line.section,
                unit=line.unit,
                display_order=line.display_order,
                subsection=line.subsection,
            )
            for line in lines
            if line.line_kind is CountLineKind.ARTICLE and line.item_number
        ]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def in_parallel(
    work: Callable[[T], R],
    items: Sequence[T],
    max_workers: int,
    *,
    on_done: Callable[[int], None] | None = None,
) -> list[R | BaseException]:
    """Exécute *work* sur chaque élément, à concurrence bornée, dans l'ordre.

    Trois propriétés, et chacune répond à un défaut de la boucle séquentielle
    qu'elle remplace :

    * **l'ordre est celui des éléments**, pas celui des réponses. Une feuille
      dont l'appel revient en premier ne doit pas prendre la place d'une autre
      dans le rapport ;
    * **une exception est une valeur**, rendue à sa place au lieu d'être levée.
      Sur une pile de cent feuilles, un refus du modèle sur la douzième ne peut
      pas emporter les quatre-vingt-huit autres ;
    * **la borne est explicite**. Le parallélisme utile est celui que
      l'endpoint absorbe : au-delà, les appels font la queue côté serving et les
      429 arrivent — le temps gagné se paie en relances.

    Les écritures en base **ne passent pas par ici** : seuls les appels au
    modèle sont parallélisés, et l'appelant écrit ensuite, séquentiellement,
    dans sa propre transaction.

    :param on_done: reçoit le nombre d'éléments terminés, à chaque fois qu'un
        de plus l'est. C'est ce qui fait avancer une barre de progression *au
        fil* du traitement : sans lui, elle reste à zéro pendant six minutes
        puis saute à cent, ce qui n'apprend rien pendant les six minutes.
    """
    if not items:
        return []

    done = 0
    counter = Lock()

    def run(item: T) -> R | BaseException:
        nonlocal done
        outcome = _captured(work, item)
        if on_done is not None:
            with counter:
                done += 1
                finished = done
            try:
                on_done(finished)
            except Exception as exc:  # pragma: no cover — l'avancement n'est
                # pas le travail : le signaler ne doit jamais le faire échouer.
                log.warning("Avancement non signalé : %s", exc)
        return outcome

    if max_workers <= 1 or len(items) == 1:
        return [run(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(run, items))


def _captured(work: Callable[[T], R], item: T) -> R | BaseException:
    try:
        return work(item)
    except Exception as exc:
        return exc


#: Budget de sortie d'une extraction : un préambule fixe, puis ce que coûte une
#: ligne de JSON. Le plafond de 8192 tokens servait pour toutes les feuilles, y
#: compris celles de dix lignes — une réservation que l'endpoint facture en
#: latence sur chacune des cent feuilles d'une pile.
_TOKENS_PER_LINE = 110
_TOKENS_PREAMBLE = 700
_TOKENS_CEILING = 8192


def extraction_tokens(expected_lines: int) -> int:
    """Ce qu'il faut pour rendre *expected_lines* lignes, sans plus."""
    return min(_TOKENS_CEILING, _TOKENS_PREAMBLE + _TOKENS_PER_LINE * expected_lines)


#: De quoi écrire ``{"page":12,"sheet":"e14f9b93","zone":"ZONE INTÉRIEUR
#: MÉTROLOGIE","pass":1}`` — un nom de zone long, accentué, qui se découpe mal —
#: avec le double de marge. Un plafond serré ne fait pas économiser : il coupe
#: la réponse, et un lot coupé perd le routage de *toutes* ses pages.
_ROUTING_TOKENS_PER_PAGE = 140
_ROUTING_TOKENS_PREAMBLE = 300


def _routing_tokens(pages: int) -> int:
    """Le routage rend trois champs courts par page, pas une transcription.

    Sans plafond proportionnel, le lot de douze pages passait sous la limite et
    revenait tronqué : douze pages non attribuées par appel, pour un pied de
    page parfaitement lisible. Le plafond suit donc la taille du lot, quelle
    que soit celle que la configuration choisit.
    """
    return _ROUTING_TOKENS_PREAMBLE + _ROUTING_TOKENS_PER_PAGE * pages


_SECTION_LABELS = {
    CountSection.LINE_SIDE: "bord de ligne",
    CountSection.WIP: "WIP non déclaré",
    CountSection.WIP_OK: "WIP assemblé",
}


def _section_label(section: CountSection) -> str:
    return _SECTION_LABELS.get(section, str(section))


def _line_label(number: str, section: CountSection, *, ambiguous: bool) -> str:
    """Comment nommer une ligne dans les listes du rapport.

    La section n'est ajoutée que lorsqu'elle départage : « MASS-1 » suffit quand
    l'article ne figure qu'une fois sur la feuille, et « MASS-1 [WIP non
    déclaré] » est indispensable quand il y figure deux fois — sans quoi la
    liste des valeurs douteuses nomme deux fois la même chose et n'indique plus
    laquelle vérifier.
    """
    return f"{number} [{_section_label(section)}]" if ambiguous else number


def _as_read(raw: Mapping[str, Any]) -> str:
    """Ce que le modèle dit avoir lu, tel quel, pour le rapport.

    Affiché à côté de la page non attribuée : sans cela, « pied de page
    illisible » ne distingue pas une bande vraiment abîmée d'une bande
    parfaitement lisible que le programme n'a pas su rapprocher — et le second
    cas est un défaut de ce code, pas du papier.
    """
    parts = [
        f"feuille {raw['sheet']}" if raw.get("sheet") else "",
        f"zone {raw['zone']}" if raw.get("zone") else "",
        f"comptage n°{raw['pass']}" if raw.get("pass") else "",
    ]
    return " · ".join(p for p in parts if p)


def _resolve_page(
    raw: Mapping[str, Any],
    by_token: Mapping[str, SheetCandidate],
    by_zone_pass: Mapping[tuple[str, int], Sequence[SheetCandidate]],
) -> tuple[SheetCandidate | None, str]:
    """La feuille que cette bande désigne, ou la raison de ne pas trancher.

    **Le rapprochement est ici, pas dans le modèle.** La version précédente lui
    demandait de ne rendre qu'un identifiant « présent dans la liste fournie » :
    une tâche de recherche, alors qu'il est là pour lire. Il s'y contredisait —
    une bande parfaitement lisible revenait avec l'identifiant correct dans sa
    note et ``null`` dans le champ, et la page tombait en non attribuée. Le
    modèle recopie désormais, et cette fonction cherche.

    **Deux identités valent mieux qu'une**, et le pied de page les imprime
    toutes les deux. Le jeton d'abord — c'est le plus court et le plus sûr —
    puis le couple zone + comptage, qui rattrape un jeton mal lu (un ``0`` pour
    un ``O``) sans rien deviner : il désigne lui aussi une feuille et une seule.

    **Deux lectures qui se contredisent ne se départagent pas.** Si le jeton
    désigne une feuille et le couple zone + comptage une autre, l'une des deux
    est fausse et rien ici ne dit laquelle. La page part à l'humain — c'est la
    règle inchangée : une page classée dans la mauvaise zone verse un comptage
    sur du stock qui n'y a jamais été.
    """
    token = str(raw.get("sheet") or "").strip().upper()
    by_id = by_token.get(token)

    zone = normalise_key(str(raw.get("zone") or ""))
    try:
        pass_no = int(raw.get("pass") or 0)
    except (TypeError, ValueError):
        pass_no = 0
    same_zone = list(by_zone_pass.get((zone, pass_no), ())) if zone and pass_no else []
    by_zone = same_zone[0] if len(same_zone) == 1 else None

    if by_id is not None and by_zone is not None and by_id.sheet_id != by_zone.sheet_id:
        return None, (
            f"Lectures contradictoires : l'identifiant « {token} » désigne la "
            f"zone {by_id.zone_code} n°{by_id.pass_no}, la ligne dit "
            f"{by_zone.zone_code} n°{by_zone.pass_no}."
        )
    if by_id is not None:
        return by_id, ""
    if by_zone is not None:
        return by_zone, ""

    if len(same_zone) > 1:
        return None, (
            f"Zone « {raw.get('zone')} » comptage n°{pass_no} : plusieurs "
            "feuilles y correspondent, et l'identifiant est illisible."
        )
    read = _as_read(raw)
    return None, (
        f"Identité lue « {read} », qui ne correspond à aucune feuille de cette "
        "campagne." if read else "Pied de page illisible."
    )


def _clean_qty(
    value: Any, *, allow_formulas: bool = False
) -> tuple[Decimal | None, str]:
    """A blank stays blank. Only a real number becomes a quantity.

    Rend aussi l'opération telle qu'elle était écrite, quand c'en était une :
    « 3*48+7 » sur le papier, 151 dans la colonne, et les deux conservés. Sans
    le texte, une case qui portait un calcul deviendrait indistinguable d'une
    case où quelqu'un aurait tapé le résultat — et le comptage cesserait d'être
    recomptable.

    **Une lecture ne refuse jamais.** Le modèle rend ce qu'il a vu, et une case
    illisible ou une opération alors que le réglage les interdit valent ici ce
    que valait déjà un gribouillis : une case vide, que quelqu'un ira remplir à
    l'écran. Lever ferait échouer la lecture des cent autres lignes de la
    feuille — et c'est précisément la ligne douteuse qui ne doit pas décider du
    sort des lignes sûres.
    """
    if value is None:
        return None, ""
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "-"):
        return None, ""
    try:
        return to_decimal(value), ""
    except ValueError:
        pass

    text = str(value).strip()
    if not (allow_formulas and looks_like_formula(text)):
        return None, ""
    try:
        return evaluate(text), text
    except FormulaError:
        return None, ""


def _clean_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(max(confidence, 0.0), 1.0)


def _clean_time(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def safe_scale(width_pt: float, height_pt: float, *, dpi: int, ceiling: int) -> float:
    """L'échelle de rendu, réduite si la page produirait un bitmap démesuré.

    ``render(scale=...)`` alloue son bitmap lui-même : la garde anti-bombe de
    PIL ne le voit pas. Une page dont le PDF déclare deux cents pouces de côté
    donne, à 150 dpi, trente mille pixels par côté — neuf cents mégaoctets pour
    une page, sur un conteneur qui en a six mille et les partage entre tous.

    Réduire plutôt que refuser : un MediaBox démesuré est presque toujours un
    artefact de scanner, et une feuille de comptage rendue à cent dpi au lieu de
    cent cinquante reste lisible. Refuser priverait l'utilisateur de sa lecture
    pour un défaut qui n'est pas le sien.
    """
    wanted = dpi / 72
    pixels = (width_pt * wanted) * (height_pt * wanted)
    if pixels <= ceiling or pixels <= 0:
        return wanted
    return wanted * math.sqrt(ceiling / pixels)


def render_pdf_pages(
    payload: bytes,
    *,
    max_pages: int = 12,
    dpi: int = 150,
    max_pixels: int | None = None,
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

    **Niveaux de gris, et sans ``optimize``.** Une feuille de comptage est du
    trait noir sur blanc : la couleur n'y porte aucune information et coûte trois
    canaux. ``optimize=True`` faisait, lui, chercher au compresseur le meilleur
    encodage possible — quelques pourcents d'octets contre un temps CPU
    proportionnel au nombre de pages, ce qui est exactement le mauvais échange
    sur une pile de deux cents.

    Le PNG reste : une feuille de comptage est du trait, où les artefacts JPEG se
    posent précisément sur les jambages que le modèle doit lire.

    :param max_pages: au-delà, l'appelant décide — ici on tronque et on le dit
        par le compte renvoyé, jamais en silence. Voir :func:`page_count`.
    :param dpi: 150 keeps a handwritten quantity legible while staying well
        under the per-request payload budget; 300 doubles the bytes for no
        measurable gain on the counting sheets this reads.
    :param max_pixels: plafond de pixels par page rendue. Une page qui le
        dépasserait est rendue moins finement — voir :func:`safe_scale`.
    """
    import pypdfium2

    from ..config import get_settings

    ceiling = max_pixels or get_settings().scan_max_pixels
    document = pypdfium2.PdfDocument(payload)
    try:
        total = len(document)
        if total > max_pages:
            log.warning("Scan truncated at %d pages (document has %d)", max_pages, total)
        pages: list[bytes] = []
        for index in range(min(total, max_pages)):
            page = document[index]
            width, height = page.get_size()
            scale = safe_scale(width, height, dpi=dpi, ceiling=ceiling)
            if scale < dpi / 72:
                log.warning(
                    "Page %d rendue à %.0f dpi au lieu de %d : le document la "
                    "déclare à %.0f × %.0f points, ce qui dépasserait %d pixels.",
                    index + 1, scale * 72, dpi, width, height, ceiling,
                )
            image = page.render(scale=scale, grayscale=True).to_pil()
            pages.append(_png(image))
        return pages
    finally:
        document.close()


def page_count(payload: bytes) -> int:
    """Combien de pages porte ce PDF, sans en rendre aucune.

    Lu avant le rendu pour pouvoir refuser une pile trop épaisse en la nommant,
    plutôt que d'en rendre le début et de perdre le reste sans le dire.
    """
    import pypdfium2

    document = pypdfium2.PdfDocument(payload)
    try:
        return len(document)
    finally:
        document.close()


#: Hauteur de la bande de pied de page découpée pour le routage, en fraction de
#: la page. L'identité est imprimée à 8 mm du bas d'un A4 (297 mm), soit à 2,7 %
#: — 10 % laisse une marge confortable pour un scan de travers ou recadré.
FOOTER_BAND = 0.10


def footer_strips(pages: Sequence[bytes], *, band: float = FOOTER_BAND) -> list[bytes]:
    """La bande basse de chaque page, celle qui porte l'identité de la feuille.

    Le routage n'a besoin de lire qu'une ligne : « <campagne> · zone <nom> ·
    comptage n°<n> · feuille <identifiant> », que l'application a imprimée
    elle-même en pied de page. Lui envoyer la page entière, c'est transmettre
    quatre-vingt-dix pour cent de surface qui ne sert à rien — et sur une pile de
    deux cents pages, c'est cette surface qui fait dépasser la charge utile
    acceptée par l'endpoint bien avant que le modèle ait un problème de lecture.

    Une bande illisible n'est pas rattrapée ici : ``route_pages`` rend la page
    comme non attribuée, et un humain la reprend. C'est la règle inchangée — une
    page classée dans la mauvaise zone verse un comptage sur du stock qui n'y a
    jamais été.
    """
    from PIL import Image

    strips: list[bytes] = []
    for blob in pages:
        try:
            image = Image.open(io.BytesIO(blob))
            width, height = image.size
            top = int(height * (1 - band))
            strips.append(_png(image.crop((0, top, width, height)).convert("L")))
        except Exception as exc:  # pragma: no cover - dépend de l'image reçue
            # Une page qui ne s'ouvre pas part telle quelle : le routage la
            # traitera comme les autres et dira ce qu'il n'a pas pu lire.
            log.warning("Pied de page non découpé, page envoyée entière : %s", exc)
            strips.append(blob)
    return strips


def _png(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
