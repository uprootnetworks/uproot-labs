import os
import random
import re
import ipaddress
import logging 

from typing import List, Dict, Optional, Tuple
from netmiko import ConnectHandler

logger = logging.getLogger(__name__)

APPLY_CHANGES = True
WRITE_MEM = False
FAULTS_PER_ROUTER = 1
BOGUS_NHOP = "203.0.113.1"


def env_optional(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    if v == "" or v.lower() in {"none", "null", "nil"}:
        return None
    return v


def ensure_privileged(conn, enable_secret: Optional[str]) -> None:
    prompt = conn.find_prompt().strip()
    if prompt.endswith("#"):
        return

    if enable_secret:
        conn.enable()
        if conn.find_prompt().strip().endswith("#"):
            return
        priv = conn.send_command("show privilege", expect_string=r"[#>]")
        raise RuntimeError(f"Enable with secret failed.\n{priv}")

    if prompt.endswith(">"):
        out = conn.send_command_timing("enable")
        prompt2 = conn.find_prompt().strip()
        if prompt2.endswith("#"):
            return
        if "Password" in out:
            raise RuntimeError("Enable password prompt detected, but SP_ROUTER*_ENABLE is not set.")
        priv = conn.send_command("show privilege", expect_string=r"[#>]")
        raise RuntimeError(f"Failed to enter privileged mode.\n{priv}")

    raise RuntimeError(f"Unexpected prompt: {prompt}")


def connect_iou_router(host: str, username: Optional[str], password: Optional[str], enable_secret: Optional[str]):
    device = {
        "device_type": "cisco_ios_telnet",
        "host": host,
        "port": 23,
        "fast_cli": False,
        "global_delay_factor": 2,
    }
    if username:
        device["username"] = username
    if password:
        device["password"] = password
    if enable_secret:
        device["secret"] = enable_secret

    conn = ConnectHandler(**device)
    ensure_privileged(conn, enable_secret)
    return conn


def get_l3_ifaces(conn) -> List[Dict[str, str]]:
    data = conn.send_command("show ip interface brief", use_textfsm=True)
    if data:
        out = []
        for r in data:
            iface = r.get("intf") or r.get("interface") or r.get("intf_name") or r.get("interface_name")
            ipaddr = r.get("ipaddr") or r.get("ip_address") or r.get("ip") or r.get("address")
            status = r.get("status") or r.get("interface_status") or r.get("line_status")
            proto = r.get("proto") or r.get("protocol") or r.get("line_protocol")
            if iface:
                out.append({
                    "interface": str(iface),
                    "ipaddr": str(ipaddr or ""),
                    "status": str(status or ""),
                    "proto": str(proto or ""),
                })
        return out

    raw = conn.send_command("show ip interface brief")
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("interface"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 6:
            continue
        rows.append({
            "interface": parts[0],
            "ipaddr": parts[1],
            "status": parts[-2],
            "proto": parts[-1],
        })
    return rows


def pick_two_interfaces(l3_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    candidates = []
    for r in l3_rows:
        iface = r["interface"]
        ipaddr = r["ipaddr"].lower()
        if iface.lower().startswith("loop"):
            continue
        if ipaddr in {"unassigned", "unknown", ""}:
            continue
        candidates.append(r)

    up = [r for r in candidates if r.get("status", "").lower() == "up" and r.get("proto", "").lower() == "up"]
    if len(up) >= 2:
        return up[:2]

    if len(candidates) < 2:
        raise RuntimeError(f"Could not identify 2 L3 interfaces. Found: {candidates}")
    return candidates[:2]


def get_default_route_oif(conn) -> Optional[str]:
    raw = conn.send_command("show ip route 0.0.0.0")
    for line in raw.splitlines():
        if line.strip().startswith("S*") or "0.0.0.0/0" in line:
            tokens = re.split(r"\s+", line.strip())
            if tokens:
                last = tokens[-1]
                if re.match(r"^[A-Za-z]+", last):
                    return last
    return None


def iface_connected_network(conn, iface: str) -> Optional[ipaddress.IPv4Network]:
    raw = conn.send_command(f"show ip interface {iface}")
    m = re.search(r"Internet address is (\d+\.\d+\.\d+\.\d+)/(\d+)", raw)
    if not m:
        return None
    ip = m.group(1)
    prefix = int(m.group(2))
    return ipaddress.ip_network(f"{ip}/{prefix}", strict=False)


def faults_full(north_if: str, south_if: str, south_net: Optional[ipaddress.IPv4Network]) -> List[Tuple[str, List[str]]]:
    faults: List[Tuple[str, List[str]]] = []

    faults.append(("remove_default_route", ["no ip route 0.0.0.0 0.0.0.0"]))

    faults.append((
        "wrong_default_next_hop_forced_interface",
        ["no ip route 0.0.0.0 0.0.0.0", f"ip route 0.0.0.0 0.0.0.0 {north_if} {BOGUS_NHOP}"]
    ))

    faults.append((
        "default_out_wrong_interface_south",
        ["no ip route 0.0.0.0 0.0.0.0", f"ip route 0.0.0.0 0.0.0.0 {south_if} {BOGUS_NHOP}"]
    ))

    faults.append(("shutdown_northbound", [f"interface {north_if}", "shutdown"]))
    faults.append(("remove_northbound_ip", [f"interface {north_if}", "no ip address"]))

    faults.append((
        "drop_all_outbound_on_northbound",
        [
            "ip access-list extended CHAOS_OUT",
            "deny ip any any",
            "exit",
            f"interface {north_if}",
            "ip access-group CHAOS_OUT out",
        ]
    ))

    if south_net:
        faults.append((
            "blackhole_south_connected_subnet",
            [f"ip route {south_net.network_address} {south_net.netmask} Null0"]
        ))

    return faults


def faults_router2_safe(north_if: str, south_if: str, south_net: Optional[ipaddress.IPv4Network]) -> List[Tuple[str, List[str]]]:
    faults: List[Tuple[str, List[str]]] = []

    faults.append(("shutdown_southbound", [f"interface {south_if}", "shutdown"]))

    faults.append(("remove_southbound_ip", [f"interface {south_if}", "no ip address"]))

    faults.append((
        "drop_all_outbound_on_southbound",
        [
            "ip access-list extended CHAOS_SB_OUT",
            "deny ip any any",
            "exit",
            f"interface {south_if}",
            "ip access-group CHAOS_SB_OUT out",
        ]
    ))

    if south_net:
        faults.append((
            "blackhole_south_connected_subnet",
            [f"ip route {south_net.network_address} {south_net.netmask} Null0"]
        ))

    return faults


def apply_fault(conn, router_label: str, mode: str) -> None:
    l3 = get_l3_ifaces(conn)
    two = pick_two_interfaces(l3)

    def_oif = get_default_route_oif(conn)

    if def_oif and any(def_oif == r["interface"] for r in two):
        north_if = def_oif
        south_if = next(r["interface"] for r in two if r["interface"] != def_oif)
    else:
        north_if = two[0]["interface"]
        south_if = two[1]["interface"]

    south_net = iface_connected_network(conn, south_if)
    north_net = iface_connected_network(conn, north_if)

    if mode == "r2_safe":
        faults = faults_router2_safe(north_if, south_if, south_net)
    else:
        faults = faults_full(north_if, south_if, south_net)

    fault_name, cmds = random.choice(faults)

    logger.info("Logging in to %s...", router_label)
    logger.info("* Northbound Intf: %s (%s)",
                north_if,
                north_net if north_net else "unknown",
                )
    logger.info("* Southbound Intf: %s (%s)",
                south_if,
                south_net if south_net else "unknown",
                )
    logger.info("Introduced fault: %s", fault_name)
#    for c in cmds:
#        logger.info("**running command: %s", c)

    if not APPLY_CHANGES:
        logger.warning("Config not applied, currently in Test Mode.  Set variable APPLY_CHANGES to True in uproot/labs/lab1/pkg/routers.py to commit changes and re-run")
        return

    try:
        conn.send_config_set(cmds, exit_config_mode=False)
        try:
            conn.exit_config_mode()
        except Exception:
            pass
    except ReadTimeout:
        logger.info("Telnet dropped during commit (expected for this fault type)")

    if WRITE_MEM:
        conn.save_config()
    logger.info("Router broken successfully")


def run_sp_routers() -> None:
    r1 = env_optional("SP_ROUTER1_NB_IP")
    r2 = env_optional("SP_ROUTER2_NB_IP")
    if not r1 or not r2:
        raise RuntimeError("Missing SP_ROUTER1_MGMT_IP or SP_ROUTER2_MGMT_IP in env")

    r1_user = env_optional("SP_ROUTER1_USERNAME")
    r1_pass = env_optional("SP_ROUTER1_PASSWORD")
    r1_en   = env_optional("SP_ROUTER1_ENABLE")

    r2_user = env_optional("SP_ROUTER2_USERNAME")
    r2_pass = env_optional("SP_ROUTER2_PASSWORD")
    r2_en   = env_optional("SP_ROUTER2_ENABLE")

    c2 = connect_iou_router(r2, r2_user, r2_pass, r2_en)
    try:
        for _ in range(FAULTS_PER_ROUTER):
            apply_fault(c2, "SP-Router2", mode="r2_safe")
    finally:
        c2.disconnect()

    c1 = connect_iou_router(r1, r1_user, r1_pass, r1_en)
    try:
        for _ in range(FAULTS_PER_ROUTER):
            apply_fault(c1, "SP-Router1", mode="full")
    finally:
        c1.disconnect()
