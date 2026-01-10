import os
import random
import time
import urllib3
import requests
import logging

from typing import Any, Dict, List, Optional, Tuple
from requests.auth import HTTPBasicAuth

APPLY_CHANGES = True   # set True when you're ready
VERIFY_TLS = False      # most lab pfSense boxes have self-signed certs
TIMEOUT = 12

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    if v == "" or v.lower() in {"none", "null"}:
        return None
    return v


class PfSenseClient:
    def __init__(self, host: str, api_key: Optional[str], username: Optional[str], password: Optional[str]):
        self.host = host
        self.base = f"https://{host}"
        self.api_key = api_key
        self.basic = HTTPBasicAuth(username, password) if (username and password) else None

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def request(self, method: str, path: str, json: Optional[dict] = None) -> Dict[str, Any]:
        url = self.base + path
        r = requests.request(
            method=method,
            url=url,
            headers=self._headers(),
            auth=self.basic,
            json=json,
            verify=VERIFY_TLS,
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        try:
            payload = r.json()
        except Exception:
            payload = {"http_status": r.status_code, "text": r.text}

        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed (HTTP {r.status_code}): {payload}")
        return payload

    def get(self, path: str) -> Dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, json: dict) -> Dict[str, Any]:
        return self.request("POST", path, json=json)

    def patch(self, path: str, json: dict) -> Dict[str, Any]:
        return self.request("PATCH", path, json=json)

    def delete(self, path: str, json: Optional[dict] = None) -> Dict[str, Any]:
        return self.request("DELETE", path, json=json)


def _detect_api_prefix(cli: PfSenseClient) -> str:
    for prefix in ("/api/v2", "/api/v1"):
        try:
            _ = cli.get(f"{prefix}/firewall/rules")
            return prefix
        except Exception:
            continue
    raise RuntimeError("Could not detect pfSense REST API (tried /api/v2 and /api/v1).")


def fault_insert_block_all(cli: PfSenseClient, api: str) -> str:
    iface = "lan"
    try:
        resp = cli.get(f"{api}/interfaces")
        data = resp.get("data") or []
        for i in data:
            if not isinstance(i, dict):
                continue
            if str(i.get("if") or i.get("interface") or "").lower() in {"wan"}:
                continue
            if str(i.get("descr") or "").lower() == "wan":
                continue
            iface = str(i.get("descr") or i.get("if") or i.get("interface") or iface).lower()
            break
    except Exception:
        pass
    body = {
        "type": "block",
        "interface": [iface],   # <-- must be array
        "ipprotocol": "inet",
        "protocol": None,
        "source": "any",
        "destination": "any",
        "descr": f"CHAOS: BLOCK ALL ({int(time.time())})",
        "disabled": False,
        "floating": True,
        "quick": True,
        "direction":"any",
    }
    if not APPLY_CHANGES:
        logger.info("Currently in Test Mode.  Changes not applied.  To change behavior, update APPLY_CHANGES to True in uproot/labs/lab1/pkg/firewalls.py")

    cli.post(f"{api}/firewall/rule", json=body)
    cli.post(f"{api}/firewall/apply", json={})
    log_msg=f"APPLIED: inserted BLOCK ALL rule on {body['interface'][0]} + applied"
    return log_msg    

def fault_disable_outbound_nat(cli: PfSenseClient, api: str) -> str:
    if not APPLY_CHANGES:
        logger.info("Currently in Test Mode.  Changes not applied.  To change behavior, update APPLY_CHANGES to True in uproot/labs/lab1/pkg/firewalls.py")
    cli.patch(f"{api}/firewall/nat/outbound/mode", json={"mode": "disabled"})
    cli.post(f"{api}/firewall/apply", json={})

    log_msg="APPLIED: outbound NAT mode set to disabled + firewall apply executed"
    return log_msg

def fault_disable_default_gateway(cli: PfSenseClient, api: str) -> str:
    if not APPLY_CHANGES:
        logger.info("Currently in Test Mode.  Changes not applied.  To change behavior, update APPLY_CHANGES to True in uproot/labs/lab1/pkg/firewalls.py")

    # Try to list gateways
    gw_list_paths = [
        f"{api}/routing/gateways",
        f"{api}/routing/gateway",
    ]

    gateways = None
    for p in gw_list_paths:
        try:
            resp = cli.get(p)
            data = resp.get("data")
            if isinstance(data, list):
                gateways = data
                break
            if isinstance(data, dict):
                gateways = [data]
                break
        except Exception:
            continue

    if not gateways:
        raise RuntimeError(
            "Unable to list gateways. This is a bug."
        )

    default_gw = None
    for g in gateways:
        if isinstance(g, dict) and str(g.get("name", "")).upper() == "WANGW":
            default_gw = g
            break

    if default_gw is None:
        for g in gateways:
            if not isinstance(g, dict):
                continue
            if g.get("default") is True or g.get("is_default") is True or g.get("defaultgw") is True:
                default_gw = g
                break

    if default_gw is None:
        for g in gateways:
            if not isinstance(g, dict):
                continue
            if str(g.get("disabled", "")).lower() in {"true", "yes"} or g.get("disabled") in (True, 1):
                continue
            default_gw = g
            break

    gw_id = default_gw.get("id")
    gw_name = default_gw.get("name") or default_gw.get("gateway") or default_gw.get("descr") or "UNKNOWN"

    if gw_id is None:
        payload = {"name": gw_name, "disabled": True, "apply": True}
        cli.patch(f"{api}/routing/gateway", json=payload)
        log_msg=f"APPLIED: disabled default gateway (name={gw_name})"
        return log_msg
    else:
        payload = {"id": gw_id, "disabled": True, "apply": True}
        cli.patch(f"{api}/routing/gateway", json=payload)
        log_msg=f"APPLIED: disabled default gateway (id={gw_id}, name={gw_name})"
        return log_msg


def _build_client(prefix: str, mgmt_ip_env: str) -> PfSenseClient:
    host = _env(mgmt_ip_env)
    if not host:
        raise RuntimeError(f"Missing {mgmt_ip_env}")

    api_key = _env(f"{prefix}_API_KEY") or _env("PFSENSE_API_KEY")
    user = _env(f"{prefix}_USERNAME")
    pw = _env(f"{prefix}_PASSWORD")

    if not api_key and not (user and pw):
        raise RuntimeError(
            f"{prefix}: Provide either {prefix}_API_KEY (preferred) or {prefix}_USERNAME/{prefix}_PASSWORD for Basic auth."
        )

    return PfSenseClient(host=host, api_key=api_key, username=user, password=pw)


def _run_one_firewall(label: str, cli: PfSenseClient) -> None:
    api = _detect_api_prefix(cli)

    faults = [
        ("default_gateway_chaos", fault_disable_default_gateway),
        ("disable_outbound_nat", fault_disable_outbound_nat),
        ("insert_block_all_rule", fault_insert_block_all),
    ]

    name, fn = random.choice(faults)
    
    logger.info("%s : Reconfiguring %s via API (%s)", label, cli.host, api)
    logger.info("* Selected fault: %s", name)

    result = fn(cli, api)
    logger.info("%s", result)


def run_firewalls() -> None:
    branch = _build_client("BRANCH_FW", "BRANCH_FW_MGMT_IP")
    app = _build_client("APP_FW", "APP_FW_MGMT_IP")

    _run_one_firewall("BRANCH_FW", branch)
    _run_one_firewall("APP_FW", app)
