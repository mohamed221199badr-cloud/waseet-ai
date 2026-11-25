"""Deterministic product matching for order text. No database, no LLM."""

from app.matching.engine import CatalogEntry, Matcher, MatchResult
from app.matching.parser import ParsedLine, parse_message

__all__ = ["CatalogEntry", "Matcher", "MatchResult", "ParsedLine", "parse_message"]
