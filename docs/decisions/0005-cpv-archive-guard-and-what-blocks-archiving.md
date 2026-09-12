# 0005 - The CPV archive guard, and what does and does not stop a notice being archived

**Context.** The deterministic rules stage routes a tender to `ARCHIVE_CANDIDATE` - "skip the model
call" - only when an exclusion matched, no domain rule matched **and** no CPV code matched. Two parts
of that sentence were specified loosely enough to be got wrong later, and both were measured on
2026-09-12 against the 2,158 TED notices ingested for 1 July - 11 September 2026. The run is
`docs/eval/2026-09-12-rules-archive-audit.txt`; the reading of it is
`docs/eval/2026-09-12-rules-archive.md`.

**Decision.**

### The CPV archive guard is a separate, narrow list, and it only ever prevents archiving

`config/rules.yaml` carries its own `cpv.archive_guard` list of seven subject-matter energy codes. It
is **not** the collection list in `config/sources/ted.yaml`, and the two must never be merged.

Every notice we hold matched the collection list by construction - that list is how TED selected it.
So `cpv_match` computed against the collection list is **true for all 2,158 notices**, the third
condition of the route can never fail, and `ARCHIVE_CANDIDATE` becomes unreachable. The rules stage
would still run, still record matches, still look correct in every test that checks a match, and
would silently route 100% of notices to the model. That is a bill, not an error message.

The guard is also strictly one-directional. It can only **stop** an archiving. It never adds a domain,
never raises a score and never appears as positive evidence, because 0004 settled that CPV describes
how a buyer chose to code a notice and says nothing about relevance. The asymmetry is the point: using
a weak signal to avoid discarding something is safe, using it to promote something is not.

It earned its place in the measured corpus. **614160-2026**, a Frankfurt qualification system for
refurbishing substations, matched no domain rule at all - the notice says "Umspannwerke" and
"Bestandsumspannwerken", German plural and compound, and the matcher does not stem - and matched the
building-design exclusion on the word HOAI. Text alone would have archived a substation notice. Its
codes `71314100` and `71323100` fall under the guard, so it routed to `ASSESS`.

If someone later "simplifies" the guard back to the ted.yaml list, nothing fails: the tests that
assert a route still pass on hand-written notices with no codes, the register fills up, and the model
bill grows by the 11% of notices that should have been skipped. If instead someone deletes the guard
altogether, the failure is the opposite and worse - notices like 614160-2026 are archived on a
fee-schedule reference. `tests/unit/test_rules_config.py` asserts the two lists differ, which catches
the first case but not the second; the second is caught only by reading this record.

### Supporting-strength terms do not block archiving. That is deliberate

A domain rule has a strength: `high`, `medium` or `supporting`. Only high and medium enter
`domains_hit` and therefore only those stop an exclusion archiving. A supporting match - "energy",
"power", "carbon", "cable", "fuel", "climate", "environment" - is recorded as evidence with its quote
and changes nothing about the route.

The reason is the one already written into the vocabulary: a supporting term can never establish
domain relevance on its own. If it could block archiving it *would* be establishing relevance on its
own, by a different door, and the word "energy" appears in procurement boilerplate every week. In the
measured corpus 180 notices matched a supporting term and 23 of them were archived; reading all 23
found development programmes, PR frameworks and building design, and no energy work.

This is a **stated rule, not an emergent one**: `domains_hit` contains only established domains, and
the route reads `domains_hit`. Anyone who later makes supporting terms block archiving should expect
the archive rate to collapse and should say why that is better.

### A notice too thin to judge is assessed

Silence is not evidence of irrelevance. A notice with no matches at all, including one with no
screening text at all, routes to `ASSESS`. Archiving requires a positive exclusion match, so an empty
notice cannot reach `ARCHIVE_CANDIDATE` by construction rather than by intention. Our vocabulary is
English and TED titles arrive in every EU language; a two-line notice means we do not know, which is a
human's question.

**Consequence.**

- `ARCHIVE_CANDIDATE` is a cost decision, never a relevance decision. Every tender on every route
  continues to the scoring policy, keeps its score and band, and stays searchable in the register.
- The guard list grows only on evidence that a subject-matter energy code was needed, and it is not a
  place to put advisory or consultancy codes - those describe what is being bought, not what it is
  about, and would guard nearly everything.
- Three numbers to re-measure whenever the rule set changes: how many notices are archived, how many
  are archived by a single exclusion alias, and how many archived notices contain a native-language
  energy word the vocabulary does not have. Measured against the rule set as seeded on 2026-09-12
  those were 236 of 2,158, 213 of 236 with no energy signal at all, and 8 of 236 - all eight building
  or civil works with incidental climate language. Five vocabulary additions made in response took
  the first to 235.
- The matcher does not stem, and German inflection and compounding defeat token matching in both
  directions. It protected 592803-2026 ("Netzmanagementsoftware" is one token, so the IT exclusion did
  not fire) and it cost us 614160-2026 ("Umspannwerke" is not "Umspannwerk"). Until that is settled,
  the guard is doing work the vocabulary cannot.
