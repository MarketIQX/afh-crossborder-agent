"""The reviewer console's visual system, kept in one place.

Design intent, stated so it does not drift.

A reviewer is not scanning a dashboard. They are making one decision
about one client: should these exact words go out. So the screen shows
one decision at a time and defers to the content. The letter is the
hero, rendered close to how the client will read it. The machinery that
produced it, run ids, digests, tool traces, is present but quiet, because
it is needed occasionally and read rarely.

What was deliberately removed: a queue dashboard, tag clutter on every
value, borders around everything, monospace as a default voice. Density
suits scanning, and scanning is not the job here.

Hierarchy comes from space and type weight rather than from rules and
boxes. One accent exists and is spent once, on the action the reviewer
came to take. Semantic colour appears only where a state genuinely
changes what someone should do.
"""

# No remote font. The console must open instantly and must not reach
# out to a third party when a reviewer loads a client's matter, which
# also means it works with no network during a demonstration.
FONT_LINK = ""

SYSTEM_SANS = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", '
    '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)

SYSTEM_MONO = (
    'ui-monospace, "SF Mono", "Cascadia Mono", "Segoe UI Mono", '
    'Menlo, Consolas, monospace'
)

CSS = """
:root {
  --ground: #f5f6f7;
  --paper: #ffffff;
  --ink: #14181d;
  --ink-soft: #454f5a;
  --muted: #7b858f;
  --hair: #e4e7ea;
  --accent: #0a5c72;
  --ok: #1d6b46;
  --bad: #98301f;
  --wait: #8a5a00;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #0f1317;
    --paper: #171c21;
    --ink: #e8ecef;
    --ink-soft: #c2cad2;
    --muted: #838f9a;
    --hair: #252c33;
    --accent: #63aabf;
    --ok: #66bd92;
    --bad: #df8472;
    --wait: #d2a24c;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: SYSTEM_SANS_TOKEN;
  font-size: 16px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

.mono {
  font-family: SYSTEM_MONO_TOKEN;
  font-variant-numeric: tabular-nums;
}

/* ---- chrome: minimal, recedes ---------------------------------- */

header.bar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 28px;
  height: 56px;
  background: var(--paper);
  border-bottom: 1px solid var(--hair);
  flex-wrap: wrap;
}

.bar a.home {
  font-weight: 600;
  font-size: 15px;
  color: var(--ink);
  text-decoration: none;
  white-space: nowrap;
}

.bar .spacer { margin-left: auto; }

.bar form { display: flex; align-items: center; gap: 8px; }

.bar label { font-size: 13px; color: var(--muted); }

select, input[type="text"], textarea {
  font-family: inherit;
  font-size: 15px;
  color: var(--ink);
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 3px;
  padding: 7px 9px;
}

.unauth {
  font-size: 12.5px;
  color: var(--wait);
  white-space: nowrap;
}

/* ---- the one column that matters ------------------------------- */

main { max-width: 720px; margin: 0 auto; padding: 40px 28px 96px; }

.eyebrow {
  font-size: 12.5px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 6px;
}

h1 {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.25;
  margin: 0 0 6px;
  text-wrap: balance;
}

.sub { color: var(--muted); margin: 0 0 36px; }

/* The agent writes a paragraph of reasoning, not a caption. Given full
   body treatment so it can be read, rather than muted subtitle grey. */
.finding {
  color: var(--ink-soft);
  font-size: 15.5px;
  max-width: 62ch;
}

h1 { margin-bottom: 30px; }

section { margin-bottom: 40px; }

section > h2 {
  font-size: 12.5px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  margin: 0 0 12px;
}

/* ---- the client's words, and ours ------------------------------ */

.quote {
  border-left: 2px solid var(--hair);
  padding-left: 18px;
  color: var(--ink-soft);
  white-space: pre-wrap;
  font-size: 15.5px;
}

.from { font-size: 13.5px; color: var(--muted); margin-bottom: 8px; }

.verdict {
  font-size: 18px;
  line-height: 1.5;
  margin: 0 0 10px;
  text-wrap: pretty;
}

.because { color: var(--muted); font-size: 14.5px; margin: 0; }

/* The letter is the hero: paper, generous, close to what is sent. */
.letter {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-radius: 4px;
  overflow: hidden;
}

.letter .env {
  padding: 16px 24px;
  border-bottom: 1px solid var(--hair);
  font-size: 14px;
  color: var(--muted);
}

.letter .env b { color: var(--ink); font-weight: 500; }

.letter .text {
  padding: 26px 24px 30px;
  white-space: pre-wrap;
  font-size: 16px;
  line-height: 1.7;
}

/* ---- the single action ----------------------------------------- */

.decide {
  margin-top: 22px;
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.decide form { display: contents; }

button {
  font-family: inherit;
  font-size: 15px;
  font-weight: 500;
  padding: 11px 22px;
  border-radius: 3px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}

button:hover { filter: brightness(1.1); }
button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

button.quiet {
  background: transparent;
  color: var(--ink-soft);
  border-color: var(--hair);
}

button.quiet:hover { filter: none; border-color: var(--muted); }

/* ---- state, said once ------------------------------------------ */

.state { font-size: 15px; margin: 0 0 4px; }
.state.ok { color: var(--ok); }
.state.bad { color: var(--bad); }
.state.wait { color: var(--wait); }

.held {
  background: var(--paper);
  border: 1px solid var(--hair);
  border-left: 2px solid var(--wait);
  border-radius: 3px;
  padding: 16px 20px;
  font-size: 15px;
}

.held.ok { border-left-color: var(--ok); }
.held.bad { border-left-color: var(--bad); }

/* ---- provenance: present, quiet -------------------------------- */

details.prov { margin-top: 30px; border-top: 1px solid var(--hair); }

details.prov summary {
  cursor: pointer;
  padding: 14px 0 0;
  font-size: 13.5px;
  color: var(--muted);
  list-style: none;
}

details.prov summary::-webkit-details-marker { display: none; }
details.prov summary::before { content: "› "; }
details.prov[open] summary::before { content: "⌄ "; }

.facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 5px 20px;
  padding: 14px 0 4px;
  font-size: 13.5px;
}

.facts dt { color: var(--muted); white-space: nowrap; }
.facts dd { margin: 0; overflow-wrap: anywhere; }

.trace { font-size: 13.5px; }
.trace div { padding: 3px 0; }
.trace .refused { color: var(--bad); }

/* ---- moving between matters, secondary ------------------------- */

.others { margin-top: 44px; border-top: 1px solid var(--hair); padding-top: 18px; }
.others h2 { margin-bottom: 10px; }

.others a {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 9px 0;
  border-bottom: 1px solid var(--hair);
  color: inherit;
  text-decoration: none;
  font-size: 14.5px;
}

.others a:last-child { border-bottom: none; }
.others a:hover .ref { color: var(--accent); }
.others .ref { font-family: SYSTEM_MONO_TOKEN; font-size: 13.5px; }
.others .need { color: var(--muted); font-size: 13.5px; }

.flash {
  max-width: 720px;
  margin: 20px auto -16px;
  padding: 12px 18px;
  background: var(--paper);
  border: 1px solid var(--hair);
  border-left: 2px solid var(--accent);
  border-radius: 3px;
  font-size: 14.5px;
}

.flash.bad { border-left-color: var(--bad); }
.flash.ok { border-left-color: var(--ok); }

.empty { color: var(--muted); }

a { color: var(--accent); }

@media (max-width: 640px) {
  main { padding: 28px 20px 72px; }
  h1 { font-size: 22px; }
  .letter .text { padding: 20px 18px 24px; }
}
"""

CSS = CSS.replace("SYSTEM_SANS_TOKEN", SYSTEM_SANS).replace(
    "SYSTEM_MONO_TOKEN", SYSTEM_MONO
)
