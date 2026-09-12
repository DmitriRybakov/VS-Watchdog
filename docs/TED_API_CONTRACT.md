# TED API contract

What the live endpoint actually does, so that a change at TED is a change to this document and one
adapter. Everything below was read off real responses between 5 and 12 September 2026, not off
documentation. Where a behaviour surprised us it says so, with the notice that demonstrated it.

The adapter is `src/watchdog/sources/ted/`. The configuration is `config/sources/ted.yaml`. The
recorded responses are `tests/fixtures/ted/`.

## Endpoint

```
POST https://api.ted.europa.eu/v3/notices/search
Accept: application/json
Content-Type: application/json
```

## Authentication

None. The search endpoint is public and takes no API key. There is nothing to put in an environment
variable and nothing to keep out of a log.

## Request shape

| Key | What we send | Notes |
| --- | --- | --- |
| `query` | the expert query | Built by `query.build_query`. See the query language section. |
| `fields` | `REQUESTED_FIELDS` | **Mandatory and non-empty.** An empty or absent list is a 400. |
| `limit` | 250 | **250 is the maximum.** 251 is refused with `SEARCH_EXCEEDS_MAX_LIMIT`. |
| `paginationMode` | `ITERATION` | See paging. |
| `iterationNextToken` | the previous page's token | Omitted on the first request. |
| `onlyLatestVersions` | `true` | Plural. `onlyLatestVersion` and `latestVersionOnly` are both rejected. |
| `page` | 1 | Only on the validate-only call, which does not paginate. |
| `checkQuerySyntax` | `true` | Only on the validate-only call. |

An unrecognised body key is a hard 400 (`JSON parse error: Unrecognized field ... not marked as
ignorable`), not a silent ignore. That is useful: a typo in a request key cannot pass unnoticed.

### `onlyLatestVersions` is worth having

Over a 60-day window: 131,681 notices with it, 145,374 without. About 9% of rows are superseded
versions of notices we would otherwise ingest twice.

**`totalNoticeCount` obeys it too, and that is a trap.** The same query over 5-12 September 2026
reports **217** without `onlyLatestVersions` and **212** with it, and 212 is what iterating actually
yields. `count_notices` therefore sends the same flag the caller will iterate with; a count taken
under different terms looks authoritative and is wrong by the number of superseded versions.

### Counting without reading

One request with `limit: 1` and `page: 1` returns the full `totalNoticeCount` for the query. That is
how `watchdog ted-probe` can say "showing 60 of 212" instead of stopping at the limit in silence.

### `checkQuerySyntax` is a validate-only mode

With it set, a valid request answers 200 with `notices: []` and `totalNoticeCount: null`. It does
**not** enable validation - a malformed query is a 400 either way. What it does is let us check a
query without running it, which is what `validate_query` and `watchdog config validate` use.

## The field list

`fields` is mandatory, and **one unrecognised name fails the whole request**. The 400 lists all 1830
supported names but never says which of ours was wrong, so `TedClient.unknown_fields` parses that
list out of the error message and diffs it locally. That turns the worst failure mode - a silent
empty result set - into a named error in one request.

`watchdog config validate` runs that check plus a query validation. Run it after editing either the
field list or `config/sources/ted.yaml`.

These are the fields we request:

```
publication-number  notice-identifier  change-notice-version-identifier
notice-title  title-proc  title-lot  description-proc  description-lot
buyer-name  buyer-country
place-of-performance  place-of-performance-country-proc  place-of-performance-country-lot
publication-date
deadline-receipt-tender-date-lot       deadline-receipt-tender-time-lot
deadline-receipt-request-date-lot      deadline-receipt-request-time-lot
deadline-receipt-expressions-date-lot  deadline-receipt-expressions-time-lot
deadline-receipt-request
classification-cpv  main-classification-proc  main-classification-type-proc
additional-classification-proc  contract-nature  contract-nature-main-proc
notice-type  form-type  notice-subtype
estimated-value-proc  estimated-value-cur-proc  estimated-value-lot  estimated-value-cur-lot
document-url-lot  links  official-language
```

Three names that look right and are not: there is no `buyer-country`-style
`place-performance-country-lot` (it is `place-of-performance-country-lot`), no `publication-language`
(it is `official-language`), and `classification-cpv` is not split into main and additional (those
are `main-classification-proc` and `additional-classification-proc`).

## The query language

Built by one pure function, `query.build_query`. It filters on publication date, CPV, stage,
contract nature and buyer country. **It never contains an Entr keyword**: TED titles arrive in every
EU language and our vocabulary is English, so a keyword there would destroy recall. Keyword work
happens locally on the original text.

| Rule | Detail |
| --- | --- |
| Dates | `YYYYMMDD` or `today(+/-n)`. An ISO date is refused with `QUERY_INVALID_FIELD_FORMAT` and the allowed pattern. |
| Lists | `field IN (a b c)` - space separated, **not** comma separated. |
| Operators | `=`, `!=`, `~`, `!~`, `IN`, `NOT`, and comparison operators. |
| Errors | Typed: `QUERY_SYNTAX_ERROR`, `QUERY_UNKNOWN_FIELD`, `QUERY_INVALID_FIELD_FORMAT`, `SEARCH_EXCEEDS_MAX_LIMIT`, with a `location` giving line and column. |

**There is no hour.** `publication-date` is a date field, so the overlap between runs can only be
expressed in whole days. The config key is `overlap_days` for that reason; an `overlap_hours` key
would be a lie in a file people read.

### CPV matching is hierarchical - this surprised us

A parent code matches every code beneath it. Demonstrated on notice **597239-2026**, which carries
`71318100` and no other 713 code:

| Filter | Returns 597239-2026 |
| --- | --- |
| `classification-cpv=71318100` | yes |
| `classification-cpv=71318000` | yes |
| `classification-cpv=71310000` | yes |
| `classification-cpv=71000000` | yes |
| `classification-cpv=45000000` | no |

So the configured list is broader than it looks: `71241000` already covers `71241100` and the rest of
its branch. Our local matcher has to reproduce this, and `startswith` on the padded code does not -
`"71318100".startswith("71318000")` is `False`. `watchdog.core.cpv` strips the trailing zeros to get
the significant prefix (`71318000` -> `71318`), with a floor of two digits so that `70000000` becomes
`70` and not `7`.

Re-verified 12 September 2026 over 5-12 September, by querying each child code alone and checking its
results against the parent's: `09310000`, `09320000`, `09330000` (under `09300000`), `09331000`
(under `09330000`), `71314100` and `71314200` (under `71314000`) each returned notices that were
**all** already in the parent's result set. `09330000` was dropped from the configured list on the
strength of it.

### `contract-nature` is an ANY match over the list - this surprised us too

`contract-nature=services` matches if services appears **anywhere** on the notice, not only as the
main nature. It therefore keeps mixed notices rather than hiding them. Measured over 30 days with the
shipped CPV list and stages:

| | Count |
| --- | --- |
| Notices kept by `contract-nature=services` | 873 |
| of those, whose nature list is mixed | 44 |
| of those, whose `contract-nature-main-proc` is **not** services | 34 |
| Notices excluded | 508 |
| of those, mentioning services anywhere | **0** |

The excluded 508 were 388 supplies, 101 works and 19 supplies+works, dominated by CPV `09310000`
Electricity (197) - buying power, not advisory. Example of a notice the filter keeps and a main-nature
filter would drop: **563282-2026**, a French design-and-build contract whose natures are
`[services, works]` and whose main nature is works.

**Consequence for us:** `Tender.contract_nature` keeps TED's procedure-level scalar, but every filter
and facet reads `Tender.contract_natures`, the list. Filtering the scalar would silently drop the 4%
of kept notices whose main nature is something else.

## Paging

`paginationMode: ITERATION`, following `iterationNextToken` until it is absent. Pages do not overlap.
Three things to know:

- The response reports `totalNoticeCount`. There is no `noticeCount`.
- The **last page can be empty and still carry a token**, so an empty batch also terminates the loop.
- A token that does not change is treated as an error rather than looped on.

Requests are **sequential**, one at a time, with a pause between pages. Not two concurrent: the public
API rate-limits at roughly twelve unpaced requests.

## Retry policy

| Condition | Behaviour |
| --- | --- |
| Connect or read timeout | Retry. Separate connect (10s) and read (60s) timeouts. |
| 408, 425, 500, 502, 503, 504 | Retry with bounded exponential backoff plus jitter, from 1s, capped at 120s. |
| 429 | Retry, backing off from **30 seconds**. |
| `Retry-After` header | Honoured if present. **TED does not send one.** |
| Any other 4xx | Never retried. A 400 becomes a `QueryError` naming the offending field. |
| `timedOut: true` | Retried; if it persists, raised. See below. |

Attempts are capped (5 by default). After that the failure is raised as a `TransportError` carrying
the status, the attempt count and the request id.

### A 429 is HTML, not JSON - this surprised us

```
HTTP/1.1 429 Too Many Requests
content-type: text/html
<html><head><title>429 Too Many Requests</title></head>...nginx/1.31.3...</html>
```

No `Retry-After`. Nothing in the client assumes an error body is JSON; the content type is checked
first and logged when it is not what we expected.

### There is no request id

No `x-request-id`. The only correlation id available is CloudFront's `x-amz-cf-id`, which is what we
log in its place.

## `timedOut` - the failure that looks like a success

Every response envelope carries a `timedOut` boolean:

```json
{ "notices": [...], "totalNoticeCount": 2593, "iterationNextToken": "AAAB...", "timedOut": false }
```

`timedOut: true` arrives with **HTTP 200 and a partial `notices` array**. It is an incomplete page,
not the end of the results. The client retries it within the normal attempt budget and then raises
`InvalidPayloadError`.

**Step 4 must not advance the watermark when any page in the run timed out.** A short window that
looks complete is exactly how a notice gets missed for good.

## Field mapping

| TED field | Tender field | Notes |
| --- | --- | --- |
| `publication-number` | `source_id` | Identity is (source, source_id). `notice-identifier` is a UUID and is not used as the key. |
| `change-notice-version-identifier` | `source_version` | Only present on notices that have been changed. |
| `links.html.ENG` | `source_url` | Language keys in `links` are **UPPER CASE**. |
| `notice-title` | `title`, `title_language` | TED's composed display title. See below. |
| `title-proc` | `title_native`, `title_native_language` | The buyer's own title. What screening reads. |
| `description-proc` | `description` | Display description, in the chosen language. |
| `title-proc`, `description-proc`, `description-lot` | `screening_blocks` | Every language variant, labelled. See docs/decisions/0002. |
| `buyer-name` | `buyer_name` | `{lang: [name]}` - a list even for one buyer. |
| `buyer-country` | `buyer_country` | ISO 3166-1 alpha-3, in a list. Named for display by `core.countries`. |
| `place-of-performance` | `place_of_performance` | NUTS regions and ISO-3 countries mixed in one array. Stored as given; see docs/decisions/0003. |
| `place-of-performance-country-proc` | `place_of_performance_country` | Where the work happens, at country level, deduplicated. A separate question from who is buying. |
| `publication-date` | `published_date` | `2026-06-15+02:00`: a date with an offset and no time. We keep TED's own calendar date, so a cross-check on the website shows the same day. |
| deadline chain | `deadline`, `deadline_date`, `deadline_source`, `deadline_type` | See below. |
| `main-classification-proc` | `cpv_main` | Null unless `main-classification-type-proc` is `cpv`. |
| `additional-classification-proc` | `cpv_additional` | |
| `classification-cpv` | `cpv_all` | The deduplicated union, including lot-level codes. What matching reads. |
| `contract-nature-main-proc` | `contract_nature` | The procedure-level scalar. |
| `contract-nature` | `contract_natures` | The deduplicated list. **What every filter reads.** |
| `form-type` -> `notice-type` | `notice_stage` | See the stage table. |
| `notice-subtype` | `notice_subtype` | Opaque passthrough: `"16"`, `"E1"`, `"T01"`. Never interpreted. |
| `estimated-value-proc` + `estimated-value-cur-proc` | `estimated_value`, `currency` | Notice level only. A value with no currency is not stored. |
| `document-url-lot` | `documents_url` | |
| `official-language` | `languages` | |
| whole payload | `raw` | Lot arrays, every language variant, and the fields we chose not to read. |

Any field that is absent, empty or unparseable becomes `None`. A notice that cannot be mapped raises
`MappingError` carrying its publication number, and the caller quarantines it rather than dropping it.

### The two title fields

`notice-title` is composed by TED as `<country> - <CPV label> - <buyer's own title>` and supplied in
**all 24 EU languages** on every notice (24/24 in every sample). Only the first two segments are
translated. So "prefer the English value" always resolves, and `title_language: "eng"` records
nothing useful.

`title-proc` is the buyer's own title in the buyer's own language. Present on **750 of 750** notices
sampled across all form types with no CPV filter. Usually one language key; **12 of 750 had two**,
where the buyer genuinely published bilingually (Belgian fr/nl, Danish da/en, Swiss de/fr).

Screening reads `title-proc`, never the composed title, because the composed title's CPV label is the
code we filtered on restated - it would make an activity rule fire on every notice and read as
independent evidence. The full reasoning is docs/decisions/0002.

We do **not** parse the buyer's title out of the composed one. Where both exist the third en-dash
segment equalled `title-proc` on 55 of 55, but the separator is not reliable: 51 of 55 titles split
into three segments and four split into four or six, because buyers' own titles contain en dashes.
A parser that is ambiguous 7% of the time, guarding a path that never ran in 750 notices, is worse
than nothing.

### The two multilingual shapes

Both occur in the same response, on different fields:

| Shape | Fields |
| --- | --- |
| `{lang: "text"}` | `notice-title`, `description-proc`, `title-proc` |
| `{lang: ["text", ...]}` | `title-lot`, `description-lot`, `buyer-name`, `title-part` |

Language keys are **lower case** three-letter codes (`eng`, `fra`). In `links` the same languages are
**UPPER CASE** (`ENG`, `FRA`). One accessor in the mapper copes with both shapes.

### The deadline chain

Read in this order. Each entry establishes what the deadline *means*, which is a different fact from
when it is; both are stored, in `deadline_source` and `deadline_type`.

1. `deadline-receipt-tender-date-lot` + `deadline-receipt-tender-time-lot` -> `TENDER_SUBMISSION`
2. `deadline-receipt-request-date-lot` + `-time-lot` -> `PARTICIPATION_REQUEST`
3. `deadline-receipt-expressions-date-lot` + `-time-lot` -> `EXPRESSION_OF_INTEREST`
4. `deadline-receipt-request` -> `UNKNOWN`

**Never read: `deadline` and `deadline-date-lot`.** They are a different deadline entirely. On
**597197-2026** they say 2026-08-18 while the tender deadline is 2026-09-07 - three weeks early.
Using them as a fallback would write a wrong date that looks right.

`deadline-receipt-request` is a **union** of all three kinds and does not say which it is, so it is a
good last resort and a poor first choice. It is the only deadline a qualification system carries
(**597259-2026**), and there `deadline_type` is honestly `UNKNOWN`.

Date and time arrays of one field pair are combined by position, but only when they are the same
length. Across 1250 sampled notices they always were, and a date field never arrived without its time
field. If the lengths ever disagree, the time is dropped rather than guessed.

**A date with no time leaves `deadline` null** and sets `deadline_date` only. No 23:59 is invented: an
made-up time would look real to a filter and to an export. The register computes urgency from
`deadline_date`; the detail page shows a time only when there is one.

Where lots have different deadlines the **earliest** wins, `multi_lot` is set and the full set stays
in `raw` (**613229-2026**, 74 lots, two distinct dates).

### The stage vocabulary

| `form-type` | `notice-type` seen | `NoticeStage` |
| --- | --- | --- |
| `planning` | `pin-only`, `pin-buyer`, `pin-rtl`, `pin-tran` | `PRIOR_INFORMATION` |
| `consultation` | `pmc` | `MARKET_CONSULTATION` |
| `competition` | `cn-standard`, `cn-desg`, `qu-sy` | `CONTRACT_NOTICE` |
| `result` | `can-standard` | `AWARD` |

`form-type` is the reliable axis and separates prior information from market consultation cleanly.
`notice-type` is only consulted when `form-type` says nothing.

### Lot arrays are not aligned

Notice **598884-2026** has six `estimated-value-lot` entries and **one** `estimated-value-cur-lot`.
Its `classification-cpv` has 35 entries for six lots, and `place-of-performance` mixes NUTS regions
and country codes. Nothing zips lot arrays together. Notice-level values are mapped, lot arrays stay
in `raw`, and `multi_lot` is set.

`multi_lot` is decided on the raw array lengths, before deduplication: six identical lot descriptions
are still six lots (**406326-2026**).

### The two geography fields

Who is buying and where the work happens are different questions, and TED answers them in different
fields. Both are mapped, and neither is derived from the other.

| Field | Source | Stored as |
| --- | --- | --- |
| `buyer_country` | `buyer-country` | One ISO-3 code. Where the buying organisation sits. |
| `place_of_performance_country` | `place-of-performance-country-proc` | Every distinct ISO-3 code, deduplicated. Where the work happens. |
| `place_of_performance` | `place-of-performance` | The raw mixed NUTS-and-country codes, unresolved. |

**619675-2026** is the case that makes this necessary: `buyer-country` is `FRA` and
`place-of-performance-country-proc` is `AGO` - a French buyer procuring a solid-waste and circular
economy roadmap for Luanda Province. With one country field it would be filed as a French tender and
lost to anyone looking for work outside the EU, or lost to anyone filtering on EU buyers. It is
recorded as `tests/fixtures/ted/buyer_in_one_country_work_in_another.json`.

The procedure-level field is the one to read: it was present on all twelve fixtures while
`place-of-performance-country-lot` was absent on one, and the lot-level field is only consulted when
the procedure-level one says nothing. It repeats once per lot - **598884-2026** carries `SWE`
twenty-one times - so deduplication is not optional.

Several distinct countries are all kept; the notice really does span them, and `Tender.multi_country`
says so. That flag is derived from the stored list rather than being a column of its own, so the two
cannot disagree. `Tender.crosses_border_from_buyer` answers the 619675-2026 question directly.

A country code we cannot name is still stored. This is a fact from the source, not a configured
value: `config/sources/ted.yaml` refuses a country the register cannot name, but a notice never gets
edited to suit our map. `core.countries.country_names` falls back to showing the code, which is
visible and is the moment to add the country.

## The monthly recall audit

The audit query profile drops **both** the CPV filter and the contract-nature filter, keeping only the
stages and the window (`QueryProfile.AUDIT`). Both are being measured, so both have to go.

The sample must be reviewed on each notice's **actual scope**, not on its CPV label. The 7-day
exclusion read that produced the contract-nature conclusion above was done by CPV label only, which
makes "the services filter costs us nothing we want" a reasonable working conclusion and not a proven
one. The audit is what would prove it.

Rough volumes, shipped configuration, 7-day window:

| Query | Notices |
| --- | --- |
| CPV only | 627 |
| + stages | 347 |
| + services (the daily query) | **218** |
| Audit profile (stages only) | 4,147 |

About 30 notices a day to screen.

## What to check first if results change

1. **Nothing comes back at all.** Run `watchdog config validate`. A rejected field name or a query
   error shows up as an empty result set, not as a failure, which is exactly what that command exists
   to catch.
2. **Fewer notices than expected.** Check the run for a timed-out page. A `timedOut: true` response is
   a 200 with partial results and must not have advanced the watermark.
3. **A notice you expected is missing.** Check its `contract-nature` list, not its main nature, and
   check whether its CPV codes are only on a lot - `cpv_all` is the union, `cpv_main` often is not.
   If you are filtering by geography, check which of the two country fields you are filtering on.
4. **Deadlines look wrong.** Check `deadline_source` on the row. If it says `deadline-receipt-request`
   the meaning is `UNKNOWN` and it may not be a tender submission deadline at all.
5. **Titles look like CPV labels.** Something is reading `title`, the composed display title, where it
   should read `title_native` or `screening_blocks`.
6. **A run dies on 429.** The client is sequential with a pause between pages; if something else is
   calling the API at the same time, that budget is shared.

## Known quirks, collected

- `fields` is mandatory; one bad name fails everything and the error does not say which.
- `limit` maximum is exactly 250.
- Dates in the query are `YYYYMMDD` only.
- CPV filtering is hierarchical; `contract-nature` filtering is an ANY match over a list.
- `timedOut: true` arrives with HTTP 200.
- A 429 body is HTML from nginx, with no `Retry-After`.
- The last page can be empty and still carry an iteration token.
- Language keys are lower case in the text fields and upper case in `links`.
- `publication-date` is a date with an offset and no time; `date.fromisoformat` cannot parse it.
- `estimated-value-proc` is a string; `total-value` is an integer.
- `notice-identifier` is a UUID and is not the publication number.
