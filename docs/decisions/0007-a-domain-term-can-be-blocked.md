# 0007 - A domain term can be blocked, because a companion cannot say what a notice is not about

**Context.** The rules stage had two ways to qualify a match, both of them positive.
`requires_context` asks what an ambiguous word means in one sentence - HVAC beside "converter" is
high-voltage AC, HVAC alone is ventilation. `requires_companion` asks what the notice is about - HOAI
beside "Schule" is a school design job, HOAI alone says only how a fee was calculated.

The domain-match audit on 2026-09-12 (`docs/eval/2026-09-12-domain-matches.md`) found a third shape
that neither can express. Sixteen of the 41 notices matching `domain_energy_system` were German
building refurbishments - a hospital radiology department, a sports hall's building physics, a leisure
pool, two fire stations, three schools and kindergartens - where *Energieeffizienz* or *energetische
Sanierung* sits in a list of design requirements beside fire safety, accessibility and acoustics.
`profile.yaml` settles those: "Sustainability or net-zero language attached to unrelated procurement -
out of domain." Four more notices read as solar because a roof gets a photovoltaic array, in one case
built by the city utility and not even inside the procured scope.

The instruction was to require a companion. **It was tried first, and measured, and it failed its own
acceptance test.**

Requiring an energy-subject word alongside the efficiency and retrofit vocabulary removes the domain
from **28 notices**, and these five are real energy advisory work:

| Notice | What it is |
| --- | --- |
| 471520-2026 | Energy and structural audit of a French département's building portfolio, feeding the next annual investment programme |
| 538632-2026 | Belgian framework agreement for *missions variées liées à l'énergie* |
| 476162-2026, 476229-2026 | Irish OPW framework of energy specialists - energy auditing and reporting, optimisation of building management systems |
| 479223-2026 | Boiler-house audits across public buildings in Haute-Savoie |

The reason it fails is not a bad word list. It is that **the discriminator is not whether the notice
mentions energy. They all do.** It is whether the procured work is building design, and no positive
list of energy words separates those two groups, because both groups are full of energy words.

The pair that settles it is **612363-2026 against 587128-2026**. One is a works specification for
photovoltaic systems on 500 to 650 Bundeswehr buildings; the other is a new school canteen whose roof
gets a photovoltaic array. Both are German, both say *Photovoltaikanlage*, both say *Gebäude*, both
are procured as planning services. Every companion word that admits the first admits the second. The
only thing that separates them is that the second is written in building-design vocabulary.

**Decision.**

`Rule` gains `blocked_by`: a list of words that stop the rule counting when any of them appears
anywhere in the notice, in the blocks the rule reads. It is the mirror of `requires_companion`, uses
the same compiled patterns and the same notice-level scope, and is refused a prefix marker for the
same reason context and companion words are.

Two rules carry it, both created by splitting a term out of a larger rule so the guard reaches only
the vocabulary that needs it:

- `domain_energy_efficiency` - energy efficiency, Energieeffizienz, energetische Sanierung, rénovation
  énergétique and the rest of the retrofit vocabulary, split out of `domain_energy_system`.
- `domain_photovoltaic` - the photovoltaic prefixes, split out of `domain_onshore_renewables`.

**The blocking list is restricted to fee-schedule and profession vocabulary** - HOAI, Leistungsphase,
Leistungsphasen, LPH, Objektplanung, Tragwerksplanung, Generalplaner, Generalplanung, Bauleitung, and
the architect family in four languages. Nothing about buildings as such: not *Gebäude*, not *Neubau*,
not *Schule*. That restriction is the whole safety of the mechanism. Those words say a German or
French building-design contract is being let under building-design fee law; they do not appear in an
industrial decarbonisation study that happens to mention a building, so the block cannot reach one.

Measured against the corpus: 17 of 38 retrofit matches blocked and every one of the 17 a building
design job; 5 of 34 photovoltaic matches blocked. Across all of version 3, 24 notices lost all domain
evidence and **none of them is energy work**; three notices moved to `ARCHIVE_CANDIDATE`, all three
German building design.

**Consequence.**

- **This mechanism will start costing recall if it is applied more widely, and there is already a
  warning case.** 497122-2026 is a genuine energy park - planning and delivery of a 110 kV grid
  connection for photovoltaics and battery storage - whose scope is written in German fee-schedule
  language. It loses its photovoltaic match and keeps its domain only through the battery storage
  rule. German municipal utilities procure real energy work under HOAI as a matter of course;
  Stadtwerke Essen's heat-network notices are in this corpus and survive only because *Wärmenetz* and
  *Dekarbonisierung* sit on rules that carry no blocking list. Adding `blocked_by` to those rules
  would silently lose them.
- A blocker is the only construct in the rule set that can make a true statement about a notice
  disappear. `requires_context` and `requires_companion` withhold a match that was never established;
  a blocker withholds one that was. Every use of it needs the measurement beside it, which is why both
  rules carry the count in their `note:` field and why this record exists rather than a comment.
- Reversing it is cheap - empty the two lists - but knowing whether to reverse it is not, because the
  notices it removes never reach a human. The step 10 audit against human review decisions is the only
  place that question can be answered.
- The vocabulary is frozen at version 3 until that audit. See the outstanding list in this directory.
