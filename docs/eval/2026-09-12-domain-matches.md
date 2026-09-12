# What the domain rules match, 1 July - 11 September 2026

The other side of [the archive audit](2026-09-12-rules-archive.md). That one asked what the exclusions
archive; this one asks what the domain rules claim about the 1,949 notices that go on to be assessed.
A wrong domain match is false evidence in the model prompt and a wrong input to the score, and nothing
downstream can tell it from a right one.

The full run is `2026-09-12-domain-match-audit.txt`: rule set **version 2 as committed**, against the
2,158 TED notices ingested for that window, before the changes this audit caused. The script was
throwaway; the method is described at the head of each part of the run file.

## The histogram

| Rule | Label / strength | Notices | In a building notice | Aliases |
| --- | --- | --- | --- | --- |
| `domain_supporting_terms` | energy_other / **supporting** | 276 | 42 | energi\* 190, energy 37, environment 16, climate 16, power 8, carbon 6, fuel 2, cable 1 |
| `domain_onshore_renewables` | onshore_renewables / medium | 49 | 4 | photovolta\* 14, fotowolta\* 12, renewable energy 11, fotovolta\* 6, zonnepane\* 2, solarisation 1, solar 1, renewables 1 |
| `domain_energy_system` | energy_other / medium | 41 | **16** | energieeffizienz\* 10, energy efficiency 10, rénovation énergétique 8, energetische sanierung 7, energy master plan 2, audit énergétique 2, assainissement énergétique 1, off-grid 1 |
| `domain_energy_transition` | industrial_decarb / medium | 23 | 5 | decarbonisation 7, dekarbonisierung\* 5, energy transition 3, transition énergétique 2, low-carbon 2, energiewende\* 2 |
| `domain_hydrogen` | hydrogen / **high** | 15 | 2 | wasserstoff\* 5, **H2 4**, hydrogen 2, hydrogène 2, elektrolyse\* 1, green hydrogen 1 |
| `domain_industrial_heat` | energy_other / medium | 15 | 4 | wärmenetz\* 6, réseau de chaleur 5, district heating 3, réseaux de chauffage 1 |
| `domain_grid_transmission` | grid_transmission / medium | 14 | 1 | umspannwerk\* 5, electrification 4, interconnector 1, HVDC 1, grid connection 1, high voltage 1, substation 1 |
| `domain_energy_storage` | energy_storage / high | 5 | 0 | BESS 4, energy storage 1 |
| `domain_ccs_co2` | ccs_co2 / **high** | 3 | 0 | CO2 3 |
| `domain_low_carbon_industry` | industrial_decarb / high | 3 | 0 | industrial decarbonisation 2, green steel 1 |
| `domain_renewable_fuels` | renewable_fuels / high | 3 | 0 | biogas\* 1, ammonia 1, bioenergy 1 |
| `domain_ccs` | ccs_co2 / **high** | 2 | 0 | CCS 2 |
| `domain_offshore_wind` | offshore_wind / high | 2 | 0 | havvind\* 1, offshore wind 1 |
| `domain_renewable_fuels_saf` | renewable_fuels / high | 1 | 0 | SAF 1 |
| `domain_grid_hvac`, `domain_grid_hv`, `domain_energy_storage_ess`, `domain_offshore_wind_acronyms`, `domain_low_carbon_industry_dri` | — | **0** | — | — |

"In a building notice" counts notices that also carry building-design or building-profession
evidence. It is a screen for reading, not a verdict: all four `domain_industrial_heat` notices flagged
by it are real heat-network jobs that happen to be procured under German planning law.

## What is wrong

### 1. H2 was a document label, not hydrogen — four of four matches false, at high strength

Five occurrences in ten weeks. Three are the same Romanian barracks design notice in successive
versions (490197, 567045, 572323), whose description lists the demolition of *PAVILIOANE HI, H2, H3,
H4*. One is a Norwegian procurement-consultancy notice whose resource table has a row *"H2. Senior:
Procurement manager"*. The fifth, 472903-2026, is the real one — and it matches `wasserstoff*`
independently, so the alias never once contributed a match we needed.

`H2` was also the only ambiguous short alias in the file with no context requirement. SAF, CO2, HV,
HVAC, ESS, IAC, OSS and DRI all had one.

### 2. Two of four CCS/CO2 matches false, also at high strength

- **494002-2026**, Spain: *"Oficina Técnica para apoyar al CCS, en la implantación del Proyecto ODIN —
  Optimización y Digitalización Integral de Negocio"*. CCS is the buyer's own internal unit inside a
  digitalisation programme.
- **494007-2026**, Romania: afforestation designed to *neutraliza emisiile de CO2 produse de traficul
  rutier* — offsetting road-traffic emissions by planting trees. The context guard passed because
  **"transport" is a word in Romanian too**, and removing it alone would not have fixed the notice:
  **"carbon" is also the Romanian word**, present as *dioxid de carbon*.

A context word that is a cognate in the notice's own language is not a guard. The mechanism was
designed against English ambiguity and is being applied to a 24-language corpus.

### 3. `domain_energy_system` leaked: 16 of its 41 notices were building refurbishments

German school, hospital-radiology, sports-hall, fire-station, swimming-pool and kindergarten jobs
where *Energieeffizienz* or *energetische Sanierung* sits in a list of design requirements beside fire
safety, accessibility and acoustics — 460638, 476058, 479798, 484189, 502486, 529662, 559918, 566229,
573705, 577382, 578494, 602281, 610924, 611739, 617948 and two Irish building-services frameworks.
`profile.yaml` settles the case: *"Sustainability or net-zero language attached to unrelated
procurement — out of domain."*

One of them was self-inflicted: `rénovation énergétique`, added after the archive audit to rescue
456280-2026, fires on 474558-2026, a French contract to administer housing-grant applications.

### 4. A roof array made four building contracts read as solar notices

535783, 545866, 587128 and 611739 are building-services design jobs where the roof gets a PV array —
in 587128 built by the city utility, i.e. not even in the procured scope.

### 5. Supporting terms fire on boilerplate

`environment` is the worst: *"an external professional environment"*, *"an enabling environment"*, *"a
research environment"*, *"ICT environment"* — the ecological sense is absent from most of its 16
notices. `climate` gets *"climate stress"* in a Tanzanian human-wildlife conflict programme; `power`
matches the company name *RWE Power AG* in a geothermal drilling notice. No route effect and no domain
established, so this is **left alone deliberately** — but it is what the register's evidence column
will show, and an evidence column that says "matched: environment" on a wildlife programme teaches
people to stop reading it. That is a register-design problem, carried into step 6.

## What is right

Two hypotheses did not survive contact with the data, and the negative results are worth as much as
the positive ones.

**`energi*` is clean.** 985 occurrences across **101 distinct surface forms, every one of them an
energy word** — *energii, energie, energia, energiezentrale, energiebilanzierung, energiebesparing,
energideklarationer, energiahatekonysagi, energiledningssystem, energinet,
energimarknadsinspektionen*. Nothing about something else. The other prefixes are equally tight:
`photovolta*` 5 forms, `fotowolta*` 6, `wärmenetz*` 5, `umspannwerk*` 5 including genitive and plural,
`wasserstoff*` 3, `dekarbonisierung*` 3.

**No heating or ventilation contract read as grid work.** `domain_grid_hvac` and `domain_grid_hv`
matched nothing at all in ten weeks. All 14 `domain_grid_transmission` matches are genuinely
electrical: 220/380 kV substations, an HVDC multiterminal market consultation, TenneT's high-voltage
grid expansion, the GREGY interconnector EIA, offshore substation platforms. The only arguable one is
523981-2026, where `grid connection` matched *"a high pressure gas grid connection pipeline"*.

**Five rules matched nothing**, and they are precisely the ambiguity-guarded acronyms: HVAC, HV, ESS,
IAC/OSS, DRI. Their guards are untested by this corpus, so H2 and CO2 above are the only evidence we
have about how that mechanism behaves in the wild — and in one case it was absent and in the other it
was defeated by a cognate.

## The sample: five at random from each of the top five rules

`random.Random(20260912)` over the sorted match list. Full spans and descriptions in part 2 of the run
file.

| Rule | Notice | Alias | Verdict |
| --- | --- | --- | --- |
| supporting | 476229 IRL, statewide energy conservation framework | `energy` | right |
| supporting | 602448 CZE, *energetické služby metodou EPC* (energy performance contracting) | `energi*` | right |
| supporting | 538260 DEU, geothermal exploration borehole | `power` | **wrong word** — matched *RWE Power AG* |
| supporting | 507459 DEU/GIZ, human-wildlife conflicts in Tanzania | `climate` | **boilerplate** |
| supporting | 459160 IRL, Energy Master Plan | `energy` | right |
| renewables | 607607 POL, *Klaster Energii* — PV, storage, hydrogen store | `fotowolta*` | right |
| renewables | 618677 CZE, *FVE Slavos Slaný* — new PV plants | `fotovolta*` | right |
| renewables | 587128 DEU, new school canteen, HOAI building services | `photovolta*` | **wrong** — the array is the utility's scope |
| renewables | 612363 DEU, works specification for PV on Bundeswehr buildings | `photovolta*` | right |
| renewables | 601219 POL, design and installation of PV panels | `fotowolta*` | right |
| energy system | 582569 DEU/GIZ, energy efficiency and renewables in Kosovo | `energy efficiency` | right |
| energy system | 474558 FRA, housing-grant application administration | `rénovation énergétique` | **wrong** |
| energy system | 484189 DEU, new secondary-school building, electrical design | `energieeffizienz*` | **wrong** |
| energy system | 595676 DEU/GIZ, on- and off-grid regulation and market development | `off-grid` | right |
| energy system | 577382 DEU, leisure-pool refurbishment, general planning | `energieeffizienz*` | **wrong** |
| transition | 608440 DEU/GIZ, just transition in Mexican industry | `decarbonization` | right |
| transition | 626032 IRL, Galway SEAI Pathfinder | `decarbonisation` | right |
| transition | 609078 IRL, MTU estate energy retrofit management | `decarbonisation` | right |
| transition | 486533 IRL, building-services engineering framework | `decarbonisation` | borderline |
| transition | 615031 DEU, Stadtwerke Essen heat-network optimisation | `dekarbonisierung*` | right, the strongest in the set |
| hydrogen | 585315 DEU, structural design for electricity and hydrogen civil structures | `wasserstoff*` | right domain |
| hydrogen | 472903 DEU, *Reallabor Wasserstoff* | `wasserstoff*` | right |
| hydrogen | 547488 DEU/GIZ, Green Hydrogen Investments in India | `green hydrogen` | right |
| hydrogen | 602839 FRA, support scheme for hydrogen by water electrolysis | `hydrogène` | right |
| hydrogen | 572323 ROU, Babadag barracks infrastructure design | **`H2`** | **wrong, high strength** — *PAVILIOANE HI, H2, H3, H4* |

## What changed: rule set version 3

| Change | Notices that lost a domain match | Route changes |
| --- | --- | --- |
| `H2` removed from `domain_hydrogen` | 4 | 0 |
| `CCS` split into `domain_ccs_acronym`, context `[carbon, capture, storage, CO2]` | 1 | 0 |
| `CO2` context cut from five words to `[capture, storage]` | 1 | 0 |
| Efficiency and retrofit terms split into `domain_energy_efficiency`, blocked by building-design vocabulary | 15 | 3 |
| Photovoltaic prefixes split into `domain_photovoltaic`, same blocking list | 5 | 0 |

**27 notices changed their domains, 24 lost all domain evidence, 3 moved `ASSESS` →
`ARCHIVE_CANDIDATE`** (535783 school campus sustainability coordination, 610924 Witten theatre
refurbishment, 617948 Friedrich-Ebert-Schule structural design). Archived count 209 → 212.

**None of the 24 is energy work.** Four are the H2 label cases, two are the false CCS and CO2 cases,
and the other eighteen are German building-design contracts: a hospital radiology department, a school
sports hall's building physics, a leisure pool, two fire stations, three kindergartens and schools, a
gymnasium extension, a school canteen. Three notices lost one match and kept another: 486533 and
626032 keep `industrial_decarb` through *decarbonisation*, and 497122-2026 keeps `energy_storage`.

### Why a blocking list and not a companion

Recorded as decision [0007](../decisions/0007-a-domain-term-can-be-blocked.md); the measurement is
here.

The instruction was to require a companion. It was tried first and measured, and it fails its own
acceptance test: requiring an energy-subject word alongside the retrofit vocabulary takes the domain
away from **28** notices, and several of those are real energy advisory work — 471520-2026, an energy
audit of a county's building portfolio feeding an investment programme; 538632-2026, a Belgian
framework for *"missions variées liées à l'énergie"*; 476162 and 476229, the Irish OPW energy
specialists framework; 479223-2026, boiler-house audits of public buildings.

The reason is that the discriminator is not whether the notice mentions energy. They all do. It is
whether the procured work is building design. Only a negative condition can express that, so `Rule`
gained a `blocked_by` list: words that stop a rule counting when they appear anywhere in the notice.
It is the mirror of `requires_companion` and uses the same machinery. The list is kept to fee-schedule
and profession vocabulary — HOAI, Leistungsphasen, LPH, Objektplanung, Tragwerksplanung,
Generalplanung, Bauleitung and the architect family — so it cannot silently kill an industrial
decarbonisation study that happens to mention a building.

### The measured cost

- **497122-2026** is a genuine energy park — a 110 kV grid connection for photovoltaics and battery
  storage — whose planning scope is written in German fee-schedule vocabulary. It loses its
  photovoltaic match and keeps its domain through battery storage. One of five photovoltaic blocks is
  wrong.
- **474558-2026**, the housing-grant administration contract, still matches `rénovation énergétique`.
  It is not a building-design notice, so nothing here reaches it. That residual is a
  topic-versus-subject problem rather than a building problem, and no list of words solves it.
- **456280-2026** keeps its domain match and stays out of the archive: it has no building-design
  vocabulary, so the rescue added after the last audit survives this one.

Nothing was done about the supporting-term boilerplate, on instruction and for the right reason: it
establishes no domain and changes no route. It is carried into the register work as a display problem.
