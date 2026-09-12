# What the exclusion rules archive, 1 July - 11 September 2026

Evidence behind decision record
[0005](../decisions/0005-cpv-archive-guard-and-what-blocks-archiving.md) and the exclusion notes in
`config/rules.yaml`. The full run is `2026-09-12-rules-archive-audit.txt`: rule set version 1 **as
seeded**, against the 2,158 TED notices ingested for that window. The five aliases added as a result
of the audit are excluded from that run, so the run file, the numbers below and the sample all
describe the same rule set. The script that produced it was throwaway; the method is described at the
head of each part of the run file and can be re-run against a later rule set.

**Why the audit happened.** The exclusion vocabulary was seeded by counting term frequency in the
corpus. Frequency proves a term is common, not that it marks irrelevant scope. HOAI is the German fee
schedule for **architects and engineers** and covers electrical and building-services engineering, so
82 notices carrying it says nothing on its own about whether archiving on it loses energy work.

## The headline numbers

| | |
| --- | --- |
| notices | 2,158 |
| routed `ASSESS` | 1,922 |
| routed `ARCHIVE_CANDIDATE` | 236 (10.9%) |
| archived with no domain match of any strength | 213 |
| archived while matching a supporting energy term | 23 |
| archived notices containing a native-language energy word | 8 |

## 1. Does a single term archive on its own?

"Alone" means the notice is archived and that alias is the only exclusion alias matching anywhere in
it: delete the alias and the notice routes to `ASSESS`.

| Term | Notices containing it | Archived | Archived on it alone |
| --- | --- | --- | --- |
| HOAI | 82 | 26 | **7** |
| Leistungsphasen | 63 | 17 | **3** |
| capacity building | 21 | 16 | **6** |

All sixteen were read in full. None is energy work:

- **HOAI alone (7)** - flood-dyke construction supervision (494737), a hospital logistics concept
  (616339), building acoustics for a school extension (453426), bridge replacement design (562156),
  school extension electrical services (544012), a residential block's technical equipment (562239),
  flood-retention basin site supervision (612617). Two of the seven are electrical engineering scopes,
  which is exactly the overlap worth worrying about - but both are building services for a school and
  a housing block, not grid or energy work.
- **Leistungsphasen alone (3)** - project control for an underground tram station refurbishment
  (620271), school canteen building services (458799), school rebuild construction supervision
  (569711).
- **capacity building alone (6)** - DG REGIO administrative capacity (464762), ENISA cybersecurity
  indexes (514037), an Italian administrative-regeneration programme (541108), a Finnish development
  cooperation DPS (568800), an EU social-economy toolkit (600667), a GIZ urban-mobility programme in
  Brazil (602152).

**Conclusion: no companion requirement is needed on this evidence.** None of the three terms archived
energy work by itself. That is a measurement of ten weeks, not a proof, which is why the numbers above
are recorded and why the next item matters more.

## 2. The case that nearly went wrong, and what caught it

**614160-2026**, Frankfurt: *"Qualifizierungssystem Sanierung von Umspannwerke"* - a qualification
system for planning conversions in existing substations. Its only rule match is
`exclusion_building_design` on **HOAI**. No domain rule fired, because the notice says *Umspannwerke*
and *Bestandsumspannwerken* and the vocabulary had only *Umspannwerk*: this matcher deliberately does
not stem, and German inflects and compounds.

On text alone this substation notice would have been archived on a fee-schedule reference - precisely
the failure the audit was looking for. It routed to `ASSESS` because `71314100` and `71323100` are in
the CPV archive guard.

Two things follow, and both are now written down rather than remembered:

1. The guard is not decoration. It is the only thing standing between a German grid notice and the
   archive when our English vocabulary misses the word. See 0005.
2. The vocabulary has an inflection and compounding hole. `umspannwerke` was added; the general
   problem is unresolved and is listed as outstanding in `docs/decisions/README.md`.

## 3. The sample: 25 of the 213 archived with no energy signal at all

Drawn with `random.Random(20260912).sample(...)`, so it is reproducible and not chosen. Full text,
evidence spans and descriptions are in part 3 of the run file; this is the reading of it.

| # | Notice | Caught by, on | What it actually is | Right to archive? |
| --- | --- | --- | --- | --- |
| 1 | 451888 NOR | building_design, `architect` | Architect, landscape and interior architect for a welfare-centre extension | yes |
| 2 | 468655 FRA | water, `assainissement` | Technical inspection of regulatory self-monitoring on a sewer collection system | yes |
| 3 | 469466 IRL | water, `drainage` | Galway surface-water network drainage assessment and modelling | yes |
| 4 | 527525 IRL | education, `training services` | Panel of trainers for a local enterprise office | yes |
| 5 | 531965 FRA | roads, `voirie` | Design supervision for roadway creation and resurfacing works | yes |
| 6 | 537465 FRA | water, `assainissement` | Framework for sewerage and stormwater master plans | yes |
| 7 | 538537 FRA | building_design, `architecte` | **Design supervision for refurbishing an estate's communal heating and hot-water networks** | outcome yes, evidence wrong - see below |
| 8 | 557170 DEU | comms_pr, `öffentlichkeitsarbeit` | Communication measures framework for the federal research ministry | yes |
| 9 | 558691 FRA | water, `assainissement` | Pre-works asbestos and tar survey plus geotechnical studies on water and sewer networks | yes |
| 10 | 561383 DEU/GIZ | agriculture, `forestry` | Decentralised environmental governance around protected areas | yes |
| 11 | 563649 FRA | water, `assainissement` | Owner's advisory for drinking-water and sewerage diagnostics and master plan | yes |
| 12 | 566443 FRA | water, `assainissement` | Owner's advisory framework for the "small water cycle" | yes |
| 13 | 572082 IRL | finance, `accounting services` | Accounting, audit and financial advisory framework for the public sector | yes |
| 14 | 580280 FRA | urban, `renouvellement urbain` | Running a programmed housing-improvement and urban-renewal operation | yes, borderline: such programmes often carry thermal retrofit |
| 15 | 584826 DEU | general_it, `software` | **Stormwater concept: survey, hydraulic sizing, flood precaution** | outcome yes, evidence wrong - matched "Kanalkataster-Software" |
| 16 | 584846 DEU | building_design, `HOAI` | Building services engineering for a new school and kindergarten | yes |
| 17 | 586974 FRA | urban, `plan local d'urbanisme` | Support studies for revising the local urban plan | yes |
| 18 | 591337 FRA | building_design, `architecte` | Design supervision for demolition, refurbishment and extension of a building | yes |
| 19 | 605981 FRA | roads, `autoroute` | Flood-vulnerability studies of 118 km of the COFIROUTE motorway | yes |
| 20 | 606711 FRA | water, `assainissement` | Diagnostic and master plan for collective sewerage | yes |
| 21 | 615656 DEU | general_it `website`, comms_pr `social media` | Web agency framework for a federal commissioner's office | yes |
| 22 | 618852 BEL | roads, `viaduct` | Groundwater modelling for the Gentbrugge viaduct project | yes |
| 23 | 619918 DEU | building_design, `architekt` | Coordination and advisory for a new literature research archive building | yes |
| 24 | 620225 FRA | water, `assainissement` | Wastewater and drinking-water master plans | yes |
| 25 | 622496 BEL | general_it, `website` | **360-degree feedback tool for team leaders** | outcome yes, evidence wrong - matched "published on the same website" in boilerplate |

**25 of 25 are correctly archived by outcome.** None is early-phase advisory work in our domain.

**3 of 25 are archived on evidence that does not describe the notice.** That matters separately from
the route: a score has to be defensible to a colleague, and "archived because it says website" on a
notice whose only mention of a website is the boilerplate about where corrigenda get published is not
defensible. `software` and `website` are the sole reason for 17 archivings between them.

**Notice 7 is the one to look at twice.** 538537-2026 is a *maîtrise d'oeuvre* contract for
refurbishing the communal heating and hot-water networks of a Nantes housing estate. Heat networks are
in domain; the work is delivery-phase design supervision, so the route is right. But no domain rule
fired, because the vocabulary had *district heating*, *heat network*, *Wärmenetz* and *fjernvarme* and
not *réseau de chauffage*. The scan in part 4 missed it too, because that scan looks for stems someone
thought of. Reading found it. French heat-network terms were added.

## 4. Did we archive anything that reads as energy work in its own language?

A substring scan - not token matching, so German compounds are caught - over every archived notice.
**8 of 236** contain any native-language energy stem, and all eight are building or civil works with
incidental climate language: a tram-station refurbishment whose funding line mentions greenhouse-gas
reduction (620271), a fire-station new-build connected to a *Wärmenetz* (481233), a school extension
mentioning *Klimaschutz* (544012), a pumping-station replacement (606105), a theatre refurbishment
(610924), a ministry PR framework for the environment ministry (451704), and two French building
projects mentioning *performance énergétique* (456280, 591337).

**456280-2026 is a false positive of the water rule**: *assainissement énergétique* is French for an
energy retrofit, not sewerage. The notice is 42 months of construction supervision for a school
retrofit - out of scope as works supervision, so the route survives - but the evidence is wrong and
the rule note now says so.

## 5. Rule-level histogram

Full table in part 2 of the run file. The exclusions, ordered by how much archiving they do alone:

| Rule | Notices matched | Archived alone | Dominant alias |
| --- | --- | --- | --- |
| exclusion_building_design | 134 | **51** | HOAI 24, Leistungsphasen 16, Objektplanung 12 |
| exclusion_water_wastewater | 61 | 33 | assainissement 27, eaux usées 11 |
| exclusion_communications_pr | 46 | 33 | öffentlichkeitsarbeit 19, social media 10 |
| exclusion_general_it | 37 | 24 | software 10, website 7 |
| exclusion_urban_planning | 24 | 17 | urbanisme 10, renouvellement urbain 7 |
| exclusion_agriculture | 23 | 12 | agricultural 6, agriculture 5 |
| exclusion_roads_transport_works | 19 | 11 | voirie 4, tunnel 3 |
| exclusion_capacity_building | 29 | 10 | capacity building 8 |
| exclusion_finance_banking | 6 | 4 | payroll 2 |
| exclusion_facilities_management | 5 | 4 | nettoyage 3 |
| exclusion_education_training | 6 | 3 | training services 2 |
| exclusion_health | 2 | 2 | nursing 1, clinical trial 1 |
| exclusion_social_welfare | 2 | 1 | welfare aziendale 1 |
| exclusion_public_admin_digitisation | 1 | 0 | - |
| exclusion_defence | 1 | 0 | - |

Building design does more archiving than the next two combined, which is where a recall loss would
concentrate. That is why parts 1, 2 and 4 above all interrogate it specifically, and why its rule note
now carries the audit result.

**Rules that matched zero times** - `domain_offshore_wind_acronyms`, `domain_grid_hvac`,
`domain_grid_hv`, `domain_energy_storage_ess`, `domain_low_carbon_industry_dri`,
`activity_energy_modelling`, `activity_dispatch_modelling`, `activity_esg_lca_acronym`. Nothing is
concluded from that. These are the ambiguity-guarded acronyms and the energy-system modelling
vocabulary, and ten weeks of a CPV-filtered European consultancy feed simply contained no notice for
them. `activity_energy_modelling` matching zero times is worth remembering when the first hybrid-system
sizing tender does arrive: it will be the first time that rule has ever fired.

## 6. What changed as a result

Two passes. The first added vocabulary only; the second, rule set **version 2**, acted on the audit.
Every change moves in the same direction - a rule becomes *less* likely to archive - and each was
measured on its own by re-running the corpus with that change alone reversed.

### First pass: vocabulary

| Change | Because |
| --- | --- |
| `umspannwerke` added to `domain_grid_transmission` | 614160-2026 says the plural and matched nothing |
| `réseau de chaleur`, `réseau de chauffage`, `réseaux de chauffage`, `chauffage urbain` added to `domain_industrial_heat` | 538537-2026, a communal heating network refurbishment, matched nothing |

236 archived → 235.

### Second pass: version 2

| Change | Route changes | Effect |
| --- | --- | --- |
| 26 prefix aliases on domain terms (`umspannwerk*`, `fotowolta*`, `photovolta*`, `wasserstoff*` …) | **0** | 31 notices gained a domain match they did not have |
| Companion requirement on HOAI, Leistungsphasen, the architect family, capacity building | **7** | all `ARCHIVE_CANDIDATE` → `ASSESS` |
| `software` and `website` narrowed to `software development`, `web portal`, `website development` … | **16** | all `ARCHIVE_CANDIDATE` → `ASSESS` |
| French and German energy-retrofit terms added to `domain_energy_system` | **3** | all `ARCHIVE_CANDIDATE` → `ASSESS`, including 456280-2026 |
| Bare `FEED` replaced by qualified forms (`FEED study`, `FEED phase` …) | 0 | removes the feed-in tariff and animal feed false positives from the stage axis |

**236 archived as seeded → 209 at version 2**, 9.7% of the corpus. No notice moved the other way.

The prefix row is the one worth reading twice: no notice changed route, and 31 notices - Polish
*fotowoltaicznych*, Czech *fotovoltaické*, French *photovoltaïques*, German *Umspannwerken* - now
carry a domain match in their evidence. The gain is not in today's routing, it is that those 31 can no
longer be archived by an exclusion tomorrow.

The companion requirement has a visible cost: 603890-2026, a Dutch architect and installation adviser
for a primary school and a sports hall, is plainly a building notice and now goes to the model,
because *basisschool* and *sporthal* are not companion terms and companions are exact by design. That
is the deliberate direction of the trade.
