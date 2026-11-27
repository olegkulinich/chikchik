"""T33: update check — reads GitHub `releases/latest`, nothing else.

A newer version is shown in Settings and on the empty screen. The update
button opens the release page, without downloading or installing anything.
The network call runs in a plain thread, imported lazily (no startup cost).

Hidden switch `QTC_UPDATE_URL` (tests only): another JSON source, e.g.
`file:///G:/Dev/fake_release.json` (or a plain path) with {"tag_name": "v1.1.0", "html_url": "https://..."}
or another public repo's `.../releases/latest`. With it the daily limit is off and
the result is kept apart from the real one (see `Config.update_state`).
"""
from __future__ import annotations

import json
import os
import re
import threading

from PySide6.QtCore import QObject, Signal

from .version import VERSION

REPO = "olegkulinich/chikchik"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 10            # s
DAY = 24 * 60 * 60      # auto check: at most once a day
AUTO_DELAY_MS = 10_000  # auto check: this long after the window shows (no startup cost)


def source_url() -> str:
    return os.environ.get("QTC_UPDATE_URL") or API_URL


def is_test_source() -> bool:
    return bool(os.environ.get("QTC_UPDATE_URL"))


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.10' → (1, 2, 10); '1.1.0-beta' → (1, 1, 0); junk → ()."""
    parts = []
    for part in text.strip().lstrip("vV").split("."):
        match = re.match(r"\d+", part)
        if not match:
            break
        parts.append(int(match.group()))
    return tuple(parts)


def is_newer(version: str, current: str = VERSION) -> bool:
    new, cur = parse_version(version), parse_version(current)
    if not new:
        return False
    width = max(len(new), len(cur))
    return new + (0,) * (width - len(new)) > cur + (0,) * (width - len(cur))


def clean_version(tag: str) -> str:
    """Shown to people: 'v1.1.0' → '1.1.0'."""
    return ".".join(map(str, parse_version(tag)))


def local_path(url: str) -> str:
    """Test source on disk: 'file:///G:/Dev/x.json' or a plain 'G:\\Dev\\x.json' → path.
    Read directly — urllib's file: handling differs between Python versions."""
    from urllib.parse import unquote

    if url.lower().startswith("file:"):
        url = unquote(url[5:]).lstrip("/")
        if not re.match(r"[A-Za-z]:", url):     # POSIX: file:///tmp/x.json
            url = "/" + url
    return url


def fetch(url: str) -> tuple[str, str]:
    """→ (version '1.1.0', release page URL). Raises on any problem."""
    if not url.lower().startswith(("https://", "http://")):
        with open(local_path(url), encoding="utf-8-sig") as file:
            data = json.loads(file.read(1_000_000))
    else:
        import urllib.request   # lazy: ssl / http.client only when a check really runs

        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ChikChik/{VERSION}",
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read(1_000_000).decode("utf-8-sig"))
    version = clean_version(str(data.get("tag_name", "")))
    if not version:
        raise ValueError("no version in the answer")
    page = str(data.get("html_url", ""))
    if not page.startswith("https://"):
        page = RELEASES_URL
    return version, page


class UpdateChecker(QObject):
    """One per app. `finished(ok, version, page, manual)` arrives in the GUI thread."""

    finished = Signal(bool, str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.last_error = ""     # shown in the Can't Connect tooltip with a test source
        self.finished.connect(self._done)   # first slot: busy is off before the others run

    def _done(self, *_args) -> None:
        self.busy = False

    def check(self, manual: bool) -> bool:
        if self.busy:
            return False
        self.busy = True
        url = source_url()
        threading.Thread(target=self._run, args=(url, manual), daemon=True,
                         name="update-check").start()
        return True

    def _run(self, url: str, manual: bool) -> None:
        try:
            version, page = fetch(url)
            ok, self.last_error = True, ""
        except Exception as error:   # noqa: BLE001 — offline, firewall, 404, bad JSON: all "can't"
            version, page, ok = "", "", False
            self.last_error = f"{type(error).__name__}: {error}"[:200]
        self.finished.emit(ok, version, page, manual)   # queued into the GUI thread
