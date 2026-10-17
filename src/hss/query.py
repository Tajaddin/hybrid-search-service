"""Query understanding: normalisation, spelling correction against the catalog vocabulary, and brand and
colour terms turned into filters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from symspellpy import SymSpell, Verbosity

WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")


@dataclass
class Understood:
    original: str
    normalized: str
    corrected: str
    corrections: list[tuple[str, str]] = field(default_factory=list)
    brand: str | None = None
    color: str | None = None
    residual: str = ""  # the query without the recognised attribute terms

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "corrected": self.corrected,
            "corrections": [{"from": a, "to": b} for a, b in self.corrections],
            "brand": self.brand,
            "color": self.color,
        }


class QueryUnderstanding:
    def __init__(self, prepared_dir: Path, max_edit_distance: int = 2) -> None:
        self.prepared_dir = prepared_dir
        self.sym = SymSpell(max_dictionary_edit_distance=max_edit_distance, prefix_length=7)
        vocab = prepared_dir / "vocabulary.txt"
        self.vocabulary_loaded = vocab.exists() and self.sym.load_dictionary(
            str(vocab), term_index=0, count_index=1, encoding="utf-8"
        )
        attributes = (
            json.loads((prepared_dir / "attributes.json").read_text(encoding="utf-8"))
            if (prepared_dir / "attributes.json").exists()
            else {}
        )
        self.brands: dict[str, str] = {k: v["value"] for k, v in attributes.get("brands", {}).items()}
        self.colors: set[str] = set(attributes.get("colors", {}))
        # Longest brand names first so "under armour" wins over "armour".
        self._brand_keys = sorted(self.brands, key=len, reverse=True)

    def normalize(self, query: str) -> str:
        return " ".join(WORD.findall(query.lower().replace("&", " and ")))

    def correct(self, normalized: str) -> tuple[str, list[tuple[str, str]]]:
        if not self.vocabulary_loaded or not normalized:
            return normalized, []
        out: list[str] = []
        changes: list[tuple[str, str]] = []
        for token in normalized.split():
            if len(token) < 4 or token.isdigit() or self.sym.words.get(token):
                out.append(token)
                continue
            suggestions = self.sym.lookup(
                token, Verbosity.TOP, max_edit_distance=2 if len(token) > 5 else 1, include_unknown=True
            )
            best = suggestions[0].term if suggestions else token
            if best != token:
                changes.append((token, best))
            out.append(best)
        return " ".join(out), changes

    def attributes(self, corrected: str) -> tuple[str | None, str | None, str]:
        padded = f" {corrected} "
        brand = None
        residual = padded
        for key in self._brand_keys:
            if f" {key} " in padded:
                brand = self.brands[key]
                residual = residual.replace(f" {key} ", " ", 1)
                break
        color = next((c for c in self.colors if f" {c} " in residual), None)
        if color:
            residual = residual.replace(f" {color} ", " ", 1)
        return brand, color, " ".join(residual.split())

    def understand(self, query: str) -> Understood:
        normalized = self.normalize(query)
        corrected, changes = self.correct(normalized)
        brand, color, residual = self.attributes(corrected)
        return Understood(query, normalized, corrected, changes, brand, color, residual or corrected)

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """Completions from the vocabulary for the last word, for the UI's suggestion list."""
        words = self.normalize(prefix).split()
        if not words or not self.vocabulary_loaded:
            return []
        last = words[-1]
        head = " ".join(words[:-1])
        candidates = sorted(
            ((n, t) for t, n in self.sym.words.items() if t.startswith(last) and t != last), key=lambda x: -x[0]
        )[:limit]
        return [f"{head} {t}".strip() for _, t in candidates]
