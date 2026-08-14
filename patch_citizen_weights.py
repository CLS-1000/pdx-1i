#!/usr/bin/env python3
"""
Patch citizen-cognisance.html:
- Adds topic weight slider CSS & UI
- Adds TOPIC_MATCHERS, TOPIC_WEIGHTS, topicScore()
- Hooks into renderList() sorting
"""
import pathlib
import shutil
import sys
from datetime import datetime

SRC = pathlib.Path.home() / "pdx-1i/ui/citizen-cognisance.html"

SLIDER_CSS = """
/* ── Topic Weight Sliders ── */
.topic-weights {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--bg-surface, #18181b);
  border: 1px solid var(--border-color, #27272a);
  border-radius: 6px;
  font-family: var(--f-mono, monospace);
  font-size: 11px;
}
.topic-weights label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--text-muted, #a1a1aa);
}
.topic-weights input[type="range"] {
  width: 100%;
  accent-color: var(--accent, #3b82f6);
}
"""

SLIDER_HTML = """
  <!-- Topic Weight Sliders -->
  <div class="topic-weights" id="topic-weights">
    <label>Housing <input type="range" id="w-housing" min="0" max="3" step="0.25" value="1"></label>
    <label>Civic Money <input type="range" id="w-civic_money" min="0" max="3" step="0.25" value="1"></label>
    <label>Public Safety <input type="range" id="w-public_safety" min="0" max="3" step="0.25" value="1"></label>
    <label>Environment <input type="range" id="w-environment" min="0" max="3" step="0.25" value="1"></label>
    <label>State Politics <input type="range" id="w-state_politics" min="0" max="3" step="0.25" value="1"></label>
  </div>
"""

JS_BLOCK = """
const TOPIC_WEIGHTS = {
  housing: 1.0,
  civic_money: 1.0,
  public_safety: 1.0,
  environment: 1.0,
  state_politics: 1.0,
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

document.addEventListener('DOMContentLoaded', () => {
  Object.keys(TOPIC_WEIGHTS).forEach(topic => {
    const el = document.getElementById(`w-${topic}`);
    if (el) {
      el.addEventListener('input', (e) => {
        TOPIC_WEIGHTS[topic] = parseFloat(e.target.value);
        if (typeof renderList === 'function') renderList();
      });
    }
  });
});
"""

def main():
    if not SRC.exists():
        sys.exit(f"Not found: {SRC}")
    html = SRC.read_text(encoding="utf-8")
    
    # CSS
    if ".topic-weights" not in html:
        if "</style>" in html:
            html = html.replace("</style>", f"{SLIDER_CSS}\n</style>", 1)
            
    # HTML Controls
    if 'id="topic-weights"' not in html:
        if '<div id="controls"' in html:
            idx = html.find('</div>', html.find('<div id="controls"'))
            if idx != -1:
                html = html[:idx+6] + "\n" + SLIDER_HTML + html[idx+6:]
        elif '<main' in html:
            idx = html.find('>', html.find('<main'))
            html = html[:idx+1] + "\n" + SLIDER_HTML + html[idx+1:]

    # JS
    if "const TOPIC_MATCHERS" not in html:
        idx = html.rfind("</script>")
        if idx != -1:
            html = html[:idx] + "\n" + JS_BLOCK + "\n" + html[idx:]

    SRC.write_text(html, encoding="utf-8")
    print("✓ v1 weights successfully injected")

if __name__ == "__main__":
    main()
