#!/usr/bin/env python3
"""
admin-watcher.py
Watches argusreach/admin/ for .py and .html file changes.
On change: restarts argusreach-admin systemd service automatically.
Runs as argus user — no sudo required (sudoers rule grants specific restart permission).
"""

import subprocess
import time
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

WATCH_DIR = Path(__file__).parent.parent / "admin"
RESTART_CMD = ["sudo", "systemctl", "restart", "argusreach-admin"]
DEBOUNCE_SECONDS = 3  # wait 3s after last change before restarting (handles rapid saves)

class RestartHandler(FileSystemEventHandler):
    def __init__(self):
        self._last_trigger = 0

    def on_modified(self, event):
        self._handle(event.src_path)

    def on_created(self, event):
        self._handle(event.src_path)

    def _handle(self, path):
        if not any(path.endswith(ext) for ext in (".py", ".html", ".css", ".js")):
            return
        now = time.time()
        if now - self._last_trigger < DEBOUNCE_SECONDS:
            return  # debounce: skip if we just restarted
        self._last_trigger = now
        log.info(f"Change detected: {path} — restarting argusreach-admin...")
        result = subprocess.run(RESTART_CMD, capture_output=True, text=True)
        if result.returncode == 0:
            log.info("✅ argusreach-admin restarted successfully")
        else:
            log.error(f"❌ Restart failed: {result.stderr.strip()}")

if __name__ == "__main__":
    log.info(f"Watching {WATCH_DIR} for changes...")
    handler = RestartHandler()
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
