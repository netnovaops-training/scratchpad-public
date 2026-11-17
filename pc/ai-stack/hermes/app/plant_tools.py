"""Plant lookup tools for Hermes.

TWO TOOLS, TWO SOURCES:

  plant_lookup   plant facts and characteristics from the USDA PLANTS database
                 via api.data.gov (read-only, no key visible to the model).

  plant_identify identify a plant from a photo URL via PlantNet (read-only,
                 top-3 candidates with confidence scores).

Both are READ-ONLY. No key value is ever placed in a tool result.

Keys are fetched from Proton Pass at call time via pass-cli. Nothing lives in
.env. The vault name defaults to the PLANT_API_VAULT env var (default: Keys).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

# Fixed 2026-10-28 night: the old domain (plants.sc.egov.usda.gov/api/plants/search)
# 404'd every time. This is the real one -- same one the plant-v2 backfill
# pipeline already uses successfully (pipeline/lib/apis.py).
_USDA_BASE = "https://plantsservices.sc.egov.usda.gov/api"
_PLANTNET_BASE = "https://my-api.plantnet.org/v2"
_TIMEOUT = 20
_PASS_CLI = os.environ.get("PASS_CLI_PATH", "pass-cli")
_VAULT = os.environ.get("PLANT_API_VAULT", "Keys")


_PAT_FILE = os.environ.get("HERMES_PAT_FILE", "")


def _ensure_logged_in(env: dict) -> None:
    """Self-heal Hermes's own pass-cli session from its dedicated PAT file --
    same pattern as pass-relogin.sh elsewhere in this project. Hermes cannot
    share the PC's session (different OS build of pass-cli; confirmed
    2026-10-28 night that a Windows-created session is not readable by the
    Linux binary, even in matching 'fs' mode). Does nothing if already
    logged in or if no PAT file is mounted."""
    check = subprocess.run([_PASS_CLI, "info"], env=env, capture_output=True, text=True, timeout=15)
    if check.returncode == 0:
        return
    if not _PAT_FILE or not os.path.isfile(_PAT_FILE):
        return  # nothing we can do; the real error surfaces from the caller
    with open(_PAT_FILE, "r", encoding="utf-8") as f:
        token = f.read().strip()
    subprocess.run([_PASS_CLI, "login", "--pat", token], env=env, capture_output=True, text=True, timeout=20)


def _pass_key(item_title: str, field: str = "API Key") -> str:
    """Fetch one field from a Proton Pass item via pass-cli. Never returns the
    value in an error message -- on failure raises RuntimeError with a safe msg."""
    env = {**os.environ, "PROTON_PASS_KEY_PROVIDER": "fs"}
    data_dir = os.environ.get("PROTON_PASS_CLI_DATA_DIR", "")
    if data_dir:
        env["PROTON_PASS_CLI_DATA_DIR"] = data_dir
    try:
        _ensure_logged_in(env)
        r = subprocess.run(
            [_PASS_CLI, "item", "view",
             "--vault-name", _VAULT,
             "--item-title", item_title,
             "--field", field],
            env=env,
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("pass-cli not found in the container. Rebuild Hermes.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("pass-cli timed out fetching the key.")
    if r.returncode != 0:
        raise RuntimeError(
            f"pass-cli could not read '{item_title}' from vault '{_VAULT}'. "
            "Check the item title matches exactly in Proton Pass."
        )
    return r.stdout.strip()


def _plantnet_key() -> str:
    # Item is titled "Plant Net Api" in the vault, not "PlantNet API key" --
    # corrected 2026-10-28 night after the original title was found to not
    # exist (plant_identify was silently broken as a result).
    return _pass_key("Plant Net Api", "API Key")


_KEY_PARAM_RE = re.compile(r"(?i)([?&](?:api[-_]?key)=)[^&]+")


def _redact_url(url: str) -> str:
    """Strip any api_key/api-key query value before a URL can reach an error
    message, a tool result, or a log -- found 2026-10-28 night: the key was
    printed in plain text via a USDA 404 error, breaking this file's own
    'no key value is ever placed in a tool result' promise."""
    return _KEY_PARAM_RE.sub(r"\1<redacted>", url)


def _get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {_redact_url(url)}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach {_redact_url(url)}: {e.reason}") from e


_TAG_RE = re.compile(r"<[^>]+>")


def plant_lookup(query: str, limit: int = 5) -> str:
    """Look up plants in the USDA PLANTS database.

    Returns scientific name, common name, rank and USDA symbol for up to
    `limit` matches. Works with common names or scientific names.

    No key needed -- this is a free, public USDA endpoint. (Corrected
    2026-10-28 night: the original code called the wrong domain entirely
    with a key this API doesn't use, which is why it only ever returned
    HTTP 404. plantsservices.sc.egov.usda.gov is the real one, confirmed
    against the same endpoint the plant-v2 backfill pipeline already uses.)
    """
    params = urllib.parse.urlencode({"searchText": query})
    url = f"{_USDA_BASE}/PlantSearch?{params}"
    try:
        data = _get(url)
    except RuntimeError as e:
        return f"USDA PLANTS lookup failed: {e}"

    if not isinstance(data, list):
        return "Unexpected response from USDA PLANTS API."
    if not data:
        return f"No USDA PLANTS results for '{query}'."

    lines = [f"USDA PLANTS results for '{query}':"]
    for r in data[:max(1, min(limit, 20))]:
        p = r.get("Plant") or {}
        symbol = p.get("Symbol", "")
        sci = _TAG_RE.sub("", p.get("ScientificName", "")).strip()
        common = p.get("CommonName", "")
        rank = p.get("Rank", "")
        parts = [f"  [{symbol}] {sci}"]
        if common:
            parts.append(f"    Common name: {common}")
        if rank:
            parts.append(f"    Rank: {rank}")
        lines.extend(parts)

    return "\n".join(lines)


def plant_identify(image_url: str, organs: str = "auto") -> str:
    """Identify a plant from a photo using PlantNet.

    Pass a publicly accessible image URL. `organs` can be 'auto', 'leaf',
    'flower', 'fruit', or 'bark'. Returns the top-3 candidates with
    scientific name, common name, and confidence score.

    Note: the image must be publicly accessible (not behind a login).
    """
    key = _plantnet_key()
    params = urllib.parse.urlencode({
        "images": image_url,
        "organs": organs,
        "include-related-images": "false",
        "lang": "en",
        "api-key": key,
    })
    url = f"{_PLANTNET_BASE}/identify/all?{params}"
    try:
        data = _get(url)
    except RuntimeError as e:
        return f"PlantNet identification failed: {e}"

    results = data.get("results", [])
    if not results:
        return "PlantNet could not identify the plant in this image."

    lines = ["PlantNet identification results:"]
    for r in results[:3]:
        score = r.get("score", 0)
        sp = r.get("species", {})
        sci = sp.get("scientificNameWithoutAuthor", "")
        common_names = sp.get("commonNames", [])
        common = common_names[0] if common_names else ""
        family = sp.get("family", {}).get("scientificNameWithoutAuthor", "")
        pct = f"{score * 100:.1f}%"
        line = f"  {pct}  {sci}"
        if common:
            line += f"  ({common})"
        if family:
            line += f"  — {family}"
        lines.append(line)

    lines.append(f"\nImage: {image_url}")
    return "\n".join(lines)


REGISTRY = {
    "plant_lookup": plant_lookup,
    "plant_identify": plant_identify,
}
