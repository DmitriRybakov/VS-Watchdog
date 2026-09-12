# Architecture decision records

One file per decision, named `NNNN-short-title.md`. Record the context, the decision and the
consequence - especially decisions that become expensive to reverse.

## Outstanding

- Ingestion reads `config/sources/ted.yaml`, which is the configuration **seed**, not the active
  configuration. The `config_version` table arrives with the settings page, and ingestion must be
  switched to read the active version at that point, or a colleague's edit in the browser will have
  no effect on what gets fetched.
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
