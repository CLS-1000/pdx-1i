# CITIZEN COGNISANCE · pdx-1i — MCM Editorial design system

Design language for the **public** surfaces of the pdx-1i product. The phosphor
terminal palette in `ui/webmap.html` and `ui/index.html` belongs to SWITCHBOARD
(internal ops) and is not used here.

MCM Editorial is a mid-century-modern editorial system: structured, authoritative,
slightly warm. Hierarchy is carried by type and rule weight, not by decoration.
Every surface is a warm neutral except one accent per product.

Implemented in `ui/citizen-cognisance.html` — a single file, no build step.

---

## 1. Color system

### Page surfaces (warm neutrals)

| Token | Hex | Use |
|---|---|---|
| `--paper` | `#F5F1E8` | page background — warm off-white, never `#fff` |
| `--paper-2` | `#EFEADD` | inset bands: control bar, legend row |
| `--card` | `#FBF8F1` | raised surfaces: tooltip card, mobile signal card |
| `--ink` | `#1A1815` | primary text — warm near-black, never `#000` |
| `--ink-2` | `#3E3830` | secondary text, body copy |
| `--ink-3` | `#6B635A` | labels, meta, captions |
| `--rule` | `#DCD4C4` | hairlines |
| `--rule-2` | `#C9BFAC` | emphasis hairlines, control borders |

### Accent — pdx-1i

| Token | Hex | Use |
|---|---|---|
| `--accent` | `#14625C` | deep muted teal — wordmark rule, active controls, links |
| `--accent-2` | `#2E8C82` | accent on dark plate |
| `--accent-wash` | `#E2EBE7` | active control fill |

Deep teal reads civic — municipal-utility, water-bureau, transit-map — without
the flag-blue/government-seal register. No primary red, no flag blue, no safety
orange anywhere in the system.

### The plate (viz canvas)

The map sits on a dark warm neutral, framed by the paper page. The contrast
between plate and paper is the strongest structural gesture on the page.

| Token | Hex | Use |
|---|---|---|
| `--plate` | `#1C1A17` | viz canvas |
| `--plate-2` | `#26231E` | floating legend box, zoom controls |
| `--plate-rule` | `#3A352E` | hairlines on plate |
| `--plate-ink` | `#EDE7DA` | node labels |
| `--plate-ink-2` | `#9A9184` | meta on plate (5.6:1 — AA) |

### Node signal states

Freshness is measured from `signal.published_at`. Color is never the only cue —
each state also carries a distinct stroke treatment, so the encoding survives
color-vision deficiency and greyscale print.

| State | Age | Hex | Stroke treatment |
|---|---|---|---|
| LIVE | < 6h | `--live` `#61A96F` | solid 1.8px + animated pulse ring |
| RECENT | < 24h | `--recent` `#D9A441` | solid 1.6px + static outer ring |
| STALE | ≥ 24h | `--stale` `#8A8175` | dashed 1.4px |
| NO SIGNAL | API offline or no record | `--none` `#6A6357` | dashed 1px, no centre dot |

Node fill is the state color at 18% opacity; stroke is the state color at full.
Node **size** is relationship degree, not signal state.

### Node categories (shape)

| Category | Shape |
|---|---|
| People | circle |
| Issues | hexagon |
| Entities | square |
| Jurisdictions | diamond |

All four are area-normalised, so a degree-6 hexagon and a degree-6 circle read
as the same size.

### Relationship types (edge color)

| Type | Hex | Means |
|---|---|---|
| Oversight | `--rel-oversight` `#4C7FA0` | formal authority, appointment, reporting, adjudication |
| Funding | `--rel-funding` `#B98526` | budget authority, appropriation, money flow |
| Conflict | `--rel-conflict` `#BF5B45` | opposition, litigation, investigation, enforcement friction |
| Coalition | `--rel-coalition` `#3E8F84` | alignment, partnership, co-sponsorship, advocacy |

Edge opacity is 0.42 at rest and 0.95 when an endpoint is selected. Width is
`0.6 + strength × 0.7`.

### Contrast (WCAG AA)

| Pair | Ratio |
|---|---|
| `--ink` on `--paper` | 15.9:1 |
| `--ink-3` on `--paper` | 5.2:1 |
| `--accent` on `--paper` | 6.4:1 |
| `--plate-ink-2` on `--plate` | 5.6:1 |
| `--live` on `--plate` | 6.1:1 |
| `--rel-oversight` on `--plate` | 4.0:1 (graphic, ≥3:1) |

---

## 2. Typography

Three families, Google Fonts, each with a system fallback stack so the page
degrades legibly with no network.

| Role | Family | Why |
|---|---|---|
| Display / headings | **Archivo** 500·600·700 | grotesque with a tight, assertive editorial cut |
| Data / labels | **IBM Plex Mono** 400·500·600 | tabular figures for gate scores, counts, timestamps |
| Body | **Source Sans 3** 400·600 | humanist sans, comfortable at paragraph length |

### Scale

| Step | Size / line | Family | Tracking | Use |
|---|---|---|---|---|
| Wordmark | 19px / 1 | Archivo 700 | `.26em` caps | `CITIZEN COGNISANCE` |
| Submark | 10px / 1 | Plex Mono 500 | `.16em` | `pdx-1i` |
| Lede | clamp(22–34px) / 1.22 | Archivo 600 | `-.005em` | value statement |
| Lede-2 | 16px / 1.6 | Source Sans 3 | — | second line of the lede |
| Section label | 10px / 1 | Plex Mono 600 | `.2em` caps | `NODE TYPE`, `SIGNAL STATE` |
| Card title | 17px / 1.2 | Archivo 700 | `.01em` | tooltip / signal-card headline |
| Role | 10px / 1.3 | Plex Mono 500 | `.14em` caps | seat or role under a name |
| Body | 13.5px / 1.62 | Source Sans 3 | — | summaries, descriptions |
| Data | 11px / 1.3 | Plex Mono 500 | `.08em` | gate scores, domains, dates |
| Micro | 9.5px / 1.3 | Plex Mono 500 | `.16em` caps | badges, footer, legend |

All numeric runs use `font-variant-numeric: tabular-nums`.

---

## 3. Component specs

### 3.1 Header strip

Full width, 62px, sticky, `--paper` with a 1px `--rule` bottom edge and a 2px
`--accent` rule above it.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ CITIZEN COGNISANCE      Portland Metropolitan          ● 12 signals indexed │
│ pdx-1i · SPEC-1         Political Intelligence         · updated 3h ago     │
└────────────────────────────────────────────────────────────────────────────┘
  ^ wordmark + submark    ^ centred descriptor            ^ live status badge
```

Three-column grid `1fr auto 1fr`. The centre descriptor is hidden below 900px;
the badge wraps under the wordmark below 620px.

### 3.2 Signal status badge

Pill, `--card` fill, 1px `--rule-2`, Plex Mono micro, tabular figures.
Leading dot is 6px:

- **online** — `--live` dot, pulsing (suppressed under `prefers-reduced-motion`);
  text `N signals indexed · updated Xh ago`
- **loading** — `--ink-3` dot; text `polling signal index…`
- **offline** — hollow `--stale` ring; text `API offline · static graph`

### 3.3 Control bar

Inset band, `--paper-2`, hairline top and bottom, sits directly on top of the
plate with no gap.

```
NODE TYPE  [People ✓][Issues ✓][Entities ✓][Jurisdictions ✓]   SIGNAL  [ALL|LIVE|RECENT|STALE]
```

Node-type chips are independent toggles (`aria-pressed`); signal state is a
single-select segmented group (`role="radiogroup"`). Active = `--accent-wash`
fill, `--accent` border and text. Focus = 2px `--accent` outline at 2px offset.
Filters drive the graph and the mobile list from the same state object.

### 3.4 Legend row

Below the plate, `--paper-2`, three groups separated by hairlines: **relationship**
(4 color chips, 2px × 14px bars), **signal state** (4 dots with their stroke
treatment), **node type** (4 miniature shapes). Purely explanatory — not
interactive.

### 3.5 Tooltip card

Floating `--card` panel, 300px, 1px `--rule-2`, 3px `--accent` top edge, 2px
radius, soft shadow. Follows the cursor on hover; a click pins it with a close
control and keyboard focus.

```
┌─ 3px accent ──────────────────────────┐
│ SIGNAL · LIVE                    [×]  │  micro, state-colored
│ Keith Wilson                          │  card title
│ MAYOR                                 │  role
│ ───────────────────────────────────── │
│ Council rejects amendment to the      │  body
│ Impact Reduction Program line item…   │
│                                       │
│ GATES                          0.40 ▾ │  micro + threshold marker
│ CRED  ███████████▏      0.72          │  label · bar · tabular value
│ VOL   ████████▏         0.55          │
│ VEL   █████████████▏    0.88          │
│ NOV   ████▏             0.28          │  under threshold → conflict color
│ ───────────────────────────────────── │
│ portland.gov · 2026-08-03             │  data
│ Read coverage →                       │  accent link, Notitia Civica
└───────────────────────────────────────┘
```

Gate bars: 4px track in `--rule`, fill in `--accent` at or above 0.40 and in
`--rel-conflict` below it, with a 1px `--ink-3` tick at the 40% mark of every
track. Loading state renders the frame plus a `fetching signal…` line — the card
never blocks on the request. Offline renders a `NO SIGNAL · STATIC RECORD` label
above the static description and omits the CTA.

### 3.6 Mobile signal card (≤768px)

The force graph is replaced by a ranked list, freshest first. Same control bar
above it.

```
┌───────────────────────────────────────┐
│ ● LIVE · 2h            ▌ 4/4 GATES    │  state dot + age · gate pips
│ Angelita Morillo                      │  card title
│ COUNCILOR · D3                        │  role
│ Amendment cutting $4.3M from the      │  body — signal headline
│ Impact Reduction Program advances…    │
│ portland.gov · 2026-08-04             │  data
│ Read coverage →                       │
└───────────────────────────────────────┘
```

Left edge is a 3px bar in the node's state color. Gate pips are four 6px squares,
filled `--accent` at or above 0.40, hollow below. Cards are also the desktop
fallback when D3 fails to load.

---

## 4. Motion

Only two animations exist: the LIVE node pulse (2.4s ease-out, expanding ring at
decreasing opacity) and hover/focus transitions (120ms). Both are disabled under
`prefers-reduced-motion: reduce`.

---

## 5. Data notes

The landing page carries a static fallback dataset of 48 nodes and 131 ties, so
the map renders with the API down. Signal state, gate scores, summaries and
coverage links come only from `GET /api/v1/nodes/{id}/signal` — nothing about
freshness is ever invented locally.

That dataset names individual officeholders. The engine's own registry
(`src/pdx1/graph.py`, served by `GET /graph`) is role-based by design — seats,
never people. The two are not interchangeable, and the role-based registry is the
authority for anything the engine publishes.
