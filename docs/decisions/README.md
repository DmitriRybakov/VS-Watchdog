# Architecture decision records

One file per decision, named `NNNN-short-title.md`. Record the context, the decision and the
consequence - especially decisions that become expensive to reverse.

## Outstanding

- **During an outage, clicking Dismiss while the filter input has focus triggers its change request,
  so the error banner reappears and Dismiss reads as not working.**

- **Intermittent timeout in the CLI-output test fixture; cause unconfirmed. Verify environment
  isolation and identify the slow command before changing the timeout.**

- **The Supabase database password must be rotated before anyone else is given the site address.**
  `watchdog config show` printed the whole `DATABASE_URL`, password included, before that leak was
  fixed. It was run against the hosted database on a laptop, so the password is in terminal
  scrollback and in whatever that terminal's buffer has been copied into. The leak is closed and
  `tests/integration/test_secrets_never_reach_output.py` now checks every command's output, but a
  password that has been printed is a password that is out. Rotating it is three steps in order:
  a new database password in Supabase, then `DATABASE_URL` updated in the Render environment, then
  `deploy/.env.render` updated to match. A Render deploy follows the variable change; nothing in
  the code changes.

- **THE RULE SET IS FROZEN AT VERSION 3 until the step 10 audit.** Step 7 tunes a scoring policy, and
  tuning against a moving vocabulary means never knowing whether a change in the distribution came
  from the policy or from the rules. Two exceptions, both narrow: a rule that crashes or corrupts, and
  a false positive at **high** strength, which distorts scoring directly - the H2 case, where four of
  four matches were document labels, is the shape that qualifies. Everything else goes on this list
  and waits for human review decisions to be judged against, instead of a reading of samples.

  Known residuals, all deliberate, none of them qualifying for the exceptions:

  - **474558-2026** matches `rénovation énergétique` on a French contract to administer housing-grant
    applications. It is not a building-design notice, so the blocking list in 0007 does not reach it.
    This is topic versus subject - the notice is *about* energy renovation and is not *procuring* any -
    and no list of words solves it.
  - **The companion lists are monolingual.** `exclusion_building_profession` and
    `exclusion_capacity_building_generic` name their subject terms mostly in German, French and
    English. Every language not enumerated makes those exclusions weaker, which is safe and costs
    model calls. 603890-2026, a Dutch architect and installation adviser for a primary school and
    sports hall, is the measured example: plainly a building notice, assessed because *basisschool*
    and *sporthal* are not companion terms.
  - **Supporting-term boilerplate reaches the evidence column.** `environment` matches "an enabling
    environment" and "ICT environment"; `climate` matches "climate stress" in a wildlife programme;
    `power` matches the company name RWE Power AG. It establishes no domain and changes no route, so
    the rule set is right to leave it - but a register that shows "matched: environment" on a wildlife
    programme teaches a colleague to stop reading the column. That is register design, for step 6.
  - **497122-2026 is the `blocked_by` warning case** (0007): a 110 kV grid connection for a
    photovoltaic and battery park, written in HOAI language, which loses its photovoltaic match and
    survives only through its battery storage match. If the blocking list is ever widened or applied
    to more rules, this is the notice that shows what it costs.

  The freeze has no enforcement today - it is a comment at the top of `config/rules.yaml` and the
  paragraph above. The check belongs in the rules save path in step 9: refuse a save unless an
  explicit flag is set, so a settings-page edit cannot quietly produce version 4 while step 7 is
  being tuned.

  **Enforced in step 9.** `services/configuration.py` refuses any rules save that would produce a
  version above `FROZEN_RULES_VERSION` unless the override is set explicitly, and the refusal says
  why and which two cases qualify. It covers the import path as well as the settings page: an import
  is a save whose payload came from a file, and a route that skipped the check would be a way round
  it.

- **Step 7 owes three answers about the rules-only grade.** All three come out of the version 3
  baseline, `docs/eval/2026-09-12-rules-baseline-v3.md`, which is the reference point to tune
  against - no screening result has ever been stored, so that file is the only starting distribution
  there is.

  - **Evidence COUNT tracks text length and language, not fit.** Three of the four notices with both
    a high-strength domain and a service term are GIZ development programmes - long, English, full of
    both vocabularies - while 523353-2026, *Machbarkeitsstudie Netzanschluss Lubmin* and the one
    Entr-shaped notice among them, got there on two matches. A grade that sums matches will rank long
    English development programmes above short German feasibility studies. The grade has to be shaped
    by **what** matched - the highest domain strength, whether a service term is present at all - and
    not by **how many** matched.
  - **An exclusion should probably demote, not only archive.** 568229-2026 is public relations for a
    hydrogen project and sits at rank 10 of the whole corpus, because the exclusion signal is used
    solely to decide archiving and does nothing to a notice that is not archived. Decide whether an
    exclusion match caps or reduces a score in that case.
  - **The rules-only grade has nowhere to live.** `ScreeningResult` has `score` (1-5) and `band`, and
    no rules-only column. Decide whether the grade maps into `score` with band REVIEW, or gets its
    own column. It affects the register sort, the migration, and whether switching the model on later
    rewrites history or appends to it. Cheap now, a migration later.
  - **The baseline window is pinned and step 7 must say what it did with it.** The file measures
    `published_date` between 2026-07-01 and 2026-09-11, 2,158 notices. Every daily ingest changes the
    denominator, so a policy tuned in October against "the corpus" is not being compared with
    anything in that file. Either filter to the pinned window or regenerate the baseline at the start
    of the step, and **write down which** - choosing by accident is the failure mode here, not
    choosing wrongly.

- **Step 10 needs a repeatable measurement command for the rules stage.** The three eval files
  describing version 3 - the archive audit, the domain-match audit and the baseline - were generated
  by throwaway scripts that no longer exist, so re-measuring after a rule change means rewriting them
  from the method descriptions in their headers. Not needed before step 10, but that is the first
  point where the same measurement has to be run twice and compared.
- **The assessment-stage confidence is invented in step 6** (`screening/assess.py`): it measures how
  well evidenced a judgement is, not how relevant a notice is, so step 7 must either take it as an
  input to the real confidence or replace it, and it must not quietly become the stored value.
- **Azure strict schema mode is unverified against a real endpoint.** If the first live run fails with
  a schema error, look first at `_UNSUPPORTED_KEYWORDS` in `llm/azure_openai.py`, which strips the
  JSON Schema keywords pydantic emits and Azure rejects; the constraints those keywords express are
  still enforced by our own validation of the answer.
- **The step 10 recall audit must report recall BY LANGUAGE, not as one figure.** The rules vocabulary
  is English-seeded and the corpus is 24 languages, so the stage under-fires on non-English notices by
  construction (0006). A single overall recall number will be dominated by English and Irish notices
  and will look healthy while Polish or Greek recall is half of it. The measurement can only come from
  human review decisions - the register itself is blind to this, because everything in it already
  passed our own vocabulary.
- **PostgreSQL is stricter than SQLite about lengths and types, so this class of failure cannot be
  caught by the local test suite.** SQLite ignores a declared `String(n)` entirely and converts
  loosely between types; PostgreSQL rejects the row. A column that is too narrow, or a value of the
  wrong type, therefore works on a laptop and raises `DataError` on the first hosted write - which is
  what `deadline_source`, declared `String(64)` and always 65 to 75 characters long, did to the first
  ingest against Supabase. Every test we have runs on SQLite and none of them can see it.
  `tests/unit/test_column_lengths.py` checks the mapped fixtures against the declared lengths, which
  catches the obvious case and proves nothing about a notice we have not recorded. The real answer is
  to run the test suite against PostgreSQL in CI, or to stop declaring a length on anything that is
  not a short code from a closed vocabulary. Until one of those is done, assume any new `String(n)`
  on text that a source composes is a hosted-only failure waiting to happen.
- Ingestion reads `config/sources/ted.yaml`, which is the configuration **seed**, not the active
  configuration. The `config_version` table arrives with the settings page, and ingestion must be
  switched to read the active version at that point, or a colleague's edit in the browser will have
  no effect on what gets fetched.
- **`page_size` and `REQUESTED_FIELDS` are two halves of one setting, and only one of them is
  configuration.** TED prices a request as fields per page, so widening the field list lowers the
  largest page size that works. The offline guard is deliberately pessimistic and the live check in
  `watchdog config validate` confirms the real request, but when the settings page lets a colleague
  edit the source configuration, `page_size` must not be editable without that check running - a page
  size raised in the browser fails every subsequent run on its first request. See 0008 and the cap
  section of docs/TED_API_CONTRACT.md.
- **The exact per-field cost at TED is not known.** Our own 55-name list is charged 55.0 per notice;
  a 40-name list built from `organisation-*-lot` names is charged 41.0, so at least one field name
  costs more than one and we could not identify which. Nothing depends on knowing, because the
  offline guard errs pessimistic and the live check is authoritative, but a future field that costs
  two would shrink the page size without the arithmetic predicting it.
- **Lot-level attribution, if it is ever wanted, comes from the notice XML.** The search response
  cannot supply it at all (0008). `links.xml.MUL` is already stored on every notice and the summary
  step already fetches documents, so the fetching exists; parsing eForms lot structure is what would
  be new. Anything that starts attaching a search-response value to a lot is wrong regardless of how
  well the lengths line up.
- **Migration 0008 must be followed by a re-screen in the same deployment step.** Run
  `alembic upgrade head` and then `watchdog screen --rescreen` as one step, not two. The migration
  adds `domain_strength_rank` and `domain_rules_matched` with a zero default, and until a screening
  run writes the real values every pre-existing row orders as though it had no domain evidence.
  Nothing on screen says so: the register looks correct and is silently wrong at the top.
- **`rules_priority` mixes assessed and rules-only numbers in one ordering column.** That is what lets
  the register sort with or without a model, but during a partial run - `--limit`, or a run stopped
  after the model was switched on - both kinds are present at once and a rules-only 3 sorts above an
  assessed 2. It clears on the next full run and the reason codes distinguish them, but the column
  heading is not honest in that state. Step 8 should decide whether the register says so when it
  detects a mixed set.
- The web routes have no schema guard. The CLI checks the schema and explains a stale one in a
  sentence; a browser request against the same database raises an unhandled `OperationalError`. Fix
  it in step 8, where the register starts reading real data.
- The step 10 recall audit owes four answers, all recorded in 0004: whether `71334000` earns its
  place, whether the `09123000` bet paid, whether the services filter's named cost is still worth
  paying, and a quality baseline built from human review decisions rather than from CPV. It must
  sample from `QueryProfile.AUDIT`, which drops the CPV and contract-nature filters - sampling from
  what we collected cannot reveal what the filters excluded.
- That audit must **report on the provisional CPV codes specifically**, not on all fifteen as though
  they were equal. Which codes those are is data, not a comment: the `provisional:` blocks in
  `config/sources/ted.yaml`, listed by `watchdog config show` with their reason and the date they
  went on trial. A provisional code that survives an audit unmentioned has not been revisited, it has
  been forgotten.
