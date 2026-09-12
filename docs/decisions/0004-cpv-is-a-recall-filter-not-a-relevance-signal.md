# 0004 - CPV is a recall filter, not a relevance signal, and no CPV figure is a quality baseline

**Context.** On 12 September 2026 the TED CPV list was widened after a measurement of the complete
5-12 September window: 212 notices under the list as it stood, read in full rather than through the
60-row page a probe had printed. Four things that measurement settled are worth keeping, because each
of them is the kind of mistake that produces a plausible-looking number rather than a visible failure.

The measurement itself is kept in `docs/eval/2026-09-12-ted-cpv-window.txt` (the full run) and
`docs/eval/2026-09-12-cpv-expansion.md` (the reconciliation and the decisions).

**Decision.**

### CPV selects what we *see*; it says nothing about what is *relevant*

`config/sources/ted.yaml` is a recall instrument and is tuned for recall alone. Nothing downstream may
treat the presence, absence or specificity of a CPV code as evidence of relevance.

The case that settles it is **626958-2026**, Ville de Nîmes: assistance à maîtrise d'ouvrage for a
study of a roof-solarisation strategy for the city's schools. It carries `71241000` - feasibility
study, advisory service, analysis - **and no energy code at all**. It is a solar study classified as a
study, because that is what the buyer was buying. Any rule that reached for an energy CPV to decide
this was a solar tender would have missed it, and any recall measure counting "energy notices" by CPV
does not count it.

The electrolysis programme 623022-2026 is **not** evidence for this. It carries `71314000`, "Energy
and related services", which is in the list and which found it. That is a configured code doing its
job, and citing it here would have made the argument look broader than the evidence supports.

### No CPV-derived figure is a quality baseline

These four measures, all from the 212-notice window, describe **how buyers chose to code their
notices**. They are not measures of our coverage, our recall or our relevance, and none of them may
become a target or a baseline:

| Measure | Count of 212 |
| --- | --- |
| `cpv_main` begins `09` | 6 |
| any code in `cpv_all` begins `09` | 12 |
| any code in `cpv_all` begins `65` | 7 |
| any solar CPV anywhere in `cpv_all` | 4 |

`cpv_main` is the weakest of the four and the easiest to quote: the filter matches on `cpv_all`, so a
figure built on `cpv_main` describes neither what we ask for nor what we keep.

**The step 10 quality baseline must be built from human review decisions** - what a colleague marked
relevant, irrelevant or uncertain, and what they said about why - not from any property of the source
classification.

### The recall audit must sample from a broader query than the one we run

`QueryProfile.AUDIT` already exists for this and must stay that way: same window and stages, **no CPV
filter and no contract-nature filter**. The audit samples from that broader set and asks what our
daily query would have missed.

Reviewing only what we collected can never reveal what the filters excluded. Every notice in the
register is by construction one that passed both filters, so a review of the register measures
precision and is structurally blind to recall. This decision record exists because exactly that blind
spot nearly closed: a conclusion about a week ("no hydrogen, no CCS, no wind, no solar") was drawn
from 60 of 212 notices, and the 212 were themselves only what the filters had already allowed
through. Both layers had to be opened to see the error.

### The services filter's cost is accepted and named

`contract_natures: [services]` stays. Over 5-12 September it removed 33 of the 45 notices the widened
CPV list reaches. Most were commodity gas purchasing, but these four are real intelligence we do not
see, named so the audit can weigh them rather than rediscover them:

- **619013-2026** - design, build and commissioning of a municipal waste-to-energy plant (ITPO) at
  Wysokie Mazowieckie.
- **619753-2026** - an Open Cycle / Combined Cycle Gas Turbine qualification system.
- **626464-2026** and **626966-2026** - two small-wind works notices.

They are project intelligence rather than opportunities for us, which is why the filter stays. Revisit
at the step 10 audit with a month of data.

### Two codes are in the list on terms other than measured gain

Recorded here because the reasons will not survive in a diff:

- **71334000**, provisional. Five notices that week, one an opportunity (626032-2026, Galway County
  Council, SEAI decarbonisation Pathfinder design-team consultancy) and four ordinary building
  services design. Kept because excluding a demonstrated opportunity at collection loses it
  permanently and invisibly, whereas a weak notice only has to be sorted downwards - the same
  reasoning that makes keyword matching route rather than reject, and that keeps archived notices
  searchable.
- **09123000**, a bet. Contribution that week was zero; its only notice arrives via 76000000 anyway.
  It is a wager that hydrogen blending and CCS retrofit work gets classified as natural gas.

Both are settled by the step 10 audit, against human review decisions.

**Consequence.**

- The CPV list grows by measurement and shrinks by redundancy proof, and both are recorded in the file
  itself. Three redundant codes were removed the same day - `09330000`, `71314300`, `79411000` - each
  confirmed by the local matcher and by a live child-versus-parent query returning zero lost notices.
- A recall claim about a window is only valid if it was made against the complete window. The probe
  now prints "showing 60 of 218, lowest publication numbers first" for that reason: `--limit` truncates
  in publication-number order, so a limited probe shows the start of a window and hides the end.
- **A plausible number is the failure mode here, not an error message.** The count that makes the
  probe honest was itself wrong on its first attempt: it reported 217 where iterating the same query
  returns 212, because the count request omitted `onlyLatestVersions` and therefore counted superseded
  versions. Nothing failed, nothing logged a warning, and the banner would have been authoritative and
  wrong by five. It was caught only by comparing the count against a full page of the same query.
  Every number this tool prints about its own coverage needs that kind of check before it is believed.
- The screening stage carries the relevance judgement alone. That is a larger obligation on screening
  than a CPV-assisted design would have been, and it is deliberate.
