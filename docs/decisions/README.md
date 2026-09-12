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
