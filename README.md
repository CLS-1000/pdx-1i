# PDX-1i — Portland Metro Intelligence

Real-time open-source intelligence (OSINT) platform for Portland-area politics, infrastructure, and civic intelligence.

**PDX-1i** monitors and analyzes public records across the Portland bi-state metro (Oregon: Multnomah, Washington, Clackamas counties; Washington: Clark County).

## What It Does

### Signal Collection
- Aggregates RSS feeds, government data, and public records
- Parses campaign finance (ORESTAR, WA-PDC), legislation (OLIS, WA Legislature)
- Monitors infrastructure: OHSU, PPB, TriMet, PGE, NW Natural, Water Bureau
- Tracks financial disclosures (SEI) and emergency incidents (PDX-911)
- Ingests Portland metro press (OregonLive, Willamette Week, KOIN, Pamplin, NW Politics)

### Intelligence Analysis
- Detects anomalies (sigma-based outlier detection)
- Resolves entities across sources
- Scores confidence tiers: HARD_RECORD, REPORTED, INFERRED
- Publishes curated "Metro Citizens Brief" with neutrality gates

### Data Output
- JSON Lines (JSONL) for streaming ingestion
- SQLite for queryable archives
- PDF newsletters with network diagrams

## Installation

```bash
git clone https://github.com/cls-1000/pdx-1i.git
cd pdx-1i
pip install -e ".[dev]"
```

**Requirements**: Python 3.12+

## Quick Start

```bash
# Run intelligence pipeline
python -m pdx1.pipeline

# Ingest Portland press
from pdx1.sources.portland_press import PortlandPressAdapter
press = PortlandPressAdapter()
result = press.fetch()
```

## Repository Structure

```
pdx-1i/
├── src/pdx1/              # Main package (36 modules)
│   ├── sources/           # Data adapters (ORESTAR, OLIS, SEI, WA-PDC, Portland Press)
│   ├── watch/             # Infrastructure monitoring (7 agencies)
│   ├── models.py          # Pydantic schemas
│   ├── pipeline.py        # Orchestration
│   ├── anomaly.py         # Sigma detection
│   ├── resolver.py        # Entity resolution
│   ├── gates.py           # Publication gates
│   ├── neutrality/        # Quality checks
│   ├── publication/       # Brief generation
│   └── demos/             # Examples
├── tests/                 # 11 test files
├── .github/workflows/     # CI/CD (Python 3.12)
└── pyproject.toml         # Dependencies
```

## Data Sources

| Source | Type | Coverage |
|--------|------|----------|
| **ORESTAR** | OR Campaign Finance | Statewide |
| **OLIS** | OR Legislation | Statewide |
| **SEI** | OR Disclosures | Statewide |
| **WA-PDC** | WA Campaign Finance | Statewide |
| **PDX-911** | Emergency Incidents | Portland metro |
| **Portland Press** | Local news RSS (5 feeds) | Portland metro |
| **Infrastructure** | OHSU, PPB, TriMet, PGE, NW Natural, Water Bureau | Portland metro |

## Testing

```bash
pytest tests/ -v
pytest --cov=src --cov-report=term-missing tests/
```

## Configuration

Create `.env` file:

```env
PDX1_STORE_PATH=pdx1_signals.jsonl
PDX1_DB_PATH=pdx1.db
PDX1_LOG_LEVEL=INFO
ANTHROPIC_API_KEY=sk-ant-...
```

## License

MIT — See LICENSE file.

## More Information

- **SPEC-8**: Full technical specification (SPEC-8_PDX1I.md)
- **AUDIT_REPORT**: Complete data source audit
- **CLAUDE.md**: Development guide for agents

## Contact

CLS-1000 — Portland Metro Intelligence Project  
https://github.com/cls-1000/pdx-1i

---

*PDX-1i is part of the SPEC-1 OSINT ecosystem.*
