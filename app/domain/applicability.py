"""Whether a rule applies to this client, in three values.

A rule can be true, verified, on topic, in date, and still have nothing
to do with the person asking. The decision layer had no way to express
that: a test drove it directly with no model involved, and it authorised
a supported answer to a non-resident resting on a verified rule about
residents, recording the rationale "in scope, material facts confirmed,
effective guidance retrieved, no declared conflict". Every clause true,
conclusion wrong.

Three values rather than two, and the third is the whole point.

    TRUE      the rule's conditions match what has been established
    FALSE     they contradict it
    UNKNOWN   the rule turns on something nobody has established yet

A boolean would have to fold UNKNOWN into one of the others, and both
choices are wrong. Treating it as TRUE admits a rule that may not apply.
Treating it as FALSE silently removes evidence and hides the reason: if
residency status is unestablished, dropping every resident-rule conceals
the fact that residency is exactly what needs establishing. UNKNOWN
instead becomes a missing fact, so the system asks the question.

The case side of the comparison comes only from CONFIRMED facts. A fact
the agent proposed is not established, by the same rule that governs
every other fact here, so an applicability decision can never rest on
something the model asserted about the client.
"""

from dataclasses import dataclass

TRUE = "TRUE"
FALSE = "FALSE"
UNKNOWN = "UNKNOWN"

# Each dimension: the unit column that restricts, the case predicate
# that resolves it, and how a client's own words map onto the values.
#
# Deliberately two. This is the corridor this build demonstrates, not an
# ontology, and it should not grow without evals showing the need.
DIMENSIONS = (
    {
        "name": "residency",
        "unit_field": "applies_to_residency",
        "predicate": "residency_status",
        "values": {
            "resident": "RESIDENT",
            "resident and ordinarily resident": "RESIDENT",
            "ordinarily resident": "RESIDENT",
            "resident indian": "RESIDENT",
            "ror": "RESIDENT",
            "non-resident": "NON_RESIDENT",
            "non resident": "NON_RESIDENT",
            "nonresident": "NON_RESIDENT",
            "non resident indian": "NON_RESIDENT",
            "nri": "NON_RESIDENT",
            "nr": "NON_RESIDENT",
            "rnor": "RNOR",
            "not ordinarily resident": "RNOR",
            "resident but not ordinarily resident": "RNOR",
            "nor": "RNOR",
        },
    },
    {
        "name": "citizenship",
        "unit_field": "applies_to_citizenship",
        "predicate": "citizenship_status",
        "values": {
            "indian": "INDIAN",
            "indian citizen": "INDIAN",
            "india": "INDIAN",
            "pio": "PIO",
            "person of indian origin": "PIO",
            "oci": "PIO",
            "foreign": "FOREIGN",
            "foreign national": "FOREIGN",
            "neither": "FOREIGN",
        },
    },
)


def normalise(dimension, raw):
    """Map recorded wording onto a dimension value, or None.

    Spacing, hyphens and case are levelled first, so "Non-Resident" and
    "non  resident" reach the same entry. The lookup itself stays exact.
    Substring matching is the obvious alternative and it is dangerous
    here: "non-resident" contains "resident", so a system that guessed
    would answer confidently from the wrong half of the law. Better to
    return None and say so.

    None means one of two different things, and the caller must not
    treat them alike: nobody has recorded a value, or a value was
    recorded that this vocabulary cannot read. `uninterpretable` tells
    them apart.
    """
    text = " ".join(str(raw or "").replace("-", " ").lower().split())

    if not text:
        return None

    return dimension["values"].get(text)


def case_attributes(confirmed_facts):
    """Build the case side from confirmed facts only.

    `confirmed_facts` maps predicate to value and must already exclude
    anything merely proposed.
    """
    attributes = {}

    for dimension in DIMENSIONS:
        value = normalise(
            dimension, confirmed_facts.get(dimension["predicate"])
        )

        if value is not None:
            attributes[dimension["name"]] = value

    return attributes


def uninterpretable(confirmed_facts):
    """Predicates confirmed with a value this vocabulary cannot read.

    This is a fault on our side, not a gap on the client's. Asking again
    would be asking a reviewer to repeat themselves in words we have not
    told them we need, so it escalates instead.
    """
    unreadable = []

    for dimension in DIMENSIONS:
        raw = confirmed_facts.get(dimension["predicate"])

        if raw is None or not str(raw).strip():
            continue

        if normalise(dimension, raw) is None:
            unreadable.append(dimension["predicate"])

    return tuple(sorted(unreadable))


def restricts_on(unit):
    """The dimensions this unit narrows itself to."""
    narrowed = {}

    for dimension in DIMENSIONS:
        value = getattr(unit, dimension["unit_field"], None)
        value = (value or "").strip().upper()

        # NULL and ANY both mean the rule does not restrict here.
        if value and value != "ANY":
            narrowed[dimension["name"]] = value

    return narrowed


def applies(unit, attributes):
    """TRUE, FALSE or UNKNOWN, with the predicates that decided it.

    Returns (verdict, predicates). For UNKNOWN they are the case
    predicates that would settle it: those become missing facts, which
    is how the system turns "I cannot tell whether this applies" into a
    question rather than a silent exclusion. For FALSE it is the
    predicate that contradicted, so the record can say why a unit was
    put aside instead of merely that it was.
    """
    narrowed = restricts_on(unit)

    if not narrowed:
        return TRUE, ()

    unresolved = []

    for dimension in DIMENSIONS:
        name = dimension["name"]

        if name not in narrowed:
            continue

        known = attributes.get(name)

        if known is None:
            unresolved.append(dimension["predicate"])
            continue

        if known != narrowed[name]:
            # A contradiction is decided immediately. Nothing later can
            # make a resident-rule apply to a confirmed non-resident.
            return FALSE, (dimension["predicate"],)

    if unresolved:
        return UNKNOWN, tuple(sorted(set(unresolved)))

    return TRUE, ()


@dataclass(frozen=True)
class Partition:
    """Units split by whether they apply, and why."""

    applicable: tuple
    excluded: tuple
    unknown: tuple

    # unit_id -> the predicates that would settle an UNKNOWN. Kept per
    # unit because who asks matters: only verified guidance may put a
    # question to a client.
    unresolved_by_unit: dict

    # The predicates on which something was excluded outright.
    excluded_on: tuple

    @property
    def unresolved_predicates(self):
        merged = set()

        for predicates in self.unresolved_by_unit.values():
            merged.update(predicates)

        return tuple(sorted(merged))


def partition(units, attributes):
    """Split units by applicability and collect what is unresolved."""
    applicable = []
    excluded = []
    unknown = []
    unresolved_by_unit = {}
    excluded_on = set()

    for unit in units:
        verdict, predicates = applies(unit, attributes)

        if verdict == TRUE:
            applicable.append(unit)
        elif verdict == FALSE:
            excluded.append(unit)
            excluded_on.update(predicates)
        else:
            unknown.append(unit)
            unresolved_by_unit[unit.unit_id] = predicates

    return Partition(
        applicable=tuple(applicable),
        excluded=tuple(excluded),
        unknown=tuple(unknown),
        unresolved_by_unit=unresolved_by_unit,
        excluded_on=tuple(sorted(excluded_on)),
    )


@dataclass(frozen=True)
class Assessment:
    """The single applicability view both layers read."""

    attributes: dict
    partition: Partition

    # Verified and applicable outright: the only units an answer may
    # rest on.
    usable: tuple

    # Topics that verified guidance still covers for this client.
    covered_topics: frozenset

    # Facts that must be established before applicability can be
    # settled. Derived from verified units only.
    unresolved_predicates: tuple

    # Facts already established, in wording this vocabulary cannot
    # read. Never asked of the client: a professional resolves these.
    unreadable_predicates: tuple = ()

    @property
    def excluded(self):
        return self.partition.excluded

    @property
    def unknown(self):
        return self.partition.unknown

    @property
    def excluded_on(self):
        return self.partition.excluded_on


def assess(units, confirmed_facts):
    """Applicability for one case, computed once for every reader.

    The tool that shows evidence to the model and the layer that
    validates the model's answer must agree about what applies. So this
    is the only place that decides, and both read the same fields.
    """
    attributes = case_attributes(confirmed_facts)
    split = partition(units, attributes)

    usable = tuple(
        unit for unit in split.applicable if unit.professionally_verified
    )

    # An UNKNOWN unit may yet turn out to apply, so it does not leave
    # its topic uncovered: the honest blocker there is the missing fact,
    # not missing guidance. Reporting it as a coverage gap would send
    # the case to a professional saying "we have nothing on this" when
    # what is true is "we cannot tell until we know X". An excluded unit
    # does leave the topic uncovered, because for this client we
    # genuinely hold nothing on it.
    covered = frozenset(
        unit.topic
        for unit in split.applicable + split.unknown
        if unit.professionally_verified
    )

    # Only verified guidance may put a question to a client. An
    # unverified candidate can never be the basis of an answer, so it
    # must not become the reason we ask for a fact either.
    unresolved = set()

    for unit in split.unknown:
        if unit.professionally_verified:
            unresolved.update(
                split.unresolved_by_unit.get(unit.unit_id, ())
            )

    # A predicate already answered is never asked again. It is unset
    # here only because we cannot read the answer, which is ours to fix.
    unreadable = uninterpretable(confirmed_facts)
    unresolved.difference_update(unreadable)

    return Assessment(
        attributes=attributes,
        partition=split,
        usable=usable,
        covered_topics=covered,
        unresolved_predicates=tuple(sorted(unresolved)),
        unreadable_predicates=unreadable,
    )
