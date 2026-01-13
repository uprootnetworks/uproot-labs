import requests as r
import json
import logging
import os
import tempfile
import zipfile
import shutil
import tarfile

from pathlib import Path

VERSION_API = "https://api.github.com/repos/uprootnetworks/uproot-labs/releases/latest"

UPROOT_DIR = Path.home() / "uproot"
VERSION_FILE = UPROOT_DIR / "version"

PRESERVE_PATHS = [
        Path.home() / "uproot" / "opt",
        UPROOT_DIR / "labs" / "lab1" / ".env",
        ]

logger = logging.getLogger()

def setup_logging():
    level_name = os.getenv("UPROOT_LOG_LEVEL", "INFO").upper()

    level = logging.INFO
    if isinstance(level_name, str):
        level = getattr(logging, level_name, logging.INFO)

        logging.basicConfig(
                level=level,
                format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
                )

def read_current_version() -> str:
    logger.info("Checking current version...")
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        logger.info("Current version of Uproot Labs: %s", version)
        return version
    except FileNotFoundError:
        logger.info("Unable to find current version")
        return ""


def fetch_latest_release():
    logger.info("Checking GitHub for latest release")
    headers = {
            "Accept": "application/json",
            }
    resp = r.get(VERSION_API, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GithHub API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    version = data.get("tag_name")
    tar_url = data.get("tarball_url")
    return version, tar_url

def update_check(version, latest_version, tarball_url):
    if version == latest_version:
        logger.info("Current version is up to date")
    elif version != latest_version:
        logger.info("Newer release is available: %s", latest_version)
        download_latest(tarball_url, latest_version)


def backup_preserve_paths(tmp: Path) -> Path:
    backup_root = tmp / "preserve_backup"
    backup_root.mkdir(parents=True, exist_ok=True)

    for p in PRESERVE_PATHS:
        if not p.exists():
            logger.info("No files tagged for backup, skipping backup(s)")
            continue

        dest = backup_root / p.name
        logger.info("Backing up %s -> %s", p, dest)

        if p.is_dir():
            shutil.copytree(p, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)

    return backup_root

def restore_backup_paths(backup_root: Path) -> None:
    for p in PRESERVE_PATHS:
        src = backup_root / p.name
        if not src.exists():
            continue

        logger.info("Restoring backup %s -> %s", src, p)
        p.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(src, p, dirs_exist_ok=True)
        else:
            shutil.copy2(src, p)



def download_latest(tarball_url, latest_version):
    logger.info("Downloading update from GitHub (%s)", tarball_url)
    resp=r.get(tarball_url, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed with response code {resp.status_code}: {resp.text[:300]}")

    with tempfile.TemporaryDirectory(prefix="uproot_update_") as td:
        tmp = Path(td)
        tar_path = tmp / "release.tar.gz"
        extract_dir = tmp / "extract"
        
        logger.info("Initiating backups...")
        backup_root = backup_preserve_paths(tmp)

        with open(tar_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 356):
                if chunk:
                    f.write(chunk)


        logger.info("Extracting update.tar.gz to tmp/uproot_update_/")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path) as tf:
            tf.extractall(extract_dir)

        top_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(top_dirs) !=1:
            raise RuntimeError(f"Unexpected zip layout.  Top directories: {top_dirs}")

        src = top_dirs[0]

        logger.info("Installing update into %s ...)", UPROOT_DIR)
        UPROOT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("***Removing old files")
        for item in UPROOT_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        logger.info("Copying new files")
        for item in src.iterdir():
            dest = UPROOT_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        VERSION_FILE.write_text(latest_version + "\n", encoding="utf-8")
        
        restore_backup_paths(backup_root)

        logger.info("Update complete! New version is %s", latest_version)

setup_logging()
current_version = read_current_version()
latest_version, tar_url = fetch_latest_release()
update_check(current_version, latest_version, tar_url)
