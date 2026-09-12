# Screening policy

**This document is authoritative.** It states how Watchdog scores a procurement notice, and the code
follows it exactly. `config/policy.yaml` is its machine-readable form: every number below is written
there and nowhere else, so the two can be compared line by line.

It is owned by Entr Advisory & Decision Support, not by whoever edits the code. If the scoring is
wrong, the fix is to change this document and the configuration file, not the program.

Scope note: this document covers **relevance**. Whether Entr can realistically pursue an opportunity —
geography, contract value, language, eligibility, local presence, capacity, bid route — is a separate
question, answered by a person after the notice reaches the register. Geography, value and language
never change a relevance score. That principle is stated in `config/profile.yaml` and it is the single
most important thing this policy protects.

---

## 1. The mandate

Entr Advisory & Decision Support is a team within Aker Solutions that supports early-stage energy and
decarbonisation projects. We are most valuable before major commitments are locked in: while options
are still open, before a concept is frozen, before a contracting strategy is set.

We are not an EPC delivery entity, not a detailed-design house and not an equipment supplier. Our work
is advice, analysis and structured decision support.

The mandate itself — the core domains, the service categories, the decision stages, the edge cases —
lives in `config/profile.yaml` and is sent verbatim to the assessment. This document does not restate
it; it states what is done with the answer.

---

## 2. The three axes

Every notice is judged on three **independent** questions, each scored 0–5, or left unknown. They are
judged separately on purpose: a notice can be perfectly in domain and entirely the wrong kind of work,
and averaging those two into one middling answer loses both facts.

An axis is **unknown** when the notice does not say. Unknown is a real answer. It is never a zero, it
never lowers a score, and it never archives a notice — it sends it to a person.

### Domain fit — is the procured subject in our domains?

Judged on the procured scope, not on who is buying. A hospital procuring a decarbonisation study for
its estate is in domain; a hospital procuring medical equipment is not, however much the notice says
about sustainability.

| Score | Meaning |
| --- | --- |
| 5 | Squarely a **core** domain and unmistakable from the text |
| 4 | A **core** domain, with some ambiguity about the exact scope |
| 3 | **Adjacent**: energy or industrial work needing this kind of decision support, but not energy-transition work as such |
| 2 | **Adjacent**, weakly or only partly |
| 1 | **Excluded**: energy or climate wording is present but incidental to the contract |
| 0 | **Excluded**: nothing in our domains at all |
| unknown | The text does not say what is being procured |

### Service fit — is this advisory, study and decision-support work?

| Score | Meaning |
| --- | --- |
| 5 | Advisory, study, optioneering, modelling, techno-economic assessment, due diligence or structured decision support, named as the deliverable |
| 4 | Clearly advisory or analytical work, with some delivery content attached |
| 3 | A framework or mixed scope in which advisory or study work is explicitly named among the services |
| 2 | Mostly delivery, design or supply, with an analytical component |
| 1 | Construction, installation, commissioning, equipment supply, operations, or embedded personnel |
| 0 | Plainly none of our services |
| unknown | The text does not say what kind of work is wanted |

Time-based staff augmentation, embedded resourcing, procurement administration and institutional
capacity building are not our services, whatever the sector and however often a notice calls them
"technical assistance".

### Stage fit — how early is the decision?

| Score | Meaning |
| --- | --- |
| 5 | Concept selection, option screening, pre-feasibility, feasibility, pre-FEED; or a prior information notice or market consultation where the buyer is still forming the scope |
| 4 | Early, but the concept looks largely settled |
| 3 | FEED, project definition, delivery or contracting strategy, due diligence before an investment decision |
| 2 | Late definition work against a concept already chosen |
| 1 | Detailed design against a frozen concept, construction, installation, commissioning |
| 0 | Operations and maintenance of something already built |
| unknown | The text does not say where the project stands |

---

## 3. The score

The three axes are combined into one number on the same 0–5 scale:

| Axis | Weight |
| --- | --- |
| Domain fit | 0.50 |
| Service fit | 0.30 |
| Stage fit | 0.20 |

Domain carries half the weight because it is the primary filter: everything else is a question about
work we might want, and domain is the question of whether the work is ours at all.

The weighted number is rounded **half upwards** — 3.5 becomes 4, 4.5 becomes 5 — and recorded as a
whole number from **1 to 5**.

**An unknown axis is left out of the sum**, and the weights of the axes that *were* established are
re-shared between them. A notice judged 5 on domain, with service and stage unknown, scores 5 and goes
to review. It does not score 2.5 because of two things nobody knew.

---

## 4. The caps

A cap is a ceiling. It can only ever **lower** a score, never raise one.

| Cap | Applies when | Ceiling |
| --- | --- | --- |
| Out of domain | Domain fit is 1 or 0 | 1 |
| Execution dominated | Service fit is 1 or 0 | 2 |

**Out of domain** is what stops a well-written consultancy framework for the health service from
reaching the shortlist on the strength of its service and stage fit alone. A perfect 5 on both cannot
rescue a notice whose subject matter is not ours.

**Execution dominated** is what a construction, supply or operations package hits. It is capped rather
than archived, for the reason in the next section.

A cap is never applied on an axis that is unknown. "Domain fit is 1 or 0" is not true of a domain fit
nobody could establish.

### Project intelligence

A notice with domain fit 3 or better and service fit 1 or 0 is tagged **PROJECT_INTELLIGENCE**. An
offshore wind construction package is not an advisory opportunity, and it is still worth knowing about:
it says a project exists and is moving. The tag keeps the capped score and makes the group a saved view
in the register, rather than something that has to be hunted for in the archive.

---

## 5. The bands

| Band | Score | What it means |
| --- | --- | --- |
| **Shortlist** | 4–5 | Looks like ours. Read it. |
| **Review** | 3 | Worth a person's judgement. |
| **Archive** | 1–2 | Low priority. |

**Archive never means deleted.** Nothing in Watchdog is ever deleted — not a rejected notice, not a
superseded score, not a tender that has vanished from the source. An archived notice stays in the
database, stays searchable and stays filterable, and it is re-screened like any other when the policy
or the vocabulary changes.

---

## 6. Review triggers

Any of these puts a notice in front of a person regardless of what it scored. The score is kept as it
is; only the band moves.

| Trigger | Why |
| --- | --- |
| Any axis unknown | We do not know. That is a question for a person, not a reason to archive. |
| The assessment listed missing information | The notice does not say something that matters. |
| Confidence below 0.5 | The judgement rests on too little. |
| The rules and the assessment disagree on domain | The buyer's own words say energy and the assessment did not. One of them is wrong and it matters which. |
| The notice covers several lots | See below. |

A review trigger never archives and never shortlists. It only ever produces a review.

### Why multi-lot is a trigger

Not because the scope may be split between suppliers — that is a bid-route question and would have no
business touching a relevance score. It is because **we assess the whole notice as one text**. The word
"hydrogen" from an equipment lot and the words "feasibility study" from an entirely unrelated lot can
combine into a 5 on domain and a 5 on service for a tender in which no single lot is our work.

That is not an uncertain judgement, which confidence would describe. It is a confident judgement about
a scope that does not exist, and nothing in the score can show it. The trigger stays until the
assessment can establish that domain, service and stage all came from the same scope — which means
processing lots separately, and that is not built.

---

## 7. Every notice has an outcome

There are six ways a notice can finish. There is no seventh, and nothing falls through.

| | What happened | Outcome |
| --- | --- | --- |
| 1 | Assessed normally | The weighted score, the caps applied, the band from the thresholds, then any review trigger |
| 2 | An exclusion rule matched, with no domain evidence and no subject-matter CPV code | Score 1, **archive**, reason `EXCLUDED_BY_RULE`. Not deleted, still searchable |
| 3 | The assessment errored, or failed validation twice | Score 3, **review**, reason `ASSESSMENT_FAILED`. Left unscreened at the current versions, so the next run tries again. Visible in a "needs attention" filter |
| 4 | No model configured, with domain or activity evidence | A graded rules-only score of 1–3, **review**, reason `RULES_ONLY` |
| 5 | No model configured, with no evidence at all | Score 2, **archive**, reason `RULES_ONLY_NO_EVIDENCE`. Flagged, because with no model this is where a missed opportunity would hide |
| 6 | Assessed, and none of the three axes could be established | Score 3, **review**, reason `INSUFFICIENT_INFORMATION` |

Case 6 does not use the weights at all. There is nothing to weigh, and a number arrived at by dividing
nothing by nothing would look exactly like a judgement while standing for the absence of one. The score
is a fixed 3, which is the review band, because that is what a person needs to do with it. A two-line
notice means we do not know; it never means the notice is not for us.

Where the assessment established **some** of the axes but not all, case 1 applies: the score is weighed
on the axes that were established, the band is forced to review, and the register shows how many axes
it rests on — "5 (1 of 3 axes established)" — so a thin judgement is never read as a full one.

---

## 8. Running without a model

Watchdog works with no language model configured, and that mode matters more than it looks. It is what
runs before AI access is approved, what runs if a key expires or a provider is down, and what a
colleague sees the first time anyone demonstrates the tool.

In that mode the keyword rules are the only evidence there is, and they are **graded**, so that the
register still sorts:

| Rules-only score | Evidence |
| --- | --- |
| 3 | At least one high-strength domain term **and** an activity term. The buyer's own words say both energy domain and advisory work |
| 2 | A high or medium domain term on its own, or a subject-matter CPV code together with an activity term. Real signal, incomplete |
| 1 | Activity evidence only, or supporting terms only. A supporting term such as "energy" or "cable" never establishes a domain on its own |
| 0 | Nothing. Banded archive and flagged for the recall audit |

**Nothing is ever shortlisted in this mode**, whatever the evidence. A shortlist claims a judgement,
and without a model nothing has made one. The band is review, and the register orders on the
**rules-only score** rather than on the score — labelled "Rules priority" on screen, so nobody reads it
as a judged score. It grades "no evidence at all" below "weak evidence", which the score cannot: there,
an unscreened notice sits at 2 and a notice that matched something sits at 1.

Within that, the order is: rules-only score, then the strength of the strongest domain term, then how
many distinct domain terms matched, then how recently the notice was published. The notices with real
domain evidence and an activity match sit at the top of the register; the boilerplate matches sit at
the bottom.

A rules-only score is stored in a **field of its own**, never in the same field as an assessed score. A
3 that four keywords produced and a 3 that a reasoned assessment produced are different claims, and
merging them would make "score 3" ambiguous for as long as the database exists.

When a model is later switched on, every rules-only result is stale by version and is re-screened on
the next run. Nothing has to be rebuilt and nothing has to be deleted.

---

## 9. Confidence

Confidence says how much weight a result can carry. It is **not** a measure of how relevant the notice
is, and it is never asked of the model — a model's own opinion of how sure it is cannot be checked by
anyone. It is computed from facts that can be seen on the notice itself.

It starts at 1.0, and each of these subtracts from it:

| Observation | Cost |
| --- | --- |
| A description shorter than 400 characters | Up to 0.15, in proportion to how much is missing |
| No CPV code on the notice at all | 0.10 |
| An axis that could not be established | 0.20 each |
| The rules and the assessment disagreeing on domain | 0.20 |
| A notice with no English text available | 0.10 |

Every deduction is recorded as a sentence in plain language, so the register can show *why* a
confidence is what it is. Where the assessment stage already reported a lower number — because the
notice was too long to send in full, for instance — that lower number wins. This stage may narrow
confidence; it never widens it.

Confidence below 0.5 forces a review.

---

## 10. How a score is explained

An explanation is **assembled from the parts that produced the score**, never written by a model.
Every word of it can be checked against something stored:

> Domain: offshore wind 5/5. Service: early phase study 5/5. Stage: pre feed feasibility 4/5.
> Weighted 4.8 -> 5. Confidence 0.9. Evidence: 'feasibility study for the offshore wind connection'.

The evidence quote is copied from the buyer's own text, in the buyer's own language, and is checked
character for character against the notice before it is accepted. A quote the notice never contained is
worse than no quote at all: it is the one thing a colleague cannot catch without opening the notice.

---

## 11. Worked examples

**A pre-FEED hydrogen study.** Domain 5, service 5, stage 5. Weighted 5.0, no cap applies, score 5,
**shortlist**.

**An offshore wind construction package.** Domain 5, service 1, stage 1. Weighted 3.0, which the
execution-dominated cap lowers to 2, so the band is **archive** — and the notice is tagged
`PROJECT_INTELLIGENCE`, because a construction package tells us a project exists and is moving.

**A consultancy framework for a health authority.** Domain 1, service 5, stage 5. Weighted 3.0, which
the out-of-domain cap lowers to 1: **archive**. A perfect service and stage fit cannot rescue it.

**A two-line framework notice for "technical services".** Every axis unknown. The weights are not used
at all: the score is a fixed 3 and the band is **review**, tagged `INSUFFICIENT_INFORMATION`. Thin text
means we do not know; it never means no.

**A German notice reading "Machbarkeitsstudie Wasserstoffinfrastruktur", with no model configured.**
The rules find a high-strength domain term and an activity term: rules-only score 3, band **review**,
near the top of the register. When the model is switched on it is re-screened automatically.

---

## 12. Changing this policy

`config/policy.yaml` carries a `version`. Bump it on any change that would make a notice score
differently. Every stored screening result records the policy version it was judged under, so a change
marks earlier results stale and they are re-screened on the next run. Past results are superseded and
kept, never rewritten — which is what makes it possible to answer "why did this score 2 in September?"
