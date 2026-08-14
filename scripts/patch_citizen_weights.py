#!/usr/bin/env python3
"""
Idempotent patcher for ui/citizen-cognisance.html:
- Adds topic weight slider CSS
- Adds slider UI section after controls
- Adds JS weight/ranking helpers before renderList()
- Updates renderList sort logic
- Updates list-note text

Safe to run multiple times (won't duplicate injected blocks). Every injection is
guarded by a sentinel that is part of the injected text, so a second run reports
"already patched" rather than stacking a second copy.

Usage:
    python scripts/patch_citizen_weights.py                 # patch the default file
    python scripts/patch_citizen_weights.py --dry-run       # show the diff, write nothing
    python scripts/patch_citizen_weights.py --file path.html

A timestamped backup is written next to the target before the file is modified.
--dry-run makes no backup, because it makes no change.
"""

import argparse
import difflib
import pathlib
import re
import shutil
import sys
from datetime import datetime

# -------------------------
# Config
# -------------------------

#: Default target, resolved from this file so the script works from any cwd.
DEFAULT_SRC = pathlib.Path(__file__).resolve().parents[1] / "ui" / "citizen-cognisance.html"

CSS_SENTINEL = "/* ── Topic weight sliders ───────────────────────────────────────────── */"
HTML_SENTINEL = '<section class="weights" id="weights" aria-label="Topic priority weights">'
JS_SENTINEL = "// ── Topic weights + re-rank ──────────────────────────────────────────────"
SORT_SENTINEL = "if (weightsActive()) return topicScore(b) - topicScore(a);"

CSS_BLOCK = """
/* ── Topic weight sliders ───────────────────────────────────────────── */
.weights { border-bottom: 1px solid var(--rule); }
.weights-in {
  display: flex; align-items: center; flex-wrap: wrap;
  gap: 10px 18px; padding-block: 11px;
}
.slider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px 24px; flex: 1;
}
.slider-row { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.slider-label {
  font-family: var(--f-mono); font-size: 10px; font-weight: 500;
  letter-spacing: .08em; color: var(--ink-3); min-width: 160px;
}
.slider-row input[type=range] {
  flex: 1; accent-color: var(--accent); cursor: pointer;
}
.slider-val {
  font-family: var(--f-mono); font-size: 10.5px; font-weight: 600;
  color: var(--accent); min-width: 26px; text-align: right;
  font-variant-numeric: tabular-nums;
}
.weights-reset { margin-left: auto; }
"""

SLIDER_HTML = """
  <!-- ═══ 3b · Topic weight sliders ═══════════════════════════════════ -->
  <section class="weights" id="weights" aria-label="Topic priority weights">
    <div class="shell weights-in">
      <span class="ctl-label">TOPIC WEIGHTS</span>
      <div class="slider-grid">
        <label class="slider-row">
          <span class="slider-label">Housing &amp; development</span>
          <input type="range" min="0" max="2" step="0.1" value="1" id="w-housing"
                 oninput="TOPIC_WEIGHTS.housing=+this.value;_sv('v-housing',this.value);renderList()">
          <span class="slider-val" id="v-housing">1.0</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Civic money</span>
          <input type="range" min="0" max="2" step="0.1" value="1" id="w-civic"
                 oninput="TOPIC_WEIGHTS.civic_money=+this.value;_sv('v-civic',this.value);renderList()">
          <span class="slider-val" id="v-civic">1.0</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Public safety</span>
          <input type="range" min="0" max="2" step="0.1" value="1" id="w-safety"
                 oninput="TOPIC_WEIGHTS.public_safety=+this.value;_sv('v-safety',this.value);renderList()">
          <span class="slider-val" id="v-safety">1.0</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">Environment &amp; utilities</span>
          <input type="range" min="0" max="2" step="0.1" value="1" id="w-env"
                 oninput="TOPIC_WEIGHTS.environment=+this.value;_sv('v-env',this.value);renderList()">
          <span class="slider-val" id="v-env">1.0</span>
        </label>
        <label class="slider-row">
          <span class="slider-label">State politics</span>
          <input type="range" min="0" max="2" step="0.1" value="1" id="w-state"
                 oninput="TOPIC_WEIGHTS.state_politics=+this.value;_sv('v-state',this.value);renderList()">
          <span class="slider-val" id="v-state">1.0</span>
        </label>
      </div>
      <button type="button" class="chip weights-reset" onclick="resetWeights()">Reset</button>
    </div>
  </section>
"""

JS_BLOCK = """
// ── Topic weights + re-rank ──────────────────────────────────────────────
const TOPIC_WEIGHTS = {
  housing: 1.0, civic_money: 1.0, public_safety: 1.0,
  environment: 1.0, state_politics: 1.0,
};
const TOPIC_MATCHERS = {
  housing:        s => /housing|zoning|permit|rezone|development|land.use/i.test(s.pattern || ''),
  civic_money:    s => /budget|allocation|contract|auditor|financ|appropriat/i.test(s.pattern || '')
                    || /BUDGET|FINANCE|AUDIT/.test(s.source_type || ''),
  public_safety:  s => /police|ppb|court|crime|incident|enforcement|safety/i.test(s.pattern || '')
                    || /PPB|COURT/.test(s.source_type || ''),
  environment:    s => /pge|pnw.natural|trimet|water.bureau|utility|environ|transit/i.test(s.pattern || '')
                    || /PGE|TRIMET|WATER|OHSU/.test(s.source_type || ''),
  state_politics: s => /olis|orestar|legislat|bill|measure|campaign|pac/i.test(s.pattern || '')
                    || /OLIS|ORESTAR/.test(s.source_type || ''),
};
function topicScore(node) {
  const e = entryFor(node.id), sig = e.sig;
  const base = sig ? (sig.confidence || 0) : 0;
  let tagSum = 0;
  if (sig) {
    for (const [t, w] of Object.entries(TOPIC_WEIGHTS))
      tagSum += w * (TOPIC_MATCHERS[t](sig) ? 1.0 : 0.0);
  }
  const hours    = e.hours != null ? e.hours : 48;
  const vPenalty = Math.min(hours / 48, 1.0);
  const wBreaking = Math.min(TOPIC_WEIGHTS.state_politics / 2, 1);
  return base + tagSum - vPenalty * (1 - wBreaking);
}
function weightsActive() {
  return Object.values(TOPIC_WEIGHTS).some(w => w !== 1.0);
}
function _sv(id, v) {
  document.getElementById(id).textContent = Number(v).toFixed(1);
}
function resetWeights() {
  Object.keys(TOPIC_WEIGHTS).forEach(k => TOPIC_WEIGHTS[k] = 1.0);
  [['housing','v-housing'],['civic','v-civic'],['safety','v-safety'],
   ['env','v-env'],['state','v-state']].forEach(([sid, vid]) => {
    document.getElementById('w-' + sid).value = 1;
    document.getElementById(vid).textContent = '1.0';
  });
  renderList();
}

"""

NEW_SORT_BLOCK = """  const visible = NODES.filter(passesFilter).sort((a, b) => {
    if (weightsActive()) return topicScore(b) - topicScore(a);
    const ha = entryFor(a.id).hours, hb = entryFor(b.id).hours;
    if (ha == null && hb == null) return (degree.get(b.id) || 0) - (degree.get(a.id) || 0);
    if (ha == null) return 1;
    if (hb == null) return -1;
    return ha - hb;
  });"""

#: Matches the whole `const visible = NODES.filter(...).sort(...)` statement, from its
#: own indentation through the closing `});`, without consuming the newlines on either
#: side — replacing them would weld the statement onto its neighbours.
SORT_RE = re.compile(
    r"^[ \t]*const\s+visible\s*=\s*NODES\.filter\(passesFilter\)\.sort\(\(a,\s*b\)\s*=>\s*\{"
    r".*?"
    r"^[ \t]*\}\);",
    re.MULTILINE | re.DOTALL,
)

OLD_NOTE = ": 'Ranked by signal freshness');"
NEW_NOTE = ": weightsActive() ? 'Ranked by topic weight score' : 'Ranked by signal freshness');"


def backup_file(path: pathlib.Path) -> pathlib.Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(f".pre-weights-{ts}.html")
    shutil.copy(path, bak)
    return bak


def inject_css(html: str) -> tuple[str, bool]:
    if CSS_SENTINEL in html:
        return html, False
    if "</style>" not in html:
        raise RuntimeError("Could not find </style>")
    return html.replace("</style>", CSS_BLOCK + "\n</style>", 1), True


def inject_slider_html(html: str) -> tuple[str, bool]:
    if HTML_SENTINEL in html:
        return html, False
    controls_pattern = r'(<section class="controls"[^>]*aria-label="Map filters"[^>]*>.*?</section>)'
    m = re.search(controls_pattern, html, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find controls section")
    return html[: m.end()] + SLIDER_HTML + html[m.end():], True


def inject_js(html: str) -> tuple[str, bool]:
    if JS_SENTINEL in html:
        return html, False
    anchor = "function renderList() {"
    if anchor not in html:
        raise RuntimeError("Could not find renderList()")
    return html.replace(anchor, JS_BLOCK + anchor, 1), True


def replace_sort_block(html: str) -> tuple[str, bool]:
    if SORT_SENTINEL in html:
        return html, False
    m = SORT_RE.search(html)
    if not m:
        raise RuntimeError("Could not find visible sort block")
    return html[: m.start()] + NEW_SORT_BLOCK + html[m.end():], True


def update_note_text(html: str) -> tuple[str, bool]:
    if NEW_NOTE in html:
        return html, False
    if OLD_NOTE not in html:
        raise RuntimeError("Could not find list-note string")
    return html.replace(OLD_NOTE, NEW_NOTE, 1), True


#: Each step is (label, function). Order matters only in that the sort-block
#: replacement must see the original statement, which the JS injection leaves alone.
STEPS = [
    ("CSS injected", inject_css),
    ("Slider HTML injected", inject_slider_html),
    ("JS injected", inject_js),
    ("Sort replaced", replace_sort_block),
    ("List note updated", update_note_text),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Idempotently add topic weight sliders to citizen-cognisance.html.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-f",
        "--file",
        type=pathlib.Path,
        default=DEFAULT_SRC,
        help=f"HTML file to patch (default: {DEFAULT_SRC})",
    )
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the diff and exit without writing anything (no backup is made)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src: pathlib.Path = args.file

    if not src.exists():
        print(f"Not found: {src}", file=sys.stderr)
        return 1

    original = src.read_text(encoding="utf-8")
    html = original
    changed: list[str] = []

    try:
        for label, step in STEPS:
            html, did = step(html)
            if did:
                changed.append(label)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("No file written. Original remains intact.", file=sys.stderr)
        return 2

    if not changed:
        print("No changes needed (already patched).")
        return 0

    if args.dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            html.splitlines(keepends=True),
            fromfile=f"a/{src.name}",
            tofile=f"b/{src.name}",
        )
        sys.stdout.writelines(diff)
        print("\n--dry-run: would apply:")
        for c in changed:
            print(f"  - {c}")
        print(f"\nNothing written. {src}")
        return 0

    bak = backup_file(src)
    print(f"Backup → {bak}")

    src.write_text(html, encoding="utf-8")
    print("\n✓ Applied changes:")
    for c in changed:
        print(f"  - {c}")
    print(f"\nDone. {src}  ({src.stat().st_size:,} bytes)")
    print(f"Open in browser to test: file://{src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
