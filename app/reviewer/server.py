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

There is no authentication. The reviewer is chosen from a control in the
header and the page says so plainly. Grants and role separation are
real; nothing yet verifies that the person clicking is who they claim to
be. That is the next boundary, not a solved one.
"""

import html
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.dispatch import dispatcher, providers
from app.domain import approval as approval_domain
from app.domain import drafting
from app.reviewer import inbox, queries, style, workqueue

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


def esc(value):
    return html.escape("" if value is None else str(value))


def page(title, body, reviewers, reviewer_id, flash=None, crumb="", nav=None, counts=0):
    options = "".join(
        f'<option value="{esc(rid)}"'
        f'{" selected" if rid == reviewer_id else ""}>{esc(name)}</option>'
        for rid, name, _email, _qual, _verify in reviewers
    ) or '<option value="">no reviewers</option>'

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
  <span class="caution">No sign-in &mdash; reviewer selected, not verified</span>
  <form method="get" action="">
    <label for="reviewer">Acting as</label>
    <select id="reviewer" name="reviewer" onchange="this.form.submit()">
      {options}
    </select>
  </form>
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
  <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
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
  <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
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
    <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
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
        <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
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


def _context(messages, revision, run, trace, draft, items, case_id):
    blocks = []

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

    if run:
        facts += [("Run", run[0]), ("Runner", run[2]), ("Model", run[3])]

    if draft:
        facts.append(("Content digest", draft["content_digest"]))

        if draft["approved_digest"]:
            facts.append(("Approved digest", draft["approved_digest"]))

    rows = "".join(
        f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in facts
    )

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


NAV = (
    ("/", "Inbox"),
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
   href="/case/{esc(item['case_id'])}?reviewer={esc(reviewer_id)}">
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
        body = """<div class="panes"><div class="primary"><div class="empty">
  <h1 style="font-size:20px;margin:0 0 6px">Not looked at yet.</h1>
  The agent has not read this matter. Its conclusion and the letter it
  proposes will appear here once it runs.
</div></div><div class="context"></div></div>"""

        return page(reference, body, reviewers, reviewer_id, flash, crumb)

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
    {_context(messages, revision, run, trace, draft, items, case_id)}
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

    @staticmethod
    def _pick_reviewer(requested, reviewers):
        ids = [row[0] for row in reviewers]

        if requested in ids:
            return requested

        return ids[0] if ids else ""

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
                reviewer_id = self._pick_reviewer(
                    (params.get("reviewer") or [""])[0], people
                )
                items = inbox.queue(conn)

                if path == "/":
                    self._send(
                        200,
                        render_inbox(people, reviewer_id, items, flash),
                    )
                    return

                if path in ("/knowledge", "/learning", "/train"):
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
        form = self._form()
        reviewer_id = form.get("reviewer", "")

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

        query = urllib.parse.urlencode(
            {"reviewer": reviewer_id, "msg": message, "kind": kind}
        )
        self._redirect(f"/case/{case_id}?{query}")

    def _do_draft(self, form):
        try:
            with workqueue.app_connection() as conn:
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
                    form["reviewer"],
                    form.get("decision", "REJECTED"),
                    form.get("seen_digest", ""),
                    note=form.get("note") or None,
                )
        except approval_domain.ApprovalRefused as exc:
            return (str(exc), "bad")

        if result["decision"] == "APPROVED":
            return ("Approved. Sending is a separate step.", "ok")

        return ("Returned. Nothing will be sent.", "ok")

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
                    form["reviewer"],
                    form.get("subject", ""),
                    form.get("body_text", ""),
                )
                approval_domain.record_decision(
                    conn,
                    written["draft_id"],
                    form["reviewer"],
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
        provider = providers.SimulatedProvider()

        try:
            with workqueue.app_connection() as conn:
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


def make_server(port=DEFAULT_PORT):
    return ThreadingHTTPServer((HOST, port), Handler)


def main(argv):
    port = (
        int(argv[argv.index("--port") + 1])
        if "--port" in argv
        else DEFAULT_PORT
    )

    httpd = make_server(port)
    host, bound = httpd.server_address[:2]

    print(f"Reviewer console on http://{host}:{bound}/")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
