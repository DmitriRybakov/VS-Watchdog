# 0001 - Tender detail field names are not pinned yet

**Context.** `TenderDetail` stores the summary-template fields as a dictionary of name to
`{value, source_ref}`. The summary template does not exist yet, so there is no list of field names to
validate against, and nothing writes to the table until the summary step is built.

**Decision.** Keep the dictionary. The field names are data, not schema: a new template field is a
prompt change, not a migration.

**Consequence.** Nothing checks the names today, so a field renamed in a prompt shows up in the
detail view as a blank rather than as an error - the page looks complete while a fact is silently
missing. When the summary template is written, decide there whether to pin the names (an enum or a
Pydantic model per field) and how a missing field should be shown to the reviewer.
