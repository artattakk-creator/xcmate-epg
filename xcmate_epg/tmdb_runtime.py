"""Explicit TMDB runtime-secret handoff, without files or secret discovery.

The inherited normal HTTP transport retains the platform's proxy/TLS behavior.
A managed binding is nonsecret platform metadata, not permission to discover or
retrieve a credential elsewhere. Constructing a client performs no request and
never establishes that live authentication has succeeded.
"""
import os

from .artwork import TMDB
from .fetch import http_get

TMDB_LIVE_PENDING = 'TMDB_LIVE_PENDING'
TMDB_RUNTIME_BOUND = 'TMDB_RUNTIME_BOUND'


def runtime_tmdb_client(*, environ=None, binding=None, transport=None, sleep=None):
    """Return (client-or-None, safe-status) from the exact runtime binding only.

    Prefer the platform-provided TMDB_READ_ACCESS_TOKEN environment value as-is.
    Otherwise explicit ready metadata for that name and api.themoviedb.org can
    enable header-free managed HTTP, whose platform proxy supplies credentials.
    No placeholder spelling is guessed, no other key/store is inspected, and no
    credential is fetched from GitHub or copied to image/CDN requests.
    """
    environment = os.environ if environ is None else environ
    value = environment.get('TMDB_READ_ACCESS_TOKEN')
    options = {'transport': http_get if transport is None else transport}
    if sleep is not None:
        options['sleep'] = sleep
    if isinstance(value, str) and value.strip() and not any(c in value for c in '\r\n'):
        return TMDB(value, **options), TMDB_RUNTIME_BOUND
    if (isinstance(binding, dict)
            and binding.get('name') == 'TMDB_READ_ACCESS_TOKEN'
            and binding.get('host') == 'api.themoviedb.org'
            and binding.get('state') == 'ready'):
        return TMDB(managed_http=True, **options), TMDB_RUNTIME_BOUND
    return None, TMDB_LIVE_PENDING
