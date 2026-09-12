# Recorded TED responses

Tests never call the network; they read these files. Each one is a single notice as the live search
endpoint returned it on 12 September 2026, requested with exactly the field list in
`REQUESTED_FIELDS` (`src/watchdog/sources/ted/mapper.py`). Nothing has been edited: the bodies are
public procurement notices, so there is nothing to redact, and trimming them would destroy the very
shapes they exist to prove.

Each file is named for the one thing it proves. If you add one, name it the same way.

| File | Notice | What it proves |
| --- | --- | --- |
| `notice_title_in_24_languages` | 406326-2026 | `notice-title` is composed by TED as "country - CPV label - buyer's title" and supplied in all 24 EU languages, so "prefer English" always resolves and records nothing. Also carries six identical lot descriptions and no deadline of any kind. |
| `bilingual_title_proc` | 622963-2026 | `title-proc` usually has one language key but can have two, when the buyer genuinely published in two. Both are real source text and both are screened. |
| `description_proc_is_a_string_description_lot_is_a_list` | 612558-2026 | The two multilingual shapes in one response: `{lang: "text"}` and `{lang: ["text", ...]}`. |
| `cpv_child_code_matches_parent_filter` | 597239-2026 | Carries `71318100` and no other 713 code, and TED returns it for `71318000`, `71310000` and `71000000` but not `45000000`. The evidence that CPV matching is hierarchical. |
| `six_lots_with_unaligned_parallel_arrays` | 598884-2026 | Six `estimated-value-lot` entries against one `estimated-value-cur-lot`. Lot arrays must never be zipped together. |
| `lots_with_different_deadlines` | 613229-2026 | 74 lots whose tender deadlines are not all the same. The earliest wins and `multi_lot` is set. |
| `no_deadline_of_any_kind` | 612645-2026 | None of the deadline fields are present. Every deadline field on the tender stays null. |
| `mixed_nature_works_with_services` | 563282-2026 | `contract-nature-main-proc` is works while `contract-nature` lists both services and works. TED's own services filter keeps it, and so must ours. |
| `buyer_in_one_country_work_in_another` | 619675-2026 | `buyer-country` is `FRA` and `place-of-performance-country-proc` is `AGO`: a French buyer procuring a study for Luanda Province. Why the two geography fields are separate. |
| `form_type_competition_is_a_contract_notice` | 596416-2026 | `form-type: competition` maps to `CONTRACT_NOTICE`. The general-purpose fixture for field-by-field mapping. |
| `form_type_planning_is_prior_information` | 596426-2026 | `form-type: planning` maps to `PRIOR_INFORMATION`. |
| `form_type_consultation_is_a_market_consultation` | 597019-2026 | `form-type: consultation` maps to `MARKET_CONSULTATION`, kept separate from prior information. |

## Two cases with no fixture, on purpose

**A deadline date with no time of day.** Not observed once in 1250 sampled notices: every notice
carrying `deadline-receipt-tender-date-lot` also carried `deadline-receipt-tender-time-lot`, and the
two arrays were always the same length. The mapper still handles it - leaving `deadline` null rather
than inventing 23:59 - and the test constructs the payload by hand. If TED ever starts sending one,
record it here.

**A notice that cannot be mapped.** Constructed in the tests, because a real one would be a bug at
TED rather than a shape to depend on.

## Re-recording

`tests/conftest.py` exposes `load_ted_fixture(name)` and a `ted_fixture_name` fixture that runs over
every file here, so a new fixture is exercised by the mapper without any other change.
