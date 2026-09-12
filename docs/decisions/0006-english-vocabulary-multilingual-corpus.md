# 0006 - The vocabulary is English, the corpus is twenty-four languages

**Context.** The deterministic rules stage matches explicit aliases against the buyer's own words. The
vocabulary was seeded from an English keyword sheet and corrected against term frequency in 2,158 TED
notices. The notices are in every EU language, and roughly three quarters of them are not in English.

The audit on 2026-09-12 (`docs/eval/2026-09-12-rules-archive.md`) produced two facts that are more
important than any count in it:

- **614160-2026** is a Frankfurt qualification system for refurbishing substations. It matched no
  domain rule, because the notice says *Umspannwerke* and *Bestandsumspannwerken* and the vocabulary
  had *Umspannwerk*. Its only rule match was the word HOAI. It was kept out of the archive by two CPV
  codes that happened to be among the seven in the archive guard - **the design did not save it, a
  coincidence did.**
- **538537-2026**, a communal heating-network refurbishment, matched no domain rule either, because
  the vocabulary had *district heating*, *Wärmenetz* and *fjernvarme* and not *réseau de chauffage*.
  The scan written to find exactly this class of miss did not find it; a person reading a random
  sample did. **A scan only finds stems someone thought of**, so "8 of 236 archived notices contain a
  native-language energy word" is a floor, not a count.

**Decision.**

### Say it plainly: the rules stage under-fires on non-English notices

This is a property of the design, not a bug to be closed. Explicit aliases with no stemming are what
make a score explicable to a colleague and to IT; the price is that recall depends on whether someone
wrote the right word in the right language. Nothing downstream may assume that an absence of domain
matches means an absence of domain.

Three things follow, and all three are now implemented:

1. **A prefix marker, on domain aliases only.** `umspannwerk*` matches Umspannwerke and
   Umspannwerksanierung. It does not match *Bestandsumspannwerken*, because a prefix still has to
   start a word and German puts the head noun at the end of that compound. A suffix or substring
   wildcard would catch it, and would also put `software` back inside *Netzmanagementsoftware*, which
   is the false positive token boundaries exist to prevent. The marker is rejected on exclusion
   aliases and on context and companion words: it widens recall on a domain term and widens archiving
   everywhere else, and those are opposite risks.
2. **A term that names how a service is priced or staffed may not archive on its own.** HOAI,
   Leistungsphasen, the architect family and "capacity building" now require a companion subject term
   somewhere in the notice. Where there is none, the notice routes to `ASSESS`. This is the direct
   answer to 614160-2026: had its CPV codes been outside the guard, it would have been archived on a
   fee schedule.
3. **The CPV archive guard stays** (0005), and is understood as compensation for this gap rather than
   as a refinement of it.

### What compensates, and what cannot

The model assessment is the part of the pipeline that reads a Polish or Greek notice as well as an
English one. `ARCHIVE_CANDIDATE` is the one route that skips it. Archiving is therefore the only
decision the rules stage makes that the rest of the pipeline cannot correct, which is why every
question in this record is about archiving and none is about scoring.

Nothing here is measurable from the register. Every notice in it passed both TED filters and then our
own vocabulary, so counting how many archived notices look wrong measures only the part of the
problem we can already see. **The only real measurement is the step 10 audit against human review
decisions, and it must report recall by language.** A single overall recall figure will be dominated
by English and Irish notices and will look healthy while Polish and Greek recall is half of it.

**Consequence.**

- Rule set version 2 carries 26 prefix aliases. Against the corpus they changed **no** notice's route
  and gave **31 notices a domain match they did not have** - Polish *fotowoltaicznych*, Czech
  *fotovoltaické*, French *photovoltaïques*, German *Umspannwerken*. That ratio is the point: the
  gain is in evidence and in future protection from an exclusion, not in today's routing.
- The companion requirement moved 7 notices from `ARCHIVE_CANDIDATE` to `ASSESS`, one of which
  (603890-2026, a Dutch architect and installation adviser for a primary school and sports hall) is
  plainly a building notice that now costs a model call because *basisschool* and *sporthal* are not
  in the companion list. That is the deliberate direction of the trade.
- Adding native vocabulary one word at a time does not scale to 24 languages and must not be mistaken
  for a fix. The vocabulary grows when evidence shows a miss; the measurement of what is still missing
  comes from human review decisions, by language, at step 10.
