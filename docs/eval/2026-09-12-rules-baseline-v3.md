# Rules-only baseline, rule set version 3

The reference point step 7 tunes against. Rule set **version 3**, provider disabled, over a pinned
window:

```sql
where published_date >= '2026-07-01' and published_date <= '2026-09-11'
```

2,158 notices, which is every row in the table today - no notice in it lacks a publication date, and
none falls outside the window. The window is written down anyway, because the corpus grows with every
ingest and a baseline whose denominator moves is not a baseline. Step 7 must either filter to this
window or regenerate the file, and say which.

**Nothing was stored.** There is no scoring policy yet: `config/policy.yaml` is a placeholder,
`docs/SCREENING_POLICY.md` is a stub, and `src/watchdog/screening/` contains only the rule config and
the matcher. `ScreeningResult` requires a score, a band, a confidence and a policy version, every one
of which is a policy output, so writing rows would have meant inventing the policy. This file is what
the rules stage says on its own.

**The three-level grade below is illustrative.** It is not a policy, it is not in the code, and there
is no column for it. It exists here for one purpose: to test whether ordering the register by rules
evidence alone puts better things at the top than chance does. Its definition is stated so the test
can be judged independently of the particular thresholds chosen.

## Routes

| | |
| --- | --- |
| `ASSESS` | 1,946 |
| `ARCHIVE_CANDIDATE` | 212 |

## Evidence shape (policy-free)

"Service" means one of the categories `profile.yaml` calls ours: advisory, early-phase study,
techno-economic, energy modelling, due diligence, market assessment, ESG/LCA.

| Evidence shape | Notices | Share |
| --- | --- | --- |
| no domain term + no service term | **1,650** | 76.5% |
| no domain term + a service term | 188 | 8.7% |
| supporting terms only + no service | 175 | 8.1% |
| medium domain + no service | 69 | 3.2% |
| supporting terms only + service | 36 | 1.7% |
| medium domain + service | 18 | 0.8% |
| high domain + no service | 18 | 0.8% |
| **high domain + service** | **4** | 0.2% |

Three quarters of the corpus carries no domain term and no service term at all. With the provider
disabled the rules stage cannot distinguish those 1,650 notices from each other in any way.

## Established domains

128 notices carry an established domain - high or medium strength. Supporting terms establish nothing
and are not counted here.

| Domain | Notices |
| --- | --- |
| onshore_renewables | 44 |
| energy_other | 39 |
| industrial_decarb | 23 |
| grid_transmission | 14 |
| hydrogen | 11 |
| energy_storage | 5 |
| renewable_fuels | 3 |
| ccs_co2 | 2 |
| offshore_wind | 2 |

## Activity labels

| Service | Notices |
| --- | --- |
| advisory | 197 |
| early_phase_study | 51 |
| market_assessment | 7 |
| technical_assistance | 6 |
| techno_economic | 5 |
| due_diligence | 3 |
| esg_lca | 3 |
| **energy_modelling** | **0** |

Two labels carry 248 of the 272 matches. `energy_modelling` - sizing, dispatch, production profile,
capacity factor, curtailment - has never fired in ten weeks. Whatever step 7 does with the service
axis will be almost entirely the model's work.

## Illustrative grade

- **3** - a high-strength domain **and** one of our service categories
- **2** - any established domain, or a service term together with a CPV archive-guard code
- **1** - everything else

| Grade | Notices | of which archive candidates |
| --- | --- | --- |
| 3 | **4** | 0 |
| 2 | 124 | 0 |
| 1 | 2,030 | 212 |

## Top 20 by evidence strength

Ordered by grade, then whether a high-strength domain matched, then whether one of our service terms
matched, then whether an early stage matched, then the number of distinct domains, then absence of an
exclusion, then publication number.

| # | Notice | Title as the buyer wrote it | Grade | Domains | Services |
| --- | --- | --- | --- | --- | --- |
| 1 | 547488-2026 | 10005247 - Green Hydrogen Investments in India | 3 | hydrogen, renewable_fuels, onshore_renewables, industrial_decarb | advisory, market_assessment |
| 2 | 604181-2026 | 10030774 - Technical Assistance on Industrial Decarbonisation, CBAM Readiness, Gender Inclusion | 3 | industrial_decarb, energy_other | advisory, esg_lca |
| 3 | 608440-2026 | 10040388 - Consulting Services for Fostering a Just Transition in the Mexican Industrial Sector | 3 | onshore_renewables, industrial_decarb | advisory, early_phase_study |
| 4 | 523353-2026 | Machbarkeitsstudie Netzanschluss am Standort (STO) Lubmin | 3 | hydrogen | early_phase_study |
| 5 | 550587-2026 | Südbaden - Energiepark | 2 | onshore_renewables, grid_transmission, energy_storage | - |
| 6 | 626265-2026 | Request for Information (RFI) - CCS as a service | 2 | renewable_fuels, ccs_co2, energy_other | - |
| 7 | 478947-2026 | Markterkundung für die Wärmeversorgung in Bad Neustadt a.d. Saale | 2 | renewable_fuels, energy_other | - |
| 8 | 574262-2026 | Rámcová dohoda – Dodávky a instalace FVE, FVE s akumulací a samostatně stojící BESS | 2 | onshore_renewables, energy_storage | - |
| 9 | 450191-2026 | Gas Storage Denmark: Onshore Intervention Services related to wells on natural gas storage | 2 | hydrogen, ccs_co2 | - |
| 10 | 568229-2026 | Öffentlichkeitsarbeit AQD | 2 | hydrogen, onshore_renewables | - |
| 11 | 456898-2026 | Projekter vedrørende sameksistens mellem havvind og havnatur | 2 | offshore_wind | - |
| 12 | 486590-2026 | Hohe See_Albatros: wiederkehrende Prüfung Überwasser | 2 | offshore_wind | - |
| 13 | 497122-2026 | Planungsleistungen Elektrotechnik Haus Aden für die RAG Aktiengesellschaft | 2 | energy_storage | - |
| 14 | 597259-2026 | Präqualifikationsverfahren für Großbatteriespeicher | 2 | energy_storage | - |
| 15 | 597369-2026 | Procédure de mise en concurrence portant sur le soutien à la production d'hydrogène renouvelable ou bas-carbone | 2 | hydrogen | - |
| 16 | 598463-2026 | European medium temperature heat for industrial processes: needs, barriers, and opportunities | 2 | energy_storage | - |
| 17 | 602839-2026 | Procédure de mise en concurrence portant sur le soutien à la production d'hydrogène renouvelable ou bas-carbone | 2 | hydrogen | - |
| 18 | 608830-2026 | Hydrogen Import Terminal (HITW) Terminal-FEED Study | 2 | hydrogen | - |
| 19 | 617173-2026 | Erstsicherung und Entstörbereitschaft (TEO) | 2 | hydrogen | - |
| 20 | 623022-2026 | Wissenschaftliche Begleitung des Elektrolyse-Förderprogramms ("ELY") für UM BW | 2 | hydrogen | - |

## Twenty drawn at random, for comparison

`random.Random(20260912).sample` over the same population **sorted by publication number**, listed by
publication number.

The sample is stable: the same seed over the same window now draws the same twenty, whatever order
the rows were ingested in. The first version of this file sampled the population in database order,
which is ingest order - reproducible only until someone re-ingests the corpus, and reproducible by
accident even then. The twenty below is therefore not the twenty that first version listed.

| Notice | Title as the buyer wrote it | Grade | Domains | Services |
| --- | --- | --- | --- | --- |
| 459421-2026 | Planung EÜ Mosbach BIM | 1 | - | - |
| 464817-2026 | T26/27-009 PEACE PLUS - Habitat for Peace - RETENDER | 1 | - | - |
| 469224-2026 | Audits stratégiques de la mandature 2026-2032 de la commune de Sainte-Marie | 1 | - | - |
| 471619-2026 | Rahmenvereinbarung für die integrierte Projekt- und Produktberatung | 1 | - | - |
| 472348-2026 | Monitor Nationaal Programma Grieppreventie en Nationaal Programma Pneumokokkenvaccinatie Volwassenen | 1 | - | - |
| 478084-2026 | R01917 - Electrical services - REØS, Oslo | 1 | - | - |
| 480193-2026 | Tender for Legal Services to CHC for the commercial exploitation of hydrocarbons | **2** | - | advisory |
| 488938-2026 | Elektroenerģijas piegāde un saražotās elektroenerģijas atpirkšana | 1 | - | - |
| 496336-2026 | Selectie van een marktpartij/consortium voor de ontwikkeling en operationalisering van een aanbod van geïntegreerde zorg | 1 | - (supporting only) | - |
| 524024-2026 | Ortsgemeinde Ober-Hilbersheim - Neubau der Kindertagesstätte "Räuberhöhle" | 1 | - | - |
| 529505-2026 | Servicios para el Apoyo Técnico a la Operación y Administración de los Servicios CIS/TIC en el CESTIC | 1 | - | - |
| 568497-2026 | ID 26FIN127 servizio per il monitoraggio dello studio "TRIAL CLINICO MULTICENTRICO" | 1 | - | - |
| 600667-2026 | Creation and dissemination of a Toolkit for Local and Regional Social Economy Policy Support | 1 | - | - |
| 604532-2026 | Magnet Consulting | 1 | - | advisory |
| 606594-2026 | Europese openbare aanbestedingsprocedure levering integrale BHV-dienstverlening t.b.v. gemeente Venlo | 1 | - | - |
| 612946-2026 | Διακήρυξη Ηλεκτρονικού Διαγωνισμού για το Υποέργο 2: «Εθνικό Πρόγραμμα Απλούστευσης Διαδικασιών» | 1 | - | - |
| 613264-2026 | Dostawa subskrypcji wraz z zapewnieniem usług wsparcia technicznego i gwarancji | 1 | - | - |
| 616238-2026 | CONTRAT DE PERFORMANCE ENERGETIQUE ET DE FOURNITURE D'ENERGIE, D'EXPLOITATION ET DE MAINTENANCE DES INSTALLATIONS | 1 | - (supporting only) | - |
| 619801-2026 | Missions maîtrise d'oeuvre et missions Assistance à Maîtrise d'Ouvrage, de faisabilité et de programmation | 1 | - | advisory |
| 623893-2026 | Levering af ydelser i relation til efter- og videreuddannelsesaktiviteter-EA | 1 | - | - |

## What the comparison shows

**The premise holds.** The top twenty contains the Lubmin grid-connection feasibility study, the
Eidsiva CCS RFI, a hydrogen import terminal FEED study, the Baden-Württemberg electrolysis programme,
two French hydrogen support schemes, a Czech PV-and-battery framework, a German energy park, two
offshore wind notices and a market exploration for district heating. The random twenty contains a
flu-vaccination programme monitor, a Peace Plus habitat retender, municipal strategic audits, a BIM
bridge design, integrated-care procurement, a kindergarten new-build, Spanish military IT support, a
clinical trial monitor, a social-economy toolkit, emergency-response staffing for a Dutch
municipality, Greek administrative simplification, a software subscription and Danish continuing
education. Not one is an Entr opportunity.

**Two notices in the random twenty are energy work, and the rules stage graded both 1.**

- **488938-2026**, Latvia: *Elektroenerģijas piegāde un saražotās elektroenerģijas atpirkšana* -
  electricity supply and repurchase of generated electricity. It matched **nothing at all**, not even
  a supporting term: `energi*` is a prefix and *elektroenerģijas* begins with *elektro*.
- **616238-2026**, France: *contrat de performance énergétique et de fourniture d'énergie,
  d'exploitation et de maintenance des installations* - an energy performance contract. Supporting
  terms only; *performance énergétique* is deliberately not a domain alias, because adding the bare
  adjective would pull in every French building notice.

Neither is work Entr would bid - one is a commodity purchase, the other operations and maintenance -
so the grade is not wrong about relevance. It is wrong about domain, and it is wrong in the same
direction both times: the rules stage cannot see an energy notice written in a language whose words
we have not enumerated. That is the finding in
[0006](../decisions/0006-english-vocabulary-multilingual-corpus.md), turning up twice in twenty
notices drawn at random, and it is why the step 10 audit has to report recall by language.

**The second branch of the illustrative grade is weak.** 480193-2026, legal services for the
commercial exploitation of hydrocarbons, is the only grade 2 in the random twenty, and it got there
on "service term plus a CPV guard code" with no domain term anywhere. If step 7 keeps a branch like
that, it will promote notices on the strength of a consultancy word and a procurement code.

**Three of the four grade-3 notices are GIZ development programmes.** They are long, English and full
of both vocabularies; 523353-2026, the one Entr-shaped notice at the top, reached grade 3 on two
matches. Evidence *count* tracks text length and language, not fit.

**Rank 10 is a public relations contract.** 568229-2026, *Öffentlichkeitsarbeit AQD*, matched hydrogen
and onshore renewables because it is communications work for a hydrogen project. Nothing demotes it:
the exclusion signal is used only for archiving, and no service term matched.
