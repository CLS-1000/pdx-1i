# pdx-1i — Design system blueprint

The one map of every visual system in the product: which surfaces exist, which family
each belongs to, what each family's contract is, the invariants that hold everywhere,
and the one decision still open. Detail specs live next to their surface; this
document is the blueprint that relates them.

Nothing here is aspirational. Every token below is quoted from a shipped file, and
where a rule is enforced by a test, the test is named. The one section describing
something undecided says so in its first line.

---

## 1. The system map

Three visual families exist, deliberately distinct, one per audience:

| Family | Register | Surfaces | Spec | Enforced by |
|---|---|---|---|---|
| **SPEC-1 / SWITCHBOARD** | internal ops — phosphor terminal | `ui/webmap.html` | §2 below | `tests/test_webmap_ui.py` |
| **MCM Editorial** | public — warm editorial print | `ui/citizen-cognisance.html` (the GitHub Pages front page) | [`ui/DESIGN.md`](DESIGN.md) | — |
| **Brief** *(de facto, uncodified)* | document — the assembled brief | `ui/index.html` · the PDF (`src/pdx1/publication/pdf_renderer.py`) | §4 below | — |

The split is audience, not taste. An analyst watching feeds at 06:00 and a resident
opening the public map are reading the same records with different jobs to do; the
families exist so each surface signals which job it serves. A surface never mixes
families.

---

## 2. SPEC-1 / SWITCHBOARD — the ops family

Monochrome `#000` canvas; hierarchy carried by a white opacity ramp, never by hue;
exactly two hues in the whole system, reserved for machine status.

### Palette (normative — this *is* the test allowlist)

`tests/test_webmap_ui.py::test_the_vacancy_flag_is_the_only_hue` scans the file
against exactly this set. A colour not listed here fails CI:

```
#000  #fff  #ccc  #999  #666  #00ff00  #ff0000
```

### Tokens (as declared in `ui/webmap.html`)

| Token | Value | Role |
|---|---|---|
| `--bg` | `#000` | canvas |
| `--surface` | `rgba(10,10,10,.94)` | floating panels |
| `--fg1` | `#fff` | primary — the top of the ramp |
| `--fg2` | `#ccc` | secondary |
| `--fg3` | `#999` | tertiary |
| `--muted` | `#666` | quietest legible step |
| `--line` | `rgba(255,255,255,.20)` | rules |
| `--line-soft` | `rgba(255,255,255,.12)` | soft rules |
| `--vacant` | `#00ff00` | PASS / live status — one of the two permitted hues |
| `--alert` | `#ff0000` | CAUTION — the other |
| `--mono` | IBM Plex Mono → Courier Prime → Courier New → monospace | the only type family |

### Rules

- **Emphasis is brightness, not hue.** More important = closer to `#fff`. Severity
  and hierarchy both ride the ramp; a colour introduced to "highlight" something is
  a defect.
- **Two hues, machine status only.** `#00ff00` for PASS, `#ff0000` for CAUTION.
  Brightness of a hue may vary; the hue may not — a softened red reads as a third
  colour and breaks the rule (the comment in `webmap.html` says this at the token).
- **Both hues resolve through custom properties.**
  `test_status_hues_are_declared_as_tokens_not_inlined` exists because an inlined
  `rgba(255,0,0,…)` would slip a hue past the hex allowlist.
- **No external scripts, no external fonts** (`test_no_external_scripts_or_fonts`).
  The mono stack degrades to system monospace with no network.
- **No built-in dataset.** The surface draws from the API or says it cannot; it never
  falls back to baked-in nodes that would drift while looking authoritative
  (`test_no_node_registry_is_hardcoded`, `test_no_static_fallback_dataset`).

---

## 3. MCM Editorial — the public family

Fully specified in [`ui/DESIGN.md`](DESIGN.md); this section is the contract in
brief, not a replacement.

Warm neutrals (`--paper #F5F1E8`, `--ink #1A1815` — never `#fff`, never `#000`), one
accent per product (`--accent #14625C`, a civic teal — no flag blue, no primary red,
no safety orange anywhere), hierarchy by type and rule weight rather than decoration.
Three type families (Archivo display, IBM Plex Mono data, Source Sans 3 body) with
system fallbacks. The viz sits on a dark warm plate framed by the paper page.

Encodings never lean on colour alone: node freshness carries a distinct stroke
treatment per state, category is shape, size is degree. Two animations exist in the
whole family, both disabled under `prefers-reduced-motion`.

One documented exception rides this surface: its static fallback dataset names
individual officeholders, where the engine's registry is role-based by design.
`DESIGN.md §5` and the README both scope that exception; the role-based registry
remains the authority for anything the engine publishes.

---

## 4. The Brief family — de facto, and the one open decision

**Status: uncodified. The convergence question is undecided, and this section
documents the situation rather than settling it.**

The original Brief reader in `ui/index.html` carries a light multi-hue palette. The
District Map, Signal Feed, and Statistics views added there use a scoped SPEC-1
component palette; this does not decide whether the existing Brief reader should
converge:

| Token | Value | | Token | Value |
|---|---|---|---|---|
| `--navy` | `#1a1a2e` | | `--red` | `#e74c3c` |
| `--slate` | `#2c3e50` | | `--orange` | `#e67e22` |
| `--teal` | `#16a085` | | `--green` | `#27ae60` |
| `--bg` | `#f4f6f8` | | `--light` | `#ecf0f1` |
| `--card` | `#ffffff` | | `--muted` | `#7f8c8d` |

The load-bearing fact the open question has been missing: **the PDF brief uses the
same ink.** `pdf_renderer.py::_make_styles` sets `#1a1a2e` and `#2c3e50` — the brief
reader and the printed brief already share a palette. `index.html` is not an orphan
awaiting SPEC-1; it is half of an undeclared third family whose other half shipped.

That changes the shape of the decision:

1. **Codify the Brief family** — declare navy/slate/one-restrained-accent as the
   document register shared by reader and PDF, spec it, and strike the README's open
   question. Cheapest; matches what shipped; a printed brief on a `#000` canvas was
   never plausible anyway.
2. **Converge `index.html` to SPEC-1** — the README's original framing. Makes the
   reader consistent with the ops family, but strands the PDF (which cannot go
   monochrome-on-black) and so *creates* a single-surface family instead of removing
   one.
3. **Move the reader to MCM Editorial** — treat the brief as a public document.
   Defensible if the brief's audience is residents rather than analysts, but the
   reader serves the API the ops surfaces serve.

**Recommendation — not a decision:** option 1. It is the only option that reduces
the number of unexplained palettes to zero without restyling a shipped PDF. The
multi-hue set would need pruning to survive codification (four accent hues is a
palette, not a system; the family needs the neutrality posture of the other two —
red reserved for a stated meaning or absent). Whoever decides should also decide
what `--red`/`--orange`/`--green` *mean* in a brief, because today they are moods.

---

## 5. Invariants — every family, every surface

These derive from the engine's publication rules (`CLAUDE.md`, "not negotiable") and
are design constraints, not styling preferences. A surface in any family:

1. **Shows seats, never people.** Role-based labels ("Metro Councilor · D2") in
   anything the engine publishes. The CC static dataset is the one documented,
   confined exception.
2. **Carries no characterising vocabulary and no affiliation labels.** Enforced on
   webmap by `test_no_political_affiliation_labels` and
   `test_no_characterising_vocabulary`; the same scan is the template for any new
   surface (§6).
3. **Presents counts without ranking language.** A record count says how often a
   body appears, never "most active"
   (`test_record_count_is_presented_without_ranking_language`).
4. **Never uses colour as the sole cue.** SPEC-1 pairs its two hues with text and
   brightness; MCM pairs freshness colour with stroke treatment and shape. Every
   encoding must survive greyscale.
5. **Shows live data or a visible absence.** No surface invents freshness or falls
   back to content that could drift while looking authoritative. "The API is
   unreachable" is a legitimate render.
6. **Respects `prefers-reduced-motion`** and keeps motion to status (a pulse) and
   focus transitions.
7. **Anomalies render as measurements** — the sigma, the baseline, the n — never as
   adjectives or alarm styling beyond the family's status encoding.

---

## 6. Enforcing a family on a new surface

`tests/test_webmap_ui.py` is the pattern, and it is cheap — a source-string scan, no
browser:

1. **Palette allowlist.** Collect every hex in the file; assert the set is a subset
   of the family palette. This is what makes §2's palette *normative*.
2. **Token-resolution check.** Assert each status hue appears only as a custom
   property declaration, so an inlined `rgba()` cannot smuggle a hue past the hex
   scan.
3. **Vocabulary scans.** Affiliation labels, characterising adjectives, ranking
   language — the invariants in §5 that a stylesheet cannot carry.
4. **Structural encodings.** Every node group and tie kind the API can serve has a
   declared style, so a new kind fails visibly instead of rendering invisibly.

A new SPEC-1 panel (District Map, Signal Feed, Statistics — the three the README
lists as absent) should land with this test file from its first commit. If the Brief
family is codified (§4), `index.html` and the PDF styles get the same treatment with
their own allowlist.
