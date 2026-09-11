"""Read-only reviewer view.

A human needs to see exactly what the agent saw and exactly what it is
proposing, with the provenance attached, before anyone builds approval
or dispatch on top of it.

What is actually true: only GET is routed, every other method returns
405, the implemented handlers perform reads only, and no approve or send
control is rendered because neither capability exists in the system.

What is NOT true, and was previously claimed here: that the database
prevents this process from writing. The connection uses the runtime
role, which also performs ingestion writes, records proposed facts and
writes proposal revisions. A write bug in a GET handler would not be
stopped by the database. Making read-only a privilege rather than a
property of the code needs a separate restricted identity, with both a
permitted read and a denied write demonstrated. That does not exist yet,
so keep this interface local and do not treat it as an access control.

Deliberately built on the standard library. Adding a web framework now
would mean regenerating the dependency lock immediately before pinning
`strands-agents`, and the next real step is that pin. Swapping this for
FastAPI later is mechanical; the queries are already separate.

Usage:

    python -m app.reviewer.server            # serves on 127.0.0.1:8080
    python -m app.reviewer.server --port 9000
"""

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.reviewer import queries

HOST = "127.0.0.1"
DEFAULT_PORT = 8080

DECISION_LABELS = {
    "MISSING_FACTS": "Needs client facts",
    "MISSING_KNOWLEDGE": "Internal knowledge gap, needs a professional",
    "SOURCE_CONFLICT": "Sources conflict, a professional must resolve",
    "OUT_OF_SCOPE": "Out of scope, or could not be routed",
    "SUPPORTED_WITHIN_POLICY": (
        "Covered for review, not yet approved for the client"
    ),
    "SYSTEM_FAILURE": "System failure, draw no conclusion from this run",
}

VERIFICATION_LABELS = {
    "UNVERIFIED": "not verified",
    "SOURCE_RECORDED": "locator recorded, source content not captured",
    "SOURCE_VERIFIED": "source passage captured and checked",
    "PROFESSIONALLY_VERIFIED": "signed off by a qualified professional",
}

STYLE = """
:root { color-scheme: light; }
body {
  margin: 0; padding: 0 16px 48px;
  font: 14px/1.55 -apple-system, Segoe UI, Roboto, sans-serif;
  color: #17202a; background: #f7f8fa;
}
header {
  margin: 0 -16px 20px; padding: 14px 16px;
  background: #17202a; color: #fff;
}
header a { color: #9ecbff; }
h1 { font-size: 18px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 26px 0 8px; }
.banner {
  background: #fff4d6; border: 1px solid #e0c477;
  padding: 8px 12px; border-radius: 4px; margin-bottom: 18px;
}
.wrap { max-width: 1000px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; margin-bottom: 10px; }
th, td {
  text-align: left; padding: 6px 8px; border-bottom: 1px solid #e3e6ea;
  vertical-align: top;
}
th { background: #eef1f4; font-weight: 600; }
code, .mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.card {
  background: #fff; border: 1px solid #e3e6ea; border-radius: 4px;
  padding: 12px 14px; margin-bottom: 12px;
}
.tag {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 11px; font-family: ui-monospace, Consolas, monospace;
  background: #eef1f4; border: 1px solid #d5dae0;
}
.tag.warn { background: #fff4d6; border-color: #e0c477; }
.tag.bad { background: #fde8e8; border-color: #e6a1a1; }
.tag.ok { background: #e6f4ea; border-color: #9ccdae; }
pre {
  white-space: pre-wrap; margin: 6px 0 0; padding: 8px;
  background: #f2f4f6; border-radius: 3px; font-size: 12px;
}
.muted { color: #5b6673; }
"""


def esc(value):
    if value is None:
        return '<span class="muted">not set</span>'

    return html.escape(str(value))


def tag(text, kind=""):
    classes = f"tag {kind}".strip()
    return f'<span class="{classes}">{html.escape(str(text))}</span>'


def decision_tag(state):
    if state is None:
        return '<span class="muted">no proposal yet</span>'

    kind = {
        "SUPPORTED_WITHIN_POLICY": "ok",
        "SYSTEM_FAILURE": "bad",
        "SOURCE_CONFLICT": "bad",
    }.get(state, "warn")

    label = DECISION_LABELS.get(state, state)

    return f"{tag(state, kind)} {html.escape(label)}"


def page(title, body):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body><header><div class="wrap">
<h1>NRI professional agent, reviewer view</h1>
<div class="mono"><a href="/">all cases</a></div>
</div></header>
<div class="wrap">
<div class="banner">
Inspection interface. Its handlers only read, and no approval, edit or
send capability exists anywhere in the system yet. Read-only here is a
property of this code, not a database privilege. Check the verification
status shown against each source before relying on anything.
</div>
{body}
</div></body></html>"""


def render_index(conn):
    rows = queries.case_list(conn)

    if rows:
        cells = []

        for case_id, reference, lifecycle, service, messages, decision, _ in rows:
            cells.append(
                "<tr>"
                f'<td><a class="mono" href="/case/{esc(case_id)}">'
                f"{esc(reference or case_id[:8])}</a></td>"
                f"<td>{esc(service) if service else tag('untriaged', 'warn')}</td>"
                f"<td>{esc(lifecycle)}</td>"
                f"<td>{esc(messages)}</td>"
                f"<td>{decision_tag(decision)}</td>"
                "</tr>"
            )

        table = (
            "<table><tr><th>reference</th><th>service</th>"
            "<th>status</th><th>messages</th><th>latest decision</th></tr>"
            + "".join(cells)
            + "</table>"
        )
    else:
        table = '<p class="muted">No cases yet.</p>'

    quarantine = queries.quarantined_messages(conn)

    if quarantine:
        rows_html = "".join(
            "<tr>"
            f"<td>{esc(sender)}</td><td>{esc(subject)}</td>"
            f"<td class='mono'>{esc(received)}</td>"
            f"<td>{tag(status, 'bad')}</td>"
            "</tr>"
            for _mid, _pid, sender, subject, received, status in quarantine
        )
        quarantine_html = (
            "<h2>Quarantined messages</h2>"
            "<p class='muted'>Correlation could not safely choose a case. "
            "A human must decide. Nothing is guessed.</p>"
            "<table><tr><th>from</th><th>subject</th><th>received</th>"
            "<th>state</th></tr>" + rows_html + "</table>"
        )
    else:
        quarantine_html = ""

    return page(
        "Cases", f"<h2>Cases</h2>{table}{quarantine_html}"
    )


def _render_facts(conn, case_id, service_id):
    facts = queries.case_facts(conn, case_id)
    required = queries.required_facts(conn, service_id)

    confirmed = {f[0] for f in facts if f[2] == "CONFIRMED"}
    material = [r[0] for r in required if r[1]]
    missing = [p for p in material if p not in confirmed]

    if facts:
        rows = "".join(
            "<tr>"
            f"<td class='mono'>{esc(predicate)}</td>"
            f"<td>{esc(value)}</td>"
            f"<td>{tag(status, 'ok' if status == 'CONFIRMED' else 'warn')}</td>"
            f"<td>{tag(origin)}</td>"
            f"<td class='mono'>{esc(run_id)}</td>"
            "</tr>"
            for predicate, value, status, origin, run_id, _created in facts
        )
        facts_html = (
            "<table><tr><th>predicate</th><th>value</th><th>status</th>"
            "<th>origin</th><th>recorded by run</th></tr>"
            + rows
            + "</table>"
        )
    else:
        facts_html = '<p class="muted">No facts recorded yet.</p>'

    if missing:
        hints = {r[0]: r[2] for r in required}
        missing_html = (
            "<p><strong>Material facts still unconfirmed:</strong></p>"
            "<table><tr><th>predicate</th><th>what to ask</th></tr>"
            + "".join(
                f"<tr><td class='mono'>{esc(p)}</td>"
                f"<td>{esc(hints.get(p))}</td></tr>"
                for p in missing
            )
            + "</table>"
        )
    else:
        missing_html = (
            '<p class="muted">All material facts are confirmed.</p>'
            if material
            else ""
        )

    return f"<h2>Evidence on file</h2>{facts_html}{missing_html}"


def _render_units(conn, cited_ids):
    if not cited_ids:
        return '<p class="muted">No sources cited.</p>'

    resolved = queries.units_by_id(conn, cited_ids)
    blocks = []

    for unit_id in cited_ids:
        row = resolved.get(unit_id)

        if row is None:
            blocks.append(
                f'<div class="card">{tag("unresolved citation", "bad")} '
                f'<span class="mono">{esc(unit_id)}</span><br>'
                "This unit is not in the active release. It cannot be "
                "shown, and the proposal should not be relied on until a "
                "curator explains why.</div>"
            )
            continue

        (
            _uid,
            unit_key,
            topic,
            statement,
            locator,
            source_version,
            verification,
            passage,
            effective_from,
            effective_to,
            release_version,
        ) = row

        verification_kind = (
            "ok" if verification == "PROFESSIONALLY_VERIFIED" else "warn"
        )

        blocks.append(
            '<div class="card">'
            f"<strong>{esc(unit_key)}</strong> {tag(topic)} "
            f"{tag(verification, verification_kind)} "
            f'<span class="muted">'
            f"{esc(VERIFICATION_LABELS.get(verification, ''))}</span>"
            f"<div>{esc(statement)}</div>"
            f'<div class="mono muted">source: {esc(locator)}'
            f"{' / ' + esc(source_version) if source_version else ''}"
            f" &middot; release v{esc(release_version)}"
            f" &middot; effective {esc(effective_from)}"
            f" to {esc(effective_to) if effective_to else 'open'}</div>"
            + (
                f"<pre>{esc(passage)}</pre>"
                if passage
                else '<div class="muted">No source passage captured, so '
                "this citation shows a locator only.</div>"
            )
            + "</div>"
        )

    return "".join(blocks)


def _render_revisions(conn, case_id):
    rows = queries.revisions(conn, case_id)

    if not rows:
        return (
            "<h2>Proposal</h2>"
            '<p class="muted">The agent has not proposed anything yet.</p>'
        )

    blocks = ["<h2>Proposal revisions</h2>"]

    for index, row in enumerate(rows):
        (
            _rid,
            proposal_id,
            revision,
            state,
            summary,
            payload,
            cited,
            missing,
            release_id,
            requires_verification,
            run_id,
            created_at,
        ) = row

        current = " (current)" if index == 0 else ""

        verification_note = (
            tag("professional sign-off required", "warn")
            if requires_verification
            else tag("no verification flag", "ok")
        )

        blocks.append(
            '<div class="card">'
            f"<strong>Revision {esc(revision)}{current}</strong> "
            f"{decision_tag(state)} {verification_note}"
            f"<p>{esc(summary)}</p>"
            f'<div class="mono muted">'
            f"proposal {esc(proposal_id)} &middot; run {esc(run_id)} "
            f"&middot; release {esc(release_id)} &middot; {esc(created_at)}"
            "</div>"
            + (
                "<div><strong>Missing facts named:</strong> "
                f"<span class='mono'>{esc(', '.join(missing))}</span></div>"
                if missing
                else ""
            )
            + f"<pre>{esc(json.dumps(payload, indent=2, default=str))}</pre>"
            "</div>"
        )

        if index == 0:
            blocks.append("<h2>Sources the proposal relied on</h2>")
            blocks.append(_render_units(conn, list(cited)))

    return "".join(blocks)


def _render_runs(conn, case_id):
    rows = queries.runs(conn, case_id)

    if not rows:
        return ""

    table_rows = "".join(
        "<tr>"
        f"<td class='mono'>{esc(run_id)}</td>"
        f"<td>{tag(runner, 'warn' if runner == 'DETERMINISTIC_STUB' else 'ok')}</td>"
        f"<td class='mono'>{esc(model_id)}</td>"
        f"<td>{tag(state, 'ok' if state == 'SUCCEEDED' else 'bad')}</td>"
        f"<td>{esc(latency)}</td><td>{esc(tools)}</td>"
        f"<td>{esc(reason)}</td>"
        "</tr>"
        for (
            run_id,
            _operation,
            runner,
            model_id,
            _prompt,
            _release,
            _started,
            latency,
            tools,
            state,
            reason,
            _builder,
        ) in rows
    )

    newest_run = rows[0][0]
    calls = queries.tool_calls(conn, newest_run)

    calls_html = "".join(
        "<tr>"
        f"<td>{esc(sequence)}</td><td class='mono'>{esc(name)}</td>"
        f"<td>{tag('REFUSED', 'bad') if error else tag('ok', 'ok')}</td>"
        f"<td>{esc(error) if error else ''}</td>"
        "</tr>"
        for sequence, name, _args, _result, error in calls
    )

    return (
        "<h2>Agent runs</h2>"
        "<table><tr><th>run</th><th>runner</th><th>model</th>"
        "<th>result</th><th>ms</th><th>tools</th><th>reason</th></tr>"
        + table_rows
        + "</table>"
        + (
            "<h2>Tool trace, newest run</h2>"
            "<table><tr><th>#</th><th>tool</th><th>outcome</th>"
            "<th>refusal reason</th></tr>" + calls_html + "</table>"
            if calls_html
            else ""
        )
    )


def render_case(conn, case_id):
    header = queries.case_header(conn, case_id)

    if header is None:
        return None

    (
        resolved_id,
        reference,
        lifecycle,
        service_id,
        service_key,
        service_name,
        created_at,
        _updated,
    ) = header

    messages = queries.case_messages(conn, resolved_id)

    if messages:
        first = messages[0]
        enquiry = (
            '<div class="card">'
            f"<strong>{esc(first[1])}</strong><br>"
            f'<span class="mono muted">from {esc(first[0])} '
            f"received {esc(first[3])} &middot; correlated by "
            f"{esc(first[5])}</span>"
            f"<pre>{esc(first[2])}</pre></div>"
        )

        later = "".join(
            '<div class="card">'
            f"<strong>{esc(subject)}</strong><br>"
            f'<span class="mono muted">from {esc(sender)} '
            f"received {esc(received)} &middot; {esc(method)}</span>"
            f"<pre>{esc(body)}</pre></div>"
            for sender, subject, body, received, _status, method, _pid in (
                messages[1:]
            )
        )

        later_html = (
            f"<h2>Later messages on this case</h2>{later}" if later else ""
        )
    else:
        enquiry = '<p class="muted">No message is attached to this case.</p>'
        later_html = ""

    scope = (
        f"{esc(service_key)} <span class='muted'>{esc(service_name)}</span>"
        if service_key
        else tag("untriaged, agent cannot act", "warn")
    )

    body = (
        f"<h2>Case {esc(reference or resolved_id)}</h2>"
        '<div class="card mono">'
        f"case {esc(resolved_id)}<br>service: {scope}<br>"
        f"status: {esc(lifecycle)}<br>opened: {esc(created_at)}"
        "</div>"
        f"<h2>Original enquiry</h2>{enquiry}{later_html}"
        + _render_facts(conn, resolved_id, service_id)
        + _render_revisions(conn, resolved_id)
        + _render_runs(conn, resolved_id)
    )

    return page(f"Case {reference or resolved_id}", body)


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewerView/1.0"

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/health":
            self._send(200, "ok", "text/plain; charset=utf-8")
            return

        try:
            with queries.connect() as conn:
                if path == "/":
                    self._send(200, render_index(conn))
                    return

                if path.startswith("/case/"):
                    case_id = path[len("/case/") :]
                    rendered = render_case(conn, case_id)

                    if rendered is None:
                        self._send(
                            404, page("Not found", "<p>No such case.</p>")
                        )
                        return

                    self._send(200, rendered)
                    return
        except Exception as exc:  # noqa: BLE001
            self._send(
                500,
                page(
                    "Error",
                    f"<p>Query failed: {esc(exc.__class__.__name__)}</p>",
                ),
            )
            return

        self._send(404, page("Not found", "<p>No such page.</p>"))

    def _reject(self):
        """Anything that is not a read is refused.

        The view is read-only. Approval and dispatch belong to a later
        milestone and must not be reachable from here by accident.
        """
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _reject  # noqa: N815
    do_PUT = _reject  # noqa: N815
    do_PATCH = _reject  # noqa: N815
    do_DELETE = _reject  # noqa: N815

    def log_message(self, fmt, *args):
        sys.stderr.write(f"reviewer {fmt % args}\n")


def make_server(port=DEFAULT_PORT):
    return ThreadingHTTPServer((HOST, port), Handler)


def main(argv):
    port = DEFAULT_PORT

    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])

    httpd = make_server(port)
    host, bound = httpd.server_address[:2]

    print(f"Reviewer view on http://{host}:{bound}/  (read-only, GET only)")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
