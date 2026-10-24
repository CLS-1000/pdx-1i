# Task: Wire proc_track action mapping into the OLIS adapter

## Context

The current `src/pdx1/sources/olis.py` fetches from the `Measures` endpoint,
which returns one row per bill with a `CurrentLocation` field. That field is
misleading — a vetoed bill shows "Senate - Tabled." The adapter has never
parsed a single action row.

A sibling repo at `~/proc_track` contains a replay harness with ~100 regex
rules that map raw OLIS action text to procedural states, validated against
11,723 measures across four Oregon sessions (2019–2025) at 100% replay.

This task wires that mapping into the OLIS adapter so pdx-1i gets real
procedural state instead of the stale location string.

## What exists

- `~/proc_track/replay_harness.py` — contains `COMPILED` (list of compiled
  regex rules), `parse_row(action_text)` (returns list of Emit objects + 
  uncovered fragments), `CHAMBER_FLOW` (allowed transitions), and `step()`
  (applies a transition to an Item). These are the tested components.
- `~/pdx-1i/src/pdx1/sources/olis.py` — the current adapter. Hits
  `Measures?$format=json`. Emits one Signal per bill with a prose blob.
- `~/pdx-1i/src/pdx1/models.py` — Signal model. Fields: source, source_type,
  text, url, author, published_at, credibility.

## The three changes

### 1. Add an action-history fetcher

Add a method `_fetch_actions(self, session: str) -> list[dict]` that hits:

    MeasureHistoryActions?$filter=SessionKey eq '{session}'&$format=json

Same paging logic as `_fetch_live` (OData nextLink, urljoin for relative
links, MAX_PAGES ceiling). Pages at 5,000 rows. A full session is ~27k rows.

Use the same httpx import pattern. Return the raw dicts.

The session key format is `2025R1` (regular) or `2025S1` (special). For now,
hardcode the current session or accept it as a config parameter.

### 2. Copy the mapping module

Copy `~/proc_track/replay_harness.py` into pdx-1i as a standalone module:

    src/pdx1/sources/olis_actions.py

Extract ONLY these components from replay_harness.py:
- `Emit` dataclass
- `RULES` list
- `COMPILED` list
- `parse_row()` function
- `CHAMBER_FLOW` dict
- `TERMINAL` set
- `Halt` exception
- `Item` dataclass
- `step()` function
- `replay()` function

Do NOT copy the CLI, argparse, fetch logic, or reporting. This module should
have no imports beyond stdlib (re, dataclasses, typing, collections).

### 3. Change what the adapter emits

Currently: one Signal per measure, always, with status from CurrentLocation.

After: one Signal per measure that has CHANGED procedural state since the
last cycle.

Logic:
1. Fetch actions for the current session via `_fetch_actions`
2. Group by (MeasurePrefix, MeasureNumber)
3. Sort each group by (ActionDate, MeasureHistoryId)
4. Replay each measure through `olis_actions.replay()`
5. Compare final state against last-known state (stored somewhere — see below)
6. If changed, emit a Signal with:
   - `text` includes the actual procedural state per chamber, not CurrentLocation
   - `text` includes the action text that caused the transition
   - `published_at` = the ActionDate of the most recent state-changing action
   - The existing measure metadata (title, sponsors, etc.) still comes from
     the Measures endpoint — keep that fetch

State persistence: the simplest option is a JSON file at
`~/.pdx1/olis_state.json` mapping `{measure_id: {chamber: state}}`. Read on
start, write after each cycle. If the file doesn't exist, treat everything
as new (emit all).

### What NOT to do

- Do not modify `replay_harness.py` in the proc_track repo
- Do not add proc_track as a pip dependency — copy the module
- Do not change the Signal model
- Do not change any other adapter
- Do not remove the existing Measures fetch — the metadata is still needed
- Do not emit Signals for committee churn (committee, public_hearing,
  work_session states). Only emit for: introduced (first appearance),
  passed, adopted, failed, enacted, vetoed, veto_sustained, veto_overridden,
  signed_by_presiding, tabled

### Verification

1. `pytest` must still pass (326+ tests)
2. Run `python -m pdx1.pipeline` in fixture mode — it should still work
3. New test: `test_olis_actions.py` that imports `olis_actions.replay`,
   loads `~/proc_track/data/sb976_trajectory.json`, replays it, and asserts
   final state is `{'S': 'veto_sustained', 'H': 'signed_by_presiding'}`
4. New test: `test_olis_adapter_actions.py` that mocks the OData response
   with a handful of action rows and verifies Signals are emitted only for
   state-changing transitions

### Commit

Commit to develop. Message: "olis: wire proc_track action mapping — real procedural state replaces CurrentLocation"
