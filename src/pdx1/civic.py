"""Civic geography, legal instruments, and seat/term identity for PDX-1i.

Administrative hierarchy: state -> county -> city. Regional governments
(Metro) sit between state and county. Districts are overlays, not children.

Procedural state is NOT an enum here. It is the per-chamber string vocabulary
emitted by the proc_track replay (vendored as sources/olis_actions.py), stamped
with the rules_version that produced it. `coarse_status()` derives a display
bucket from it; nothing should store or gate on the coarse bucket.

Seat / Term / Person are the internal join key between money, disclosure and
legislative records. Person is never published: output uses Seat labels only.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class StateCode(StrEnum):
    OR = "OR"
    WA = "WA"


class GovernmentLevel(StrEnum):
    STATE = "state"
    REGIONAL = "regional"  # Metro: elected regional government, adopts ordinances
    COUNTY = "county"
    CITY = "city"
    DISTRICT = "district"  # special/service districts incl. TriMet, Port, schools


class DistrictType(StrEnum):
    CONGRESSIONAL = "congressional"
    STATE_SENATE = "state_senate"
    STATE_HOUSE = "state_house"
    REGIONAL_COUNCIL = "regional_council"
    COUNTY_COMMISSION = "county_commission"
    CITY_COUNCIL = "city_council"
    SCHOOL = "school"
    TRANSIT = "transit"
    FIRE = "fire"
    WATER = "water"
    PORT = "port"
    SOIL_WATER = "soil_water"
    JUDICIAL = "judicial"
    PRECINCT = "precinct"
    OTHER = "other"


class InstrumentType(StrEnum):
    BILL = "bill"
    RESOLUTION = "resolution"
    MEMORIAL = "memorial"
    ORDINANCE = "ordinance"
    ORDER = "order"
    ADMINISTRATIVE_RULE = "administrative_rule"
    POLICY = "policy"
    BUDGET = "budget"
    PROCLAMATION = "proclamation"


class CoarseStatus(StrEnum):
    """Display bucket only. Derived, never stored as the source of truth."""

    INTRODUCED = "introduced"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    ADOPTED = "adopted"
    ENACTED = "enacted"
    VETOED = "vetoed"
    FAILED = "failed"
    UNKNOWN = "unknown"


ALLOWED_INSTRUMENTS: dict[GovernmentLevel, frozenset[InstrumentType]] = {
    GovernmentLevel.STATE: frozenset(
        {
            InstrumentType.BILL,
            InstrumentType.RESOLUTION,
            InstrumentType.MEMORIAL,
            InstrumentType.ADMINISTRATIVE_RULE,
            InstrumentType.BUDGET,
            InstrumentType.PROCLAMATION,
        }
    ),
    GovernmentLevel.REGIONAL: frozenset(
        {
            InstrumentType.ORDINANCE,
            InstrumentType.RESOLUTION,
            InstrumentType.ORDER,
            InstrumentType.BUDGET,
        }
    ),
    GovernmentLevel.COUNTY: frozenset(
        {
            InstrumentType.ORDINANCE,
            InstrumentType.RESOLUTION,
            InstrumentType.ORDER,
            InstrumentType.BUDGET,
            InstrumentType.PROCLAMATION,
        }
    ),
    GovernmentLevel.CITY: frozenset(
        {
            InstrumentType.ORDINANCE,
            InstrumentType.RESOLUTION,
            InstrumentType.ORDER,
            InstrumentType.BUDGET,
            InstrumentType.PROCLAMATION,
        }
    ),
    # TriMet and the Port of Portland boards adopt ordinances.
    GovernmentLevel.DISTRICT: frozenset(
        {
            InstrumentType.ORDINANCE,
            InstrumentType.RESOLUTION,
            InstrumentType.ORDER,
            InstrumentType.POLICY,
            InstrumentType.BUDGET,
        }
    ),
}


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

# Fields that change without the underlying record changing. Excluded from the
# content hash so a ModifiedDate bump does not look like a new record.
VOLATILE_KEYS: frozenset[str] = frozenset({"ModifiedDate", "@odata.etag"})


def _require_tz(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


class SourceRef(BaseModel):
    """Provenance for an authoritative government record.

    `source_record_id` is the identity. `payload_hash` is provenance only: it
    detects that the content changed, it is not a dedup or identity key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload_hash: str | None = None
    parser_version: str = "1"

    @field_validator("retrieved_at")
    @classmethod
    def _tz(cls, value: datetime) -> datetime:
        return _require_tz(value, "retrieved_at")  # type: ignore[return-value]

    @staticmethod
    def hash_payload(payload: Any, exclude: frozenset[str] = VOLATILE_KEYS) -> str:
        if isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k not in exclude}
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


# --------------------------------------------------------------------------- #
# Geography
# --------------------------------------------------------------------------- #


class JurisdictionRef(BaseModel):
    """Administrative scope plus optional district overlays."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: StateCode
    level: GovernmentLevel
    region_id: str | None = None
    county_ids: tuple[str, ...] = ()
    city_id: str | None = None
    district_ids: tuple[str, ...] = ()

    @field_validator("county_ids", "district_ids")
    @classmethod
    def _normalize(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({v.strip() for v in values if v.strip()}))

    @model_validator(mode="after")
    def _scope(self) -> JurisdictionRef:
        validators = {
            GovernmentLevel.STATE: self._validate_state_scope,
            GovernmentLevel.REGIONAL: self._validate_regional_scope,
            GovernmentLevel.COUNTY: self._validate_county_scope,
            GovernmentLevel.CITY: self._validate_city_scope,
            GovernmentLevel.DISTRICT: self._validate_district_scope,
        }
        validators[self.level]()
        return self

    def _validate_state_scope(self) -> None:
        if self.region_id or self.county_ids or self.city_id or self.district_ids:
            raise ValueError(
                "state scope cannot carry region, county, city or district"
            )

    def _validate_regional_scope(self) -> None:
        if not self.region_id or self.city_id or self.district_ids:
            raise ValueError(
                "regional scope requires region_id and no city/district"
            )

    def _validate_county_scope(self) -> None:
        if (
            len(self.county_ids) != 1
            or self.city_id
            or self.district_ids
            or self.region_id
        ):
            raise ValueError(
                "county scope requires exactly one county and nothing else"
            )

    def _validate_city_scope(self) -> None:
        if not self.city_id or not self.county_ids or self.district_ids:
            raise ValueError(
                "city scope requires a city and its one-or-more counties"
            )

    def _validate_district_scope(self) -> None:
        if not self.district_ids:
            raise ValueError("district scope requires at least one district ID")

    @property
    def canonical_id(self) -> str:
        parts: list[str] = [self.state.value, self.level.value]
        if self.region_id:
            parts.append(self.region_id)
        parts.extend(self.county_ids)
        if self.city_id:
            parts.append(self.city_id)
        parts.extend(self.district_ids)
        return ":".join(parts)


class County(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    state: StateCode
    name: str
    fips: str | None = None


class City(BaseModel):
    """A city may intersect multiple counties (Portland intersects three)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    state: StateCode
    name: str
    county_ids: tuple[str, ...]

    @field_validator("county_ids")
    @classmethod
    def _counties(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(values)))
        if not normalized:
            raise ValueError("a city must intersect at least one county")
        return normalized


class District(BaseModel):
    """An electoral or service-area overlay with a versioned boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    state: StateCode
    name: str
    district_type: DistrictType
    governing_level: GovernmentLevel
    county_ids: tuple[str, ...] = ()
    city_ids: tuple[str, ...] = ()
    valid_from: date
    valid_to: date | None = None
    boundary_version: str
    boundary_source_url: str

    @model_validator(mode="after")
    def _dates(self) -> District:
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        return self

    def active_on(self, day: date) -> bool:
        return self.valid_from <= day and (
            self.valid_to is None or day <= self.valid_to
        )


# --------------------------------------------------------------------------- #
# Seats, people, terms  (the internal join key)
# --------------------------------------------------------------------------- #


class Seat(BaseModel):
    """A publishable role. "Metro Councilor · D2", never a person."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    jurisdiction: JurisdictionRef
    label: str
    district_id: str | None = None
    chamber: str | None = None  # "H" / "S" for legislative seats


class Person(BaseModel):
    """Internal only. Never serialized into briefs, API output or the graph.

    `aliases` holds the spellings that appear in source records (OLIS sponsor
    strings, ORESTAR candidate names, SEI filer names) so records can resolve
    to the same person without publishing the name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    publishable: bool = False

    @model_validator(mode="after")
    def _never_publishable(self) -> Person:
        if self.publishable:
            raise ValueError("Person records are internal; publish Seat labels instead")
        return self


class Term(BaseModel):
    """A person holding a seat for a date range. Terms are what joins resolve to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seat_id: str
    person_id: str
    start: date
    end: date | None = None
    source: SourceRef

    @model_validator(mode="after")
    def _dates(self) -> Term:
        if self.end and self.end < self.start:
            raise ValueError("term end cannot precede start")
        return self

    def active_on(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day <= self.end)


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


class SeatRegistry:
    """Resolves raw source names to seats on a date. Unresolved stays unresolved."""

    def __init__(
        self, seats: list[Seat], people: list[Person], terms: list[Term]
    ) -> None:
        self.seats = {s.id: s for s in seats}
        self.people = {p.id: p for p in people}
        self.terms = list(terms)
        self._alias: dict[str, set[str]] = {}
        for p in people:
            for name in (p.display_name, *p.aliases):
                self._alias.setdefault(_norm_name(name), set()).add(p.id)
        for t in terms:
            if t.seat_id not in self.seats:
                raise ValueError(f"term references unknown seat {t.seat_id}")
            if t.person_id not in self.people:
                raise ValueError(f"term references unknown person {t.person_id}")

    def seat_for(self, raw_name: str, on: date) -> Seat | None:
        """Return a seat only when exactly one person holds exactly one seat."""
        candidates = self._alias.get(_norm_name(raw_name), set())
        hits = {
            t.seat_id
            for t in self.terms
            if t.person_id in candidates and t.active_on(on)
        }
        if len(hits) != 1:
            return None
        return self.seats[hits.pop()]


class SponsorRef(BaseModel):
    """Sponsor as it appears in the source, plus the seat it resolved to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_name: str
    role: str | None = None  # chief / regular / committee, per source vocabulary
    seat_id: str | None = None  # None until resolved; unresolved is a finding


# --------------------------------------------------------------------------- #
# Legal instruments and procedural history
# --------------------------------------------------------------------------- #


class LegislativeEvent(BaseModel):
    """One replayed transition.

    `state` is proc_track's per-chamber vocabulary, not a shared enum.
    `occurred_at` is ActionDate; `recorded_at` is CreatedDate. OLIS backdates
    rows, so both are kept. `sequence` breaks same-day ties in replay order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred_at: datetime
    recorded_at: datetime | None = None
    sequence: int = Field(ge=0)
    chamber: str | None = None
    state: str | None = None  # None when the row matched no state-changing rule
    rule_id: str | None = None
    rules_version: str
    action_text: str
    committee: str | None = None
    source: SourceRef

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _tz(cls, value: datetime | None, info: Any) -> datetime | None:
        return _require_tz(value, info.field_name)


class ChamberState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chamber: str
    state: str


# proc_track state -> display bucket. Unknown states fall to UNKNOWN rather
# than raising, so a new rule in proc_track cannot break normalization.
_COARSE: dict[str, CoarseStatus] = {
    "introduced": CoarseStatus.INTRODUCED,
    "in_committee": CoarseStatus.IN_PROGRESS,
    "tabled": CoarseStatus.IN_PROGRESS,
    "second_reading": CoarseStatus.IN_PROGRESS,
    "third_reading": CoarseStatus.IN_PROGRESS,
    "repassed_over_veto": CoarseStatus.IN_PROGRESS,
    "passed": CoarseStatus.PASSED,
    "signed_by_presiding": CoarseStatus.PASSED,
    "sent_to_governor": CoarseStatus.PASSED,
    "adopted": CoarseStatus.ADOPTED,
    "enacted": CoarseStatus.ENACTED,
    "enacted_without_signature": CoarseStatus.ENACTED,
    "enacted_line_item_veto": CoarseStatus.ENACTED,
    "veto_overridden": CoarseStatus.ENACTED,
    "vetoed": CoarseStatus.VETOED,
    "veto_sustained": CoarseStatus.VETOED,
    "failed": CoarseStatus.FAILED,
}

# Order used when chambers disagree: the most advanced disposition wins.
_COARSE_RANK = [
    CoarseStatus.UNKNOWN,
    CoarseStatus.INTRODUCED,
    CoarseStatus.IN_PROGRESS,
    CoarseStatus.FAILED,
    CoarseStatus.PASSED,
    CoarseStatus.ADOPTED,
    CoarseStatus.VETOED,
    CoarseStatus.ENACTED,
]


class LegislativeRecord(BaseModel):
    """Common envelope for legislation and governing actions.

    Original legal vocabulary stays intact: state bills, Metro ordinances,
    county orders, city resolutions and district policies are not collapsed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    jurisdiction: JurisdictionRef
    instrument_type: InstrumentType
    instrument_number: str
    title: str
    procedural_state: tuple[ChamberState, ...] = ()
    rules_version: str | None = None
    introduced_at: date | None = None
    adopted_at: date | None = None
    effective_at: date | None = None
    sponsors: tuple[SponsorRef, ...] = ()
    affected_jurisdictions: tuple[JurisdictionRef, ...] = ()
    events: tuple[LegislativeEvent, ...] = ()
    source: SourceRef
    schema_version: int = 2

    @model_validator(mode="after")
    def _checks(self) -> LegislativeRecord:
        if self.instrument_type not in ALLOWED_INSTRUMENTS[self.jurisdiction.level]:
            raise ValueError(
                f"{self.instrument_type.value} is invalid for "
                f"{self.jurisdiction.level.value} government"
            )
        if self.procedural_state and not self.rules_version:
            raise ValueError(
                "procedural_state requires the rules_version that produced it"
            )
        versions = {e.rules_version for e in self.events}
        if len(versions) > 1:
            raise ValueError(f"events mix rules versions: {sorted(versions)}")
        return self

    @property
    def ordered_events(self) -> tuple[LegislativeEvent, ...]:
        return tuple(sorted(self.events, key=lambda e: (e.occurred_at, e.sequence)))

    @property
    def latest_event(self) -> LegislativeEvent | None:
        ordered = self.ordered_events
        return ordered[-1] if ordered else None

    def coarse_status(self) -> CoarseStatus:
        """Display only. Most advanced bucket across chambers.

        A sustained veto in either chamber settles the measure, even if the
        other chamber overrode (SB 875, 2025R1). Until proc_track models a
        non-terminal `repassed_over_veto`, one chamber can report
        `veto_overridden` for a measure whose veto stood.
        """
        if any(cs.state == "veto_sustained" for cs in self.procedural_state):
            return CoarseStatus.VETOED
        buckets = [
            _COARSE.get(cs.state, CoarseStatus.UNKNOWN) for cs in self.procedural_state
        ]
        if not buckets:
            return CoarseStatus.UNKNOWN
        return max(buckets, key=_COARSE_RANK.index)


# --------------------------------------------------------------------------- #
# OLIS normalization
# --------------------------------------------------------------------------- #

# Oregon measure prefixes, matched exactly. Anything else raises.
OLIS_PREFIX_TYPES: dict[str, InstrumentType] = {
    "HB": InstrumentType.BILL,
    "SB": InstrumentType.BILL,
    "HJR": InstrumentType.RESOLUTION,
    "SJR": InstrumentType.RESOLUTION,
    "HCR": InstrumentType.RESOLUTION,
    "SCR": InstrumentType.RESOLUTION,
    "HR": InstrumentType.RESOLUTION,
    "SR": InstrumentType.RESOLUTION,
    "HJM": InstrumentType.MEMORIAL,
    "SJM": InstrumentType.MEMORIAL,
    "HM": InstrumentType.MEMORIAL,
    "SM": InstrumentType.MEMORIAL,
}


class UnknownMeasurePrefix(ValueError):
    """Raised instead of guessing. An unmapped prefix is a finding, not a default."""


def oregon_state_scope() -> JurisdictionRef:
    return JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.STATE)


def instrument_type_for_prefix(prefix: str) -> InstrumentType:
    key = prefix.strip().upper()
    if key not in OLIS_PREFIX_TYPES:
        raise UnknownMeasurePrefix(f"unmapped OLIS measure prefix: {prefix!r}")
    return OLIS_PREFIX_TYPES[key]


def normalize_olis_measure(
    payload: dict[str, Any],
    *,
    source_url: str,
    procedural_state: dict[str, str] | None = None,
    rules_version: str | None = None,
) -> LegislativeRecord:
    """Normalize one already-fetched OLIS measure.

    HTTP transport, OData paging and action-history replay stay in the OLIS
    adapter. Pass the replay's per-chamber state in; this function does not
    derive state and does not read CurrentLocation or Vetoed.
    """

    session = str(payload["SessionKey"])
    prefix = str(payload["MeasurePrefix"]).strip().upper()
    number = str(payload["MeasureNumber"])
    source_record_id = f"{session}:{prefix}:{number}"

    source = SourceRef(
        source_id="or_olis",
        agency="Oregon Legislative Assembly",
        source_record_id=source_record_id,
        source_url=source_url,
        payload_hash=SourceRef.hash_payload(payload),
        parser_version="or-olis-v2",
    )

    states = tuple(
        ChamberState(chamber=c, state=s)
        for c, s in sorted((procedural_state or {}).items())
    )

    return LegislativeRecord(
        record_id=f"OR:state:olis:{source_record_id}",
        jurisdiction=oregon_state_scope(),
        instrument_type=instrument_type_for_prefix(prefix),
        instrument_number=f"{prefix} {number}",
        title=str(
            payload.get("MeasureSummary")
            or payload.get("CatchLine")
            or "Untitled measure"
        ),
        procedural_state=states,
        rules_version=rules_version,
        source=source,
    )
