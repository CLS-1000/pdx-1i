#!/usr/bin/env python3
"""
v2 patch for citizen-cognisance.html:
- Fixes TOPIC_MATCHERS to read `text` and `credibility` (not `pattern`/`confidence`)
- Adds /signals fetch on page load, builds signal lookup map by entity_ids
- Updates topicScore to use real signal data from the API

Idempotent. Run after patch_citizen_weights.py has already been applied.
"""

import pathlib
import re
import shutil
import sys
from datetime import datetime

SRC = pathlib.Path.home() / "pdx-1i/ui/citizen-cognisance.html"

# ── Sentinels ─────────────────────────────────────────────────────────────
SIGNALS_FETCH_SENTINEL = "// ── Signals index fetch"
MATCHERS_V2_SENTINEL = "s.text ||"

# ── Signals fetch + lookup map (injected before </script>) ────────────────
SIGNALS_FETCH = """
// ── Signals index fetch ──────────────────────────────────────────────────
// Loads all signals from /signals and builds a lookup map keyed by
// every entity_id on each signal so topicScore can match them to nodes.
const SIGNAL_INDEX = new Map();   // entity_id → latest signal object

(async () => {
  try {
    const res = await fetch('/signals?limit=200');
    if (!res.ok) return;
    const data = await res.json();
    for (const sig of (data.items || [])) {
      for (const eid of (sig.entity_ids || [])) {
        // Keep the most recent signal per entity (items are newest-first)
        if (!SIGNAL_INDEX.has(eid)) SIGNAL_INDEX.set(eid, sig);
      }
    }
  } catch (_) { /* API offline — matchers return false, ordering falls back to freshness */ }
})();
"""

# ── Replacement TOPIC_MATCHERS (reads `text` and `source_type`) ───────────
OLD_MATCHERS = """const TOPIC_MATCHERS = {
  housing:        s => /housing|zoning|permit|rezone|development|land.use/i.test(s.pattern || ''),
  civic_money:    s => /budget|allocation|contract|auditor|financ|appropriat/i.test(s.pattern || '')
                    || /BUDGET|FINANCE|AUDIT/.test(s.source_type || ''),
  public_safety:  s => /police|ppb|court|crime|incident|enforcement|safety/i.test(s.pattern || '')
                    || /PPB|COURT/.test(s.source_type || ''),
  environment:    s => /pge|pnw.natural|trimet|water.bureau|utility|environ|transit/i.test(s.pattern || '')
                    || /PGE|TRIMET|WATER|OHSU/.test(s.source_type || ''),
  state_politics: s => /olis|orestar|legislat|bill|measure|campaign|pac/i.test(s.pattern || '')
                    || /OLIS|ORESTAR/.test(s.source_type || ''),
};"""

NEW_MATCHERS = """const TOPIC_MATCHERS = {
  housing:        s => /housing|zoning|permit|rezone|development|land.use/i.test(s.text || ''),
  civic_money:    s => /budget|allocation|contract|auditor|financ|appropriat/i.test(s.text || '')
                    || /BUDGET|FINANCE|AUDIT/.test(s.source_type || ''),
  public_safety:  s => /police|ppb|court|crime|incident|enforcement|safety/i.test(s.text || '')
                    || /PPB|COURT/.test(s.source_type || ''),
  environment:    s => /pge|pnw.natural|trimet|water.bureau|utility|environ|transit/i.test(s.text || '')
                    || /PGE|TRIMET|WATER|OHSU/.test(s.source_type || ''),
  state_politics: s => /olis|orestar|legislat|bill|measure|campaign|pac/i.test(s.text || '')
                    || /OLIS|ORESTAR/.test(s.source_type || ''),
};"""

# ── Replacement topicScore (reads from SIGNAL_INDEX + uses `credibility`) ─
OLD_SCORE = """function topicScore(node) {
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
}"""

NEW_SCORE = """function topicScore(node) {
  const e   = entryFor(node.id);
  const sig = SIGNAL_INDEX.get(node.id) || e.sig || null;
  const base = sig ? (sig.credibility || sig.confidence || 0) : 0;
  let tagSum = 0;
  if (sig) {
    for (const [t, w] of Object.entries(TOPIC_WEIGHTS))
      tagSum += w * (TOPIC_MATCHERS[t](sig) ? 1.0 : 0.0);
  }
  const hours     = e.hours != null ? e.hours : 48;
  const vPenalty  = Math.min(hours / 48, 1.0);
  const wBreaking = Math.min(TOPIC_WEIGHTS.state_politics / 2, 1);
  return base + tagSum - vPenalty * (1 - wBreaking);
}"""


def main():
    if not SRC.exists():
        sys.exit(f"Not found: {SRC}")

    html = SRC.read_text(encoding="utf-8")
    any_change = False

    # 1. Fix TOPIC_MATCHERS
    if MATCHERS_V2_SENTINEL in html:
        print("  TOPIC_MATCHERS   · already patched")
    elif OLD_MATCHERS not in html:
        sys.exit("✗ TOPIC_MATCHERS: original block not found — was v1 patch applied?")
    else:
        html = html.replace(OLD_MATCHERS, NEW_MATCHERS, 1)
        print("  TOPIC_MATCHERS   ✓ updated (pattern→text)")
        any_change = True

    # 2. Fix topicScore
    if "sig.credibility || sig.confidence" in html:
        print("  topicScore       · already patched")
    elif OLD_SCORE not in html:
        sys.exit("✗ topicScore: original block not found")
    else:
        html = html.replace(OLD_SCORE, NEW_SCORE, 1)
        print("  topicScore       ✓ updated (confidence→credibility, SIGNAL_INDEX)")
        any_change = True

    # 3. Add signals fetch before </script>
    if SIGNALS_FETCH_SENTINEL in html:
        print("  Signals fetch    · already present")
    elif "</script>" not in html:
        sys.exit("✗ Signals fetch: </script> anchor not found")
    else:
        # Insert before the last </script>
        idx = html.rfind("</script>")
        html = html[:idx] + SIGNALS_FETCH + "\n" + html[idx:]
        print("  Signals fetch    ✓ injected")
        any_change = True

    if not any_change:
        print("\nNothing to do — all v2 patches already applied.")
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = SRC.with_suffix(f".pre-v2-{ts}.html")
    shutil.copy(SRC, bak)
    SRC.write_text(html, encoding="utf-8")
    print(f"\nWrote  {SRC}  ({SRC.stat().st_size:,} bytes)")
    print(f"Backup {bak}")


if __name__ == "__main__":
    main()
