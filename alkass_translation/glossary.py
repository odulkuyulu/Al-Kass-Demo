"""
Domain glossary support for broadcast-specific terminology.

In sports broadcasting, certain terms (player names, team names, tournament
names, sport-specific jargon) must be translated consistently. This module
loads a glossary and applies post-translation corrections.

Glossary format: CSV with columns source_term,target_term
Example:
    الدوري,The League
    هاتريك,hat-trick
    ركلة جزاء,penalty kick
"""

import csv
import re
from pathlib import Path
from typing import Dict, Optional


class DomainGlossary:
    """
    Loads and applies domain-specific term replacements.

    Applied as a post-processing step after machine translation to ensure
    broadcast-critical terms are rendered correctly.
    """

    def __init__(self):
        self._ar_to_en: Dict[str, str] = {}
        self._en_to_ar: Dict[str, str] = {}

    def load_from_csv(self, path: str):
        """
        Load glossary from a CSV file.
        Expected columns: arabic_term, english_term
        """
        glossary_path = Path(path)
        if not glossary_path.exists():
            raise FileNotFoundError(f"Glossary file not found: {path}")

        with open(glossary_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2 or row[0].startswith("#"):
                    continue
                arabic_term = row[0].strip()
                english_term = row[1].strip()
                if arabic_term and english_term:
                    self._ar_to_en[arabic_term] = english_term
                    self._en_to_ar[english_term.lower()] = arabic_term

    def add_term(self, arabic: str, english: str):
        """Add a single glossary entry at runtime."""
        self._ar_to_en[arabic] = english
        self._en_to_ar[english.lower()] = arabic

    def apply(self, text: str, direction: str) -> str:
        """
        Apply glossary corrections to translated text.

        Args:
            text: The translated text to correct.
            direction: "ar-to-en" or "en-to-ar"

        Returns:
            Text with glossary terms replaced.
        """
        if direction == "ar-to-en":
            mapping = self._ar_to_en
        else:
            mapping = self._en_to_ar

        result = text
        for source_term, target_term in mapping.items():
            # Case-insensitive replacement for English terms
            pattern = re.compile(re.escape(source_term), re.IGNORECASE)
            result = pattern.sub(target_term, result)

        return result

    @property
    def term_count(self) -> int:
        return len(self._ar_to_en)


def load_glossary(path: Optional[str]) -> DomainGlossary:
    """Factory: load glossary from path, or return empty glossary."""
    glossary = DomainGlossary()
    if path:
        glossary.load_from_csv(path)
    return glossary
