"""DbuClient: high-level API til mit.dbu.dk.

Håndterer login, auto-relogin ved session-udløb, og henter rå HTML som
parsers.py kan behandle.

Brug standalone:
    python -m api login
    python -m api dashboard
    python -m api inbox
    python -m api children
    python -m api myteams                          # alle børn
    python -m api myteams --person 1234 --team 45  # ét specifikt barn

Bruger DBU_USERNAME / DBU_PASSWORD fra .env eller miljø.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from parsers import (
    Child,
    DashboardEvent,
    InboxMessage,
    TeamActivity,
    discover_children,
    parse_dashboard,
    parse_inbox,
    parse_myteams,
)

_LOG = logging.getLogger("kampklar.api")

WWW = "https://www.dbu.dk"
MIT = "https://mit.dbu.dk"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class DbuAuthError(Exception):
    """Login afvist eller credentials forkerte."""


class DbuClient:
    """Asynkron klient til mit.dbu.dk.

    Ejer ikke sin egen ClientSession — accepterer én udefra så caller kontrollerer
    livscyklus (vigtigt i Home Assistant hvor sessions deles via aiohttp_client).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False

    # ---- auth ----

    async def login(self) -> None:
        """Kør hele login-flowet. Raiser DbuAuthError ved fejl."""
        _LOG.debug("Login: GET %s/", WWW)
        async with self._session.get(f"{WWW}/", headers=self._base_headers()) as r:
            html = await r.text()
        token = self._extract_antiforgery(html)
        if not token:
            raise DbuAuthError("Fandt ikke __RequestVerificationToken på dbu.dk forside")

        headers = self._base_headers()
        headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": WWW,
                "Referer": f"{WWW}/",
                "RequestVerificationToken": token,
            }
        )
        data = {"username": self._username, "password": self._password, "remember": "false"}
        _LOG.debug("Login: POST %s/login/PerformLogin", WWW)
        async with self._session.post(
            f"{WWW}/login/PerformLogin", data=data, headers=headers
        ) as r:
            if r.status != 200:
                raise DbuAuthError(f"PerformLogin status {r.status}")
            payload = await r.json(content_type=None)

        if payload.get("result") != 1 or not payload.get("url"):
            raise DbuAuthError(f"Login afvist: {payload!r}")

        _LOG.debug("Login: GET redirect til mit.dbu.dk med token")
        async with self._session.get(
            payload["url"], headers=self._base_headers(), allow_redirects=True
        ) as r:
            if "login" in r.url.path.lower():
                raise DbuAuthError("Blev sendt tilbage til login efter token-redirect")
            await r.read()  # opbruge body

        self._logged_in = True
        _LOG.info("Logget ind som %s", self._username)

    async def _ensure_authed(self) -> None:
        if not self._logged_in:
            await self.login()

    # ---- fetchers ----

    async def fetch_dashboard(self) -> list[DashboardEvent]:
        html = await self._get_html(f"{MIT}/default.aspx")
        return parse_dashboard(html)

    async def fetch_inbox(self) -> list[InboxMessage]:
        html = await self._get_html(f"{MIT}/Message/Inbox.aspx")
        return parse_inbox(html)

    async def fetch_message_html(self, message_id: int) -> str:
        return await self._get_html(
            f"{MIT}/Message/MessageDetails.aspx?id={message_id}"
        )

    async def fetch_myteams(
        self, team_id: int | None = None, person_id: int | None = None
    ) -> list[TeamActivity]:
        url = f"{MIT}/MyTeam/MyTeams.aspx"
        if team_id is not None and person_id is not None:
            url = f"{url}?teamid={team_id}&contactforpersonid={person_id}"
        html = await self._get_html(url)
        return parse_myteams(html)

    async def fetch_children(self) -> list[Child]:
        events = await self.fetch_dashboard()
        return discover_children(events)

    async def fetch_all_activities(self) -> dict[str, list[TeamActivity]]:
        """Hent myteams pr. barn. Returnerer dict keyed by Child.key."""
        children = await self.fetch_children()
        results: dict[str, list[TeamActivity]] = {}
        for child in children:
            results[child.key] = await self.fetch_myteams(
                team_id=child.team_id, person_id=child.person_id
            )
        return results

    # ---- internals ----

    async def _get_html(self, url: str) -> str:
        await self._ensure_authed()
        async with self._session.get(url, headers=self._base_headers()) as r:
            text = await r.text()
            # Hvis sessionen er udløbet ryger vi typisk til login.aspx
            if r.status != 200 or "login" in r.url.path.lower():
                _LOG.info("Session udløbet (final_url=%s), logger ind igen", r.url)
                self._logged_in = False
                await self.login()
                async with self._session.get(url, headers=self._base_headers()) as r2:
                    if r2.status != 200 or "login" in r2.url.path.lower():
                        raise DbuAuthError(
                            f"Stadig redirected til login efter relogin: {r2.url}"
                        )
                    return await r2.text()
            return text

    def _base_headers(self) -> dict[str, str]:
        return {"User-Agent": UA, "Accept-Language": "da-DK,da;q=0.9,en;q=0.7"}

    @staticmethod
    def _extract_antiforgery(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.find("input", {"name": "__RequestVerificationToken"})
        if el and el.get("value"):
            return el["value"]
        m = re.search(
            r'name=["\']__RequestVerificationToken["\']\s+[^>]*value=["\']([^"\']+)',
            html,
        )
        return m.group(1) if m else None


# ---- CLI til manuel test ----

def _load_env() -> None:
    env = Path(__file__).parent / ".env"
    if env.exists():
        import os
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _serialize(o: Any) -> Any:
    from datetime import date, datetime
    if hasattr(o, "__dict__"):
        return o.__dict__
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"can't serialize {type(o)}")


async def _cli() -> int:
    import argparse, os

    _load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cmd",
        choices=["login", "dashboard", "inbox", "children", "myteams", "message", "dump_message"],
    )
    parser.add_argument("--person", type=int)
    parser.add_argument("--team", type=int)
    parser.add_argument("--id", type=int, help="message id (for 'message'/'dump_message')")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    u = os.environ.get("DBU_USERNAME")
    p = os.environ.get("DBU_PASSWORD")
    if not u or not p:
        print("Mangler DBU_USERNAME/DBU_PASSWORD i .env", file=sys.stderr)
        return 2

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        client = DbuClient(session, u, p)
        if args.cmd == "login":
            await client.login()
            print("OK")
            return 0
        if args.cmd == "dashboard":
            data = await client.fetch_dashboard()
        elif args.cmd == "inbox":
            data = await client.fetch_inbox()
        elif args.cmd == "children":
            data = await client.fetch_children()
        elif args.cmd == "myteams":
            if args.person and args.team:
                data = await client.fetch_myteams(team_id=args.team, person_id=args.person)
            else:
                data = await client.fetch_all_activities()
        elif args.cmd == "dump_message":
            if not args.id:
                # Hent inbox, vælg nyeste id
                inbox = await client.fetch_inbox()
                if not inbox:
                    print("Tom indbakke", file=sys.stderr)
                    return 1
                args.id = inbox[0].message_id
                print(f"# bruger nyeste besked: id={args.id}", file=sys.stderr)
            html = await client.fetch_message_html(args.id)
            out = Path(__file__).parent / "dumps" / f"message_{args.id}.html"
            out.write_text(html, encoding="utf-8")
            print(f"Dumped {len(html)} bytes til {out}")
            return 0
        elif args.cmd == "message":
            if not args.id:
                print("Brug --id <message_id>", file=sys.stderr)
                return 2
            html = await client.fetch_message_html(args.id)
            print(html)
            return 0
        else:
            return 2
    print(json.dumps(data, default=_serialize, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
