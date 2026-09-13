# 0009 - Register columns 22-32, the bid intelligence, are not built

**Context.** The register our team keeps has thirty-two columns. Step 9 builds the first
twenty-one: the facts a notice states, the qualification bar, the award criteria and the links,
plus the screening result and the review. The remaining eleven are what the workbook calls the bid
intelligence, and none of them is built.

They divide cleanly into two groups, and the reason for leaving each group out is different.

### The five that need a deliberate capability analysis

Entr strengths, Entr weaknesses, competence gaps, risks, value proposition.

These are **not** already produced by the screening. It is tempting to assume they are, because the
three axes sound close to them, and they are not: `domain_fit`, `service_fit` and `stage_fit` measure
one thing between them - whether a notice falls inside our mandate. None of them looks at what we can
actually deliver, who is free, what we have built before, or what a delivery would cost us. A notice
can score 5 on all three axes and still be one we would lose, and nothing in the current screening
would say so.

Producing these honestly needs a separate analysis against a capability statement and a delivery
context that do not exist in the system yet. Writing them from the mandate alone would produce five
confident paragraphs derived from the same three numbers already on the page.

### The six that need data we do not hold

Likely competitors, their strengths, their weaknesses, winning strategy, similar historical projects
with their proposal hours, estimated effort.

Each of these needs our own proposal history, our win and loss record, and our hour estimates. None
of that is in Watchdog, and none of it is in any open procurement API. A model asked for them with no
data will not refuse; it will produce a plausible, specific, entirely invented answer, and that answer
will sit on the page in the same typeface as the fields that are real.

**That is worse than an empty cell**, and the asymmetry is the whole decision. A blank prompts a
colleague to go and find out. A fabrication does not prompt anything, because it already looks like an
answer - and this is a tool whose value rests on a score being defensible to a colleague and to a
client.

### Decision

Columns 22-32 are out of scope for step 9 and are not shown as empty fields on the detail page either,
because a field labelled "Likely competitors" with nothing in it invites somebody to fill it in from a
model. The twenty-one columns that are built each show where their value came from; these eleven have
no honest provenance to show.

### Consequence

The workbook and the tool are not yet the same document, and a colleague who wants the bid
intelligence still writes it by hand. That is the intended state until two things exist:

1. a capability and delivery-context statement Entr owns, in `config/`, versioned like the mandate is,
   against which the first five can be assessed with quotes;
2. a store of our own proposal history, win/loss outcomes and hour estimates, for the other six.

When the first arrives, the five capability fields become a fourth kind of Watchdog intelligence and
need their own evidence requirement - the same rule the assessment axes already carry, that a scored
claim without a quote is refused rather than merely marked low confidence.

Until then, anything presented in those columns would be a guess wearing the clothes of a fact.
