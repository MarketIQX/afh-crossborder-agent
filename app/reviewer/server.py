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
from app.reviewer import queries, style, workqueue

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


def page(title, body, reviewers, reviewer_id, flash=None):
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
<header class="bar">
  <a class="home" href="/">Cross-border compliance</a>
  <span class="unauth">No sign-in yet &mdash; the reviewer is selected, not verified</span>
  <span class="spacer"></span>
  <form method="get" action="">
    <label for="reviewer">Acting as</label>
    <select id="reviewer" name="reviewer" onchange="this.form.submit()">
      {options}
    </select>
  </form>
</header>
{flash_html}
{body}
</body></html>"""


def render_empty(reviewers, reviewer_id):
    body = """<main>
  <p class="eyebrow">Nothing waiting</p>
  <h1>No matter needs a decision.</h1>
  <p class="sub">When the agent finishes reading an enquiry, it appears
  here with the letter it proposes to send.</p>
</main>"""

    return page("Nothing waiting", body, reviewers, reviewer_id)


def _others_html(items, current_id):
    rows = "".join(
        f'<a href="/case/{esc(i["case_id"])}">'
        f'<span class="ref">{esc(i["reference"])}</span>'
        f'<span class="need">{esc(NEEDS.get(i["awaiting"], ""))}</span></a>'
        for i in items
        if i["case_id"] != current_id
    )

    if not rows:
        return ""

    return f'<div class="others"><h2>Other matters</h2>{rows}</div>'


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


def _action_html(case_id, revision, draft, conn, reviewer_id):
    state = revision["decision_state"]

    if draft is None:
        if state in drafting.INTERNAL_ONLY:
            return f"""<section>
  <h2>Nothing to send</h2>
  <div class="held">{esc(drafting.INTERNAL_ONLY[state])}. This does not
  go to the client; it needs a colleague.</div>
</section>"""

        return f"""<section>
  <h2>Next</h2>
  <p class="note">The agent has decided. No letter has been written yet.</p>
  <div class="decide">
    <form method="post" action="/case/{esc(case_id)}/draft">
      <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
      <input type="hidden" name="revision_id" value="{esc(revision['revision_id'])}">
      <button type="submit">Write the letter</button>
    </form>
  </div>
</section>"""

    letter = _letter_html(draft)
    dispatch = workqueue.dispatch_for_approval(conn, draft["approval_id"])

    if dispatch:
        kind = {"SENT": "ok", "FAILED": "bad"}.get(dispatch["state"], "wait")
        detail = (
            f"Provider reference {dispatch['provider_message_id']}. "
            "The provider accepted it, which is not proof of delivery."
            if dispatch["state"] == "SENT"
            else (dispatch["failure_reason"] or "")
        )

        if dispatch["state"] == "SEND_UNKNOWN":
            detail += " Delivery is undetermined, so this will not be retried."

        return f"""<section>
  <h2>Sent</h2>
  {letter}
  <div class="held {kind}" style="margin-top:16px">
    <p class="state {kind}">{esc(dispatch["state"])}</p>{esc(detail)}
  </div>
</section>"""

    if draft["decision"] == "REJECTED":
        return f"""<section>
  <h2>Returned</h2>
  {letter}
  <div class="held bad" style="margin-top:16px">Returned by
  {esc(draft["decided_by"])}. {esc(draft["note"] or "")}</div>
</section>"""

    if draft["decision"] == "APPROVED":
        return f"""<section>
  <h2>Approved, not yet sent</h2>
  {letter}
  <div class="held ok" style="margin-top:16px">Approved by
  {esc(draft["decided_by"])}. Approving and sending are separate powers:
  this account can authorise a letter but cannot send one.</div>
  <div class="decide">
    <form method="post" action="/case/{esc(case_id)}/send">
      <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
      <input type="hidden" name="approval_id" value="{esc(draft["approval_id"])}">
      <button type="submit">Send it</button>
    </form>
  </div>
</section>"""

    return f"""<section>
  <h2>Your decision</h2>
  {letter}
  <div class="decide">
    <form method="post" action="/case/{esc(case_id)}/decide">
      <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
      <input type="hidden" name="draft_id" value="{esc(draft["draft_id"])}">
      <input type="hidden" name="seen_digest" value="{esc(draft["content_digest"])}">
      <input type="text" name="note" placeholder="Note, optional">
      <button type="submit" name="decision" value="APPROVED">Approve this letter</button>
      <button type="submit" name="decision" value="REJECTED" class="quiet">Return it</button>
    </form>
  </div>
  <p class="note" style="margin-top:10px">Approving records these exact
  words. If they change afterwards, the approval stops being valid.</p>
</section>"""


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

    if revision is None:
        body = f"""<main>
  <p class="eyebrow">Matter {esc(reference)}</p>
  <h1>The agent has not looked at this yet.</h1>
  <p class="sub">Its conclusion and the letter it proposes will appear
  here once it runs.</p>
  {_others_html(items, case_id)}
</main>"""
        return page(reference, body, reviewers, reviewer_id, flash)

    enquiry_html = ""

    if messages:
        sender, subject, body_text, _received, _s, _m, _p = messages[0]
        enquiry_html = f"""<section>
  <h2>What the client wrote</h2>
  <p class="from">{esc(sender)} &middot; {esc(subject)}</p>
  <div class="quote">{esc(body_text)}</div>
</section>"""

    state = revision["decision_state"]

    body = f"""<main>
  <p class="eyebrow">Matter {esc(reference)}</p>
  <h1>{esc(VERDICT.get(state, state))}</h1>
  {enquiry_html}
  <section>
    <h2>What the agent found</h2>
    <div class="finding">{esc(revision["summary"])}</div>
  </section>
  {_action_html(case_id, revision, draft, conn, reviewer_id)}
  {_provenance_html(revision, run, trace, draft)}
  {_others_html(items, case_id)}
</main>"""

    return page(reference, body, reviewers, reviewer_id, flash)


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
                items = workqueue.queue(conn)

                if path == "/":
                    if not items:
                        self._send(200, render_empty(people, reviewer_id))
                        return

                    self._redirect(
                        f"/case/{items[0]['case_id']}?reviewer={reviewer_id}"
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
            self._send(500, f"<p>Query failed: {esc(exc.__class__.__name__)}</p>")
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
