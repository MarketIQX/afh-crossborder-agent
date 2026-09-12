"""The screens for a request for help.

Two of them, and they answer different questions.

`/requests` answers "what is waiting for me". It is a queue, oldest
first, and it exists because three open requests sat in the database for
a day with no way for a person to see them. Backend truth nobody can
read is not a feature.

The block on a case answers "why did this stop". That is the moment the
whole product turns on: an agent that says "I could not establish X"
and names what it consulted is doing something a fluent wrong answer
cannot be distinguished from. So it is not rendered as another card in a
column of cards -- it is the first thing on the page, and it reads as a
statement rather than a status.

Every value here is stored. The reason codes, the question, the state and
the timestamps all come from `app.knowledge_gaps`, whose own constraints
keep `gap_state` and `resolved_at` in agreement. Nothing on these screens
is computed at render time from something softer, and nothing is
phrased as certainty that the record does not hold.

The reason codes themselves are translated. They are the decision
layer's vocabulary and they stay that way in the record, but
`MISSING_KNOWLEDGE` is not a sentence to put in front of a professional
-- which is the same defect `ClientCopyGuard` exists to catch in client
copy, and it would be odd to guard one surface and not the other.
"""

from app.domain import decision
from app.reviewer.style_helpers import esc

# What each reason means, in the words the person reading it would use.
REASON_LABEL = {
    decision.SYSTEM_FAILURE: "An operation failed",
    decision.OUT_OF_SCOPE: "Outside the trained service",
    decision.SOURCE_CONFLICT: "Approved sources disagree",
    decision.MISSING_KNOWLEDGE: "No approved guidance covers this",
    decision.MISSING_FACTS: "A fact is not established",
}

# The severity a reason carries, so the queue reads at a glance. These
# map to the chip colours the console already uses elsewhere.
REASON_TONE = {
    decision.SYSTEM_FAILURE: "bad",
    decision.OUT_OF_SCOPE: "wait",
    decision.SOURCE_CONFLICT: "bad",
    decision.MISSING_KNOWLEDGE: "wait",
    decision.MISSING_FACTS: "",
}

STATE_LABEL = {
    "OPEN": ("Waiting for you", "wait"),
    "ANSWERED": ("Answered", "ok"),
    "WITHDRAWN": ("Withdrawn", ""),
}


def reason_chips(codes):
    """The reasons, most serious first, as the record ordered them."""
    return "".join(
        f'<span class="chip {REASON_TONE.get(code, "")}">'
        f"{esc(REASON_LABEL.get(code, code))}</span>"
        for code in codes or []
    )


def _question_lines(question):
    """The question as written, paragraph by paragraph.

    `question_for` joins its clauses with a blank line because each one
    is a separate thing that is missing. Rendering them as one block of
    prose would merge two problems into what looks like one.
    """
    parts = [p.strip() for p in (question or "").split("\n\n")]

    return "".join(
        f"<p>{esc(part)}</p>" for part in parts if part
    )


def stopped_block(requests, case_id):
    """Why Anika stopped, at the top of the case.

    Returns empty when nothing is outstanding, rather than a reassuring
    panel saying so. A case with no open request has nothing to say here
    and the screen should give the space to what does.
    """
    open_ones = [row for row in requests if row[3] == "OPEN"]

    if not open_ones:
        return ""

    blocks = []

    for gap_id, codes, question, _state, raised, _done, who, draft in (
        open_ones
    ):
        assigned = (
            f"Assigned to {esc(who)}" if who
            else "Not yet assigned to anyone"
        )

        draft_link = (
            f'<a class="quiet" href="/request/{esc(gap_id)}">'
            f"Read the full request</a>"
            if draft
            else '<span class="quiet">Request not yet drafted</span>'
        )

        blocks.append(
            f"""<div class="stopped">
  <p class="stopped-eyebrow">Anika stopped here</p>
  <div class="stopped-why">{_question_lines(question)}</div>
  <div class="stopped-meta">
    {reason_chips(codes)}
    <span class="quiet">{assigned}</span>
    <span class="quiet">Raised {raised:%d %b %H:%M}</span>
  </div>
  <div class="stopped-acts">{draft_link}</div>
</div>"""
        )

    return "".join(blocks)


def requests_page(rows):
    """The queue. Oldest first, because that is the one that has waited."""
    if not rows:
        return """<div class="queue"><div class="queue-inner">
  <div class="empty-queue">
    <h2>Nothing is waiting</h2>
    <p>Anika has not needed a professional judgement it could not
    make safely. Requests appear here the moment she stops on one.</p>
  </div>
</div></div>"""

    cards = []

    for (
        gap_id,
        case_id,
        reference,
        codes,
        question,
        raised,
        who,
        has_draft,
        state,
    ) in rows:
        assigned = esc(who) if who else "Unassigned"

        cards.append(
            f"""<article class="request">
  <header class="request-head">
    <a class="ref" href="/case/{esc(case_id)}">{esc(reference)}</a>
    <span class="quiet">{assigned}</span>
    <span class="quiet">{raised:%d %b %H:%M}</span>
  </header>
  <div class="request-why">{_question_lines(question)}</div>
  <footer class="request-foot">
    {reason_chips(codes)}
    <a class="quiet" href="/request/{esc(gap_id)}">Open request</a>
  </footer>
</article>"""
        )

    plural = "request" if len(rows) == 1 else "requests"

    return f"""<div class="queue"><div class="queue-inner">
  <p class="pane-title">{len(rows)} open {plural}</p>
  <div class="requests">{"".join(cards)}</div>
</div></div>"""


def request_page(gap_id, reference, case_id, codes, state, question,
                 draft, raised, resolved, who):
    """One request, with the letter that would go to a professional.

    The letter is shown as text rather than styled into the page. It is
    the artifact that would be sent, and dressing it up would make it
    harder to tell what a recipient actually receives.
    """
    label, tone = STATE_LABEL.get(state, (state, ""))

    timing = f"Raised {raised:%d %b %Y, %H:%M}"

    if resolved:
        timing += f" &middot; settled {resolved:%d %b %Y, %H:%M}"

    body = (
        f'<pre class="letter-text">{esc(draft)}</pre>'
        if draft
        else """<div class="empty">
  <p>No request has been drafted for this gap yet. The draft is
  written from the stored case and its retrieved guidance, so it can
  be produced at any time without asking the agent again.</p>
</div>"""
    )

    return f"""<div class="queue"><div class="queue-inner">
  <div class="request-detail">
  <header class="request-detail-head">
    <div>
      <p class="pane-title">Request for guidance</p>
      <h1><a href="/case/{esc(case_id)}">{esc(reference)}</a></h1>
      <p class="quiet">{timing}</p>
    </div>
    <div class="request-detail-state">
      <span class="chip {tone}">{esc(label)}</span>
      {reason_chips(codes)}
    </div>
  </header>

  <section class="block">
    <h2>What Anika could not establish</h2>
    <div class="stopped-why">{_question_lines(question)}</div>
  </section>

  <section class="block">
    <h2>The request as a professional receives it</h2>
    {body}
  </section>
  </div>
</div></div>"""
