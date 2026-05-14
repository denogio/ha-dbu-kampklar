"""DataUpdateCoordinator for Kampklar."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DbuAuthError, DbuClient, DbuConnectionError
from .const import DOMAIN, MESSAGE_DETAIL_COUNT, UPDATE_INTERVAL
from .parsers import Child, InboxMessage, MessageDetails, TeamActivity

_LOG = logging.getLogger(__name__)


@dataclass
class KampklarData:
    children: list[Child]
    inbox: list[InboxMessage]
    activities_by_child: dict[str, list[TeamActivity]]
    message_bodies: dict[int, str]  # message_id -> fuld body


class KampklarCoordinator(DataUpdateCoordinator[KampklarData]):
    """Henter data fra mit.dbu.dk i et samlet kald hver UPDATE_INTERVAL."""

    def __init__(self, hass: HomeAssistant, client: DbuClient) -> None:
        super().__init__(
            hass,
            _LOG,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self._body_cache: dict[int, str] = {}

    async def _async_update_data(self) -> KampklarData:
        try:
            children = await self.client.fetch_children()
            inbox = await self.client.fetch_inbox()
            activities: dict[str, list[TeamActivity]] = {}
            for child in children:
                activities[child.key] = await self.client.fetch_myteams(
                    team_id=child.team_id, person_id=child.person_id
                )

            # Fetch fulde bodies for de N nyeste, men kun dem vi ikke har i cache
            top = sorted(
                inbox, key=lambda m: m.received or 0, reverse=True
            )[:MESSAGE_DETAIL_COUNT]
            for m in top:
                if m.message_id in self._body_cache:
                    continue
                try:
                    details = await self.client.fetch_message_details(m.message_id)
                    self._body_cache[m.message_id] = details.body
                except (DbuAuthError, DbuConnectionError) as err:
                    _LOG.warning("Kunne ikke hente besked-body %s: %s", m.message_id, err)

            # Prune cache: kun behold IDs vi stadig ser i inbox
            live_ids = {m.message_id for m in inbox}
            self._body_cache = {
                mid: body for mid, body in self._body_cache.items() if mid in live_ids
            }

        except DbuAuthError as err:
            raise UpdateFailed(f"Login fejlede: {err}") from err
        except DbuConnectionError as err:
            raise UpdateFailed(f"Netværksfejl: {err}") from err

        return KampklarData(
            children=children,
            inbox=inbox,
            activities_by_child=activities,
            message_bodies=dict(self._body_cache),
        )
