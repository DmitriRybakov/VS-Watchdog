# 0001 - Tender detail field names are not pinned yet

> **Closed in step 9.** The names are now pinned. `core/vocabulary.py` holds every register field
> with its stable key and the team's label for it, and `TenderDetail` validates its keys against
> `FIELDS_EXTRACTED` - the subset no source API can fill. A field renamed in a prompt is now a
> validation error on the way in, not a blank on the page. The detail page shows every field whether
> or not anything fills it, each one labelled with where its value came from, so an empty field reads
> as "nobody has established this" rather than as an absence of the field.

**Context.** `TenderDetail` stores the summary-template fields as a dictionary of name to
`{value, source_ref}`. The summary template does not exist yet, so there is no list of field names to
validate against, and nothing writes to the table until the summary step is built.

**Decision.** Keep the dictionary. The field names are data, not schema: a new template field is a
prompt change, not a migration.

**Consequence.** Nothing checks the names today, so a field renamed in a prompt shows up in the
detail view as a blank rather than as an error - the page looks complete while a fact is silently
missing. When the summary template is written, decide there whether to pin the names (an enum or a
Pydantic model per field) and how a missing field should be shown to the reviewer.
