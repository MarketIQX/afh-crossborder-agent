"""REQ01-REQ08. A screen may simplify what it shows. It may not invent it.

These surfaces exist because three requests for help sat in the database
for a day with no way for a person to read them. Backend truth nobody
can see is not a feature, and a screen that shows something the record
does not hold is worse than no screen.

So the properties checked here are about the join between the two.

Reasons must be translated, and the translation must be total. The
decision layer's vocabulary stays in the record -- the database checks
it -- but `MISSING_KNOWLEDGE` is not a sentence to put in front of a
professional. That is the same defect `ClientCopyGuard` catches in
client copy, and guarding one surface while leaking enums on another
would be arbitrary. REQ01 fails if any gap-worthy reason has no label,
so adding a seventh decision state cannot silently start printing a
column name to a reviewer.

Absence must read as absence. An empty queue says nothing is waiting; a
case with no outstanding request shows no panel at all rather than a
reassuring one. A settled request must not appear as though it were
open, which is a filter these checks pin down because the filter is the
only thing separating "waiting for you" from history.

No model, no database, no HTTP. Rows are passed in the shape the queries
return, so a change to those queries' column order fails here rather
than rendering something plausible and wrong.

Usage:

    python tests/request_surface_smoke.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.domain import decision, gaps  # noqa: E402
from app.reviewer import request_views  # noqa: E402

FAILURES = []
PASSES = []

WHEN = datetime(2026, 9, 12, 17, 31, tzinfo=timezone.utc)

QUESTION = (
    "No approved, applicable guidance covers: capital_gains_on_property."
    "\n\n"
    "These facts about the client are not yet established: "
    "Current country of tax residence."
)

LETTER = "Subject: Guidance needed: AFH-9209FE92 | MISSING_KNOWLEDGE"


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def queue_row(reference="AFH-9209FE92", codes=None, who="Anita Rao"):
    """One row in the shape `queries.open_requests` returns."""
    return (
        "gap-1",
        "case-1",
        reference,
        codes or [decision.MISSING_KNOWLEDGE, decision.MISSING_FACTS],
        QUESTION,
        WHEN,
        who,
        True,
        decision.MISSING_KNOWLEDGE,
    )


def case_row(state="OPEN", codes=None, draft=LETTER):
    """One row in the shape `queries.requests_for_case` returns."""
    return (
        "gap-1",
        codes or [decision.MISSING_KNOWLEDGE],
        QUESTION,
        state,
        WHEN,
        None if state == "OPEN" else WHEN,
        "Anita Rao",
        draft,
    )


def req01_every_reason_has_words():
    """A new decision state must not start printing an enum at a person."""
    missing = [
        code
        for code in gaps.GAP_REASONS
        if code not in request_views.REASON_LABEL
    ]

    check(
        "REQ01 EVERY REASON HAS WORDS",
        not missing,
        f"these reasons would render as raw enums: {missing}",
    )


def req02_no_surface_prints_a_raw_enum():
    """Whatever the state, the reviewer sees language."""
    surfaces = {
        "queue": request_views.requests_page([queue_row()]),
        "stop block": request_views.stopped_block([case_row()], "case-1"),
        "detail": request_views.request_page(
            "gap-1",
            "AFH-9209FE92",
            "case-1",
            [decision.MISSING_KNOWLEDGE],
            "OPEN",
            QUESTION,
            None,
            WHEN,
            None,
            "Anita Rao",
        ),
    }

    leaks = []

    for label, html in surfaces.items():
        for code in gaps.GAP_REASONS:
            if code in html:
                leaks.append(f"{label}: {code}")

    check(
        "REQ02 NO SURFACE PRINTS A RAW ENUM",
        not leaks,
        f"leaked: {leaks}",
    )


def req03_the_queue_counts_what_it_shows():
    one = request_views.requests_page([queue_row()])
    two = request_views.requests_page(
        [queue_row(), queue_row(reference="AFH-E49E4DC4")]
    )

    check(
        "REQ03 THE QUEUE COUNTS WHAT IT SHOWS",
        "1 open request" in one
        and "2 open requests" in two
        and two.count('class="request"') == 2,
        f"singular={'1 open request' in one} "
        f"plural={'2 open requests' in two} "
        f"cards={two.count('class=\"request\"')}",
    )


def req04_an_empty_queue_says_so():
    """Not a blank page, and not a fabricated row."""
    html = request_views.requests_page([])

    check(
        "REQ04 AN EMPTY QUEUE SAYS SO",
        "Nothing is waiting" in html
        and 'class="request"' not in html,
        "an empty queue did not explain itself",
    )


def req05_a_quiet_case_shows_no_panel():
    """A case with nothing outstanding gives the space to what matters."""
    settled = request_views.stopped_block(
        [case_row(state="ANSWERED")], "case-1"
    )
    withdrawn = request_views.stopped_block(
        [case_row(state="WITHDRAWN")], "case-1"
    )
    nothing = request_views.stopped_block([], "case-1")

    check(
        "REQ05 A QUIET CASE SHOWS NO PANEL",
        settled == "" and withdrawn == "" and nothing == "",
        f"answered={len(settled)}b withdrawn={len(withdrawn)}b "
        f"none={len(nothing)}b",
    )


def req06_only_open_requests_are_shown_as_waiting():
    """History belongs on the case, not in the thing demanding attention."""
    html = request_views.stopped_block(
        [case_row(state="ANSWERED"), case_row(state="OPEN")], "case-1"
    )

    check(
        "REQ06 ONLY OPEN REQUESTS ARE SHOWN AS WAITING",
        html.count("Anika stopped here") == 1,
        f"the block rendered {html.count('Anika stopped here')} time(s) "
        f"for one open and one answered request",
    )


def req07_separate_problems_stay_separate():
    """Two things missing must not merge into one paragraph of prose."""
    html = request_views.stopped_block([case_row()], "case-1")

    check(
        "REQ07 SEPARATE PROBLEMS STAY SEPARATE",
        html.count("<p>") >= 2,
        f"a two-clause question rendered {html.count('<p>')} "
        f"paragraph(s)",
    )


def req08_the_letter_is_shown_as_it_would_be_sent():
    """Styling the letter would hide what a recipient actually gets."""
    with_draft = request_views.request_page(
        "gap-1",
        "AFH-9209FE92",
        "case-1",
        [decision.MISSING_FACTS],
        "OPEN",
        QUESTION,
        LETTER,
        WHEN,
        None,
        "Anita Rao",
    )
    without = request_views.request_page(
        "gap-1",
        "AFH-9209FE92",
        "case-1",
        [decision.MISSING_FACTS],
        "OPEN",
        QUESTION,
        None,
        WHEN,
        None,
        "Anita Rao",
    )

    check(
        "REQ08 THE LETTER IS SHOWN AS IT WOULD BE SENT",
        "Guidance needed" in with_draft
        and 'class="letter-text"' in with_draft
        and "No request has been drafted" in without,
        f"drafted={'Guidance needed' in with_draft} "
        f"undrafted explains="
        f"{'No request has been drafted' in without}",
    )


CHECKS = (
    req01_every_reason_has_words,
    req02_no_surface_prints_a_raw_enum,
    req03_the_queue_counts_what_it_shows,
    req04_an_empty_queue_says_so,
    req05_a_quiet_case_shows_no_panel,
    req06_only_open_requests_are_shown_as_waiting,
    req07_separate_problems_stay_separate,
    req08_the_letter_is_shown_as_it_would_be_sent,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(
        f"\nREQUEST SURFACE: {len(PASSES)} passed, "
        f"{len(FAILURES)} failed"
    )

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
