"""AI features: reading scanned counting sheets, and analysis assistance.

Everything here is *advisory*. No AI output is ever written to a decision
column, posted to a journal, or used to close a line without a human step.
"""

from .assistant import AssistantAnswer, Attachment, CampaignAssistant
from .client import LlmClient, LlmResponse, get_llm_client
from .insights import CauseSuggestion, InsightEngine
from .sheet_extraction import (
    LOW_CONFIDENCE,
    ExpectedLine,
    ExtractionResult,
    PageRouting,
    SheetCandidate,
    SheetExtractor,
    render_pdf_pages,
)

__all__ = [
    "LlmClient", "LlmResponse", "get_llm_client",
    "AssistantAnswer", "Attachment", "CampaignAssistant",
    "CauseSuggestion", "InsightEngine",
    "LOW_CONFIDENCE", "ExpectedLine", "ExtractionResult", "PageRouting",
    "SheetCandidate", "SheetExtractor", "render_pdf_pages",
]
