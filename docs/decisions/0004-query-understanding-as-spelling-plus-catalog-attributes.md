# 0004. Query understanding as spelling correction plus catalog-derived attributes

Date: 2026-10-17

## Context

Shopping queries are short, often misspelled, and frequently carry a brand or a colour. The brief asks for
spell correction and filters. A learned query parser would need labelled queries this project does not
have.

## Decision

- **Spelling:** SymSpell over a vocabulary built from the catalog's own titles and brands (terms seen at
  least three times, with frequencies). A token is corrected only if it is not in the vocabulary, is at
  least four characters, is not a number, and has a suggestion within edit distance one (two for longer
  tokens). The vocabulary is the catalog, so "corrections" towards words no product contains cannot
  happen.
- **Attributes:** brand and colour values with enough products (brands with 20 or more, colours with 50
  or more) are recognised in the corrected query by longest match; they become filters when
  `auto_filters` is on (the default in the UI), and the API reports what it understood so the UI can
  show "Brand filter: Sony" and the user can switch it off.
- **Suggestions:** completions of the last word from the vocabulary, ranked by frequency.

## Consequences

- A brand that is also a common word ("apple", "shark") becomes a filter when typed; the switch and the
  visible explanation are the mitigation, and the offline eval measures the effect with filters on and off.
- No synonym expansion or intent classification; the vector side of the hybrid covers paraphrase.
