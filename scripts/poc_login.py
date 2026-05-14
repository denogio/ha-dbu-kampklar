"""Proof-of-concept: log ind på mit.dbu.dk og dump HTML.

Brug:
    python scripts/poc_login.py --username USER --password PASS
    # eller via .env i samme mappe:
    #   DBU_USERNAME=...
    #   DBU_PASSWORD=...
    python scripts/poc_login.py

Output havner i scripts/dumps/*.html — disse bruges som input til Fase 1 parsere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

DUMP_DIR = Path(__file__).parent / "dumps"

WWW = "https://www.dbu.dk"
MIT = "https://mit.dbu.dk"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def load_env_file() -> None:
    env = Path(__file__).parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def extract_antiforgery_token(html: str) -> str | None:
    """ASP.NET Core lægger en hidden input __RequestVerificationToken i HTML."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("input", {"name": "__RequestVerificationToken"})
    if el and el.get("value"):
        return el["value"]
    # Fallback: scan rå HTML for et token-mønster
    m = re.search(
        r'name=["\']__RequestVerificationToken["\']\s+[^>]*value=["\']([^"\']+)',
        html,
    )
    return m.group(1) if m else None


async def login(session: aiohttp.ClientSession, username: str, password: str) -> bool:
    print(f"[1/5] GET {WWW}/ for at hente CSRF-token...")
    async with session.get(f"{WWW}/", allow_redirects=True) as r:
        html = await r.text()
        print(f"      status={r.status} bytes={len(html)} cookies={len(session.cookie_jar)}")
    token = extract_antiforgery_token(html)
    if not token:
        print("      ⚠  fandt ikke __RequestVerificationToken i forsidens HTML")
        print("         Prøver alligevel uden header — POC vil sandsynligvis fejle.")
    else:
        print(f"      ✓ token: {token[:30]}... ({len(token)} tegn)")

    print(f"[2/5] GET {WWW}/login/getUser ...")
    async with session.get(
        f"{WWW}/login/getUser",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    ) as r:
        body = await r.text()
        print(f"      status={r.status} body={body[:200]!r}")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": WWW,
        "Referer": f"{WWW}/",
    }
    if token:
        headers["RequestVerificationToken"] = token

    data = {"username": username, "password": password, "remember": "false"}

    print(f"[3/5] POST {WWW}/login/PerformLogin ...")
    async with session.post(
        f"{WWW}/login/PerformLogin", data=data, headers=headers
    ) as r:
        body = await r.text()
        print(f"      status={r.status} bytes={len(body)}")
        print(f"      body={body[:500]!r}")
        if r.status != 200:
            print("      ✗ login fejlede")
            return False

    # Response er JSON: {"result":1,"url":"https://mit.dbu.dk/login.aspx?token=..."}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("      ✗ kunne ikke parse JSON")
        return False

    if payload.get("result") != 1 or not payload.get("url"):
        print(f"      ✗ login afvist: {payload!r}")
        return False
    redirect_url = payload["url"]
    print(f"      ✓ result=1, redirect: {redirect_url[:80]}...")

    print(f"[4/5] GET {redirect_url[:60]}... (følger redirects til mit.dbu.dk)")
    async with session.get(redirect_url, allow_redirects=True) as r:
        body = await r.text()
        print(f"      status={r.status} final_url={r.url} bytes={len(body)}")
        if "login" in r.url.path.lower():
            print("      ✗ blev sendt tilbage til login — token afvist")
            return False

    print(f"[5/5] Verificér: GET {MIT}/default.aspx")
    async with session.get(f"{MIT}/default.aspx") as r:
        body = await r.text()
        ok = r.status == 200 and "login" not in str(r.url).lower()
        print(f"      status={r.status} bytes={len(body)} ok={ok}")
    return ok


async def dump_pages(session: aiohttp.ClientSession) -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    pages = {
        "default.html": f"{MIT}/default.aspx",
        "inbox.html": f"{MIT}/Message/Inbox.aspx",
        "myteams.html": f"{MIT}/MyTeam/MyTeams.aspx",
    }
    for fname, url in pages.items():
        try:
            async with session.get(url) as r:
                body = await r.text()
            out = DUMP_DIR / fname
            out.write_text(body, encoding="utf-8")
            naive_count = naive_extract_counts(body)
            print(f"  → {fname}: status={r.status} bytes={len(body)} {naive_count}")
        except Exception as e:
            print(f"  → {fname}: FEJL {e}")


async def discover_and_probe_children(session: aiohttp.ClientSession) -> None:
    """Udled børn fra dashboard og test om vi kan hente myteams pr. barn via querystring."""
    sys.path.insert(0, str(Path(__file__).parent))
    from parsers import discover_children, parse_dashboard

    html = (DUMP_DIR / "default.html").read_text()
    events = parse_dashboard(html)
    children = discover_children(events)
    print(f"\nFandt {len(children)} barn/hold-kombination(er) fra dashboard:")
    for c in children:
        print(f"  • person_id={c.person_id} team_id={c.team_id} hold={c.team_name!r}")

    if len(children) <= 1:
        print(
            "\n  ℹ Kun ét barn fundet — vi tester querystring-form på myteams alligevel,\n"
            "    så vi kender URL-formen til multi-barn senere."
        )

    for c in children:
        url = (
            f"{MIT}/MyTeam/MyTeams.aspx"
            f"?teamid={c.team_id}&contactforpersonid={c.person_id}"
        )
        async with session.get(url) as r:
            body = await r.text()
        # Verifikation: parse og se om aktiviteterne ligner det aktive hold
        from parsers import parse_myteams
        acts = parse_myteams(body)
        out = DUMP_DIR / f"myteams_{c.person_id}_{c.team_id}.html"
        out.write_text(body, encoding="utf-8")
        print(
            f"  → myteams?teamid={c.team_id}&contactforpersonid={c.person_id}: "
            f"status={r.status} bytes={len(body)} aktiviteter={len(acts)} "
            f"→ {out.name}"
        )


def naive_extract_counts(html: str) -> str:
    """Helt naiv første-tjek: tæl forekomster af ord der antyder data."""
    soup = BeautifulSoup(html, "html.parser")
    txt = soup.get_text(" ", strip=True).lower()
    tables = len(soup.find_all("table"))
    rows = len(soup.find_all("tr"))
    activities = txt.count("aktivitet") + txt.count("kamp") + txt.count("træning")
    messages = txt.count("besked")
    return (
        f"tables={tables} rows={rows} "
        f"~aktivitets-ord={activities} ~besked-ord={messages}"
    )


async def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=os.environ.get("DBU_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("DBU_PASSWORD"))
    args = parser.parse_args()
    if not args.username or not args.password:
        print("Mangler --username/--password (eller DBU_USERNAME/DBU_PASSWORD i .env)")
        return 2

    jar = aiohttp.CookieJar(unsafe=False)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        cookie_jar=jar,
        timeout=timeout,
        headers={"User-Agent": UA, "Accept-Language": "da-DK,da;q=0.9,en;q=0.7"},
    ) as session:
        ok = await login(session, args.username, args.password)
        if not ok:
            print("\n✗ Login mislykkedes — stopper før dump.")
            return 1
        print(f"\n✓ Login ok. Cookies i jar: {len(jar)}")
        print("\nDumper sider til scripts/dumps/ ...")
        await dump_pages(session)
        print("\nOpdager børn + tester multi-barn URL-form...")
        await discover_and_probe_children(session)
        print("\nFærdig.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
