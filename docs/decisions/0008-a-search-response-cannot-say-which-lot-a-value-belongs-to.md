# 0008 - A search response cannot say which lot a value belongs to, so nothing is attributed to one

**Context.** TED's search API was being asked for 37 of its 1,830 fields, and the fields-per-page cap
was the reason. Measuring the cap properly freed room for 18 more, including the ones the register
most wanted: the real language requirement, the award and selection criteria, the contract duration,
the procedure type, the bidding portal and the buyer's sector.

Most of those fields are **lot-scoped**. A lot is a separately described part of one procurement, with
its own budget, deadline, duration and award weighting. The question that had to be settled before any
of them could be stored was whether a value can be attached to the lot it belongs to.

**It cannot, and the evidence is not marginal.**

The search API flattens every lot-scoped field into one array per notice and documents no ordering
relationship between those arrays. Measured over 2,158 notices, 1 July to 11 September 2026, comparing
each array's length against `identifier-lot`, which is TED's own list of lot identifiers:

| Field | Present | Length ≠ lot count |
| --- | --- | --- |
| `submission-url-lot` | 95.5% | 0.0% |
| `dps-usage-lot` | 94.5% | 0.0% |
| `framework-agreement-lot` | 94.4% | 0.05% |
| `contract-duration-period-lot` | 68.3% | 0.4% |
| `contract-duration-start-date-lot` | 32.9% | 0.4% |
| `renewal-maximum-lot` | 37.9% | 1.2% |
| `estimated-value-lot` | 43.8% | 1.6% |
| `document-url-lot` | 94.0% | 1.7% |
| `submission-language` | 96.8% | 7.9% |
| `estimated-value-cur-lot` | 43.8% | 24.0% |
| `award-criterion-type-lot` | 59.6% | **77.3%** |
| `award-criterion-description-lot` | 58.3% | **78.6%** |
| `award-criterion-number-lot` | 48.9% | **85.5%** |
| `award-criterion-number-weight-lot` | 48.2% | **86.1%** |
| `selection-criterion-lot` | 38.9% | **81.9%** |
| `selection-criterion-description-lot` | 38.9% | **81.9%** |

### The two notices that settle it

**458521-2026** - a German flood-retention basin at Nettersheim. Two lots, seven award criteria:

```
identifier-lot              ["LOT-0001", "LOT-0003"]
award-criterion-name-lot    [Honorar, Qualifikation des Projektteams, Schriftliches Konzept,
                             Präsentation, Honorar, Qualifikation des Projektteams, Präsentation]
award-criterion-number-lot  ["40", "20", "20", "20",   "50", "25", "25"]
award-criterion-type-lot    [cost, quality, quality, quality,  cost, quality, quality]
```

LOT-0001 has four criteria totalling 100 and LOT-0003 has three totalling 100. **Nothing in the
payload marks the boundary.** The only thing that reveals it is that the weights happen to sum to 100
twice, which is inference from the values, not data. Seven does not divide by two, so even a uniform
grouping is arithmetically impossible. A positional zip would attach "Honorar 40%" to LOT-0001 and
"Qualifikation des Projektteams 20%" to LOT-0003 and silently discard five of the seven criteria.

The same notice kills the fallback assumption that position is the lot number: **the identifiers are
`LOT-0001` and `LOT-0003`. There is no LOT-0002.**

**532622-2026** - Barcelona, street-maintenance supervision. Five lots, four `estimated-value-lot`
entries, one `estimated-value-cur-lot`, and eight `submission-language` values
(`CAT SPA CAT CAT SPA CAT SPA CAT`). Every count differs from every other. A lot can accept more than
one language, so even the language field is not one-per-lot.

Across the corpus, 36 notices carry an award-criterion array that is not even a whole multiple of the
lot count - 499247-2026 has 34 criteria over 6 lots, 512236-2026 has 48 selection criteria over 10.

### Equal lengths were considered and rejected

The tempting middle position is to attribute a value when the array length happens to equal the lot
count, which would have covered the top half of the table above. It was rejected: **equal length does
not prove equal ordering**, the API guarantees none, and the failure is invisible. A submission URL
shown against the wrong lot looks exactly like a submission URL shown against the right one, and
nothing downstream would ever catch it. Being right 95% of the time in a way nobody can check is worse
here than declining to answer.

**Decision.**

1. **Nothing is attributed to an individual lot.** Every lot-scoped value is stored as the distinct
   set across lots, and displayed labelled that way - "Submission languages mentioned across lots:
   Catalan, Spanish", never as though either is accepted for every lot. `Tender` has no per-lot
   structure at all, so there is nowhere for a future change to put one by accident.

2. **A single-lot notice attributes to that lot**, because there is only one lot to attribute to and
   therefore nothing to get wrong. That covers 1,667 of 2,158 notices. It is what lets
   `estimated-value-lot` fill the notice value when `estimated-value-proc` is empty, which is the 55
   notices that showed "Not stated" while carrying a value.

3. **`identifier-lot` is the only source of the lot count.** `multi_lot` is `len(lot_ids) > 1`, and the
   old array-length heuristic remains only as a fallback for the 3.1% of notices TED sends no
   identifiers for. Arrays disagreeing in length is never consulted: it means we cannot associate the
   values, which is a different fact from there being several lots.

4. **A criterion's own parts are paired by position, but only when every array present has the same
   length.** This is a strictly weaker claim than lot attribution - it lines up one criterion's type,
   name, number and number kind, not a value with a lot - and it is confirmed by the values
   themselves: in 458521-2026 "Honorar" is the `cost` criterion in both groups, carrying 40 in the
   first and 50 in the second. It is the same rule the deadline date and time arrays already follow.
   The sub-arrays disagree on 2.5% of notices carrying award criteria (395741-2026: 113 types against
   157 numbers) and on none of the 1,200 carrying selection criteria. Where they disagree **nothing is
   paired**, `criteria_unpaired` records it, and the raw arrays stay in `raw`.

5. **Lot values are never summed.** 532622-2026 carries four values for five lots, so a total would be
   confidently short. They are listed, with the count of lots beside them.

### If lot-level attribution is ever needed

It comes from the **notice XML**, which carries explicit lot references rather than flattened arrays -
`links.xml.MUL` is already in every payload we store. The summary step already fetches documents, so
the fetching machinery exists; what would be new is parsing eForms lot structure. That is the route.
It is not a matter of trying harder with the search response.

### The number beside a criterion is not a percentage

Related, and the same class of error. `award-criterion-number-weight-lot` is **not a weight**: it is a
code saying what kind of number the criterion carries. Verified against real notices rather than a
specification, because getting it wrong turns a rank into a percentage:

| Code | Meaning | Evidence |
| --- | --- | --- |
| `per-exa` | a percentage | 596416-2026 states "waga 100%" beside number 100; 313 single-code notices total exactly 100 |
| `poi-exa` | points | 541548-2026 names its criterion "Hinnan maksimipistemäärä", the maximum points for price; totals of 1000 and 0 also occur |
| `ord-imp` | a rank | 534739-2026 scores Prijs 1 and Dienstverlening 2 |
| `dec-exa` | a decimal fraction | 565418-2026 uses 0.8 and 0.2 |

A number whose code we do not recognise is shown exactly as it arrived, with a note that TED did not
say what it means. `award-criterion-weight-lot`, which the field list was originally going to ask for,
does not exist at all.
