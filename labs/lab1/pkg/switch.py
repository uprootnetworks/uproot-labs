import os
import random
import re
import subprocess
import logging

from typing import Set, Optional, List, Tuple

from netmiko import ConnectHandler

logger = logging.getLogger(__name__)

MGMT_VLAN = 1

VLAN_MIN = 2
VLAN_MAX = 4094

MAX_PORTS = 4

TRUNK_PROB = 0.25                   #Probability of port being configured as Trunk
TRUNK_ALLOWED_MIN = 3
TRUNK_ALLOWED_MAX = 12
SET_NATIVE_VLAN_ON_TRUNK = True

APPLY_CHANGES = True
WRITE_MEM = False                   #By default, changes are not written to memory and will be lost on a reload.  Change this to True if you want changes to be commited to mem.

EXCLUDE_PORTS: Set[str] = set()


# Detects which switchport your this host is connected to, and excludes it from being reconfigured.  This is so we maintain mgmt connectivity.

def _get_egress_iface(dest_ip: str) -> str:
    out = subprocess.check_output(["ip", "-o", "route", "get", dest_ip], text=True).strip()
    m = re.search(r"\bdev\s+(\S+)", out)
    if not m:
        raise RuntimeError(f"Could not determine egress interface for {dest_ip}. Output: {out}")
    return m.group(1)

def _get_iface_mac(iface: str) -> str:
    with open(f"/sys/class/net/{iface}/address", "r") as f:
        return f.read().strip().lower()

def _mac_to_cisco_dotted(mac: str) -> str:
    hexchars = mac.replace(":", "").replace("-", "").lower()
    if len(hexchars) != 12:
        raise ValueError(f"Unexpected MAC format: {mac}")
    return f"{hexchars[0:4]}.{hexchars[4:8]}.{hexchars[8:12]}"

def _find_port_for_mac(conn, mac_dotted: str) -> Optional[str]:
    out = conn.send_command(f"show mac address-table | include {mac_dotted}")
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        candidate = parts[-1]
        if candidate.lower() in {"cpu", "router", "sup"}:
            continue
        if re.match(r"^[A-Za-z]+", candidate):
            return candidate
    return None

#Used when enable user/pw/enable is set.  Have not thoroughly tested this yet.
def ensure_privileged(conn) -> None:
    prompt = conn.find_prompt().strip()
    if prompt.endswith("#"):
        return

    if prompt.endswith(">"):
        out = conn.send_command_timing("enable")
        if "Password" in out:
            raise RuntimeError(
                "Enable password prompt detected but SWITCH1_ENABLE isn't set. "
                "Either set SWITCH1_ENABLE or ensure enable has no password."
            )
        prompt2 = conn.find_prompt().strip()
        if not prompt2.endswith("#"):
            priv = conn.send_command("show privilege", expect_string=r"[#>]")
            raise RuntimeError(f"Failed to enter privileged mode. Prompt: {prompt2}\nshow privilege:\n{priv}")
        return

    raise RuntimeError(f"Unexpected prompt: {prompt}")


def _rand_vlan(exclude: Set[int]) -> int:
    for _ in range(50):
        v = random.randint(VLAN_MIN, VLAN_MAX)
        if v not in exclude:
            return v
    for v in range(VLAN_MIN, VLAN_MAX + 1):
        if v not in exclude:
            return v
    raise RuntimeError("No VLAN IDs available to choose from.")

def _rand_vlan_list(count: int, exclude: Set[int]) -> List[int]:
    vlans: Set[int] = set()
    while len(vlans) < count:
        vlans.add(_rand_vlan(exclude | vlans))
    return sorted(vlans)

def _vlan_list_to_ios(vlans: List[int]) -> str:
    return ",".join(str(v) for v in vlans)

def _maybe_create_vlans(conn, vlan_ids: List[int]) -> None:
    cmds = []
    for v in vlan_ids:
        cmds += [f"vlan {v}", "exit"]
    if cmds:
        conn.send_config_set(cmds)


def run_switch() -> None:
    host = os.getenv("SWITCH1_MGMT_IP")
    if not host:
        raise RuntimeError("SWITCH1_MGMT_IP is not set. Double check uproot/labs/lab1/.env for SWITCH1_MGMT_IP, and assign if not set.")

    username = os.getenv("SWITCH1_USERNAME")    #Not used by default.  Need to uncomment in uproot/labs/lab1/.env if you want to use this
    password = os.getenv("SWITCH1_PASSWORD")    #Not used by default.  Need to uncomment in uproot/labs/lab1/.env if you want to use this

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

    logger.info("Connecting to switch at %s...", host)

    conn = ConnectHandler(**device)
    try:
        ensure_privileged(conn)

        try:
            egress_iface = _get_egress_iface(host)
            egress_mac = _get_iface_mac(egress_iface)
            mac_dotted = _mac_to_cisco_dotted(egress_mac)

            logger.info("Ubuntu egress: %s (MAC %s)", egress_iface, egress_mac)
            logger.info("Looking up host MAC on switch: %s", egress_mac)

            host_port = _find_port_for_mac(conn, mac_dotted)
            if host_port:
                EXCLUDE_PORTS.add(host_port)
                logger.info("Auto-exclusing host switchport: %s", host_port)
            else:
                logger.warning("Host MAC not found in Switch1 MAC table; no auto-exclusion considered.")
        except Exception as e:
            logger.warning("Host port auto-detect failed (continuing): %s", e)

        interfaces = conn.send_command("show interfaces status", use_textfsm=True)
        if not interfaces:
            raise RuntimeError("Unable to parse 'show interfaces status' (TextFSM returned nothing)")

        connected = [
            i for i in interfaces
            if i.get("status") == "connected"
            and i.get("port") not in EXCLUDE_PORTS
        ]

        if not connected:
            logger.warning("No eligible connected ports found after exclusions")
            return

        random.shuffle(connected)
        targets = connected[:MAX_PORTS]

        plan: List[Tuple[str, str, Optional[int], Optional[List[int]], Optional[int]]] = []

        exclude_vlans = {MGMT_VLAN}

        for iface in targets:
            port = iface["port"]
            if random.random() < TRUNK_PROB:
                allowed_count = random.randint(TRUNK_ALLOWED_MIN, TRUNK_ALLOWED_MAX)
                allowed = _rand_vlan_list(allowed_count, exclude_vlans)
                native = _rand_vlan(exclude_vlans | set(allowed)) if SET_NATIVE_VLAN_ON_TRUNK else None
                plan.append((port, "trunk", None, allowed, native))
            else:
                access_vlan = _rand_vlan(exclude_vlans)
                plan.append((port, "access", access_vlan, None, None))

        logger.info("Planned changes:")
        for port, mode, access_vlan, allowed, native in plan:
            if mode == "access":
                logger.info("* %s -> ACCESS vlan %s", port, access_vlan)
            else:
                allowed_s = _vlan_list_to_ios(allowed or [])
                if native is not None:
                    logger.info("* %s -> TRUNK allowed [%s] native %s", port, allowed_s, native)
                else:
                    logger.info("* %s -> TRUNK allowed [%s]", port, allowed_s)

        if not APPLY_CHANGES:
            logger.warning("Currently in test mode.  Changes will not be applied.  Update APPLY_CHANGES=True in uproot/labs/lab1/pkg/vlan.py to commit changes")
            return

        vlans_to_create: Set[int] = set()
        for _, mode, access_vlan, allowed, native in plan:
            if mode == "access" and access_vlan:
                vlans_to_create.add(access_vlan)
            if mode == "trunk" and allowed:
                vlans_to_create.update(allowed)
            if mode == "trunk" and native:
                vlans_to_create.add(native)

        _maybe_create_vlans(conn, sorted(vlans_to_create))

        for port, mode, access_vlan, allowed, native in plan:
            cmds = [f"interface {port}"]

            if mode == "access":
                cmds += [
                    "switchport",
                    "switchport mode access",
                    f"switchport access vlan {access_vlan}",
                    "no shutdown",
                ]
            else:
                allowed_s = _vlan_list_to_ios(allowed or [])
                cmds += [
                    "switchport",
                    "switchport mode trunk",
                    f"switchport trunk allowed vlan {allowed_s}",
                    "no shutdown",
                ]
                if native is not None:
                    cmds.append(f"switchport trunk native vlan {native}")

            conn.send_config_set(cmds)

        if WRITE_MEM:
            conn.save_config()

        logger.info("Switch successfully broken")

    finally:
        conn.disconnect()
