# 0003 - Place of performance is stored at country level and as raw codes; NUTS stays unresolved

**Context.** TED's `place-of-performance` is a single array mixing two kinds of code: NUTS region
codes such as `FR101` (Paris) or `SE110` (Stockholm), and ISO 3166-1 alpha-3 country codes such as
`FRA`. They arrive interleaved and repeated, once per lot and once per procedure, with no marker
saying which kind each entry is. Notice 598884-2026 carries 197 entries for six lots.

TED also states the country on its own, in `place-of-performance-country-proc`. That is a different
question from `buyer-country` and the difference matters: notice 619675-2026 has a French buyer and a
place of performance of `AGO`, because a French organisation is procuring a study for Luanda
Province. A register with one country field files that as a French tender and loses it.

Resolving NUTS codes to place names is a separate problem: roughly 2,000 codes across three levels,
revised every three years, with codes reused for different regions across revisions. That is a data
set with a maintenance obligation, not a lookup table.

**Decision.**

- `Tender.place_of_performance` stores the deduplicated codes exactly as TED sent them, joined for
  display. Nothing is interpreted, dropped or reordered.
- `Tender.place_of_performance_country` stores the country level on its own, as ISO-3 codes read from
  `place-of-performance-country-proc` (falling back to `-country-lot`). It is a delimited-list column,
  so it can be filtered structurally, and `TenderFilters.place_of_performance_country` does exactly
  that. Several distinct countries are all kept and `Tender.multi_country` flags it; that flag is
  derived from the list rather than stored, so the two cannot drift apart.
- `watchdog.core.countries` maps ISO 3166-1 alpha-3 to English names, and is used for `buyer_country`
  and for the place of performance alike. It covers EU27, EEA, the UK, Switzerland, the candidate
  countries and the places of performance that turn up in EU-funded work. An unknown code returns
  `None` from `country_name`, and `country_names` falls back to showing the code.
- `config/sources/ted.yaml` validates its country lists against that map, so a code the register
  cannot name cannot be *configured*. A code that arrives *from the source* is stored as received:
  a notice is never edited to suit our vocabulary.
- NUTS codes are left unresolved.

**Consequence.**

- The register can now answer "work happening in Angola" without also answering "bought by a French
  organisation". Notice 619675-2026 - a French buyer procuring a solid-waste roadmap for Luanda
  Province - is the case that would otherwise have been filed as a French tender and lost.
- What is still not possible is filtering **by region**. `place_of_performance` remains a text column
  of mixed codes, so "work happening in Bavaria" needs someone to know that `DE2` prefixes NUTS codes
  for Bavaria. If that becomes a real requirement, the next step is a normalised NUTS column plus the
  classification itself - roughly 2,000 codes, revised every three years, with codes reused across
  revisions. That is a data set with a maintenance obligation, and it stays deferred until real usage
  shows the filter people want is regional rather than national.
- The country map is curated rather than the full ISO list, so a place of performance somewhere
  genuinely unexpected displays its three-letter code. That is visible rather than wrong, and it is
  the moment to add the country - but it does mean the register can occasionally show "XYZ" to a
  non-technical reader.
- Adding a country to `core.countries` is a code change. That is intentional: it forces someone to
  notice that the register is about to show a new geography.
