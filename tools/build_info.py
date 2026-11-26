"""Writes build_info.py for the Settings line: "ChikChik! 0.9.0 · group5-prores · 07ad572".

Run by build.bat. Reads .git directly (git isn't on PATH with GitHub Desktop); the git
command, if found, only adds the "modified" check for uncommitted changes.
"""
import os
import shutil
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIT = ROOT / ".git"


def ref_value(ref: str) -> str:
    loose = GIT / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = GIT / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return ""


def git_command() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    desktop = Path.home() / "AppData" / "Local" / "GitHubDesktop"
    for candidate in sorted(desktop.glob("app-*/resources/app/git/cmd/git.exe"), reverse=True):
        return str(candidate)
    return None


def build_line() -> str:
    if not (GIT / "HEAD").is_file():
        return ""
    head = (GIT / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        ref = head[5:]
        branch = ref.removeprefix("refs/heads/")
        commit = ref_value(ref)
        pushed = commit and ref_value(f"refs/remotes/origin/{branch}") == commit
    else:
        branch, commit, pushed = "detached", head, True
        if os.environ.get("GITHUB_REF_TYPE") == "tag":
            branch = ""  # Settings already shows the release version.
    modified = not pushed
    git = git_command()
    if git:
        try:
            status = subprocess.run([git, "status", "--porcelain", "--untracked-files=no"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=20)
            if status.returncode == 0 and status.stdout.strip():
                modified = True
        except (OSError, subprocess.SubprocessError):
            pass
    parts = [branch, commit[:7]] + (["modified"] if modified else [])
    return " · ".join(p for p in parts if p)


if __name__ == "__main__":
    line = build_line()
    (ROOT / "build_info.py").write_text(
        "# Written by tools/build_info.py during build.bat — not in git.\n"
        f"BUILD = {line!r}\n", encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")          # cmd code page may lack "·"
    print(f"Build:   {line or '(no git info)'}")
