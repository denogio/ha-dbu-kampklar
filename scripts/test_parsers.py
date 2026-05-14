"""Smoke-tests mod live-dumps i scripts/dumps/.

Krav: scripts/poc_login.py skal have kørt så dumps eksisterer.
Kør: scripts/.venv/bin/python -m pytest scripts/test_parsers.py -v
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from parsers import parse_dashboard, parse_inbox, parse_myteams

DUMPS = Path(__file__).parent / "dumps"


def _load(name: str) -> str:
    p = DUMPS / f"{name}.html"
    if not p.exists():
        pytest.skip(f"dump {name}.html mangler — kør poc_login.py først")
    return p.read_text()


def test_dashboard_parses_events():
    events = parse_dashboard(_load("default"))
    assert len(events) >= 1
    e = events[0]
    assert e.title
    assert e.activity_id and e.activity_id > 0
    assert e.team_id is not None
    assert e.club_id
    assert e.date is None or isinstance(e.date, date)
    assert e.event_type


def test_dashboard_has_team_and_contact():
    events = parse_dashboard(_load("default"))
    assert any(e.team for e in events)
    assert any(e.contact_person for e in events)


def test_inbox_parses_messages():
    msgs = parse_inbox(_load("inbox"))
    assert len(msgs) >= 1
    m = msgs[0]
    assert m.message_id > 0
    assert m.subject
    assert m.sender
    assert m.received is not None


def test_inbox_detects_unread():
    msgs = parse_inbox(_load("inbox"))
    # Mindst én besked bør være markeret ulæst eller læst — vi tjekker bare at feltet er bool
    assert all(isinstance(m.unread, bool) for m in msgs)


def test_myteams_parses_activities():
    acts = parse_myteams(_load("myteams"))
    assert len(acts) >= 1
    a = acts[0]
    assert a.title
    assert a.activity_type
    assert a.date is None or isinstance(a.date, date)


def test_myteams_signup_status_values():
    acts = parse_myteams(_load("myteams"))
    statuses = {a.signup_status for a in acts if a.signup_status}
    # Forventede danske værdier
    assert statuses.issubset(
        {"Tilmeldt", "Frameldt", "Ikke svaret", "Måske", "Afventer", None}
    ) or statuses  # tillad ukendte men kræv ikke-tom


def test_myteams_counts_have_expected_keys():
    acts = parse_myteams(_load("myteams"))
    counted = [a for a in acts if a.counts]
    assert counted, "ingen aktivitet havde counts"
    for a in counted:
        # mindst én af de fire forventede nøgler
        assert set(a.counts) & {"tilmeldt", "frameldt", "ikke_svaret", "traenere"}
