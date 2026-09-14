"""The Train and Learning screens.

The design problem here is the opposite of the inbox. In the inbox we
want the reviewer to act quickly. Here we want them to hesitate.

A candidate is a machine's reading of a document, and accepting it makes
it something a client will be told. So the passage is shown at full
size, immediately, unfolded, next to the statement it is supposed to
support. Accepting takes a deliberate look; there is no accept-all and
no keyboard path that skips the passage.

Rejecting is made as easy as accepting, and the reason is required.
Every interface that makes approval one click and rejection a form
teaches people to approve, and the reason is the most useful thing this
whole system produces.
"""

from app.reviewer.style_helpers import esc

VERDICT_TONE = {
    "SUPPORTED": ("ok", "The check agrees the passage says this"),
    "NOT_SUPPORTED": ("bad", "The check says the passage does NOT say this"),
    "UNCLEAR": ("wait", "The check could not tell"),
    "UNCHECKED": ("", "Not checked"),
}

LADDER = (
    ("UNVERIFIED", "nothing recorded"),
    ("SOURCE_RECORDED", "a locator exists, content not captured"),
    ("SOURCE_VERIFIED", "passage captured and digested"),
    ("PROFESSIONALLY_VERIFIED", "a qualified professional signed it off"),
)


def _bytes(count):
    if count is None:
        return ""

    if count < 1024:
        return f"{count} B"

    if count < 1024 * 1024:
        return f"{count / 1024:.0f} KB"

    return f"{count / (1024 * 1024):.1f} MB"


def _when(value):
    return value.strftime("%d %b %H:%M") if value else ""


def upload_form(reviewer_id, service_label):
    return f"""<section class="lane">
  <header><h2>Teach Nicole from a document</h2></header>
  <p class="note">
    Upload what you would hand a junior: an extract of the Act, a
    circular, the practice's own note. Nicole reads it and proposes what
    it establishes. Nothing she proposes reaches a client, or even
    reaches her own retrieval, until you have read it against the source
    and signed it.
  </p>
  <form method="post" action="/train/upload"
        enctype="multipart/form-data" class="upload">
    <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
    <div class="row">
      <input type="file" name="document" id="train-file"
             accept=".pdf,.docx,.txt,.md" required>
      <button type="submit">Upload and read it</button>
    </div>
    <span class="hint">
      PDF, Word, text or Markdown, up to 12&nbsp;MB. Teaching the
      {esc(service_label)} corridor. A scanned PDF with no text layer
      has nothing to quote and will be refused rather than guessed at.
    </span>
  </form>
</section>"""


def document_rows(docs):
    if not docs:
        return """<section class="lane">
  <header><h2>Documents</h2></header>
  <p class="note">Nothing uploaded yet.</p>
</section>"""

    rows = []

    for doc in docs:
        pending = (
            f'<span class="by">{doc["pending"]} awaiting you</span>'
            if doc["pending"]
            else ""
        )
        accepted = (
            f'<span class="by person">{doc["accepted"]} taught</span>'
            if doc["accepted"]
            else ""
        )

        rows.append(
            f"""<div class="matter">
  <div>
    <div class="subject">{esc(doc["filename"])}</div>
    <div class="why">
      {doc["chunks"]} passages &middot; {doc["page_count"] or "?"} page(s)
      &middot; {_bytes(doc["byte_count"])}
      &middot; uploaded by {esc(doc["uploaded_by"])}
    </div>
  </div>
  <div class="meta">{_when(doc["uploaded_at"])}<div>{pending}{accepted}</div></div>
</div>"""
        )

    return f"""<section class="lane">
  <header><h2>Documents</h2><span class="count">{len(docs)}</span></header>
  <div class="matters">{"".join(rows)}</div>
</section>"""


def _passage_block(passage):
    where = ""

    if passage.get("page_from"):
        where = f'page {passage["page_from"]}'

        if passage.get("page_to") and passage["page_to"] != passage["page_from"]:
            where += f'&ndash;{passage["page_to"]}'

    return f"""<div class="passage">
  <div class="passage-head">
    Passage {passage["ordinal"]}{" &middot; " + where if where else ""}
    &middot; verbatim from the document
  </div>
  <div class="passage-text">{esc(passage["passage"])}</div>
</div>"""


def candidate_card(cand, reviewer_id):
    tone, verdict_words = VERDICT_TONE.get(
        cand["verifier_verdict"], ("", cand["verifier_verdict"])
    )

    passages = "".join(_passage_block(p) for p in cand["passages"])

    return f"""<article class="candidate">
  <div class="cand-head">
    <span class="chip">{esc(cand["topic"])}</span>
    <span class="hint">from {esc(cand["filename"])}</span>
  </div>

  <p class="pane-title">What Nicole proposes to learn</p>
  <p class="proposal">{esc(cand["guidance"])}</p>

  <div class="held {tone}">
    <p class="{tone}">{esc(verdict_words)}</p>
    {esc(cand["verifier_note"])}
    <div class="hint" style="margin-top:8px">
      A second model read the statement against only the passages below.
      It can warn you. It cannot accept anything.
    </div>
  </div>

  <p class="pane-title">The source, unedited</p>
  {passages}

  <div class="bar" style="border-radius:0 0 6px 6px">
    <form method="post" action="/train/accept">
      <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
      <input type="hidden" name="candidate_id"
             value="{esc(cand["candidate_id"])}">
      <input type="text" name="source_locator"
             placeholder="Citation for the file, e.g. s.6(1)(a) Income-tax Act">
      <button type="submit">I confirm this, teach it to Nicole</button>
    </form>
    <form method="post" action="/train/reject">
      <input type="hidden" name="reviewer" value="{esc(reviewer_id)}">
      <input type="hidden" name="candidate_id"
             value="{esc(cand["candidate_id"])}">
      <input type="text" name="note" placeholder="What is wrong with it?"
             required>
      <button type="submit" class="quiet">Reject</button>
    </form>
  </div>
</article>"""


def candidates_section(cands, reviewer_id):
    if not cands:
        return """<section class="lane">
  <header><h2>Awaiting your judgment</h2></header>
  <p class="note">
    Nothing is waiting. Upload a document and Nicole will propose what it
    establishes.
  </p>
</section>"""

    cards = "".join(candidate_card(c, reviewer_id) for c in cands)

    return f"""<section class="lane act">
  <header>
    <h2>Awaiting your judgment</h2>
    <span class="count">{len(cands)}</span>
  </header>
  <p class="note">
    Read the statement against the passage. Accepting makes it something
    a client will be told, over your name.
  </p>
  {cards}
</section>"""


def coverage_block(summary):
    cells = []

    for topic in summary["topics"]:
        count = summary["covered"].get(topic, 0)
        tone = "ok" if count else "bad"
        words = f"{count} verified" if count else "nothing verified"

        cells.append(
            f"""<div class="cover {tone}">
  <b>{esc(topic.replace("_", " "))}</b>
  <span>{words}</span>
</div>"""
        )

    return f"""<div class="covers">{"".join(cells)}</div>"""


def learning_page(summary, taught, rejected):
    rate = (
        f"{summary['acceptance_rate']}%"
        if summary["acceptance_rate"] is not None
        else "&mdash;"
    )

    metrics = f"""<div class="metrics">
  <div class="metric"><b>{summary["documents"]}</b><span>documents</span></div>
  <div class="metric"><b>{summary["proposed"]}</b><span>proposed</span></div>
  <div class="metric"><b>{summary["accepted"]}</b><span>you accepted</span></div>
  <div class="metric"><b>{summary["rejected"]}</b><span>you rejected</span></div>
  <div class="metric"><b>{summary["pending"]}</b><span>awaiting you</span></div>
  <div class="metric"><b>{rate}</b><span>accepted of decided</span></div>
</div>"""

    ladder = "".join(
        f"<dt>{esc(name)}</dt><dd>{esc(words)}</dd>" for name, words in LADDER
    )

    taught_rows = []

    for unit in taught:
        signed = (
            f'signed by {esc(unit["verified_by"])} '
            f'{_when(unit["verified_at"])}'
            if unit["verified_by"]
            else "not professionally verified"
        )

        taught_rows.append(
            f"""<div class="matter">
  <div>
    <div class="subject">{esc(unit["statement"][:150])}</div>
    <div class="why">
      {esc(unit["topic"].replace("_", " "))} &middot;
      {esc(unit["source_locator"][:90])} &middot; {signed}
    </div>
  </div>
  <div class="meta">{esc(unit["verification_status"])}</div>
</div>"""
        )

    taught_block = (
        f'<div class="matters">{"".join(taught_rows)}</div>'
        if taught_rows
        else '<p class="note">Nothing has been taught yet, so Nicole '
        'correctly refuses every question and escalates it to you.</p>'
    )

    rejected_rows = "".join(
        f"""<div class="matter">
  <div>
    <div class="subject">{esc(r["guidance"][:130])}</div>
    <div class="why">you said: {esc(r["reviewer_note"][:150])}</div>
  </div>
  <div class="meta">{esc(r["topic"].replace("_", " "))}</div>
</div>"""
        for r in rejected
    )

    rejected_block = (
        f"""<section class="lane">
  <header><h2>What you rejected</h2>
  <span class="count">{len(rejected)}</span></header>
  <p class="note">
    Your reasons are the most useful thing this produces. They are what
    a future version of the extraction prompt should be measured
    against.
  </p>
  <div class="matters">{rejected_rows}</div>
</section>"""
        if rejected
        else ""
    )

    return f"""<div class="queue"><div class="queue-inner">
  <section class="lane">
    <header><h2>What Nicole has learned</h2></header>
    {metrics}
    <p class="note">
      Coverage is counted in professionally verified units only, because
      that is the only rung the retrieval gate accepts. A topic with
      nothing verified is a topic Nicole will refuse to answer on, which
      is the correct behaviour and not a defect.
    </p>
    {coverage_block(summary)}
  </section>

  <section class="lane">
    <header><h2>Everything signed, newest first</h2></header>
    {taught_block}
  </section>

  {rejected_block}

  <section class="lane">
    <header><h2>The verification ladder</h2></header>
    <p class="note">
      A unit cannot claim either of the top two rungs without a captured
      passage, its digest and a timestamp. That is a database
      constraint, not a convention.
    </p>
    <dl class="facts">{ladder}</dl>
  </section>
</div></div>"""


def train_page(service_label, reviewer_id, docs, cands):
    return f"""<div class="queue"><div class="queue-inner">
  {upload_form(reviewer_id, service_label)}
  {candidates_section(cands, reviewer_id)}
  {document_rows(docs)}
</div></div>"""
