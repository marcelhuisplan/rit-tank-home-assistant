#!/usr/bin/env python3
"""One-time 34.05 source archive verifier. No network or Home Assistant access."""
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from zipfile import ZipFile

NAME = "rit-tank-34.05-local-addon.zip"
NEEDED = ("rit_tank/app.py", "rit_tank/config.yaml", "rit_tank/Dockerfile",
          "rit_tank/oil_history.py", "rit_tank/oil_history.js",
          "rit_tank/README.md", "rit_tank/CHANGELOG.md")


def check_archive(content):
    with ZipFile(io.BytesIO(content), "r") as z:
        entries = z.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or not set(NEEDED).issubset(names):
            raise ValueError("Incomplete archive or duplicate entries")
        for entry in entries:
            p = PurePosixPath(entry.filename)
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "rit_tank":
                raise ValueError("Unexpected archive path: " + entry.filename)
            if stat.S_ISLNK((entry.external_attr >> 16) & 0xFFFF):
                raise ValueError("Archive contains a symlink: " + entry.filename)
            if any(part in ("data", "receipts", "receipt_archive", "backups",
                            "administration_backups", ".git") for part in p.parts):
                raise ValueError("Private data directory: " + entry.filename)
            if p.name.lower().endswith((".db", ".db-wal", ".db-shm", ".sqlite",
                                         ".sqlite3", ".rtbackup", ".pem", ".key",
                                         ".env", ".secret")) or p.name == "options.json":
                raise ValueError("Private data filename: " + entry.filename)
        app = z.read("rit_tank/app.py").decode("utf-8")
        config = z.read("rit_tank/config.yaml").decode("utf-8")
        if not re.search(r"(?m)^APP_VERSION = ['\"]34\.05['\"]$", app):
            raise ValueError("Incorrect runtime version")
        if not re.search(r"(?m)^version: ['\"]34\.05['\"]$", config):
            raise ValueError("Incorrect Home Assistant version")
        if "oil_history.init_schema(con)" not in app or "oil-history.js?v={APP_VERSION}" not in app:
            raise ValueError("Oil maintenance backend or PWA cache missing")
        if not all(key in app for key in ("fuel_event_id", "fuel_address", "fuelAddress",
                                         "fuel-stop", "⛽")):
            raise ValueError("Fuel-trip-stop functionality is incomplete")
        if not all(key in app for key in ("oilPageTitle", "oilAddButton", "Oliebeurt toevoegen")):
            raise ValueError("Dedicated oil tab was not packaged")
    return len(names), hashlib.sha256(content).hexdigest()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--verify-only", "--package"):
        raise SystemExit("Usage: release_3404.py --verify-only|--package")
    data = subprocess.run(["git", "archive", "--format=zip", "HEAD", "rit_tank"],
                          check=True, stdout=subprocess.PIPE).stdout
    count, sha256 = check_archive(data)
    if sys.argv[1] == "--package":
        Path(NAME).write_bytes(data)
    print(f"SAFE_34_05_ARCHIVE: {count} tracked entries, sha256={sha256}, mode={sys.argv[1]}")
    print("No private data paths; no deployment or release created")


if __name__ == "__main__":
    main()
