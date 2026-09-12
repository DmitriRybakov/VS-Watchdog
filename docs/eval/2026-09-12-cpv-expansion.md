# TED CPV measurement, 5-12 September 2026

Evidence behind decision record
[0004](../decisions/0004-cpv-is-a-recall-filter-not-a-relevance-signal.md) and the CPV list in
`config/sources/ted.yaml`. Kept here rather than under `data/`, which is disposable by design.

The full run is `2026-09-12-ted-cpv-window.txt`: four complete queries over the window, every notice
paged to the end, 0 unmappable. The scripts that produced it were throwaway and are not kept; the
queries they sent are printed verbatim at the top of that file and can be re-sent by hand.

**Window.** `publication-date >= 20260905 AND publication-date <= 20260912`, explicit dates rather
than `--days`, so the probe's window fix could not contaminate the before-and-after. 5 September was
a Saturday and TED published nothing that day, so this window and a corrected 7-day window contain
the same notices.

## Why the measurement happened

A conclusion was drawn from `watchdog ted-probe --days 7 --limit 60`: that the week contained no
hydrogen, CCS, offshore wind or solar notices. `--limit` truncates in publication-number order, so
those 60 were the 60 lowest-numbered notices of 212 - the start of the window, not a sample of it.
152 notices, 72% of the week, were never printed, and every hydrogen and solar notice was among them.

Tested against all 212:

| Claim | Verdict |
| --- | --- |
| no hydrogen | **false** - 623022-2026, scientific accompaniment of Baden-Württemberg's electrolysis funding programme, was already being collected |
| no CCS | **true of what we collected, false of the week** - 626265-2026, Eidsiva Bioenergi's "CCS as a service" RFI, existed and the query could not see it |
| no offshore wind | **true in substance** - no wind CPV in the 212; two small-wind works notices exist in the week and are excluded by the contract-nature filter |
| no solar | **false** - five in the 212, including 626958-2026, a Nîmes roof-solarisation strategy study |

## The comparison

| Run | CPV codes | Contract nature | Notices |
| --- | --- | --- | --- |
| A | 14 (as configured before) | services | 212 |
| B | +6 candidates | services | 224 |
| C | 14 | any | 341 |
| D | +6 candidates | any | 386 |

The six candidates were `09123000`, `65400000`, `71334000`, `45251000`, `31121000`, `76000000`.
B ⊇ A exactly: the expansion lost nothing. Without the nature filter the six candidates reach 45 new
notices; **the services filter removes 33 of those 45**, mostly commodity gas purchasing.

## Reconciliation of the 12 new notices

Read off the `caught by` lines of the saved run, one row per notice. This corrects an earlier summary
that said `71334000` produced 5 of the 6 noise items; it produced **4 of 6**, and 626032-2026 was
counted once, not twice. The per-code table was right and the sentence about it was wrong.

| Notice | Caught by | Judgement |
| --- | --- | --- |
| 612982-2026 railway drainage maintenance, DNK | `76000000` | noise |
| 614418-2026 electrical supply upgrade, water treatment plant, ESP | `31121000` | noise |
| 615077-2026 fire station building services, DEU | `71334000` | noise |
| 615124-2026 LNG regasification station and supply, POL | `09123000` + `76000000` | arguable |
| 616527-2026 EBN coring services DPS, NLD | `76000000` | arguable, provisional |
| 617173-2026 GASCADE standby and fault response, DEU | `76000000` | arguable, provisional |
| 623910-2026 S18 tunnel ventilation planning, AUT | `71334000` | noise |
| 626032-2026 Galway SEAI Pathfinder design team, IRL | `71334000` | **opportunity** |
| 626128-2026 Pécs market-square design, HUN | `71334000` | noise |
| 626265-2026 Eidsiva "CCS as a service" RFI, NOR | `45251000` | **opportunity** |
| 627158-2026 Ørsted Achilles qualification system, DNK | `76000000` | arguable |
| 628612-2026 pump and pumping-station maintenance, NLD | `71334000` | noise |

The relevance labels are provisional. GASCADE's standby scope and EBN's coring DPS are the right
industries but are not established as advisory work; collection takes them and screening decides.

### Marginal contribution, given the other codes

| Code | Notices caught | Caught **only** by this code | Outcome |
| --- | --- | --- | --- |
| `71334000` | 5 | 5 | added, provisionally |
| `76000000` | 5 | 4 | added |
| `45251000` | 1 | 1 | added |
| `31121000` | 1 | 1 | **rejected** - its one notice is noise, and generating sets are equipment |
| `09123000` | 1 | 0 | added as a bet, not a gain |
| `65400000` | 0 | 0 | **rejected** - its notices already arrive via their 71 codes |

## Redundancy: eight codes checked, eight covered

Each child was queried alone over the same window, same stages, nature and countries, and its results
compared with the parent's. Zero notices reachable only by the child, in every case.

| Child | Parent in the list | Notices for the child alone | Not covered by the parent |
| --- | --- | --- | --- |
| `09310000` | `09300000` | 5 | 0 |
| `09320000` | `09300000` | 3 | 0 |
| `09330000` | `09300000` | 4 | 0 |
| `09331000` | `09330000` | 4 | 0 |
| `71314100` | `71314000` | 5 | 0 |
| `71314200` | `71314000` | 7 | 0 |
| `71314300` | `71314000` | 10 | 0 |
| `79411000` | `79400000` | 18 | 0 |

`09330000`, `71314300` and `79411000` were in the configured list and were removed on this evidence.

## Result

| | CPV codes | `totalNoticeCount` | Paged | Unmappable |
| --- | --- | --- | --- | --- |
| Before | 14 | 212 | 212 | 0 |
| **After** | **15** | **223** | **223** | 0 |

**Net +11, gained 11, lost 0.** Removing the three redundant codes cost nothing, exactly as the
child-versus-parent check predicted.

Gained: 612982, 615077, 615124, 616527, 617173, 623910, 626032, 626128, 626265, 627158, 628612.

## What `totalNoticeCount` does

Worth recording, because it was wrong in a way that looked right. A count request that omits
`onlyLatestVersions` reports **217** for the before-query where iterating the same query yields
**212**; the five are superseded versions. `TedClient.count_notices` therefore sends the same flag the
caller will iterate with, and count and listing now agree exactly. See `docs/TED_API_CONTRACT.md`.
