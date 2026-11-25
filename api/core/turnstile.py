"""
Cloudflare Turnstile (CAPTCHA) verification for the admin login.

Turnstile is on only when both ``[api] turnstile_site_key`` and
``turnstile_secret_key`` are set, and it is enforced only for requests that
came through the Cloudflare tunnel (see ``rate_limit.is_tunnel_request``).
Only the stdlib is used so there is nothing extra to install on the Pis.
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional, Tuple

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0
USER_AGENT = "lightshowpi-neo-api"


def keys(api_config) -> Tuple[str, str]:
    """
    ``(site_key, secret_key)`` from the API config, or ``("", "")`` when
    Turnstile is off. Half-configured (one key blank) counts as off.
    """
    site = (getattr(api_config, "turnstile_site_key", "") or "").strip()
    secret = (getattr(api_config, "turnstile_secret_key", "") or "").strip()
    if not site or not secret:
        return "", ""
    return site, secret


def verify(secret: str, token: str, remoteip: Optional[str] = None) -> bool:
    """
    Ask Cloudflare whether ``token`` is a valid Turnstile response.

    Fails closed: a blank token, network error, timeout, bad JSON or
    ``success: false`` all return False. Never logs the secret or the token.
    """
    if not token:
        return False
    fields = {"secret": secret, "response": token}
    if remoteip:
        fields["remoteip"] = remoteip
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        SITEVERIFY_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # URLError, timeout, http.client errors, bad JSON: fail closed
        log.warning(f"Turnstile verification failed: {type(e).__name__}")
        return False
    if isinstance(body, dict) and body.get("success") is True:
        return True
    codes = body.get("error-codes") if isinstance(body, dict) else None
    log.info(f"Turnstile rejected a login token: {codes}")
    return False
