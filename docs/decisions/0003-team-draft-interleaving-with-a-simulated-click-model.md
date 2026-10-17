# 0003. Team-draft interleaving, with a simulated click model until there is traffic

Date: 2026-10-17

## Context

An A/B test of two rankers needs a lot of traffic because users are split between arms and the metric is
noisy. Interleaving shows every user a merge of both rankers' lists and credits clicks to the ranker that
contributed the clicked result, which is far more sensitive per query. The brief asks for an A/B-style
interleaving comparison.

## Decision

- **Team-draft interleaving:** per query the two rankers each produce a list; rounds alternate who drafts
  first (coin flip when the teams are level), each team takes its highest-ranked result not yet shown,
  and the page remembers the team of every position. The client never sees the team.
- **Credit:** a query is won by the team with more clicked results; equal counts are a tie. The readout is
  wins, losses, ties, the win rate of the challenger with a 95% interval, a two-sided binomial test, and
  the number of decided queries needed to detect a 55/45 split at 80% power (783).
- **Live path:** `/interleave` serves a page and stores the teams in Redis under an impression id;
  `/click` records positions; `/interleave/report` (admin key) aggregates every impression with at least
  one click.
- **Simulation:** the same code runs offline over the labelled queries with a click model (probability
  of a click given the ESCI label, discounted by position), so the readout exists before any user has
  clicked and the pipeline from impression to verdict is tested end to end.

## Consequences

- The simulated verdict inherits the click model's assumptions; it demonstrates the method and the
  direction, not a user preference. The README says so.
- Team draft is biased towards the ranker that drafts first only through the coin flip, which is why the
  flip is per round and not per query.
- Impressions expire after six hours; the readout is about recent traffic by design.
