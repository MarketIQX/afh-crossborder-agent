"""The reviewer console's visual system, kept in one place.

Design intent, stated so it does not drift.

This is an application, not a document. The first attempt was a dense
terminal. The second was a column of prose floating in whitespace with
no action visible until you scrolled. Both were wrong for the same
reason: neither started from what the reviewer is here to do.

They are here to decide whether one letter goes to one client. So:

- A toolbar carries the matter, its state and who is acting, always.
- The letter occupies the primary pane, because it is the thing being
  decided, rendered close to how the client will read it.
- Context sits in a second pane: what the client wrote, what the agent
  found, how it was produced. Available, subordinate, and scrollable
  without moving the letter.
- The decision sits in a bar pinned to the bottom of the primary pane.
  It never requires scrolling to reach.

Type is the platform's own. No webfont: the console must open instantly,
must not reach out to a third party when a reviewer opens a client's
matter, and must work with no network during a recorded demonstration.
"""

FONT_LINK = ""

SYSTEM_SANS = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", '
    '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)

SYSTEM_MONO = (
    'ui-monospace, "SF Mono", "Cascadia Mono", "Segoe UI Mono", '
    "Menlo, Consolas, monospace"
)

CSS = """
:root {
  --shell: #e9ebee;
  --paper: #ffffff;
  --sunk: #f4f5f7;
  --ink: #14181d;
  --ink-soft: #454f5a;
  --muted: #78828d;
  --hair: #dfe3e7;
  --hair-soft: #eceff2;
  --accent: #0a5c72;
  --accent-ink: #ffffff;
  --ok: #1d6b46;
  --bad: #98301f;
  --wait: #8a5a00;
  --shadow: 0 1px 2px rgba(20, 24, 29, .06), 0 6px 16px rgba(20, 24, 29, .04);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --shell: #0c1014;
    --paper: #171c22;
    --sunk: #12171c;
    --ink: #e8ecef;
    --ink-soft: #c2cad2;
    --muted: #8794a0;
    --hair: #262e36;
    --hair-soft: #1d242b;
    --accent: #4e9cb4;
    --accent-ink: #06181e;
    --ok: #66bd92;
    --bad: #df8472;
    --wait: #d2a24c;
    --shadow: 0 1px 2px rgba(0, 0, 0, .5);
  }
}

* { box-sizing: border-box; }

html, body { height: 100%; }

body {
  margin: 0;
  background: var(--shell);
  color: var(--ink);
  font-family: SYSTEM_SANS_TOKEN;
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}

.mono {
  font-family: SYSTEM_MONO_TOKEN;
  font-variant-numeric: tabular-nums;
}

/* ---- the shell ------------------------------------------------- */

.app {
  height: 100%;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
}

/* ---- toolbar: always present ----------------------------------- */

.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 18px;
  height: 50px;
  background: var(--paper);
  border-bottom: 1px solid var(--hair);
  overflow: hidden;
}

.toolbar .brand {
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  text-decoration: none;
  white-space: nowrap;
}

.toolbar .sep {
  width: 1px;
  height: 22px;
  background: var(--hair);
  flex: none;
}

.toolbar .matter {
  font-family: SYSTEM_MONO_TOKEN;
  font-size: 13px;
  white-space: nowrap;
}

.toolbar .spacer { margin-left: auto; }

.toolbar .caution {
  font-size: 12px;
  color: var(--wait);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.toolbar form { display: flex; align-items: center; gap: 7px; flex: none; }
.toolbar label { font-size: 12.5px; color: var(--muted); white-space: nowrap; }

select, input[type="text"] {
  font-family: inherit;
  font-size: 14px;
  color: var(--ink);
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 5px;
  padding: 6px 8px;
}

input[type="text"]:focus-visible, select:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
}

/* ---- two panes ------------------------------------------------- */

.panes {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  min-height: 0;
}

.primary {
  min-width: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  border-right: 1px solid var(--hair);
  background: var(--sunk);
}

.scroller { overflow-y: auto; padding: 20px 22px; min-height: 0; }

.context {
  min-width: 0;
  overflow-y: auto;
  padding: 20px 22px 40px;
  background: var(--shell);
}

/* ---- the verdict, stated once ---------------------------------- */

.verdict {
  margin: 0 0 18px;
  max-width: 640px;
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.verdict h1 {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
  margin: 0;
  text-wrap: balance;
}

.chip {
  display: inline-block;
  font-size: 11.5px;
  font-weight: 500;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--hair);
  background: var(--paper);
  color: var(--ink-soft);
  white-space: nowrap;
}

.chip.ok { border-color: var(--ok); color: var(--ok); }
.chip.bad { border-color: var(--bad); color: var(--bad); }
.chip.wait { border-color: var(--wait); color: var(--wait); }

/* ---- the letter: the thing being decided ----------------------- */

.pane-title {
  font-size: 11.5px;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  margin: 0 0 10px;
}

.letter {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 8px;
  box-shadow: var(--shadow);
  overflow: hidden;
  max-width: 640px;
}

.letter .env {
  padding: 13px 22px;
  border-bottom: 1px solid var(--hair-soft);
  background: var(--sunk);
  font-size: 13.5px;
  color: var(--muted);
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 12px;
}

.letter .env b { color: var(--ink); font-weight: 500; }

.letter .text {
  padding: 24px 22px 28px;
  white-space: pre-wrap;
  font-size: 15.5px;
  line-height: 1.68;
}

/* ---- the decision bar: pinned, never scrolled away ------------- */

.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 22px;
  background: var(--paper);
  border-top: 1px solid var(--hair);
}

.bar form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.bar .hint { font-size: 12.5px; color: var(--muted); }
.bar input[type="text"] { min-width: 190px; }

button {
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  padding: 9px 18px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: var(--accent-ink);
  cursor: pointer;
  white-space: nowrap;
}

button:hover { filter: brightness(1.12); }
button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

button.quiet {
  background: var(--paper);
  color: var(--ink-soft);
  border-color: var(--hair);
}

button.quiet:hover { filter: none; border-color: var(--muted); }

/* ---- context pane ---------------------------------------------- */

.block { margin-bottom: 26px; }

.block h2 {
  font-size: 11.5px;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  margin: 0 0 9px;
}

.from { font-size: 13px; color: var(--muted); margin: 0 0 8px; }

.quote {
  border-left: 2px solid var(--hair);
  padding-left: 14px;
  color: var(--ink-soft);
  white-space: pre-wrap;
  font-size: 14.5px;
  margin: 0;
}

.finding { color: var(--ink-soft); font-size: 14.5px; margin: 0; }

.held {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-left: 3px solid var(--wait);
  border-radius: 6px;
  padding: 14px 18px;
  font-size: 14.5px;
  max-width: 640px;
}

.held.ok { border-left-color: var(--ok); }
.held.bad { border-left-color: var(--bad); }
.held p { margin: 0 0 6px; font-weight: 500; }
.held p.ok { color: var(--ok); }
.held p.bad { color: var(--bad); }
.held p.wait { color: var(--wait); }

.facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 14px;
  font-size: 13px;
  margin: 0;
}

.facts dt { color: var(--muted); white-space: nowrap; }

.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-family: SYSTEM_MONO_TOKEN;
}

.trace { font-size: 13px; margin-top: 10px; }
.trace div { padding: 2px 0; color: var(--ink-soft); }
.trace .refused { color: var(--bad); }

.others a {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--hair);
  color: inherit;
  text-decoration: none;
  font-size: 13.5px;
}

.others a:last-child { border-bottom: none; }
.others a:hover .ref { color: var(--accent); }
.others .ref { font-family: SYSTEM_MONO_TOKEN; }
.others .need { color: var(--muted); }

.flash {
  padding: 9px 18px;
  background: var(--paper);
  border-bottom: 1px solid var(--hair);
  border-left: 3px solid var(--accent);
  font-size: 14px;
}

.flash.bad { border-left-color: var(--bad); }
.flash.ok { border-left-color: var(--ok); }

.empty { color: var(--muted); padding: 40px 22px; }

a { color: var(--accent); }

/* ---- narrow: one column, action stays reachable ---------------- */

@media (max-width: 900px) {
  body { overflow: auto; }
  .app { height: auto; }
  .panes { grid-template-columns: minmax(0, 1fr); }
  .primary { border-right: none; border-bottom: 1px solid var(--hair); }
  .scroller, .context { overflow: visible; }
  .bar { position: sticky; bottom: 0; }
  .toolbar { flex-wrap: wrap; height: auto; padding: 8px 14px; gap: 10px; }
}

/* ---- navigation: the app has places now ----------------------- */

.nav {
  display: flex;
  gap: 2px;
  align-items: stretch;
  background: var(--paper);
  border-bottom: 1px solid var(--hair);
  padding: 0 18px;
  overflow-x: auto;
}

.nav a {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 14px;
  height: 40px;
  font-size: 13.5px;
  color: var(--muted);
  text-decoration: none;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
}

.nav a:hover { color: var(--ink); }

.nav a.on {
  color: var(--ink);
  font-weight: 500;
  border-bottom-color: var(--accent);
}

.nav .n {
  font-family: SYSTEM_MONO_TOKEN;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--sunk);
  color: var(--muted);
}

.nav a.on .n { background: var(--accent); color: var(--accent-ink); }
.nav .n.urgent { background: var(--wait); color: #fff; }

/* ---- the queue ------------------------------------------------- */

.queue { overflow-y: auto; padding: 18px 22px 60px; }
.queue-inner { max-width: 940px; }

.lane { margin-bottom: 30px; }

.lane > header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 4px;
  padding: 0;
  height: auto;
  background: none;
  border: none;
}

.lane h2 {
  font-size: 14px;
  font-weight: 600;
  margin: 0;
}

.lane .count {
  font-family: SYSTEM_MONO_TOKEN;
  font-size: 12px;
  color: var(--muted);
}

.lane .note {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 10px;
}

.lane.act h2 { color: var(--accent); }

.matters {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 6px;
  overflow: hidden;
}

.matter {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px 16px;
  align-items: center;
  padding: 13px 18px;
  border-bottom: 1px solid var(--hair);
  color: inherit;
  text-decoration: none;
}

.matter:last-child { border-bottom: none; }
.matter:hover { background: var(--sunk); }

.matter .who {
  font-size: 12.5px;
  color: var(--muted);
  margin-bottom: 2px;
}

.matter .subject {
  font-size: 15px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.matter .why {
  font-size: 13px;
  color: var(--muted);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.matter .meta {
  text-align: right;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.matter .ref {
  font-family: SYSTEM_MONO_TOKEN;
  font-size: 12px;
  display: block;
}

.by {
  display: inline-block;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--hair);
  color: var(--muted);
}

.by.person { border-color: var(--ok); color: var(--ok); }

/* ---- the three actions on a draft ------------------------------ */

.acts { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.acts form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

details.editor { width: 100%; margin-top: 12px; }

details.editor > summary {
  cursor: pointer;
  font-size: 13.5px;
  color: var(--accent);
  list-style: none;
}

details.editor > summary::-webkit-details-marker { display: none; }
details.editor > summary::before { content: "\\u203a  "; }
details.editor[open] > summary::before { content: "\\u2304  "; }

details.editor .pad {
  padding: 14px 0 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

textarea, .editor input[type="text"] {
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  color: var(--ink);
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 5px;
  padding: 10px 12px;
  width: 100%;
  resize: vertical;
}

textarea { min-height: 190px; }

.editor .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

.empty-queue {
  padding: 50px 22px;
  color: var(--muted);
  max-width: 60ch;
}

.empty-queue h1 { font-size: 20px; margin: 0 0 8px; color: var(--ink); }

/* ---- training: designed to slow the reader down ---------------- */

.upload {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 6px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.upload .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.upload input[type="file"] { font-size: 14px; max-width: 100%; }

.candidate {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 6px;
  margin-bottom: 18px;
  overflow: hidden;
}

.cand-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 20px 0;
}

.candidate .pane-title { padding: 0 20px; margin: 16px 0 6px; }

/* The proposal is the machine's words. Sized so it cannot be skimmed
   past, but not so large it looks authoritative. */
.proposal {
  padding: 0 20px;
  margin: 0 0 14px;
  font-size: 16.5px;
  line-height: 1.55;
  max-width: 68ch;
}

.candidate .held { margin: 0 20px 4px; }

/* The source is shown unfolded, always. Anything behind a disclosure is
   something a hurried reviewer will accept without reading. */
.passage {
  margin: 0 20px 12px;
  border: 1px solid var(--hair);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  background: var(--sunk);
  overflow: hidden;
}

.passage-head {
  padding: 8px 14px;
  font-size: 12px;
  color: var(--muted);
  border-bottom: 1px solid var(--hair);
  font-family: SYSTEM_MONO_TOKEN;
}

.passage-text {
  padding: 14px;
  white-space: pre-wrap;
  font-size: 15px;
  line-height: 1.62;
  max-height: 340px;
  overflow-y: auto;
}

/* Reject is given the same weight as accept. An interface where
   approval is one click and rejection is a chore teaches approval. */
.candidate .bar { margin-top: 8px; }
.candidate .bar input[type="text"] { min-width: 240px; flex: 1; }

/* ---- coverage ---------------------------------------------------- */

.covers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 4px;
}

.cover {
  border: 1px solid var(--hair);
  border-left: 3px solid var(--hair);
  border-radius: 4px;
  background: var(--paper);
  padding: 11px 14px;
}

.cover b { display: block; font-size: 14.5px; font-weight: 600; }
.cover span { font-size: 13px; color: var(--muted); }
.cover.ok { border-left-color: var(--ok); }
.cover.ok span { color: var(--ok); }
.cover.bad { border-left-color: var(--wait); }
.cover.bad span { color: var(--wait); }

.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  gap: 1px;
  background: var(--hair);
  border: 1px solid var(--hair);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 14px;
}

.metric { background: var(--paper); padding: 13px 15px; }

.metric b {
  display: block;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -.02em;
  font-variant-numeric: tabular-nums;
}

.metric span { font-size: 12px; color: var(--muted); }

/* ---- where Anika stopped --------------------------------------- */

.stopped {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-left: 3px solid var(--wait);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 18px 22px 16px;
  margin-bottom: 18px;
}

.stopped-eyebrow {
  font-size: 11.5px;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--wait);
  margin: 0 0 8px;
}

.stopped-why p {
  margin: 0 0 8px;
  font-size: 15px;
  line-height: 1.5;
  color: var(--ink);
}

.stopped-why p:last-child { margin-bottom: 0; }

.stopped-meta,
.stopped-acts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-top: 12px;
}

.quiet { font-size: 12.5px; color: var(--muted); }

a.quiet { color: var(--accent); text-decoration: none; }
a.quiet:hover { text-decoration: underline; }

/* ---- the queue -------------------------------------------------- */

.requests {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 940px;
}

.request {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 16px 20px;
}

.request-head,
.request-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}

.request-head { margin-bottom: 10px; }
.request-foot { margin-top: 12px; }

.request-head .ref {
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  text-decoration: none;
}

.request-head .ref:hover { color: var(--accent); }

.request-why p {
  margin: 0 0 6px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink-soft);
}

.request-why p:last-child { margin-bottom: 0; }

/* ---- one request, with the letter ------------------------------- */

.request-detail { max-width: 760px; }

.request-detail-head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 20px;
}

.request-detail-head h1 {
  margin: 4px 0 6px;
  font-size: 22px;
  font-weight: 600;
}

.request-detail-head h1 a {
  color: var(--ink);
  text-decoration: none;
}

.request-detail-head h1 a:hover { color: var(--accent); }

.request-detail-state {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.letter-text {
  margin: 0;
  padding: 18px 20px;
  background: var(--sunk);
  border: 1px solid var(--hair-soft);
  border-radius: 6px;
  font-family: SYSTEM_MONO_TOKEN;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-soft);
  white-space: pre-wrap;
  overflow-x: auto;
}
"""

CSS = CSS.replace("SYSTEM_SANS_TOKEN", SYSTEM_SANS).replace(
    "SYSTEM_MONO_TOKEN", SYSTEM_MONO
)
