"""
meraki_ap_crawler.py
--------------------
Crawls the Meraki Dashboard API and collects AP data across one or more
scopes (all orgs, single org, or specific networks).

New in this version:
  - Per-AP client connectivity scores (association, auth, DHCP, DNS step counts)
    fetched from the wireless client connectivity endpoint.

Scope is controlled by CLI flags:
  --all-orgs                Crawl every org accessible to the API key
  --org-id  <ORG_ID>        Crawl a single org (all its networks)
  --network-ids <N1> <N2>   Crawl specific network IDs only

Output is a JSON file (default: ap_data.json) consumed by the report
generator.  Pass --output <path> to override.

Usage examples:
  python meraki_ap_crawler.py --all-orgs
  python meraki_ap_crawler.py --org-id 123456
  python meraki_ap_crawler.py --network-ids L_111 L_222 --output my_data.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
Row     = dict[str, Any]
Headers = dict[str, str]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL:    str = "https://api.meraki.com/api/v1"
RETRY_LIMIT: int = 3
RETRY_WAIT:  int = 5   # seconds between retries on 429 / 5xx

# Lookback window for client connectivity data (seconds).  2 hours default.
CONNECTIVITY_TIMESPAN: int = 7200

# Connection steps we track, in order.
CONNECTION_STEPS: list[str] = ["association", "auth", "dhcp", "dns"]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _build_headers(api_key: str) -> Headers:
    return {
        "X-Cisco-Meraki-API-Key": api_key,
        "Content-Type":           "application/json",
        "Accept":                 "application/json",
    }


def _get(url: str, headers: Headers, params: dict[str, Any] | None = None) -> Any:
    """GET with simple retry logic for rate-limits and transient errors."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as exc:
            if attempt == RETRY_LIMIT:
                raise SystemExit(f"[ERROR] Network error reaching {url}: {exc}") from exc
            time.sleep(RETRY_WAIT)
            continue

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", RETRY_WAIT))
            print(f"  [rate-limit] waiting {retry_after}s …", flush=True)
            time.sleep(retry_after)
            continue
        if resp.status_code in (500, 502, 503, 504):
            print(f"  [transient {resp.status_code}] retrying in {RETRY_WAIT}s …", flush=True)
            time.sleep(RETRY_WAIT)
            continue

        # Non-retryable error
        raise SystemExit(
            f"[ERROR] {resp.status_code} from {url}: {resp.text[:200]}"
        )

    raise SystemExit(f"[ERROR] Exceeded retry limit for {url}")


def _get_paginated(url: str, headers: Headers, params: dict[str, Any] | None = None) -> list[Any]:
    """Follow Meraki's Link-header pagination and collect all pages."""
    params = dict(params or {})
    params.setdefault("perPage", 1000)
    results: list[Any] = []
    next_url: str | None = url

    while next_url:
        resp = requests.get(next_url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", RETRY_WAIT))
            print(f"  [rate-limit] waiting {retry_after}s …", flush=True)
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        results.extend(resp.json())

        # Parse Link header for next page
        link = resp.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                params = {}   # URL already has query params baked in
                break

    return results


# ---------------------------------------------------------------------------
# Meraki API calls
# ---------------------------------------------------------------------------

def get_organizations(headers: Headers) -> list[Row]:
    print("Fetching organizations …")
    return _get(f"{BASE_URL}/organizations", headers)


def get_networks(org_id: str, headers: Headers) -> list[Row]:
    print(f"  Fetching networks for org {org_id} …")
    return _get_paginated(f"{BASE_URL}/organizations/{org_id}/networks", headers)


def get_network_devices(network_id: str, headers: Headers) -> list[Row]:
    return _get(f"{BASE_URL}/networks/{network_id}/devices", headers)


def get_org_device_statuses(org_id: str, headers: Headers) -> dict[str, Row]:
    """Return a serial → status mapping for all devices in an org."""
    print(f"  Fetching device statuses for org {org_id} …")
    statuses = _get_paginated(
        f"{BASE_URL}/organizations/{org_id}/devices/statuses",
        headers,
    )
    return {s["serial"]: s for s in statuses}


def get_org_alerts(org_id: str, headers: Headers) -> list[Row]:
    """Fetch active alerts (alarms) for an org."""
    print(f"  Fetching alerts for org {org_id} …")
    try:
        data = _get(f"{BASE_URL}/organizations/{org_id}/assurance/alerts", headers)
        if isinstance(data, dict):
            return data.get("items", [])
        return data
    except SystemExit:
        return []


def get_network_client_connectivity(network_id: str, headers: Headers) -> list[Row]:
    """
    Fetch wireless client connection step data for a network.

    Endpoint: GET /networks/{networkId}/wireless/clients/connectionStats
    Returns a list of records, one per client, each with a 'connectionStats'
    dict containing counts for: assoc, auth, dhcp, dns, success.

    The AP serial is available in each record as 'apSerial' (or 'serial'
    depending on Dashboard version).
    """
    print(f"      Fetching client connectivity for network {network_id} …")
    try:
        data = _get(
            f"{BASE_URL}/networks/{network_id}/wireless/clients/connectionStats",
            headers,
            params={"timespan": CONNECTIVITY_TIMESPAN},
        )
        # Endpoint returns a list of per-client objects
        if isinstance(data, list):
            return data
        return []
    except SystemExit:
        return []


# ---------------------------------------------------------------------------
# Client score helpers
# ---------------------------------------------------------------------------

def _empty_step_counts() -> dict[str, Any]:
    """Zero-valued step count record for an AP with no client data."""
    return {
        "assoc_total":   0, "assoc_fail":   0,
        "auth_total":    0, "auth_fail":    0,
        "dhcp_total":    0, "dhcp_fail":    0,
        "dns_total":     0, "dns_fail":     0,
        "success_total": 0,
        "client_score":  None,   # None = no data
    }


def _build_ap_client_scores(client_records: list[Row]) -> dict[str, dict[str, Any]]:
    """
    Aggregate per-client connection step counts into per-AP totals.

    Returns: serial → step count dict (same shape as _empty_step_counts).

    Meraki's connectionStats shape per client:
      {
        "mac": "...",
        "serial": "Q2KN-...",          # AP serial
        "connectionStats": {
          "assoc": 5, "auth": 4, "dhcp": 4, "dns": 3, "success": 3
        }
      }
    The counts represent *failed* attempts for assoc/auth/dhcp/dns and
    *successful* connections for "success".
    """
    totals: dict[str, dict[str, Any]] = {}

    for rec in client_records:
        serial = rec.get("serial") or rec.get("apSerial") or ""
        if not serial:
            continue
        cs = rec.get("connectionStats") or {}

        if serial not in totals:
            totals[serial] = _empty_step_counts()
            totals[serial]["client_score"] = 0   # reset to int now we have data

        t = totals[serial]

        # Meraki returns fail counts for each step; success is full successes.
        assoc_fail = int(cs.get("assoc", 0))
        auth_fail  = int(cs.get("auth",  0))
        dhcp_fail  = int(cs.get("dhcp",  0))
        dns_fail   = int(cs.get("dns",   0))
        success    = int(cs.get("success", 0))

        # Total attempts per step = failures at that step + all subsequent
        # attempts (approximation: total = fail + success for the innermost
        # step; outer steps accumulate their own failures on top).
        # Simpler and accurate: store raw fail counts + successes; let the
        # report compute pass rates.
        t["assoc_fail"] += assoc_fail
        t["auth_fail"]  += auth_fail
        t["dhcp_fail"]  += dhcp_fail
        t["dns_fail"]   += dns_fail
        t["success_total"] += success

        # Total attempts = sum of all failures (each represents a drop-off
        # at that step) plus final successes.
        t["assoc_total"] += assoc_fail + auth_fail + dhcp_fail + dns_fail + success
        t["auth_total"]  += auth_fail  + dhcp_fail + dns_fail  + success
        t["dhcp_total"]  += dhcp_fail  + dns_fail  + success
        t["dns_total"]   += dns_fail   + success

    # Compute composite 0-100 client score per AP:
    # Score = (successful connections / total association attempts) * 100
    for serial, t in totals.items():
        if t["assoc_total"] > 0:
            t["client_score"] = round(t["success_total"] / t["assoc_total"] * 100, 1)
        else:
            t["client_score"] = None

    return totals


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _is_ap(device: Row) -> bool:
    model: str = device.get("model", "")
    return model.upper().startswith("MR") or model.upper().startswith("CW")


def collect_for_networks(
    network_ids: list[str],
    org_id: str | None,
    headers: Headers,
) -> list[Row]:
    """
    Gather AP rows for a list of network IDs.
    org_id is used to fetch statuses/alerts in bulk when available.
    """
    # Build a network-id → network-name map (best-effort)
    net_name: dict[str, str] = {}
    if org_id:
        for nw in get_networks(org_id, headers):
            net_name[nw["id"]] = nw.get("name", nw["id"])

    # Build status map (bulk)
    status_map: dict[str, Row] = {}
    if org_id:
        status_map = get_org_device_statuses(org_id, headers)

    # Build alert map: serial → list[alert]
    alert_map: dict[str, list[Row]] = {}
    if org_id:
        for alert in get_org_alerts(org_id, headers):
            for scope in alert.get("scope", {}).get("devices", []):
                serial = scope.get("serial", "")
                if serial:
                    alert_map.setdefault(serial, []).append(alert)

    rows: list[Row] = []

    for net_id in network_ids:
        name = net_name.get(net_id, net_id)
        print(f"    Scanning network: {name} ({net_id}) …")
        try:
            devices = get_network_devices(net_id, headers)
        except SystemExit as exc:
            print(f"      [WARN] skipping network {net_id}: {exc}")
            continue

        # Fetch client connectivity for this network and build per-AP scores
        client_records = get_network_client_connectivity(net_id, headers)
        ap_scores = _build_ap_client_scores(client_records)

        for dev in devices:
            if not _is_ap(dev):
                continue

            serial: str = dev.get("serial", "")
            status_rec = status_map.get(serial, {})

            last_seen_raw: str = (
                status_rec.get("lastReportedAt")
                or status_rec.get("lastSeenAt")
                or dev.get("lastReportedAt", "")
            )
            last_seen: str = _fmt_ts(last_seen_raw)

            tags: list[str] = dev.get("tags", []) or []

            alarms: list[str] = [
                a.get("type", "Unknown alarm")
                for a in alert_map.get(serial, [])
            ]

            score_data = ap_scores.get(serial, _empty_step_counts())

            rows.append({
                "network_name":  name,
                "network_id":    net_id,
                "name":          dev.get("name", serial),
                "serial":        serial,
                "model":         dev.get("model", ""),
                "tags":          tags,
                "status":        status_rec.get("status", "unknown"),
                "last_seen":     last_seen,
                "alarms":        alarms,
                "org_id":        org_id or "",
                # Client connectivity step counts
                "assoc_total":   score_data["assoc_total"],
                "assoc_fail":    score_data["assoc_fail"],
                "auth_total":    score_data["auth_total"],
                "auth_fail":     score_data["auth_fail"],
                "dhcp_total":    score_data["dhcp_total"],
                "dhcp_fail":     score_data["dhcp_fail"],
                "dns_total":     score_data["dns_total"],
                "dns_fail":      score_data["dns_fail"],
                "success_total": score_data["success_total"],
                "client_score":  score_data["client_score"],
            })

    return rows


def _fmt_ts(raw: str) -> str:
    """Convert ISO-8601 string to a readable local-ish string, or return raw."""
    if not raw:
        return "N/A"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# Scope resolvers
# ---------------------------------------------------------------------------

def collect_all_orgs(headers: Headers) -> list[Row]:
    orgs = get_organizations(headers)
    all_rows: list[Row] = []
    for org in orgs:
        org_id = str(org["id"])
        print(f"Processing org: {org.get('name', org_id)} ({org_id})")
        networks = get_networks(org_id, headers)
        net_ids = [n["id"] for n in networks]
        all_rows.extend(collect_for_networks(net_ids, org_id, headers))
    return all_rows


def collect_single_org(org_id: str, headers: Headers) -> list[Row]:
    print(f"Processing org: {org_id}")
    networks = get_networks(org_id, headers)
    net_ids = [n["id"] for n in networks]
    return collect_for_networks(net_ids, org_id, headers)


def collect_specific_networks(network_ids: list[str], headers: Headers) -> list[Row]:
    return collect_for_networks(network_ids, org_id=None, headers=headers)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Crawl Meraki Dashboard for AP data and write JSON output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--all-orgs",
        action="store_true",
        help="Crawl all organisations accessible to the API key.",
    )
    scope.add_argument(
        "--org-id",
        metavar="ORG_ID",
        help="Crawl a single organisation (all its networks).",
    )
    scope.add_argument(
        "--network-ids",
        nargs="+",
        metavar="NETWORK_ID",
        help="Crawl specific network IDs only.",
    )

    p.add_argument(
        "--api-key",
        metavar="KEY",
        default=os.environ.get("MERAKI_API_KEY", ""),
        help="Meraki Dashboard API key (or set MERAKI_API_KEY env var).",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default="ap_data.json",
        help="Path to write the collected JSON data (default: ap_data.json).",
    )
    p.add_argument(
        "--connectivity-timespan",
        metavar="SECONDS",
        type=int,
        default=CONNECTIVITY_TIMESPAN,
        help=f"Lookback window for client connectivity data in seconds (default: {CONNECTIVITY_TIMESPAN}).",
    )
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    api_key: str = args.api_key
    if not api_key:
        parser.error(
            "Meraki API key required. Use --api-key or set MERAKI_API_KEY."
        )

    # Allow CLI override of the timespan constant
    global CONNECTIVITY_TIMESPAN
    CONNECTIVITY_TIMESPAN = args.connectivity_timespan

    headers = _build_headers(api_key)
    started = datetime.now(timezone.utc).isoformat()

    if args.all_orgs:
        rows = collect_all_orgs(headers)
    elif args.org_id:
        rows = collect_single_org(args.org_id, headers)
    else:
        rows = collect_specific_networks(args.network_ids, headers)

    payload: dict[str, Any] = {
        "crawled_at": started,
        "scope": (
            "all_orgs"               if args.all_orgs  else
            f"org:{args.org_id}"     if args.org_id    else
            f"networks:{','.join(args.network_ids)}"
        ),
        "ap_count": len(rows),
        "access_points": rows,
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"\nDone. {len(rows)} APs written to {args.output}")


if __name__ == "__main__":
    main()