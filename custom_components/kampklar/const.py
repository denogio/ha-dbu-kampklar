"""Konstanter for Kampklar-integrationen."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "kampklar"
PLATFORMS = ["sensor", "calendar"]

CONF_USERNAME = "username"
CONF_PASSWORD = "password"

UPDATE_INTERVAL = timedelta(minutes=60)

# Antal nyeste beskeder hvor vi henter fuld body (cached pr. message_id).
MESSAGE_DETAIL_COUNT = 10
