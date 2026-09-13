# 0002 - Screening reads the buyer's own words, not the source's display title

**Context.** TED composes `notice-title` as `<country> - <CPV label> - <buyer's own title>` and
supplies it in all 24 EU languages on every notice. The middle segment is a translation of the CPV
code's official label. Since our query filters on CPV, that label is the code we searched for,
restated back to us.

If screening read the composed English title, an activity rule looking for "feasibility study",
"advisory" or "consultancy" would fire on essentially every notice the query returns - and the
resulting match would be recorded as evidence, with a quote, looking exactly like a genuine signal
found in the buyer's text. A score built on it would be indefensible in the one situation the score
exists for: explaining to a colleague why a tender was shortlisted.

TED also provides `title-proc`: the buyer's own title, in the buyer's own language, untranslated. It
was present on 750 of 750 notices sampled across all form types with no CPV filter. `description-proc`
and `description-lot` are likewise the buyer's own text.

**Decision.**

1. `Tender.title` keeps the composed title verbatim, with `title_language`. It is the display value
   and nothing else reads it.
2. `Tender.title_native` comes from `title-proc` only, with `title_native_language`. No fallback
   parses it out of the composed title: where both existed the third en-dash segment matched on 55 of
   55, but 4 of 55 titles contained extra en dashes, so the parse is ambiguous about 7% of the time -
   guarding a path that never ran in 750 notices. If `title-proc` is ever absent, `title_native` is
   null, a warning is logged with the publication number, and the notice is screened on its
   description rather than discarded.
3. Where a buyer published in two languages (12 of 750), the displayed variant is English if the
   *buyer* published English, else the first official language with text, else the alphabetically
   first key so the choice is repeatable rather than dependent on dictionary order.
4. Screening reads `Tender.screening_blocks`: one block per field and language, covering every
   `title-proc` language, every `description-proc` language and the `description-lot` entries with
   identical lot descriptions collapsed. Each block is labelled `[field/language]`. The composed
   title is never included.
5. `content_hash` is computed over exactly those blocks plus `cpv_all`. We hash what we screen.

**Consequence.**

- A Flemish buyer's Dutch and French titles are two chances to match, and neither is discarded.
- Two rules follow for the screening stage, which does not exist yet and must honour them:
  - **A canonical rule counts once per tender**, however many blocks it appears in. Bilingual
    publication must not inflate a score.
  - **An acronym's context requirement is satisfied within a single block, never across a block
    boundary.** Two independent texts joined end to end create an adjacency that means nothing, and a
    proximity window straddling the join would fire on it. This is why blocks are a list and not one
    joined string.
- Hashing the blocks rather than a single title means a correction to any language variant makes the
  existing assessment stale, which is the failure this field exists to prevent.
- The register's text search covers `title`, `title_native` and `description`, so a colleague can find
  a notice by either the English label or the words the buyer used.
- The cost: `screening_text` for a bilingual multi-lot notice is longer than one title and one
  description, which matters when it reaches a model prompt. If that becomes expensive, the place to
  economise is block selection at prompt time, not at storage time - the blocks stay.

## Amendment, 2026-09-13: what is *read* and what is *displayed* are different questions

This record governs what the screening reads. It does **not** govern what the register shows, and the
two must not be conflated - which is easy to do, because the reasoning above reads like an argument
against the composed title in general. It is not.

The argument is about circularity. Our query filters on CPV; the composed title restates that CPV
label back to us; so matching a rule against it would manufacture evidence for the thing we searched
for. That is a fatal objection to **screening** on it. It is not an objection to **displaying** it,
because displaying a line makes no claim about relevance at all.

Measured on the 2,158-notice corpus of 1 July to 11 September 2026:

| | |
| --- | --- |
| notices with a non-empty `title_native` | 2,158 of 2,158 |
| of those, in English | 335 (15.5%) |
| in French | 467 &middot; German 393 &middot; Spanish 240 &middot; Polish 124 |

A triage screen showing only `title_native` is therefore unreadable to a Norwegian or British
colleague on roughly 85% of rows. The composed `title` carries the country and the CPV category in
English and is the most legible single line available.

**Decision.**

6. The register's triage line and card heading are `Tender.title`, the composed title.
   `Tender.title_native` is shown on the card directly beneath it, labelled "Buyer's own title" with
   its language named, because that is the text the screening actually read.
7. The composed title is **never presented as a translation**. It is not one: TED translates the
   country name and the CPV category, and the buyer's own title inside it stays in its original
   language. The detail page says so in as many words. Anything that labels it "English" or
   "translated scope" is wrong and should be changed back.
8. The table mode leads with `title_native` and shows the composed title beneath it as secondary
   text, because a dense table is a scanning tool where the buyer's exact words are the useful key.
   Triage leads with the composed title because it is a reading tool.

Nothing in points 1 to 5 changes. `screening_blocks` still excludes the composed title, `content_hash`
still covers exactly what is screened, and no rule may ever match against `Tender.title`.
