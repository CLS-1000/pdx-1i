from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pdx1.civic import (
    City,
    CoarseStatus,
    District,
    DistrictType,
    GovernmentLevel,
    InstrumentType,
    JurisdictionRef,
    LegislativeEvent,
    LegislativeRecord,
    Person,
    Seat,
    SeatRegistry,
    SourceRef,
    StateCode,
    Term,
    UnknownMeasurePrefix,
    normalize_olis_measure,
)

UTC = timezone.utc
RV = "5d6c1b8bf766"


def src(rid: str = "1") -> SourceRef:
    return SourceRef(
        source_id="test",
        agency="Test agency",
        source_record_id=rid,
        source_url="https://example.gov/1",
        retrieved_at=datetime.now(UTC),
    )


def metro() -> JurisdictionRef:
    return JurisdictionRef(
        state=StateCode.OR, level=GovernmentLevel.REGIONAL, region_id="or:metro"
    )


# -- geography ------------------------------------------------------------- #


def test_state_scope_rejects_lower_geography() -> None:
    with pytest.raises(ValidationError):
        JurisdictionRef(
            state=StateCode.OR, level=GovernmentLevel.STATE, county_ids=("multnomah",)
        )


def test_portland_can_cross_counties() -> None:
    city = City(
        id="or:portland",
        state=StateCode.OR,
        name="Portland",
        county_ids=("washington", "multnomah", "clackamas"),
    )
    assert city.county_ids == ("clackamas", "multnomah", "washington")


def test_district_is_an_overlay() -> None:
    d = District(
        id="or:state-house:33",
        state=StateCode.OR,
        name="Oregon House District 33",
        district_type=DistrictType.STATE_HOUSE,
        governing_level=GovernmentLevel.STATE,
        county_ids=("multnomah", "washington"),
        valid_from=date(2022, 1, 1),
        boundary_version="2021-redistricting",
        boundary_source_url="https://example.gov/boundary",
    )
    assert d.active_on(date(2026, 9, 24))
    assert not d.active_on(date(2021, 12, 31))


def test_regional_scope_requires_region() -> None:
    with pytest.raises(ValidationError):
        JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.REGIONAL)
    assert metro().canonical_id == "OR:regional:or:metro"


# -- instruments ----------------------------------------------------------- #


def test_county_cannot_issue_state_bill() -> None:
    with pytest.raises(ValidationError):
        LegislativeRecord(
            record_id="x",
            jurisdiction=JurisdictionRef(
                state=StateCode.OR,
                level=GovernmentLevel.COUNTY,
                county_ids=("multnomah",),
            ),
            instrument_type=InstrumentType.BILL,
            instrument_number="HB 1",
            title="Invalid county bill",
            source=src(),
        )


def test_metro_can_adopt_ordinance() -> None:
    r = LegislativeRecord(
        record_id="OR:regional:metro:ord:26-1500",
        jurisdiction=metro(),
        instrument_type=InstrumentType.ORDINANCE,
        instrument_number="Ordinance 26-1500",
        title="Test",
        source=src(),
    )
    assert r.jurisdiction.level == GovernmentLevel.REGIONAL


def test_transit_district_can_adopt_ordinance() -> None:
    LegislativeRecord(
        record_id="x",
        jurisdiction=JurisdictionRef(
            state=StateCode.OR,
            level=GovernmentLevel.DISTRICT,
            district_ids=("or:trimet",),
        ),
        instrument_type=InstrumentType.ORDINANCE,
        instrument_number="Ordinance 1",
        title="Test",
        source=src(),
    )


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("HB", InstrumentType.BILL),
        ("SB", InstrumentType.BILL),
        ("HJR", InstrumentType.RESOLUTION),
        ("SCR", InstrumentType.RESOLUTION),
        ("SJM", InstrumentType.MEMORIAL),
    ],
)
def test_olis_prefix_sets_instrument_type(
    prefix: str, expected: InstrumentType
) -> None:
    r = normalize_olis_measure(
        {"SessionKey": "2025R1", "MeasurePrefix": prefix, "MeasureNumber": 1},
        source_url="https://example.gov",
    )
    assert r.instrument_type == expected


def test_unknown_prefix_raises_rather_than_guessing() -> None:
    with pytest.raises(UnknownMeasurePrefix):
        normalize_olis_measure(
            {"SessionKey": "2025R1", "MeasurePrefix": "XX", "MeasureNumber": 1},
            source_url="https://example.gov",
        )


# -- procedural state ------------------------------------------------------ #


def test_proc_track_states_survive_without_flattening() -> None:
    r = normalize_olis_measure(
        {"SessionKey": "2025R1", "MeasurePrefix": "SB", "MeasureNumber": 875},
        source_url="https://example.gov",
        procedural_state={"S": "veto_overridden", "H": "tabled"},
        rules_version=RV,
    )
    assert {(c.chamber, c.state) for c in r.procedural_state} == {
        ("H", "tabled"),
        ("S", "veto_overridden"),
    }
    assert r.rules_version == RV


def test_sustained_veto_beats_one_chamber_override() -> None:
    r = normalize_olis_measure(
        {"SessionKey": "2025R1", "MeasurePrefix": "SB", "MeasureNumber": 875},
        source_url="https://example.gov",
        procedural_state={"S": "veto_overridden", "H": "veto_sustained"},
        rules_version=RV,
    )
    assert r.coarse_status() == CoarseStatus.VETOED


def test_line_item_veto_and_signed_by_presiding_are_representable() -> None:
    r = normalize_olis_measure(
        {"SessionKey": "2023R1", "MeasurePrefix": "SB", "MeasureNumber": 5506},
        source_url="https://example.gov",
        procedural_state={"S": "enacted_line_item_veto"},
        rules_version=RV,
    )
    assert r.coarse_status() == CoarseStatus.ENACTED
    r2 = normalize_olis_measure(
        {"SessionKey": "2026R1", "MeasurePrefix": "HB", "MeasureNumber": 1},
        source_url="https://example.gov",
        procedural_state={"H": "signed_by_presiding"},
        rules_version=RV,
    )
    assert r2.coarse_status() == CoarseStatus.PASSED


def test_unknown_state_degrades_to_unknown_bucket() -> None:
    r = normalize_olis_measure(
        {"SessionKey": "2025R1", "MeasurePrefix": "HB", "MeasureNumber": 1},
        source_url="https://example.gov",
        procedural_state={"H": "some_future_state"},
        rules_version=RV,
    )
    assert r.coarse_status() == CoarseStatus.UNKNOWN
    assert r.procedural_state[0].state == "some_future_state"


def test_state_without_rules_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        normalize_olis_measure(
            {"SessionKey": "2025R1", "MeasurePrefix": "HB", "MeasureNumber": 1},
            source_url="https://example.gov",
            procedural_state={"H": "passed"},
        )


def _ev(
    day: int, seq: int, text: str, rv: str = RV, recorded_lag: int = 0
) -> LegislativeEvent:
    occurred = datetime(2025, 3, day, tzinfo=UTC)
    return LegislativeEvent(
        occurred_at=occurred,
        recorded_at=occurred + timedelta(days=recorded_lag),
        sequence=seq,
        chamber="H",
        state=None,
        rules_version=rv,
        action_text=text,
        source=src(f"ev{seq}"),
    )


def test_same_day_events_order_by_sequence() -> None:
    r = LegislativeRecord(
        record_id="x",
        jurisdiction=JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.STATE),
        instrument_type=InstrumentType.BILL,
        instrument_number="HB 1",
        title="t",
        events=(_ev(5, 2, "third"), _ev(5, 0, "first"), _ev(5, 1, "second")),
        source=src(),
    )
    assert [e.action_text for e in r.ordered_events] == ["first", "second", "third"]
    assert r.latest_event is not None and r.latest_event.action_text == "third"


def test_backdated_row_keeps_both_timestamps() -> None:
    e = _ev(5, 0, "late insert", recorded_lag=400)
    assert e.recorded_at is not None and (e.recorded_at - e.occurred_at).days == 400


def test_mixed_rules_versions_rejected() -> None:
    with pytest.raises(ValidationError):
        LegislativeRecord(
            record_id="x",
            jurisdiction=JurisdictionRef(
                state=StateCode.OR, level=GovernmentLevel.STATE
            ),
            instrument_type=InstrumentType.BILL,
            instrument_number="HB 1",
            title="t",
            events=(_ev(5, 0, "a", rv="aaa"), _ev(6, 1, "b", rv="bbb")),
            source=src(),
        )


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        LegislativeEvent(
            occurred_at=datetime(2025, 3, 5),
            sequence=0,
            rules_version=RV,
            action_text="x",
            source=src(),
        )


# -- provenance ------------------------------------------------------------ #


def test_modified_date_does_not_change_content_hash() -> None:
    base = {"SessionKey": "2025R1", "MeasurePrefix": "HB", "MeasureNumber": 1}
    a = SourceRef.hash_payload({**base, "ModifiedDate": "2025-01-01"})
    b = SourceRef.hash_payload({**base, "ModifiedDate": "2026-01-01"})
    assert a == b
    assert a != SourceRef.hash_payload({**base, "MeasureNumber": 2})


# -- seats and terms ------------------------------------------------------- #


def _registry() -> SeatRegistry:
    house = JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.STATE)
    seats = [
        Seat(
            id="or:house:33",
            jurisdiction=house,
            label="State Representative · HD 33",
            district_id="or:state-house:33",
            chamber="H",
        ),
        Seat(
            id="or:house:34",
            jurisdiction=house,
            label="State Representative · HD 34",
            district_id="or:state-house:34",
            chamber="H",
        ),
    ]
    people = [
        Person(
            id="p1",
            display_name="Jane Q Example",
            aliases=("Representative Example", "Example J"),
        ),
        Person(id="p2", display_name="Sam Other", aliases=("Representative Other",)),
    ]
    terms = [
        Term(
            seat_id="or:house:33",
            person_id="p1",
            start=date(2023, 1, 9),
            end=date(2025, 1, 12),
            source=src("t1"),
        ),
        Term(
            seat_id="or:house:34",
            person_id="p1",
            start=date(2025, 1, 13),
            source=src("t2"),
        ),
        Term(
            seat_id="or:house:33",
            person_id="p2",
            start=date(2025, 1, 13),
            source=src("t3"),
        ),
    ]
    return SeatRegistry(seats, people, terms)


def test_seat_resolution_is_date_bound() -> None:
    reg = _registry()
    s2024 = reg.seat_for("Representative Example", date(2024, 3, 1))
    s2026 = reg.seat_for("representative  example", date(2026, 3, 1))
    assert s2024 is not None and s2024.id == "or:house:33"
    assert s2026 is not None and s2026.id == "or:house:34"


def test_unresolved_name_returns_none() -> None:
    assert _registry().seat_for("Nobody", date(2026, 3, 1)) is None


def test_ambiguous_alias_returns_none() -> None:
    house = JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.STATE)
    seats = [
        Seat(id="a", jurisdiction=house, label="A"),
        Seat(id="b", jurisdiction=house, label="B"),
    ]
    people = [
        Person(id="p1", display_name="Smith", aliases=()),
        Person(id="p2", display_name="Smith", aliases=()),
    ]
    terms = [
        Term(seat_id="a", person_id="p1", start=date(2025, 1, 1), source=src()),
        Term(seat_id="b", person_id="p2", start=date(2025, 1, 1), source=src()),
    ]
    assert (
        SeatRegistry(seats, people, terms).seat_for("Smith", date(2026, 1, 1)) is None
    )


def test_person_cannot_be_marked_publishable() -> None:
    with pytest.raises(ValidationError):
        Person(id="p", display_name="X", publishable=True)


def test_term_with_unknown_seat_rejected() -> None:
    house = JurisdictionRef(state=StateCode.OR, level=GovernmentLevel.STATE)
    with pytest.raises(ValueError):
        SeatRegistry(
            [Seat(id="a", jurisdiction=house, label="A")],
            [Person(id="p", display_name="X")],
            [
                Term(
                    seat_id="missing",
                    person_id="p",
                    start=date(2025, 1, 1),
                    source=src(),
                )
            ],
        )
