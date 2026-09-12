"""The deterministic stage: find the vocabulary in the buyer's own words.

What this stage is for, and what it is not for:

- It produces **evidence**, not a verdict. Nothing is rejected here and nothing
  stops here. Every tender continues to the scoring policy, on every route.
- ``ARCHIVE_CANDIDATE`` means "skip the model call". It is reached only when an
  exclusion matched, no domain rule matched and no CPV code matched. A notice with
  no match at all routes to ``ASSESS``: our vocabulary is English and TED titles
  arrive in every EU language, so silence is not evidence of irrelevance.

Three rules the matching itself has to honour, from docs/decisions/0002:

- It reads ``Tender.screening_blocks``, never the composed display title. That
  title carries TED's own CPV label, so an activity rule would fire on every
  notice and the quote would look exactly like a real signal.
- A canonical rule counts **once per tender**, however many blocks it appears in.
  A Flemish buyer publishing the same title in Dutch and French must not score
  twice, so this module records at most one match per rule.
- A required context word is satisfied **inside one block**. Two independent texts
  joined end to end create an adjacency that means nothing.

Normalisation happens for comparison only. Stored text is never altered, and the
evidence quote is cut from the original characters, not from the normalised form.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from watchdog.core.cpv import matched_cpv_codes
from watchdog.core.enums import Domain, Polarity, RuleSignal, RulesRoute, RuleStrength
from watchdog.core.models import RuleMatch, RulesResult, Tender, TextBlock
from watchdog.screening.config import PREFIX_MARKER, Rule, RulesConfig

# Characters that mean "word break" here. A hyphenated term, a spaced one and an
# em-dashed one are the same term: "power-to-x" and "power to x" normalise alike.
_WORD_BREAKS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212_/\\\u00b7"

# Typographic apostrophes, so a curly and a straight "maitrise d'ouvrage" match.
_APOSTROPHES = "\u2018\u2019\u02bc\u00b4`"

_ELLIPSIS = "\u2026"


@dataclass(frozen=True)
class _Normalised:
    """A block's text prepared for matching, with the way back to the original.

    ``offsets[i]`` is the index in the source text that normalised character ``i``
    came from. Normalising changes lengths - accents are dropped, \u00df becomes ss,
    H\u2082 becomes H2, runs of whitespace collapse - so without this map an evidence
    quote would drift away from the words it is supposed to show.
    """

    text: str
    offsets: tuple[int, ...]
    source: str

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        return self.offsets[start], self.offsets[end - 1] + 1


def normalise(text: str) -> _Normalised:
    """Casefold, strip accents, unify dashes and collapse whitespace. Never stored."""
    characters: list[str] = []
    offsets: list[int] = []

    for index, character in enumerate(text):
        # Casefold first: the German eszett only becomes "ss" that way round.
        for decomposed in unicodedata.normalize("NFKD", character.casefold()):
            if unicodedata.combining(decomposed):
                continue

            if decomposed in _APOSTROPHES:
                decomposed = "'"
            elif decomposed in _WORD_BREAKS or decomposed.isspace():
                decomposed = " "

            if decomposed == " " and (not characters or characters[-1] == " "):
                continue

            characters.append(decomposed)
            offsets.append(index)

    while characters and characters[-1] == " ":
        characters.pop()
        offsets.pop()

    return _Normalised(text="".join(characters), offsets=tuple(offsets), source=text)


def _term_pattern(term: str) -> re.Pattern[str]:
    """A pattern matching ``term`` on token boundaries, never inside a word.

    This is what keeps CO2 out of ECO2000 and H2 out of H2O.

    An alias ending in the prefix marker drops the closing boundary only, so
    ``umspannwerk*`` finds Umspannwerke and Umspannwerksanierung. It still cannot
    start in the middle of a word, so it does **not** find Bestandsumspannwerken,
    where German has put the head noun at the end. A suffix wildcard would find
    that one, and would also put "software" back inside "Netzmanagementsoftware",
    which is the false positive token boundaries exist to prevent.
    """
    if term.endswith(PREFIX_MARKER):
        return re.compile(rf"(?<!\w){re.escape(normalise(term[: -len(PREFIX_MARKER)]).text)}")
    return re.compile(rf"(?<!\w){re.escape(normalise(term).text)}(?!\w)")


@dataclass(frozen=True)
class _CompiledRule:
    rule: Rule
    aliases: tuple[tuple[str, re.Pattern[str]], ...]
    context: tuple[re.Pattern[str], ...]
    companions: tuple[re.Pattern[str], ...]
    blockers: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class _Hit:
    """One alias found in one block, before it is turned into evidence."""

    alias: str
    block: TextBlock
    normalised: _Normalised
    start: int
    end: int


class RuleEngine:
    """The compiled rule set. Build it once, evaluate many tenders with it.

    Compiling is not free - one pattern per alias - and a run screens thousands of
    notices against the same rules.
    """

    def __init__(self, config: RulesConfig) -> None:
        self.config = config
        self._compiled = [
            _CompiledRule(
                rule=rule,
                aliases=tuple((alias, _term_pattern(alias)) for alias in rule.aliases),
                context=tuple(_term_pattern(word) for word in rule.requires_context),
                companions=tuple(_term_pattern(word) for word in rule.requires_companion),
                blockers=tuple(_term_pattern(word) for word in rule.blocked_by),
            )
            for rule in config.active_rules()
        ]

    def evaluate(self, tender: Tender) -> RulesResult:
        """What the rules found on one tender, and where that routes it."""
        blocks = [(block, normalise(block.text)) for block in tender.screening_blocks]

        matches: list[RuleMatch] = []
        domains_hit: list[Domain] = []

        for compiled in self._compiled:
            hit = self._first_hit(compiled, blocks)
            if hit is None:
                continue

            matches.append(self._as_match(compiled.rule, hit))

            label = compiled.rule.label
            if label and _establishes_domain(compiled.rule):
                domain = Domain(label)
                if domain not in domains_hit:
                    domains_hit.append(domain)

        cpv_match = bool(matched_cpv_codes(self.config.cpv.archive_guard, tender.cpv_all))
        has_exclusion = any(match.signal is RuleSignal.EXCLUSION for match in matches)

        return RulesResult(
            tender_id=tender.id,
            matches=matches,
            domains_hit=domains_hit,
            cpv_match=cpv_match,
            has_exclusion=has_exclusion,
            route=_route(has_exclusion=has_exclusion, domains=domains_hit, cpv_match=cpv_match),
            rules_version=self.config.rules_version,
        )

    def _first_hit(
        self,
        compiled: _CompiledRule,
        blocks: list[tuple[TextBlock, _Normalised]],
    ) -> _Hit | None:
        """The best occurrence in the earliest block that has one, or nothing.

        "Earliest block, then earliest position, then longest alias" is arbitrary
        but fixed, so the same notice always produces the same quote. The longest
        alias wins a tie because "green hydrogen" explains more than "hydrogen".
        """
        if not self._has_companion(compiled, blocks):
            return None

        if self._is_blocked(compiled, blocks):
            return None

        for block, normalised in blocks:
            if not compiled.rule.reads(block.field):
                continue

            candidates: list[_Hit] = []
            for alias, pattern in compiled.aliases:
                for found in pattern.finditer(normalised.text):
                    if self._has_context(compiled, normalised, found.start(), found.end()):
                        candidates.append(
                            _Hit(
                                alias=alias,
                                block=block,
                                normalised=normalised,
                                start=found.start(),
                                end=found.end(),
                            )
                        )
                        break

            if candidates:
                return min(candidates, key=lambda hit: (hit.start, -len(hit.alias)))

        return None

    def _has_context(
        self, compiled: _CompiledRule, normalised: _Normalised, start: int, end: int
    ) -> bool:
        """True when a required context word is near this alias, in this block.

        The window is clamped to the block, so context can never be borrowed from
        a different language variant or a different lot.
        """
        if not compiled.context:
            return True

        window = self.config.matching.context_window
        around = normalised.text[max(0, start - window) : end + window]
        return any(pattern.search(around) for pattern in compiled.context)

    def _has_companion(
        self, compiled: _CompiledRule, blocks: list[tuple[TextBlock, _Normalised]]
    ) -> bool:
        """True when the notice says somewhere what the procured work is about.

        Anywhere in the notice, with no proximity window: this asks what the work
        is, not what one ambiguous word means in one sentence. A notice whose title
        says HOAI and whose description says school satisfies it; a notice that only
        says HOAI does not, and nothing is archived on it.
        """
        if not compiled.companions:
            return True

        return any(
            pattern.search(normalised.text)
            for block, normalised in blocks
            if compiled.rule.reads(block.field)
            for pattern in compiled.companions
        )

    def _is_blocked(
        self, compiled: _CompiledRule, blocks: list[tuple[TextBlock, _Normalised]]
    ) -> bool:
        """True when the notice says somewhere that it is about something else.

        The mirror of a companion, and the only thing that separates an energy word
        that is the subject of a contract from the same word inside a building
        design brief, where it sits in a list beside fire safety and acoustics.
        """
        if not compiled.blockers:
            return False

        return any(
            pattern.search(normalised.text)
            for block, normalised in blocks
            if compiled.rule.reads(block.field)
            for pattern in compiled.blockers
        )

    def _as_match(self, rule: Rule, hit: _Hit) -> RuleMatch:
        start, end = hit.normalised.original_span(hit.start, hit.end)
        return RuleMatch(
            rule_id=rule.id,
            signal=rule.signal,
            alias_matched=hit.alias,
            field=hit.block.field,
            evidence=_evidence(
                hit.normalised.source, start, end, self.config.matching.evidence_chars
            ),
            polarity=(
                Polarity.NEGATIVE if rule.signal is RuleSignal.EXCLUSION else Polarity.POSITIVE
            ),
        )


def _establishes_domain(rule: Rule) -> bool:
    """A supporting term is evidence beside a real match, never a domain on its own."""
    return rule.signal is RuleSignal.DOMAIN and rule.strength is not RuleStrength.SUPPORTING


def _route(*, has_exclusion: bool, domains: list[Domain], cpv_match: bool) -> RulesRoute:
    """An exclusion archives only when nothing else speaks for the notice."""
    if has_exclusion and not domains and not cpv_match:
        return RulesRoute.ARCHIVE_CANDIDATE
    return RulesRoute.ASSESS


def _evidence(source: str, start: int, end: int, width: int) -> str:
    """The matched words with the source text around them, verbatim.

    Cut from the original characters, so accents, case and hyphens are the ones
    the buyer wrote. Marked with an ellipsis where it was cut, so nobody reads a
    fragment as a whole sentence.
    """
    padding = max(width // 2, 1)
    left = max(0, start - padding)
    right = min(len(source), end + padding)

    quote = source[left:right].strip()
    if left > 0:
        quote = f"{_ELLIPSIS}{quote}"
    if right < len(source):
        quote = f"{quote}{_ELLIPSIS}"
    return quote
