import os
import subprocess
import socket
import time
import logging

from pathlib import Path
from typing import Optional

from netmiko import ConnectHandler


logger = logging.getLogger(__name__)

BASELINE_DIR = Path("/home/user/uproot/opt/pfsense")
BRANCH_BASELINE = BASELINE_DIR / "lab1-branch_fw_default_config.xml"
APP_BASELINE = BASELINE_DIR / "lab1-app_fw_default_config.xml"


def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    if v == "" or v.lower() in {"none", "null"}:
        return None
    return v


def _run(cmd: list[str], check: bool = True) -> None:
    subprocess.run(cmd, check=check)

def _tcp_open(host: str, port: int, timeout_s: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _wait_tcp_down(host: str, port: int, timeout_s: int = 60, interval_s: float = 2.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if not _tcp_open(host, port):
            return True
        time.sleep(interval_s)
    return False


def _wait_tcp_up(host: str, port: int, timeout_s: int = 300, interval_s: float = 3.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if _tcp_open(host, port):
            return True
        time.sleep(interval_s)
    return False


def _ssh_pfsense_boottime(host: str, user: str, password: str) -> str:
    p = subprocess.run(
        [
            "sshpass", "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{user}@{host}",
            "sysctl -n kern.boottime",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return (p.stdout or "").strip()


def _scp_to_pfsense(host: str, user: str, password: str, local_path: Path, remote_path: str) -> None:
    subprocess.run(
        [
            "sshpass", "-p", password,
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            str(local_path),
            f"{user}@{host}:{remote_path}",
        ],
        check=True,
    )


def _ssh_reboot_pfsense(host: str, user: str, password: str) -> None:
    subprocess.run(
        [
            "sshpass", "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{user}@{host}",
            "reboot",
        ],
        check=False,  # expected to drop connection
    )

def _restore_pfsense(label: str, host: str, user: str, password: str, baseline: Path) -> None:
    if not baseline.exists():
        raise FileNotFoundError(f"{label}: baseline file missing: {baseline}")
    logger.info("%s: restoring /conf/config.xml from %s -> %s ...", label, baseline, host)
    _scp_to_pfsense(host, user, password, baseline, "/conf/config.xml")
    boot_before = _ssh_pfsense_boottime(host, user, password)
    PRE_REBOOT_SLEEP = 15
    
    logger.info("%s: Changes committed. Waiting  %ss before reboot...", label, PRE_REBOOT_SLEEP)
    time.sleep(PRE_REBOOT_SLEEP)

    logger.info("%s: attempting reboot...", label)
    _ssh_reboot_pfsense(host, user, password)

    DOWN_WAIT = 60
    UP_WAIT = 300

    logger.info("%s: waiting for ssh session to drop (up to %ss)...", label, DOWN_WAIT)
    dropped = _wait_tcp_down(host, 22, timeout_s=DOWN_WAIT)
    if not dropped:
        logger.warning("%s: ssh  never appeared to drop (continuing).", label)

    logger.info("%s: waiting for ssh to return (up to %ss)...", label, UP_WAIT)
    if not _wait_tcp_up(host, 22, timeout_s=UP_WAIT):
        logger.error("%s: ssh did not return. Check node console in EVE, manual reboot may required.", label)
        return

    boot_after = _ssh_pfsense_boottime(host, user, password)
    if boot_before and boot_after and boot_before == boot_after:
        logger.error("%s: SSH returned up but boot time did not change. Node may not have rebooted, manual reboot via EVE-NG may required.", label)
        return
    logger.info("%s: reboot successful!", label)


def rollback_firewalls() -> None:
    b_ip = _env("BRANCH_FW_MGMT_IP")
    b_pw = _env("BRANCH_FW_PASSWORD")
    a_ip = _env("APP_FW_MGMT_IP")
    a_pw = _env("APP_FW_PASSWORD")

    if not b_ip or not b_pw:
        raise RuntimeError("Missing BRANCH_FW_MGMT_IP or BRANCH_FW_PASSWORD")
    if not a_ip or not a_pw:
        raise RuntimeError("Missing APP_FW_MGMT_IP or APP_FW_PASSWORD")

    b_user = "root"
    a_user = "root"

    _restore_pfsense("BRANCH_FW", b_ip, b_user, b_pw, BRANCH_BASELINE)
    _restore_pfsense("APP_FW", a_ip, a_user, a_pw, APP_BASELINE)

def _netmiko_connect_telnet(host: str, username: Optional[str], password: Optional[str], enable: Optional[str]):
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
    if enable:
        device["secret"] = enable

    conn = ConnectHandler(**device)

    prompt = conn.find_prompt().strip()
    if not prompt.endswith("#"):
        if enable:
            conn.enable()
        else:
            out = conn.send_command_timing("enable")
            if "Password" in out and not enable:
                raise RuntimeError(f"{host}: enable password required but SWITCH/ROUTER *_ENABLE not set")

    return conn


def _rollback_cisco(label: str, host: str, username=None, password=None, enable=None) -> None:
    logger.info("%s: restoring config from unix:golden-backup.cfg(stored locally on %s, generated during setup.sh) -> running-config...", label, label)
    conn = _netmiko_connect_telnet(host, username, password, enable)
    try:
        conn.send_command_timing("terminal length 0")

        out = conn.send_command_timing("configure replace unix:golden-backup.cfg force")

        conn.find_prompt()

        out2 = conn.send_command_timing("write memory")

        logger.info("%s: config replaced + write mem", label)
    finally:
        conn.disconnect()


def rollback_cisco_switch() -> None:
    sw_ip = _env("SWITCH1_MGMT_IP")
    if sw_ip:
        _rollback_cisco(
            "SWITCH1",
            sw_ip,
            _env("SWITCH1_USERNAME"),
            _env("SWITCH1_PASSWORD"),
            _env("SWITCH1_ENABLE"),
        )

def rollback_cisco_routers() -> None:
    r1_ip = _env("SP_ROUTER1_SB_IP")
    if r1_ip:
        _wait_for_tcp(r1_ip, 23, "SP-ROUTER1 telnet", timeout_s=240)
        _rollback_cisco(
            "SP-ROUTER1",
            r1_ip,
            _env("SP_ROUTER1_USERNAME"),
            _env("SP_ROUTER1_PASSWORD"),
            _env("SP_ROUTER1_ENABLE"),
        )

    r2_ip = _env("SP_ROUTER2_NB_IP")
    if r2_ip:
        _wait_for_tcp(r2_ip, 23, "SP-ROUTER2 telnet", timeout_s=240)
        _rollback_cisco(
            "SP-ROUTER2",
            r2_ip,
            _env("SP_ROUTER2_USERNAME"),
            _env("SP_ROUTER2_PASSWORD"),
            _env("SP_ROUTER2_ENABLE"),
        )


def _get_ens4_gateway() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default", "dev", "ens4"],
            text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None

    parts = out.split()
    if "via" in parts:
        return parts[parts.index("via") + 1]
    return None


def _wait_for_ens4_gateway(timeout_s: int = 240, interval_s: int = 3) -> str:
    logger.info("Waiting for ens4 default gateway to come back up...")
    deadline = time.time() + timeout_s

    last_gw = None
    while time.time() < deadline:
        gw = _get_ens4_gateway()
        if gw:
            last_gw = gw
            rc = subprocess.call(["ping", "-c", "1", "-W", "1", gw],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if rc == 0:
                logger.info("* ens4 gateway now reachable: %s", gw)
                return gw
        time.sleep(interval_s)

    raise TimeoutError(f"ens4 gateway not reachable within {timeout_s}s (last seen: {last_gw})")

def _wait_for_tcp(host: str, port: int, label: str, timeout_s: int = 180, interval_s: int = 3) -> None:
    logger.info("Waiting for %s (%s:%s)...", label, host, port)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                logger.info("* %s reachable: %s:%s", label, host, port)
                return
        except OSError:
            time.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for {label} {host}:{port}")

def rollback_all() -> None:
    rollback_firewalls()
    rollback_cisco_switch()
    _wait_for_ens4_gateway(timeout_s=300, interval_s=3)
    rollback_cisco_routers()
    logger.info("Rollback Complete!")
