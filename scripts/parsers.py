"""HTML-parsere til mit.dbu.dk.

Hver parser tager rå HTML-streng og returnerer en liste/dict af dataklasser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

DK_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


@dataclass
class Child:
    """Et barn/hold-tilknytning udledt fra dashboard events.

    contact_for_person_id er barnets person-ID i DBU's system.
    Et barn kan i princippet være på flere hold (sjældent) — her giver vi
    én Child pr. unik (person_id, team_id) kombination.
    """
    person_id: int
    team_id: int
    club_id: str | None
    team_name: str | None       # fx "U12 Drenge Snejbjerg (årgang 2014) 25/26"
    name: str | None = None     # barnets navn fra "Kontaktperson:" på dashboard

    @property
    def key(self) -> str:
        return f"{self.person_id}_{self.team_id}"

    @property
    def short_name(self) -> str:
        """Kort, slug-venligt navn — fornavn hvis muligt, ellers person_id."""
        if self.name:
            return self.name.split(" ", 1)[0]
        return str(self.person_id)


@dataclass
class DashboardEvent:
    title: str
    date: date | None
    weekday_short: str | None
    team: str | None
    contact_person: str | None
    event_type: str | None
    activity_id: int | None
    team_id: int | None
    club_id: str | None
    contact_for_person_id: int | None
    url: str | None


@dataclass
class InboxMessage:
    message_id: int
    subject: str
    category: str | None
    unread: bool
    preview: str
    sender: str | None
    received: datetime | None


@dataclass
class MessageDetails:
    message_id: int
    subject: str | None
    sender: str | None
    category: str | None
    received: datetime | None
    body: str            # plaintext med \n som linjeskift


@dataclass
class TeamActivity:
    activity_id: int | None
    activity_type: str | None
    title: str
    weekday: str | None
    time_range: str | None
    location: str | None
    signup_status: str | None
    signup_locked: bool
    date: date | None
    counts: dict[str, int] = field(default_factory=dict)
    url: str | None = None


def _qs_int(url: str, key: str) -> int | None:
    try:
        v = parse_qs(urlparse(url).query).get(key, [None])[0]
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _qs_str(url: str, key: str) -> str | None:
    return parse_qs(urlparse(url).query).get(key, [None])[0]


def parse_dashboard(html: str) -> list[DashboardEvent]:
    """Parse /default.aspx — viser brugerens kommende begivenheder."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[DashboardEvent] = []

    for art in soup.find_all("article", class_="list__item"):
        if "list__personal" in (art.get("class") or []):
            continue
        a = art.find("h3")
        a = a.find("a") if a else None
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href", "")

        time_el = art.find("time")
        weekday_short: str | None = None
        d: date | None = None
        if time_el:
            ws = time_el.find("span", class_="day_short")
            if ws:
                weekday_short = ws.get_text(strip=True)
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", time_el.get_text(" ", strip=True))
            if m:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

        team = contact = None
        p = art.find("p")
        if p:
            txt = p.get_text("\n", strip=True)
            for line in txt.split("\n"):
                if line.lower().startswith("hold:"):
                    team = line.split(":", 1)[1].strip()
                elif line.lower().startswith("kontaktperson:"):
                    contact = line.split(":", 1)[1].strip()

        tag_el = art.find("span", class_="event_tag")
        event_type = tag_el.get_text(strip=True) if tag_el else None

        events.append(
            DashboardEvent(
                title=title,
                date=d,
                weekday_short=weekday_short,
                team=team,
                contact_person=contact,
                event_type=event_type,
                activity_id=_qs_int(href, "activityid"),
                team_id=_qs_int(href, "teamid"),
                club_id=_qs_str(href, "clubid"),
                contact_for_person_id=_qs_int(href, "contactforpersonid"),
                url=href or None,
            )
        )
    return events


def parse_inbox(html: str) -> list[InboxMessage]:
    """Parse /Message/Inbox.aspx — Telerik RadGrid."""
    soup = BeautifulSoup(html, "html.parser")
    messages: list[InboxMessage] = []

    rows = soup.find_all("tr", class_=lambda c: c in ("rgRow", "rgAltRow"))
    for row in rows:
        subject_a = row.find("a", id=re.compile(r"_hlType$"))
        if not subject_a:
            continue
        msg_id = _qs_int(subject_a.get("href", ""), "id") or 0

        # category-tag: lblType. unread-tag: Label1 ("Ny besked")
        category = None
        unread = False
        for tag in row.find_all("span", class_="tag"):
            tid = tag.get("id", "")
            text = tag.get_text(strip=True)
            if tid.endswith("_lblType"):
                category = text
            elif "Ny besked" in text:
                unread = True

        preview_el = row.find("span", class_="MessageText")
        preview = preview_el.get_text(" ", strip=True) if preview_el else ""

        from_el = row.find("span", id=re.compile(r"_lblFrom$"))
        sender = from_el.get_text(strip=True) if from_el else None

        created_el = row.find("span", id=re.compile(r"_lblCreated$"))
        received: datetime | None = None
        if created_el:
            txt = created_el.get_text(strip=True)
            try:
                received = datetime.strptime(txt, "%d-%m-%Y %H:%M")
            except ValueError:
                pass

        messages.append(
            InboxMessage(
                message_id=msg_id,
                subject=subject_a.get_text(strip=True),
                category=category,
                unread=unread,
                preview=preview,
                sender=sender,
                received=received,
            )
        )
    return messages


def parse_message_details(html: str, message_id: int) -> MessageDetails:
    """Parse /Message/MessageDetails.aspx?id=X.

    Body ligger i <span id="cphMain_lblMsg"> med <br/> som linjeskift —
    vi erstatter dem med newlines så vi får ren tekst.
    """
    soup = BeautifulSoup(html, "html.parser")

    def _text(el_id: str) -> str | None:
        el = soup.find(id=el_id)
        return el.get_text(" ", strip=True) if el else None

    body_el = soup.find(id="cphMain_lblMsg")
    if body_el:
        for br in body_el.find_all("br"):
            br.replace_with("\n")
        body = body_el.get_text().strip()
        # Fjern overflødige whitespace-runs, men bevar newlines
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body)
    else:
        body = ""

    received: datetime | None = None
    date_txt = _text("cphMain_lblDate")
    if date_txt:
        try:
            received = datetime.strptime(date_txt, "%d-%m-%Y %H:%M")
        except ValueError:
            pass

    return MessageDetails(
        message_id=message_id,
        subject=_text("cphMain_lblSubject"),
        sender=None,  # sender er i <h3> ikke som id'd span; vi parser separat
        category=_text("cphMain_lblCategory"),
        received=received,
        body=body,
    )


def discover_children(events: list[DashboardEvent]) -> list[Child]:
    """Udled liste af børn/hold fra dashboard-events.

    Grupperer efter (contact_for_person_id, team_id) — første event vinder
    for team_name/club_id. Events uden person_id+team_id ignoreres.
    """
    seen: dict[tuple[int, int], Child] = {}
    for e in events:
        if e.contact_for_person_id is None or e.team_id is None:
            continue
        key = (e.contact_for_person_id, e.team_id)
        if key in seen:
            continue
        seen[key] = Child(
            person_id=e.contact_for_person_id,
            team_id=e.team_id,
            club_id=e.club_id,
            team_name=e.team,
            name=e.contact_person,
        )
    return list(seen.values())


def _infer_year(month: int, today: date) -> int:
    """myteams viser månednavn+dag uden år. Antag samme år, eller næste år hvis
    måneden er mere end 6 måneder bagud — så ruller vi over årsskifte korrekt."""
    year = today.year
    candidate = date(year, month, 1)
    diff_months = (today.year - candidate.year) * 12 + (today.month - candidate.month)
    if diff_months > 6:
        return year + 1
    return year


def parse_myteams(html: str, today: date | None = None) -> list[TeamActivity]:
    """Parse /MyTeam/MyTeams.aspx — listen af aktivitetItem-divs."""
    soup = BeautifulSoup(html, "html.parser")
    today = today or date.today()
    out: list[TeamActivity] = []

    for div in soup.find_all("div", class_="activityItem"):
        month_el = div.find("span", id=re.compile(r"_lblMonth_\d+$"))
        day_el = div.find("span", id=re.compile(r"_lblDate_\d+$"))
        d: date | None = None
        if month_el and day_el:
            try:
                m = DK_MONTHS.get(month_el.get_text(strip=True).lower()[:3])
                day = int(day_el.get_text(strip=True))
                if m:
                    d = date(_infer_year(m, today), m, day)
            except (ValueError, TypeError):
                pass

        name_el = div.find("span", id=re.compile(r"_lblActivityName_\d+$"))
        activity_type = name_el.get_text(strip=True) if name_el else None

        link = div.find("a", id=re.compile(r"_hlName_\d+$"))
        title = link.get_text(strip=True) if link else ""
        href = link.get("href") if link else None
        activity_id = _qs_int(href, "activityid") if href else None

        dt_el = div.find("span", id=re.compile(r"_lblDatetime_\d+$"))
        weekday = time_range = None
        if dt_el:
            txt = dt_el.get_text(" ", strip=True)
            m = re.match(r"(\S+)\s+kl\.\s+(.+)", txt)
            if m:
                weekday, time_range = m.group(1), m.group(2)
            else:
                weekday = txt

        loc_el = div.find("span", id=re.compile(r"_lblMeetingDateTime_\d+$"))
        location = None
        if loc_el:
            location = loc_el.get_text(strip=True).strip("()") or None

        status_el = div.find("span", id=re.compile(r"_lblSignUpStatus_\d+$"))
        signup_status = status_el.get_text(strip=True) if status_el else None

        locked_el = div.find("img", id=re.compile(r"_imgLocked_\d+$"))
        signup_locked = bool(
            locked_el and "ikke" not in (locked_el.get("title", "").lower())
            and "lukket" in (locked_el.get("title", "").lower())
        )

        counts: dict[str, int] = {}
        for sp in div.find_all("span", class_="status"):
            classes = sp.get("class", [])
            title_attr = sp.get("title", "").strip().lower()
            try:
                n = int(sp.get_text(strip=True))
            except ValueError:
                continue
            key = None
            if "green" in classes or "tilmeldt" == title_attr:
                key = "tilmeldt"
            elif "red" in classes:
                key = "frameldt"
            elif "gray" in classes:
                key = "ikke_svaret"
            elif "blue" in classes:
                key = "traenere"
            if key:
                counts[key] = n

        out.append(
            TeamActivity(
                activity_id=activity_id,
                activity_type=activity_type,
                title=title,
                weekday=weekday,
                time_range=time_range,
                location=location,
                signup_status=signup_status,
                signup_locked=signup_locked,
                date=d,
                counts=counts,
                url=href,
            )
        )
    return out


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    dumps = Path(__file__).parent / "dumps"
    name = sys.argv[1] if len(sys.argv) > 1 else "default"
    fn = {"default": parse_dashboard, "inbox": parse_inbox, "myteams": parse_myteams}[name]
    html = (dumps / f"{name}.html").read_text()
    result = fn(html)

    def default(o):
        if hasattr(o, "__dict__"):
            return o.__dict__
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        raise TypeError

    print(json.dumps(result, default=default, indent=2, ensure_ascii=False))
