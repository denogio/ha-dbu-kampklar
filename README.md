# Kampklar — Home Assistant integration for mit.dbu.dk

Henter dine børns fodboldaktiviteter, kampe og beskeder fra
[mit.dbu.dk](https://mit.dbu.dk) ind i Home Assistant.

## Features

- 🗓️ **Kalender pr. barn** — alle kommende aktiviteter som HA `CalendarEntity`
- ⚽ **Sensorer pr. barn**:
  - Næste aktivitet (timestamp + lokation, type, tilmeldingsstatus)
  - Kommende aktiviteter (state = antal, attribut = fuld liste)
  - Mangler tilmelding (state = antal aktiviteter du skal svare på)
- 📩 **Beskeder fra trænere/klub** — seneste 10 med fuld body cached lokalt
- 🔁 **Multi-barn-støtte** — opdager automatisk børn fra dashboardet
- 🔐 **UI-baseret config** — brugernavn/password gemmes krypteret i HA

## Installation

### Via HACS (anbefalet)

1. HACS → Integrations → tre prikker → "Custom repositories"
2. Tilføj `https://github.com/denogio/ha-dbu-kampklar` som *Integration*
3. Installer "Kampklar (mit.dbu.dk)" og genstart Home Assistant
4. **Settings → Devices & Services → Add Integration → "Kampklar"** og indtast credentials

### Manuelt

Kopiér `custom_components/kampklar/` til din HA-config's `custom_components/`,
genstart, og tilføj integrationen via UI'en.

## Entity-IDs

Forventede navne efter første opsætning (Josva er bare et eksempel —
fornavnet udledes automatisk fra mit.dbu.dk's "Kontaktperson"-felt):

- `sensor.kampklar_beskeder`
- `sensor.kampklar_<navn>_naeste_aktivitet`
- `sensor.kampklar_<navn>_kommende_aktiviteter`
- `sensor.kampklar_<navn>_mangler_tilmelding`
- `calendar.kampklar_<navn>_aktiviteter`

## Dashboard

Et færdigt dashboard ligger i [`dashboard.yaml`](dashboard.yaml). Indsæt via
**Settings → Dashboards → Add Dashboard → Raw configuration editor**.

## Automatiseringer

Eksempler ligger i [`automations/kampklar.yaml`](automations/kampklar.yaml):

- Push ved ny besked
- Push ved ny planlagt aktivitet
- 2 timer før-påmindelse (via calendar trigger)
- Daglig påmindelse om manglende tilmelding

Notifikationerne åbner Kampklar-dashboardet ved tryk via `clickAction`.

## Hvor ofte poller den?

Hver 60 minutter. Tilrettes i [`custom_components/kampklar/const.py`](custom_components/kampklar/const.py).

## Begrænsninger

- mit.dbu.dk eksponerer ikke "læst/ulæst"-status i indbakkelisten — vi viser
  bare de seneste 10 beskeder uanset.
- Afmelding fra aktiviteter er endnu ikke implementeret (på roadmap).

## Udvikling

POC-script + parsere ligger i [`scripts/`](scripts/). Sæt en venv op:

```bash
cd scripts
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # tilret med dine credentials
.venv/bin/python poc_login.py
.venv/bin/python -m pytest test_parsers.py
```

## License

MIT
