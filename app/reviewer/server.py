"""The reviewer console.

One decision at a time. A reviewer opens this to answer a single
question: should these exact words go to this client. So the page shows
the client's message, what the agent concluded, the letter itself, and
the two things the reviewer can do about it. Everything else is quiet.

Three powers live behind three routes, and they are not the same power:

    /draft    composes the letter        runtime role
    /decide   authorises or returns it   reviewer role
    /send     carries it out             runtime role

The database enforces that separation. The runtime role holds no insert
privilege on approvals and the reviewer role holds none on dispatches,
so the process that sends cannot authorise, and the account that
authorises cannot send.

There is still no authentication, and the page says so. What changed is
narrower and load-bearing: the acting identity is bound when the process
starts, from CONSOLE_ACTING_REVIEWER or the `--acting-reviewer` flag,
and no query string or form field can change it. Case grants are only an
authorization boundary if the identity being checked is not the
caller's to choose, and it used to be.

So the honest description is a controlled identity, server-bound.
Nothing here verifies that the person at the keyboard is the reviewer
this process is bound to; that needs real authentication and is not
built. Selecting an assignee is data and may come from the browser.
Selecting the actor is not.
"""

import html
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import config
from app.dispatch import dispatcher, providers
from app.domain import access
from app.domain import approval as approval_domain
from app.domain import drafting
from app.domain import receipt
from app.reviewer import (
    inbox,
    queries,
    request_views,
    style,
    train_views,
    upload as upload_module,
    workqueue,
)
from app.training import pipeline, store as training_store

HOST = "127.0.0.1"
DEFAULT_PORT = 8080

VERDICT = {
    "MISSING_FACTS": "The client needs to tell us something first.",
    "MISSING_KNOWLEDGE": "We hold no approved guidance on this yet.",
    "SOURCE_CONFLICT": "Our sources disagree about this.",
    "OUT_OF_SCOPE": "This sits outside what we advise on.",
    "SUPPORTED_WITHIN_POLICY": "We can answer this from approved guidance.",
    "SYSTEM_FAILURE": "A lookup failed. Draw no conclusion from this run.",
}

NEEDS = {
    "draft": "needs a letter",
    "decision": "needs your decision",
    "send": "approved, not sent",
    "sent": "sent",
    "rejected": "returned",
}


from app.reviewer.style_helpers import esc  # noqa: F401


ACTING_REVIEWER_SETTING = "CONSOLE_ACTING_REVIEWER"


def page(title, body, reviewers, reviewer_id, flash=None, crumb="", nav=None, counts=0):
    acting = next(
        (
            name
            for rid, name, _email, _qual, _verify in reviewers
            if rid == reviewer_id
        ),
        "nobody",
    )

    flash_html = ""

    if flash:
        text, kind = flash
        flash_html = f'<div class="flash {esc(kind)}">{esc(text)}</div>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>{style.FONT_LINK}
<style>{style.CSS}</style></head><body>
<div class="app">
<header class="toolbar">
  <a class="brand" href="/">Cross-border compliance</a>
  <span class="sep"></span>
  {crumb}
  <span class="spacer"></span>
  <span class="caution">No sign-in &mdash; identity is server-bound, not authenticated</span>
  <span class="acting">Acting as {esc(acting)}</span>
</header>
{nav_html(nav, counts) if nav else ''}
{flash_html}
{body}
</div>
</body></html>"""


def render_empty(reviewers, reviewer_id):
    body = """<div class="panes"><div class="primary"><div class="empty">
  <h1 style="font-size:20px;margin:0 0 6px">No matter needs a decision.</h1>
  When the agent finishes reading an enquiry it appears here, with the
  letter it proposes to send.
</div></div><div class="context"></div></div>"""

    return page("Nothing waiting", body, reviewers, reviewer_id)


def _provenance_html(revision, run, trace, draft):
    facts = [("Decision", revision["decision_state"])]

    if run:
        facts += [
            ("Run", run[0]),
            ("Runner", run[2]),
            ("Model", run[3]),
        ]

    if draft:
        facts.append(("Content digest", draft["content_digest"]))

        if draft["approved_digest"]:
            facts.append(("Approved digest", draft["approved_digest"]))

    rows = "".join(
        f"<dt>{esc(k)}</dt><dd class='mono'>{esc(v)}</dd>" for k, v in facts
    )

    trace_html = "".join(
        f'<div class="{"refused" if err else ""}">'
        f"{esc(seq)}. {esc(name)}"
        f'{" &mdash; refused: " + esc(err) if err else ""}</div>'
        for seq, name, _args, _res, err in trace
    )

    return f"""<details class="prov">
  <summary>How this was produced</summary>
  <dl class="facts">{rows}</dl>
  <div class="trace">{trace_html}</div>
</details>"""


def _letter_html(draft):
    return f"""<div class="letter">
  <div class="env">
    <div><b>To</b> {esc(draft["recipient"])}</div>
    <div><b>Subject</b> {esc(draft["subject"])}</div>
  </div>
  <div class="text">{esc(draft["body_text"])}</div>
</div>"""


CHIP = {
    "MISSING_FACTS": ("wait", "Needs client facts"),
    "MISSING_KNOWLEDGE": ("bad", "Needs a professional"),
    "SOURCE_CONFLICT": ("bad", "Sources conflict"),
    "OUT_OF_SCOPE": ("wait", "Out of scope"),
    "SUPPORTED_WITHIN_POLICY": ("ok", "Covered for review"),
    "SYSTEM_FAILURE": ("bad", "Run failed"),
}


def _primary(case_id, revision, draft, conn, reviewer_id):
    """The letter and the decision. Returns (scroller_html, bar_html)."""
    state = revision["decision_state"]
    kind, label = CHIP.get(state, ("", state))

    verdict = f"""<div class="verdict">
  <h1>{esc(VERDICT.get(state, state))}</h1>
</div>"""

    if draft is None:
        if state in drafting.INTERNAL_ONLY:
            return (
                verdict
                + f"""<div class="held">{esc(drafting.INTERNAL_ONLY[state])}.
This does not go to the client; it needs a colleague.</div>""",
                '<span class="hint">Nothing to send on this matter.</span>',
            )

        return (
            verdict
            + """<div class="held">The agent has decided. No letter has
been written yet.</div>""",
            f"""<form method="post" action="/case/{esc(case_id)}/draft">
  <input type="hidden" name="revision_id" value="{esc(revision['revision_id'])}">
  <button type="submit">Write the letter</button>
  <span class="hint">You will read it before anything is sent.</span>
</form>""",
        )

    letter = _letter_html(draft)
    dispatch = workqueue.dispatch_for_approval(conn, draft["approval_id"])

    if dispatch:
        tone = {"SENT": "ok", "FAILED": "bad"}.get(dispatch["state"], "wait")
        detail = (
            f"Provider reference {dispatch['provider_message_id']}. "
            "The provider accepted it, which is not proof of delivery."
            if dispatch["state"] == "SENT"
            else (dispatch["failure_reason"] or "")
        )

        if dispatch["state"] == "SEND_UNKNOWN":
            detail += (
                " Delivery is undetermined, so this will not be retried."
            )

        return (
            verdict
            + '<p class="pane-title">The letter that was sent</p>'
            + letter
            + f"""<div class="held {tone}" style="margin-top:16px">
  <p class="{tone}">{esc(dispatch["state"])}</p>{esc(detail)}</div>""",
            f'<span class="hint">Sent. Nothing further to decide.</span>',
        )

    if draft["decision"] == "REJECTED":
        return (
            verdict
            + '<p class="pane-title">The letter you returned</p>'
            + letter
            + f"""<div class="held bad" style="margin-top:16px">
  <p class="bad">Returned</p>By {esc(draft["decided_by"])}.
  {esc(draft["note"] or "")}</div>""",
            '<span class="hint">Returned. Nothing will be sent.</span>',
        )

    if draft["decision"] == "APPROVED":
        return (
            verdict
            + '<p class="pane-title">Approved, not yet sent</p>'
            + letter
            + f"""<div class="held ok" style="margin-top:16px">
  <p class="ok">Approved by {esc(draft["decided_by"])}</p>
  Approving and sending are separate powers. This account can authorise
  a letter but cannot send one.</div>""",
            f"""<form method="post" action="/case/{esc(case_id)}/send">
  <input type="hidden" name="approval_id" value="{esc(draft["approval_id"])}">
  <button type="submit">Send it</button>
  <span class="hint">Carried out by the runtime, which cannot approve.</span>
</form>""",
        )

    return (
        verdict
        + '<p class="pane-title">The letter the agent proposes</p>'
        + letter,
        f"""<div class="acts">
  <form method="post" action="/case/{esc(case_id)}/decide">
      <input type="hidden" name="draft_id" value="{esc(draft["draft_id"])}">
    <input type="hidden" name="seen_digest"
           value="{esc(draft["content_digest"])}">
    <button type="submit" name="decision" value="APPROVED">Send as it stands</button>
    <button type="submit" name="decision" value="REJECTED" class="quiet">Return it</button>
    <input type="text" name="note" placeholder="What was wrong with it?">
  </form>
  <span class="hint">Approving binds these exact words.</span>

  <details class="editor">
    <summary>Edit and send</summary>
    <form method="post" action="/case/{esc(case_id)}/edit">
      <div class="pad">
              <input type="hidden" name="draft_id" value="{esc(draft["draft_id"])}">
        <input type="text" name="subject" id="edit-subject"
               value="{esc(draft["subject"])}">
        <textarea name="body_text" id="edit-body">{esc(draft["body_text"])}</textarea>
        <div class="row">
          <button type="submit">Save and approve my version</button>
          <span class="hint">Recorded as written by you, replacing
          Anika's. Sending is still carried out by the runtime, which
          cannot approve.</span>
        </div>
      </div>
    </form>
  </details>
</div>""",
    )


def _context(
    messages,
    revision,
    run,
    trace,
    draft,
    items,
    case_id,
    requests=(),
    receipt_data=None,
):
    """The panel beside the decision.

    `receipt_data` is the decision receipt, and when it is present the
    account of how this was produced comes from it rather than from the
    tuples above. The fallback path exists for the case where building
    it fails: a receipt is an explanation, and failing to explain a
    decision must not take the decision off the screen.
    """
    blocks = []

    # Ahead of the enquiry and the trace. A reviewer opening a case
    # needs to know whether anything is waiting on them before they
    # read anything else, and this is the claim the product is making.
    stopped = request_views.stopped_block(requests, case_id)

    if stopped:
        blocks.append(stopped)

    if messages:
        sender, subject, body_text, _r, _s, _m, _p = messages[0]
        blocks.append(
            f"""<div class="block"><h2>What the client wrote</h2>
  <p class="from">{esc(sender)} &middot; {esc(subject)}</p>
  <div class="quote">{esc(body_text)}</div></div>"""
        )

    blocks.append(
        f"""<div class="block"><h2>What the agent found</h2>
  <p class="finding">{esc(revision["summary"])}</p></div>"""
    )

    facts = [("Decision", revision["decision_state"])]

    if receipt_data:
        facts += [
            ("Run", receipt_data["run"]["run_id"]),
            ("Outcome", receipt_data["run"]["result_state"]),
            ("Runner", receipt_data["system"]["agent_runtime"]),
            ("Model", receipt_data["system"]["model_id"]),
            ("Prompt", receipt_data["system"]["prompt_version"]),
            (
                "Knowledge release",
                receipt_data["knowledge"]["release_relied_on"]
                or "none recorded",
            ),
            (
                "Cited units",
                str(len(receipt_data["knowledge"]["cited_unit_ids"])),
            ),
            (
                "Still unknown",
                ", ".join(receipt_data["facts"]["unknown"]) or "nothing",
            ),
            (
                "Actionable",
                "yes" if receipt_data["actionable"] else "no",
            ),
        ]
    elif run:
        facts += [("Run", run[0]), ("Runner", run[2]), ("Model", run[3])]

    if draft:
        facts.append(("Content digest", draft["content_digest"]))

        if draft["approved_digest"]:
            facts.append(("Approved digest", draft["approved_digest"]))

    if receipt_data:
        facts.append(("Receipt", receipt_data["receipt_digest"]))

    rows = "".join(
        f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in facts
    )

    if receipt_data:
        trace_html = "".join(
            f'<div class="{"refused" if call["error"] else ""}">'
            f'{esc(call["sequence"])}. {esc(call["tool"])}'
            f'{" &mdash; refused: " + esc(call["error"]) if call["error"] else ""}'
            f"</div>"
            for call in receipt_data["tools"]
        )
    else:
        trace_html = "".join(
            f'<div class="{"refused" if err else ""}">{esc(seq)}. {esc(name)}'
            f'{" &mdash; refused: " + esc(err) if err else ""}</div>'
            for seq, name, _a, _r, err in trace
        )

    blocks.append(
        f"""<div class="block"><h2>How this was produced</h2>
  <dl class="facts">{rows}</dl>
  <div class="trace">{trace_html}</div></div>"""
    )

    others = "".join(
        f'<a href="/case/{esc(i["case_id"])}">'
        f'<span class="ref">{esc(i["reference"])}</span>'
        f'<span class="need">'
        f'{esc(dict(inbox.LANES).get(i["lane"], ""))}</span></a>'
        for i in items
        if i["case_id"] != case_id
    )

    if others:
        blocks.append(
            f'<div class="block others"><h2>Other matters</h2>{others}</div>'
        )

    return "".join(blocks)


ACCESS_REFUSED = (
    "You do not have access to this matter, so nothing was done."
)

ACCESS_DENIED = (
    "<p>You do not have access to this matter. If you should, "
    "ask whoever administers the firm's cases to grant it.</p>"
)

NAV = (
    ("/", "Inbox"),
    ("/requests", "Requests"),
    ("/knowledge", "Knowledge"),
    ("/learning", "Learning"),
    ("/train", "Train Anika"),
)


def nav_html(current, counts=None):
    """The places this application has, with what needs a person."""
    out = []

    for href, label in NAV:
        on = " on" if href == current else ""
        badge = ""

        if href == "/" and counts:
            urgent = " urgent" if counts else ""
            badge = f'<span class="n{urgent}">{counts}</span>'

        out.append(
            f'<a class="nav-item{on}" href="{href}">{esc(label)}{badge}</a>'
        )

    return f'<nav class="nav">{"".join(out)}</nav>'


def _received(value):
    if value is None:
        return ""

    return value.strftime("%d %b %H:%M")


def _matter_row(item, reviewer_id):
    """One line in the queue. Sender and subject, because it is email."""
    why = ""

    if item["lane"] == inbox.NEEDS_TRIAGE and item["triage_detail"]:
        why = item["triage_detail"]
    elif item["lane"] == inbox.NEEDS_PROFESSIONAL and item["summary"]:
        why = item["summary"]
    elif item["lane"] == inbox.CLOSED and item["note"]:
        why = f"Returned: {item['note']}"
    elif item["summary"]:
        why = item["summary"]

    by = ""

    if item["authored_by"] == "REVIEWER":
        by = '<span class="by person">you wrote this</span>'
    elif item["authored_by"] == "AGENT":
        by = '<span class="by">Anika drafted</span>'

    return f"""<a class="matter"
   href="/case/{esc(item['case_id'])}">
  <div>
    <div class="who">{esc(item['sender'] or 'no sender recorded')}</div>
    <div class="subject">{esc(item['subject'])}</div>
    <div class="why">{esc(why[:150])}</div>
  </div>
  <div class="meta">
    <span class="ref">{esc(item['reference'])}</span>
    {_received(item['received_at'])}
    <div>{by}</div>
  </div>
</a>"""


ACTIONABLE = (
    inbox.NEEDS_DECISION,
    inbox.READY_TO_SEND,
    inbox.NEEDS_TRIAGE,
    inbox.NEEDS_PROFESSIONAL,
)


def render_inbox(people, reviewer_id, items, flash=None):
    """The working day."""
    if not items:
        body = """<div class="empty-queue">
  <h1>Nothing has arrived.</h1>
  When someone emails the practice, Anika reads it, decides what it
  needs, and it appears here. Nothing is sent without you.
</div>"""

        return page(
            "Inbox", body, people, reviewer_id, flash, nav="/", counts=0
        )

    lanes = []

    for key, label, rows in inbox.grouped(items):
        act = " act" if key in ACTIONABLE else ""
        lanes.append(
            f"""<section class="lane{act}">
  <header>
    <h2>{esc(label)}</h2>
    <span class="count">{len(rows)}</span>
  </header>
  <p class="note">{esc(inbox.LANE_NOTE[key])}</p>
  <div class="matters">
    {"".join(_matter_row(row, reviewer_id) for row in rows)}
  </div>
</section>"""
        )

    body = f'<div class="queue"><div class="queue-inner">{"".join(lanes)}</div></div>'

    return page(
        "Inbox",
        body,
        people,
        reviewer_id,
        flash,
        nav="/",
        counts=inbox.needs_you(items),
    )


def render_soon(title, explain, people, reviewer_id, nav):
    """A place that exists in the navigation but not yet in the product."""
    body = f"""<div class="empty-queue">
  <h1>{esc(title)}</h1>
  {esc(explain)}
</div>"""

    return page(title, body, people, reviewer_id, nav=nav)


SERVICE_KEY = "nri_india_tax_filing"


def active_service(conn):
    """The one corridor this build teaches. A second is a row, not code."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, name FROM app.services "
            "WHERE service_key = %s AND is_active",
            (SERVICE_KEY,),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError(f"service {SERVICE_KEY} is not active")

    return row[0], row[1]


def render_train(conn, people, reviewer_id, flash=None):
    service_id, service_label = active_service(conn)

    with workqueue.reviewer_connection() as rconn:
        docs = training_store.documents_for(rconn, service_id)
        cands = training_store.candidates_for(rconn, state="PENDING")

    body = train_views.train_page(
        service_label, reviewer_id, docs, cands
    )

    return page(
        "Train Anika", body, people, reviewer_id, flash, nav="/train"
    )


def render_requests(conn, people, reviewer_id, flash=None):
    """The queue of things Anika could not answer."""
    rows = queries.open_requests(conn)
    body = request_views.requests_page(rows)

    return page(
        "Requests", body, people, reviewer_id, flash, nav="/requests"
    )


def render_request(conn, gap_id, people, reviewer_id, flash=None):
    """One request, and the letter a professional would receive.

    Returns None for an unknown id so the handler can answer 404 rather
    than render a page about nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id::text, c.reference, c.id::text, g.reason_codes,
                   g.gap_state, g.question, g.draft_body, g.created_at,
                   g.resolved_at, r.display_name
            FROM app.knowledge_gaps g
            JOIN app.cases c ON c.id = g.case_id
            LEFT JOIN app.reviewers r ON r.id = g.assigned_reviewer_id
            WHERE g.id = %s
            """,
            (gap_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    body = request_views.request_page(*row)

    return page(
        row[1], body, people, reviewer_id, flash, nav="/requests"
    )

def render_learning(conn, people, reviewer_id, flash=None):
    service_id, _label = active_service(conn)

    with workqueue.reviewer_connection() as rconn:
        summary = training_store.learning_summary(rconn, service_id)
        taught = training_store.taught_units(rconn, service_id)
        rejected = training_store.candidates_for(rconn, state="REJECTED")

    body = train_views.learning_page(summary, taught, rejected)

    return page(
        "Learning", body, people, reviewer_id, flash, nav="/learning"
    )


# What to say on a case with no current recommendation, keyed on what
# became of its most recent run. `None` is the only one of these that
# means nothing has read the matter.
EMPTY_CASE = {
    None: (
        "Not looked at yet.",
        "The agent has not read this matter. Its conclusion and the "
        "letter it proposes will appear here once it runs.",
    ),
    "RUNNING": (
        "Anika is still working.",
        "A run is in progress. Nothing written so far is a "
        "recommendation, so there is nothing to decide yet.",
    ),
    "FAILED": (
        "Anika could not finish.",
        "The run broke before it completed. Whatever it wrote is kept "
        "on the record as evidence of the attempt, but none of it is a "
        "recommendation and no letter can be written from it.",
    ),
    "REFUSED": (
        "Anika declined to proceed.",
        "The run stopped deliberately rather than guessing. That "
        "refusal is the outcome, not a recommendation to act on.",
    ),
}


def render_case(conn, case_id, reviewers, reviewer_id, items, flash=None):
    header = queries.case_header(conn, case_id)

    if header is None:
        return None

    reference = header[1] or case_id[:8]
    revision = workqueue.latest_revision(conn, case_id)
    messages = queries.case_messages(conn, case_id)
    draft = workqueue.draft_for_case(conn, case_id)
    runs = queries.runs(conn, case_id)
    run = runs[0] if runs else None
    trace = queries.tool_calls(conn, run[0]) if run else []

    crumb = f'<span class="matter">{esc(reference)}</span>'

    if revision is None:
        # No current recommendation, which is not the same as never
        # having been read. A run that broke, declined, or has not
        # finished leaves its proposal on the case without standing,
        # and "not looked at yet" would be false about it.
        headline, explain = EMPTY_CASE.get(
            run[9] if run else None, EMPTY_CASE[None]
        )

        reason = (
            f'<div class="held">Recorded reason: {esc(run[10])}</div>'
            if run and run[10]
            else ""
        )

        body = f"""<div class="panes"><div class="primary"><div class="empty">
  <h1 style="font-size:20px;margin:0 0 6px">{esc(headline)}</h1>
  {esc(explain)}
</div>{reason}</div><div class="context"></div></div>"""

        return page(reference, body, reviewers, reviewer_id, flash, crumb)

    # An explanation that cannot be assembled must not take the
    # decision off the screen, so a failure here degrades the panel
    # rather than the page.
    try:
        with conn.cursor() as cur:
            receipt_data = receipt.build(cur, revision["revision_id"])
    except Exception:  # noqa: BLE001
        receipt_data = None

    scroller, bar = _primary(case_id, revision, draft, conn, reviewer_id)
    kind, label = CHIP.get(
        revision["decision_state"], ("", revision["decision_state"])
    )

    crumb += f'<span class="chip {kind}">{esc(label)}</span>'

    body = f"""<div class="panes">
  <div class="primary">
    <div class="scroller">{scroller}</div>
    <div class="bar">{bar}</div>
  </div>
  <div class="context">
    {_context(
        messages,
        revision,
        run,
        trace,
        draft,
        items,
        case_id,
        queries.requests_for_case(conn, case_id),
        receipt_data,
    )}
  </div>
</div>"""

    return page(reference, body, reviewers, reviewer_id, flash, crumb)


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewerConsole/2.0"

    def _send(self, status, body, headers=None):
        payload = body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")

        for key, value in (headers or {}).items():
            self.send_header(key, value)

        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)

        return {key: values[0] for key, values in parsed.items()}

    def _actor(self, reviewers=None):
        """Who is acting. From the process, never from the request.

        Accepts an id or an email so the setting can be readable, and
        resolves it against the active reviewers. Pinned to someone who
        is not one of them, this returns nobody: every case check then
        refuses, which is the safe direction for a misconfiguration.
        """
        if reviewers is None:
            with workqueue.app_connection() as conn:
                reviewers = workqueue.reviewers(conn)

        bound = (
            getattr(self.server, "acting_reviewer", "")
            or config.get(ACTING_REVIEWER_SETTING, "")
            or ""
        ).strip()

        if not bound:
            # Nothing pinned this process to a person. Fall back to a
            # deterministic choice rather than to whatever was asked
            # for: unpinned still must not mean browser-chosen.
            return reviewers[0][0] if reviewers else ""

        wanted = bound.lower()

        for rid, _name, email, _qual, _verify in reviewers:
            if wanted in (rid.lower(), (email or "").lower()):
                return rid

        return ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"console {fmt % args}\n")

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self._send(200, "ok")
            return

        flash = None

        if params.get("msg"):
            flash = (params["msg"][0], (params.get("kind") or ["ok"])[0])

        try:
            with workqueue.app_connection() as conn:
                people = workqueue.reviewers(conn)
                reviewer_id = self._actor(people)
                items = inbox.queue(conn)

                if path == "/":
                    self._send(
                        200,
                        render_inbox(people, reviewer_id, items, flash),
                    )
                    return

                if path == "/requests":
                    self._send(
                        200,
                        render_requests(conn, people, reviewer_id, flash),
                    )
                    return

                if path.startswith("/request/"):
                    gap_id = path[len("/request/") :]

                    with conn.cursor() as cur:
                        gap_case = access.case_of_gap(cur, gap_id)

                        if not access.may_access_case(
                            cur, reviewer_id, gap_case
                        ):
                            self._send(403, ACCESS_DENIED)
                            return

                    rendered = render_request(
                        conn,
                        gap_id,
                        people,
                        reviewer_id,
                        flash,
                    )

                    if rendered is None:
                        self._send(404, "no such request")
                        return

                    self._send(200, rendered)
                    return

                if path == "/train":
                    self._send(
                        200,
                        render_train(conn, people, reviewer_id, flash),
                    )
                    return

                if path == "/learning":
                    self._send(
                        200,
                        render_learning(conn, people, reviewer_id, flash),
                    )
                    return

                if path in ("/knowledge",):
                    titles = {
                        "/knowledge": (
                            "Knowledge",
                            "What Anika knows, where each piece came from, "
                            "and how far up the verification ladder it "
                            "sits. Not built yet.",
                        ),
                        "/learning": (
                            "Learning",
                            "What Anika has been taught, by whom, and "
                            "where a lesson has since been reused. Not "
                            "built yet.",
                        ),
                        "/train": (
                            "Train Anika",
                            "Upload the documents a junior would be given, "
                            "review what Anika proposes to learn from "
                            "them, and sign off what is correct. Not "
                            "built yet.",
                        ),
                    }
                    title, explain = titles[path]
                    self._send(
                        200,
                        render_soon(
                            title, explain, people, reviewer_id, path
                        ),
                    )
                    return

                if path.startswith("/case/"):
                    case_id = path[len("/case/") :]

                    # Before anything is read. The buttons on this page
                    # were already guarded; the page itself was not, so
                    # the private content arrived before the guard did.
                    with conn.cursor() as cur:
                        if not access.may_access_case(
                            cur, reviewer_id, case_id
                        ):
                            self._send(403, ACCESS_DENIED)
                            return

                    rendered = render_case(
                        conn, case_id, people, reviewer_id, items, flash
                    )

                    if rendered is None:
                        self._send(
                            404,
                            page(
                                "Not found",
                                "<main><h1>No such matter.</h1></main>",
                                people,
                                reviewer_id,
                            ),
                        )
                        return

                    self._send(200, rendered)
                    return
        except Exception as exc:  # noqa: BLE001
            self._send(
                500,
                f"<p>Query failed: {esc(exc.__class__.__name__)}: "
                f"{esc(exc)}</p>",
            )
            return

        self._send(404, "<p>No such page.</p>")

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/")

        content_type = (self.headers.get("Content-Type") or "").lower()
        self._upload = None

        if "multipart/form-data" in content_type:
            try:
                form, self._upload = upload_module.read_multipart(
                    self.headers, self.rfile
                )
            except upload_module.UploadRefused as exc:
                self._after_action("/train", "", str(exc), "bad")
                return
        else:
            form = self._form()

        # Not form.get("reviewer"). A form field naming the actor would
        # make every grant check below a question about a name the
        # caller chose.
        reviewer_id = self._actor()

        if path.startswith("/train/"):
            message, kind = self._do_training(path, form)
            self._after_action("/train", reviewer_id, message, kind)
            return

        if not path.startswith("/case/"):
            self._send(404, "<p>No such page.</p>")
            return

        case_id, _, action = path[len("/case/") :].partition("/")

        handler = {
            "draft": self._do_draft,
            "decide": self._do_decide,
            "edit": self._do_edit,
            "send": self._do_send,
        }.get(action)

        if handler is None:
            self._send(405, "<p>Not an action.</p>", {"Allow": "GET, POST"})
            return

        message, kind = handler(form)

        query = urllib.parse.urlencode({"msg": message, "kind": kind})

        # Where the outcome is readable. A reviewer refused on this case
        # cannot read its page either, so returning them to it would
        # replace the reason with a 403. They go to the inbox, which
        # they can see, carrying the same message.
        with workqueue.app_connection() as conn:
            with conn.cursor() as cur:
                readable = access.may_access_case(
                    cur, reviewer_id, case_id
                )

        destination = f"/case/{case_id}" if readable else "/"

        self._redirect(f"{destination}?{query}")

    def _do_draft(self, form):
        try:
            with workqueue.app_connection() as conn:
                # Composing took no reviewer, so nothing stopped a
                # visitor having a letter written on a case they cannot
                # see. The revision names the case; the case is what
                # needs authorising.
                with conn.cursor() as cur:
                    case_id = access.case_of_revision(
                        cur, form.get("revision_id")
                    )

                    if not access.may_access_case(
                        cur, self._actor(), case_id
                    ):
                        return (ACCESS_REFUSED, "bad")

                drafting.compose(conn, form["revision_id"])
        except (drafting.DraftingRefused, approval_domain.DraftRefused) as exc:
            return (str(exc), "bad")

        return ("Letter written. Read it before you decide.", "ok")

    def _do_decide(self, form):
        try:
            with workqueue.reviewer_connection() as conn:
                result = approval_domain.record_decision(
                    conn,
                    form["draft_id"],
                    self._actor(),
                    form.get("decision", "REJECTED"),
                    form.get("seen_digest", ""),
                    note=form.get("note") or None,
                )
        except approval_domain.ApprovalRefused as exc:
            return (str(exc), "bad")

        if result["decision"] == "APPROVED":
            return ("Approved. Sending is a separate step.", "ok")

        return ("Returned. Nothing will be sent.", "ok")

    def _after_action(self, where, reviewer_id, message, kind):
        """Redirect back with the outcome in the query string."""
        target = (
            f"{where}?msg={urllib.parse.quote(message[:400])}"
            f"&kind={urllib.parse.quote(kind)}"
        )
        self._redirect(target)

    def _do_training(self, path, form):
        action = path[len("/train/") :]

        if action == "upload":
            return self._do_upload(form)

        if action == "accept":
            return self._do_accept(form)

        if action == "reject":
            return self._do_reject(form)

        return ("Not a training action.", "bad")

    def _do_upload(self, form):
        if not self._upload:
            return ("Choose a file first.", "bad")

        # Whoever this process is bound to. Signing a document
        # into the corpus is an act of professional authority, so the
        # identity performing it cannot come off the form.
        reviewer_id = self._actor()

        if not reviewer_id:
            return (
                "This console is not bound to an active reviewer, so "
                "nothing can be signed.",
                "bad",
            )

        from app.training import documents as documents_module

        try:
            with workqueue.app_connection() as conn:
                service_id, _label = active_service(conn)
        except Exception as exc:  # noqa: BLE001
            return (f"No active service: {exc}", "bad")

        try:
            factory = pipeline.build_agent_factory()
        except Exception as exc:  # noqa: BLE001
            return (
                f"Anika cannot read documents right now: "
                f"{exc.__class__.__name__}. The file was not stored, so "
                f"nothing is half done.",
                "bad",
            )

        try:
            with workqueue.reviewer_connection() as rconn, \
                    workqueue.app_connection() as tconn:
                report = pipeline.ingest(
                    rconn,
                    tconn,
                    service_id,
                    self._upload["filename"],
                    self._upload["content"],
                    reviewer_id,
                    factory,
                )
        except documents_module.DocumentRefused as exc:
            return (str(exc), "bad")
        except training_store.TrainingRefused as exc:
            return (str(exc), "bad")
        except Exception as exc:  # noqa: BLE001
            return (
                f"Reading that document failed: {exc.__class__.__name__}.",
                "bad",
            )

        if report.get("failure"):
            return (report["failure"], "bad")

        if not report["proposed"]:
            return (
                f"Read {report['chunks']} passages from "
                f"{report['filename']} and proposed nothing. That is a "
                f"real answer: the passages did not establish anything "
                f"on their own.",
                "ok",
            )

        warned = (
            f" {report['not_supported']} of them failed the check and are "
            f"marked."
            if report["not_supported"]
            else ""
        )

        return (
            f"Read {report['chunks']} passages from "
            f"{report['filename']}. Anika proposes {report['proposed']} "
            f"thing(s) to learn, all awaiting your judgment.{warned}",
            "ok",
        )

    def _do_accept(self, form):
        try:
            with workqueue.app_connection() as conn:
                service_id, _label = active_service(conn)

            with workqueue.reviewer_connection() as rconn:
                result = training_store.accept_and_publish(
                    rconn,
                    form.get("candidate_id", ""),
                    # The professional recorded against the release.
                    self._actor(),
                    service_id,
                    form.get("source_locator", ""),
                )
        except training_store.TrainingRefused as exc:
            return (str(exc), "bad")
        except Exception as exc:  # noqa: BLE001
            return (f"Could not publish: {exc.__class__.__name__}", "bad")

        return (
            f"Taught. Anika may now use this, cited to you, from release "
            f"{result['release_id'][:8]}.",
            "ok",
        )

    def _do_reject(self, form):
        try:
            with workqueue.reviewer_connection() as rconn:
                training_store.reject(
                    rconn,
                    form.get("candidate_id", ""),
                    self._actor(),
                    form.get("note", ""),
                )
        except training_store.TrainingRefused as exc:
            return (str(exc), "bad")
        except Exception as exc:  # noqa: BLE001
            return (f"Could not reject: {exc.__class__.__name__}", "bad")

        return (
            "Rejected, with your reason kept. That reason is the most "
            "useful thing this produces.",
            "ok",
        )

    def _do_edit(self, form):
        """Rewrite the letter as the reviewer, then approve those words.

        Two writes, one click. The reviewer authors and approves; the
        runtime still performs the send and still cannot approve.
        """
        try:
            with workqueue.reviewer_connection() as conn:
                written = approval_domain.edit_draft(
                    conn,
                    form["draft_id"],
                    self._actor(),
                    form.get("subject", ""),
                    form.get("body_text", ""),
                )
                approval_domain.record_decision(
                    conn,
                    written["draft_id"],
                    self._actor(),
                    "APPROVED",
                    written["content_digest"],
                    note="edited by the reviewer before approval",
                )
        except approval_domain.DraftRefused as exc:
            return (str(exc), "bad")
        except approval_domain.ApprovalRefused as exc:
            return (str(exc), "bad")

        return (
            "Your version is saved and approved. Sending is a separate "
            "step, carried out by the runtime.",
            "ok",
        )

    def _do_send(self, form):
        # Simulated unless an operator has deliberately authorised real
        # mail in the shell. If authorised and unpreparable this raises
        # rather than quietly simulating: a reviewer told the message was
        # handed over must not have been shown a simulation instead.
        try:
            provider = providers.selected(
                config.get(providers.REAL_SEND_APPROVAL_KEY),
                token_file=config.get("GMAIL_TOKEN_FILE"),
                expected_address=config.get("GMAIL_EXPECTED_ADDRESS"),
            )
        except providers.ProviderRefused as exc:
            return (str(exc), "bad")

        try:
            with workqueue.app_connection() as conn:
                # The approval was grant-checked when it was
                # recorded. The person pressing send was not, until now.
                with conn.cursor() as cur:
                    case_id = access.case_of_approval(
                        cur, form.get("approval_id")
                    )

                    if not access.may_access_case(
                        cur, self._actor(), case_id
                    ):
                        return (ACCESS_REFUSED, "bad")

                result = dispatcher.dispatch(
                    conn, form["approval_id"], provider
                )
        except dispatcher.DispatchRefused as exc:
            return (str(exc), "bad")

        if result["state"] == providers.SENT:
            return (
                f"Handed to the provider, reference "
                f"{result['provider_message_id']}.",
                "ok",
            )

        return (f"Not sent: {result['state']}.", "bad")


def make_server(port=DEFAULT_PORT, acting_reviewer=None):
    """Bind the console to one acting identity for the life of the process.

    A test that needs a different actor starts a second server rather
    than passing a different parameter, because passing a parameter is
    exactly what must not work.
    """
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    httpd.acting_reviewer = acting_reviewer or ""

    return httpd


def _flag(argv, name, default=""):
    return argv[argv.index(name) + 1] if name in argv else default


def main(argv):
    port = (
        int(argv[argv.index("--port") + 1])
        if "--port" in argv
        else DEFAULT_PORT
    )

    httpd = make_server(
        port, _flag(argv, "--acting-reviewer")
    )
    host, bound = httpd.server_address[:2]

    print(f"Reviewer console on http://{host}:{bound}/")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
