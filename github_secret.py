"""Update repository Actions Secrets via the GitHub API.

Used so runtime changes (/slot, /cookies) survive the next workflow restart.
Best-effort: callers wrap this in try/except. Requires GITHUB_TOKEN with
`secrets:write` (the default Actions token works for the same repo) and PyNaCl
for the libsodium sealed-box encryption GitHub mandates.
"""
from __future__ import annotations

from base64 import b64encode

import requests

API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _encrypt(public_key_b64: str, value: str) -> str:
    from nacl import encoding, public

    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode("utf-8"))
    return b64encode(sealed).decode("utf-8")


def update_secrets(repo: str, token: str, secrets: dict[str, str], timeout: float = 15.0) -> None:
    """PUT one or more `name -> value` secrets into `owner/repo`."""
    key_resp = requests.get(
        f"{API}/repos/{repo}/actions/secrets/public-key",
        headers=_headers(token),
        timeout=timeout,
    )
    key_resp.raise_for_status()
    key = key_resp.json()
    key_id, public_key = key["key_id"], key["key"]

    for name, value in secrets.items():
        encrypted = _encrypt(public_key, value)
        r = requests.put(
            f"{API}/repos/{repo}/actions/secrets/{name}",
            headers=_headers(token),
            json={"encrypted_value": encrypted, "key_id": key_id},
            timeout=timeout,
        )
        r.raise_for_status()
