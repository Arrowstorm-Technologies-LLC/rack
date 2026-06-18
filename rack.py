#!/usr/bin/env python3
"""rack.py — personal binary manager for GitHub releases (Python variant)
Supports same commands + flags as the Bash version (including -y / -n).
"""

import hashlib
import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

# ── config ────────────────────────────────────────────────────────────────────
INSTALL_DIR = Path(os.environ.get('RACK_DIR',      '~/.local/bin')).expanduser()
REGISTRY    = Path(os.environ.get('RACK_REGISTRY', '~/.local/share/rack/registry.tsv')).expanduser()
HISTORY     = Path(os.environ.get('RACK_HISTORY',  '~/.local/share/rack/history.tsv')).expanduser()

GH_HEADERS   = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
ARCHIVE_EXTS = ('.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.zip')
NAME_RE      = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')

# ── colors ────────────────────────────────────────────────────────────────────
if sys.stdout.isatty():
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    GREEN  = '\033[1;32m'
    YELLOW = '\033[1;33m'
    CYAN   = '\033[1;36m'
    RED    = '\033[1;31m'
    RESET  = '\033[0m'
else:
    BOLD = DIM = GREEN = YELLOW = CYAN = RED = RESET = ''

# ── output helpers ────────────────────────────────────────────────────────────
def step(msg): print(f'\n{CYAN}::{RESET} {BOLD}{msg}{RESET}')
def info(msg): print(f'   {DIM}{msg}{RESET}')
def ok(msg):   print(f'   {GREEN}✔{RESET} {msg}')
def warn(msg): print(f'   {YELLOW}⚠{RESET}  {msg}')

def die(msg):
    print(f'\n{RED}✖ error:{RESET} {msg}', file=sys.stderr)
    sys.exit(1)

# ── spinner ───────────────────────────────────────────────────────────────────
class Spinner:
    _CHARS = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

    def __init__(self, msg):
        self._msg  = msg
        self._stop = threading.Event()
        self._t    = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        for ch in itertools.cycle(self._CHARS):
            if self._stop.is_set():
                break
            print(f'\r   {CYAN}{ch}{RESET} {self._msg}...', end='', flush=True, file=sys.stderr)
            time.sleep(0.08)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()
        print('\r\033[K', end='', file=sys.stderr)

# ── registry ──────────────────────────────────────────────────────────────────
def _reg_rows():
    if not REGISTRY.exists():
        return []
    return [
        line.split('\t', 3)
        for line in REGISTRY.read_text().splitlines()
        if line
    ]

def registry_ensure():
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.touch(exist_ok=True)

def registry_lookup(name):
    return next((r for r in _reg_rows() if r[0] == name), None)

def registry_add(name, url, path):
    registry_ensure()
    rows = [r for r in _reg_rows() if r[0] != name]
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows.append([name, url, str(path), ts])
    REGISTRY.write_text('\n'.join('\t'.join(r) for r in rows) + '\n')

def registry_remove(name):
    registry_ensure()
    rows = [r for r in _reg_rows() if r[0] != name]
    REGISTRY.write_text(('\n'.join('\t'.join(r) for r in rows) + '\n') if rows else '')

# ── history ───────────────────────────────────────────────────────────────────
def history_append(name, url):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.touch(exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with HISTORY.open('a') as f:
        f.write(f'{name}\t{url}\t{ts}\n')

def history_for(name):
    if not HISTORY.exists():
        return []
    return [
        line.split('\t', 2)
        for line in HISTORY.read_text().splitlines()
        if line and line.startswith(name + '\t')
    ]

# ── GitHub API ────────────────────────────────────────────────────────────────
def gh_get(path):
    req = Request(f'https://api.github.com{path}', headers=GH_HEADERS)
    try:
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except HTTPError:
        return None
    except URLError as e:
        die(f'Network error: {e.reason}')

def gh_latest_release(slug):
    data = gh_get(f'/repos/{slug}/releases/latest')
    return data if (data and 'tag_name' in data) else None

def gh_search(query):
    data = gh_get(f'/search/repositories?q={quote_plus(query)}&per_page=10&sort=stars&order=desc')
    return data.get('items', []) if data else []

def slug_from_url(url):
    m = re.search(r'github\.com/([^/]+/[^/]+)/releases/', url)
    return m.group(1) if m else None

def tag_from_url(url):
    m = re.search(r'github\.com/[^/]+/[^/]+/releases/download/([^/]+)/', url)
    return m.group(1) if m else None

# ── interactive picker ────────────────────────────────────────────────────────
def pick_numbered(prompt, items):
    for i, item in enumerate(items, 1):
        print(f'   {CYAN}{i:3d}){RESET}  {item}')
    print()
    while True:
        print(f'   {BOLD}{prompt}{RESET} ', end='', flush=True)
        try:
            choice = sys.stdin.readline().strip()
            n = int(choice)
            if 1 <= n <= len(items):
                return items[n - 1]
        except (ValueError, EOFError):
            pass
        warn(f'Enter a number between 1 and {len(items)}.')

# ── slug resolution ───────────────────────────────────────────────────────────
def is_archive(url):
    return any(url.endswith(ext) for ext in ARCHIVE_EXTS)

def select_asset(release, slug, archive_mode):
    """Present asset picker for a release. Returns (url, archive_mode)."""
    assets = release.get('assets', [])
    if not assets:
        die(f"Release '{release['tag_name']}' for '{slug}' has no downloadable assets.")

    if len(assets) == 1:
        url = assets[0]['browser_download_url']
        ok(f"Single asset: {assets[0]['name']}")
    else:
        step('Select asset')
        info(f"Release {release['tag_name']} · {len(assets)} assets for '{slug}'")
        print()
        if not sys.stdin.isatty():
            die('Asset selection requires an interactive terminal.')
        chosen = pick_numbered('Asset number:', [a['name'] for a in assets])
        url    = next(a['browser_download_url'] for a in assets if a['name'] == chosen)
        ok(f'Selected: {chosen}')

    return url, archive_mode or is_archive(url)

def resolve_release(slug, allow_search, archive_mode):
    """Fetch latest release for owner/repo. Falls back to search on failure."""
    with Spinner(f"Fetching latest release for '{slug}'"):
        release = gh_latest_release(slug)

    if not release:
        if allow_search:
            warn(f"No release found for '{slug}' — searching GitHub...")
            print()
            return search_and_pick(slug.split('/')[-1], archive_mode)
        die(f"Could not fetch a release for '{slug}'. Verify the repo exists and has published releases.")

    ok(f"Found release: {release['tag_name']}")
    return select_asset(release, slug, archive_mode)

def search_and_pick(query, archive_mode):
    """Search GitHub, let user pick a repo, then resolve its latest release."""
    step('Searching GitHub')
    info(f'Query: {query}')

    with Spinner('Querying GitHub'):
        results = gh_search(query)

    if not results:
        die(f"No GitHub repositories found matching '{query}'.")

    seen, repos = set(), []
    for r in results:
        if r['full_name'] not in seen:
            repos.append(r['full_name'])
            seen.add(r['full_name'])

    ok(f'Found {len(repos)} matching repository(s)')
    print()

    if len(repos) == 1:
        selected = repos[0]
        ok(f'Only match: {selected}')
    else:
        if not sys.stdin.isatty():
            die('Repository selection requires an interactive terminal.')
        selected = pick_numbered('Repo number:', repos)
        ok(f'Selected: {selected}')

    print()
    return resolve_release(selected, False, archive_mode)

def resolve_slug(input_str, archive_mode):
    """Entry point: resolve a URL, owner/repo slug, or bare name to (url, archive_mode)."""
    if re.match(r'^https?://', input_str):
        return input_str, archive_mode or is_archive(input_str)

    step('Resolving GitHub reference')
    info(f'Input: {input_str}')

    if re.match(r'^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$', input_str):
        return resolve_release(input_str, True, archive_mode)
    return search_and_pick(input_str, archive_mode)

# ── download ──────────────────────────────────────────────────────────────────
def download(url, dest):
    step('Downloading')
    info(f'Source: {url}')
    print()
    req = Request(url, headers={'User-Agent': 'rack'})
    try:
        with urlopen(req, timeout=120) as resp, open(dest, 'wb') as f:
            total      = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            while chunk := resp.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct    = downloaded / total * 100
                    filled = int(40 * downloaded / total)
                    bar    = '█' * filled + '░' * (40 - filled)
                    mb, total_mb = downloaded / 1048576, total / 1048576
                    print(f'\r   [{bar}] {pct:5.1f}%  {mb:.1f}M / {total_mb:.1f}M',
                          end='', flush=True)
            if total:
                print()
    except HTTPError as e:
        die(f'Download failed: HTTP {e.code} {e.reason}')
    except URLError as e:
        die(f'Download failed: {e.reason}')

    size     = os.path.getsize(dest)
    size_str = f'{size/1048576:.1f}M' if size >= 1048576 else f'{size/1024:.0f}K'
    ok(f'Download complete ({size_str})')

    # Log SHA256 of downloaded payload for verification / audit
    sha = hashlib.sha256(Path(dest).read_bytes()).hexdigest()
    info(f'SHA256: {sha}')

# ── archive extraction ────────────────────────────────────────────────────────
def extract_archive(archive_path, extract_dir):
    step('Extracting archive')
    info(f'Archive: {archive_path.name}')
    name = archive_path.name

    with Spinner('Extracting'):
        if name.endswith(('.tar.gz', '.tgz')):
            with tarfile.open(archive_path, 'r:gz') as tf:
                try:
                    tf.extractall(extract_dir, filter='data')
                except TypeError:
                    tf.extractall(extract_dir)
            fmt = 'tar.gz'
        elif name.endswith('.tar.bz2'):
            with tarfile.open(archive_path, 'r:bz2') as tf:
                try:
                    tf.extractall(extract_dir, filter='data')
                except TypeError:
                    tf.extractall(extract_dir)
            fmt = 'tar.bz2'
        elif name.endswith('.tar.xz'):
            with tarfile.open(archive_path, 'r:xz') as tf:
                try:
                    tf.extractall(extract_dir, filter='data')
                except TypeError:
                    tf.extractall(extract_dir)
            fmt = 'tar.xz'
        elif name.endswith('.zip'):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)
            fmt = 'zip'
        else:
            die(f"Unrecognised archive format: '{name}'. Supported: .tar.gz .tgz .tar.bz2 .tar.xz .zip")

    ok(f'Extracted ({fmt})')

    info('Archive contents (top level):')
    for p in sorted(extract_dir.rglob('*')):
        rel   = p.relative_to(extract_dir)
        depth = len(rel.parts)
        if depth <= 2:
            print(f'      {DIM}{rel}{RESET}')

    return fmt

def find_binary(extract_dir, name):
    for p in extract_dir.rglob(name):
        if p.is_file():
            return p
    for p in sorted(extract_dir.rglob('*')):
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None

# ── install core ──────────────────────────────────────────────────────────────
def do_install(name, url, archive_mode, dry_run=False):
    archive_name = url.split('/')[-1].split('?')[0]

    if dry_run:
        step('Dry run — no downloads or filesystem changes will be performed')
        info(f'Would fetch: {url}')
        info(f'Would install: {INSTALL_DIR / name}')
        ok(f"Mode: {'archive extraction' if archive_mode else 'direct binary'}")
        ok('Dry run complete (no changes made)')
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir  = Path(tmp)
        tmpfile = tmpdir / archive_name

        download(url, tmpfile)

        if archive_mode:
            extract_dir = tmpdir / 'extracted'
            extract_dir.mkdir()
            extract_archive(tmpfile, extract_dir)

            step('Locating binary')
            with Spinner(f"Searching for '{name}'"):
                binary = find_binary(extract_dir, name)

            if binary and binary.name == name:
                ok(f'Found exact match: {binary.relative_to(extract_dir)}')
            elif binary:
                warn(f"No file named '{name}' found — using: {binary.name} → installed as '{name}'")
            else:
                die('No executable binary found in archive. You may need to inspect it manually.')

            step('Installing binary')
            with Spinner('Copying'):
                shutil.copy2(binary, INSTALL_DIR / name)
        else:
            step('Installing binary')
            with Spinner('Copying'):
                shutil.copy2(tmpfile, INSTALL_DIR / name)

    step('Setting permissions')
    with Spinner('chmod +x'):
        dest = INSTALL_DIR / name
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ok('Marked as executable')

    step('Updating registry')
    with Spinner('Recording install'):
        entry = registry_lookup(name)
        if not entry or entry[1] != url:
            history_append(name, url)
        registry_add(name, url, INSTALL_DIR / name)
    ok(f'Registered: {REGISTRY}')

# ── commands ──────────────────────────────────────────────────────────────────
def validate_name(name):
    if not name or name in ('.', '..') or '/' in name or not NAME_RE.match(name):
        die(f"Invalid name '{name}'. Use only letters, numbers, dots, dashes, underscores; must start with alphanum; reject '.' and '..'.")
    # also reject leading dash explicitly (regex helps but be sure)
    if name.startswith('-'):
        die(f"Invalid name '{name}'. Names may not start with '-' .")

def package_for_path(path):
    checks = (
        ('pacman', ['-Qo', path], lambda out: out.split(' owned by ', 1)[-1].split()[0] if ' owned by ' in out else ''),
        ('dpkg',   ['-S', path],   lambda out: out.split(':', 1)[0] if out else ''),
        ('rpm',    ['-qf', path],  lambda out: out.strip()),
        ('apk',    ['info', '-W', path], lambda out: out.splitlines()[0].strip() if out else ''),
    )
    for cmd, args, parse in checks:
        if not shutil.which(cmd):
            continue
        try:
            r = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            pkg = parse(r.stdout.strip())
            if pkg:
                return pkg
    return ''

def detect_name_conflict(name):
    existing = shutil.which(name)
    if not existing:
        return None, ''
    target = str(INSTALL_DIR / name)
    if existing == target:
        return None, ''
    install_prefix = str(INSTALL_DIR) + os.sep
    if existing.startswith(install_prefix):
        return None, ''
    return existing, package_for_path(existing)

def resolve_name_conflict(name, yes_mode=False, dry_run=False):
    """Return the install name after handling conflicts with system commands."""
    conflict_path, conflict_pkg = detect_name_conflict(name)
    if not conflict_path:
        return name

    if dry_run:
        warn(f"Name '{name}' conflicts with existing command: {conflict_path}")
        if conflict_pkg:
            info(f'Package: {conflict_pkg}')
        info('Dry run — conflict resolution skipped')
        return name

    if yes_mode:
        warn(f"Name '{name}' conflicts with {conflict_path} — auto-shadowing (-y)")
        if conflict_pkg:
            info(f'Package: {conflict_pkg}')
        return name

    if not sys.stdin.isatty():
        die(f"Name '{name}' conflicts with existing command '{conflict_path}'. "
            f'Run interactively to shadow it or choose a different name.')

    step('Name conflict')
    warn(f"'{name}' already exists on your system: {conflict_path}")
    if conflict_pkg:
        info(f'Provided by package: {conflict_pkg}')
    print()
    print(f"   {CYAN}1){RESET}  Shadow — install as '{name}' to {INSTALL_DIR}")
    print(f"       {DIM}(takes precedence when {INSTALL_DIR} appears before system paths in PATH){RESET}")
    print(f"   {CYAN}2){RESET}  Pick a different name")
    print()

    while True:
        choice = input(f'   {BOLD}Choice [1/2]:{RESET} ').strip()
        if choice == '1':
            ok(f"Will shadow existing command as '{name}'")
            path_dirs = os.environ.get('PATH', '').split(':')
            if str(INSTALL_DIR) not in path_dirs:
                warn(f"Shadowing requires '{INSTALL_DIR}' to be in your PATH.")
            return name
        if choice == '2':
            while True:
                new_name = input(f'   {BOLD}New name:{RESET} ').strip()
                validate_name(new_name)
                new_conflict, _ = detect_name_conflict(new_name)
                if new_conflict:
                    warn(f"'{new_name}' also conflicts with {new_conflict} — try another.")
                elif (INSTALL_DIR / new_name).exists():
                    warn(f"'{new_name}' already exists in {INSTALL_DIR} — try another.")
                else:
                    ok(f'Using name: {new_name}')
                    return new_name
        warn('Enter 1 or 2.')

def cmd_install(args, archive_mode, dry_run=False, yes_mode=False):
    if len(args) < 2:
        usage(); sys.exit(1)
    name, url_or_slug = args[0], args[1]

    print(f'\n{BOLD}rack — installing \'{name}\'{RESET}')
    step('Validating arguments')
    validate_name(name)
    ok(f'Name:  {name}')

    url, archive_mode = resolve_slug(url_or_slug, archive_mode)
    ok(f'URL:   {url}')
    ok(f"Mode:  {'archive extraction' if archive_mode else 'direct binary install'}")

    step('Checking install directory')
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    ok(f'Install dir: {INSTALL_DIR}')

    path_dirs = os.environ.get('PATH', '').split(':')
    if str(INSTALL_DIR) not in path_dirs:
        warn(f"'{INSTALL_DIR}' is not in your PATH.")
        warn('Add this to your ~/.bashrc or ~/.zshrc:')
        print(f'      {DIM}export PATH="$HOME/.local/bin:$PATH"{RESET}')
    else:
        ok('Install dir is in PATH')

    name = resolve_name_conflict(name, yes_mode=yes_mode, dry_run=dry_run)
    ok(f'Install name: {name}')

    if (INSTALL_DIR / name).exists():
        entry = registry_lookup(name)
        if entry:
            warn(f"'{name}' was previously installed by rack — it will be overwritten.")
            info(f'Original source: {entry[1]}')
        else:
            warn(f"'{name}' already exists at {INSTALL_DIR / name} — it will be overwritten.")

    do_install(name, url, archive_mode, dry_run)

    if not dry_run:
        print(f'\n{GREEN}{BOLD}✔ Done!{RESET}')
        print(f'   Installed {BOLD}{name}{RESET} → {INSTALL_DIR / name}')
        print(f'   Remove later with: {BOLD}rack.py -R {name}{RESET}')
        print(f'   Run it with: {BOLD}{name}{RESET}\n')
    else:
        print(f'\n{YELLOW}{BOLD}Dry run complete — no changes made.{RESET}\n')

def cmd_remove(args, dry_run=False):
    if not args:
        die('Remove mode requires a name. Usage: rack.py -R <name>')
    name = args[0]

    print(f'\n{BOLD}rack — removing \'{name}\'{RESET}')
    step('Validating arguments')
    validate_name(name)
    ok(f'Name: {name}')
    ok('Mode: remove (-R)')

    step('Checking registry')
    info(f'Registry: {REGISTRY}')
    with Spinner(f"Looking up '{name}'"):
        entry = registry_lookup(name)

    if entry:
        install_path = Path(entry[2])
        source_url   = entry[1]
        installed_at = entry[3]
        ok('Registry entry found')
        info(f'Install path: {install_path}')
        info(f'Source URL:   {source_url}')
        info(f'Installed at: {installed_at}')
    else:
        warn(f"No registry entry for '{name}'")
        install_path = INSTALL_DIR / name
        source_url   = '(unknown — not in registry)'
        warn(f'Will attempt to remove from default install dir anyway: {install_path}')

    step('Verifying target on disk')
    info(f'Target: {install_path}')

    if not install_path.exists():
        warn(f"File not found at '{install_path}' — nothing to delete on disk.")
        if entry:
            step('Cleaning up stale registry entry')
            with Spinner(f"Removing entry for '{name}'"):
                registry_remove(name)
            ok('Stale registry entry removed')
        print(f'\n{YELLOW}{BOLD}⚠ Nothing removed from disk{RESET} (file was already gone)\n')
        return

    size     = install_path.stat().st_size
    size_str = f'{size/1048576:.1f}M' if size >= 1048576 else f'{size/1024:.0f}K'
    ok(f'File exists ({size_str})')

    step('Confirming removal')
    info(f'Binary:  {install_path}')
    info(f'Source:  {source_url}')

    if sys.stdin.isatty():
        print(f'\n   {YELLOW}This will permanently delete {BOLD}{name}{RESET}{YELLOW} from {install_path}.{RESET}')
        ans = input(f'   {YELLOW}Continue? [y/N]{RESET} ').strip()
        if ans.lower() != 'y':
            print(f'\n   {DIM}Aborted. Nothing was changed.{RESET}\n')
            return
        ok('Confirmed')
    else:
        info('Non-interactive mode — skipping confirmation prompt')

    if dry_run:
        step('Dry run')
        info(f'Would delete: {install_path}')
        ok('Dry run — no changes made')
        print(f'\n{YELLOW}{BOLD}Dry run complete — nothing removed.{RESET}\n')
        return

    step('Removing binary')
    info(f'Deleting: {install_path}')
    with Spinner('Removing file'):
        install_path.unlink()
    ok(f'Deleted: {install_path}')

    step('Updating registry')
    with Spinner(f"Removing entry for '{name}'"):
        registry_remove(name)
    ok('Registry entry removed')
    info(f'{len(_reg_rows())} install(s) remaining in registry')

    print(f'\n{GREEN}{BOLD}✔ Done!{RESET}')
    print(f'   {BOLD}{name}{RESET} has been removed.')
    print(f'   It was originally installed from: {DIM}{source_url}{RESET}')
    print(f'   Reinstall with: {BOLD}rack.py {name} <url>{RESET}\n')

def cmd_list():
    print(f'\n{BOLD}rack — managed installs{RESET}')
    step('Locating registry')
    registry_ensure()
    info(f'Registry: {REGISTRY}')

    if not REGISTRY.exists():
        warn('Registry does not exist yet — nothing has been installed.')
        print(); return
    ok('Registry found')

    step('Reading entries')
    rows = _reg_rows()
    if not rows:
        warn('Registry is empty — no managed installs recorded.')
        print(); return
    ok(f'Found {len(rows)} managed install(s)')

    step('Verifying installs on disk')
    missing = 0
    for name, url, path, ts in rows:
        p = Path(path)
        if p.exists():
            size     = p.stat().st_size
            size_str = f'{size/1048576:.1f}M' if size >= 1048576 else f'{size/1024:.0f}K'
            ok(f'{name}  {DIM}({size_str}){RESET}')
            info(f'Path:      {path}')
            info(f'Source:    {url}')
            info(f'Installed: {ts}')
        else:
            warn(f'{name}  {RED}(missing from disk){RESET}')
            info(f'Expected:  {path}')
            info(f'Source:    {url}')
            info(f'Installed: {ts}')
            missing += 1

    print()
    if missing:
        print(f'{YELLOW}{BOLD}⚠ {missing} install(s) missing from disk.{RESET}')
        print(f'   Run {BOLD}rack.py -R <name>{RESET} to clean up stale registry entries.')
    else:
        print(f'{GREEN}{BOLD}✔ All installs verified on disk.{RESET}')
    print()

def cmd_history(args):
    if not args:
        die('History mode requires a name. Usage: rack.py -H <name>')
    name = args[0]

    print(f'\n{BOLD}rack — URL history for \'{name}\'{RESET}')
    step('Validating arguments')
    validate_name(name)
    ok(f'Name: {name}')

    step('Reading history')
    info(f'History file: {HISTORY}')

    if not HISTORY.exists():
        warn('No history file found — nothing has been installed yet.')
        print(); return

    entries = history_for(name)
    if not entries:
        warn(f"No history found for '{name}'.")
        print(); return

    ok(f'Found {len(entries)} URL(s) in history')
    cur     = registry_lookup(name)
    cur_url = cur[1] if cur else None

    print()
    print(f'   {"INDEX":<5}  {"INSTALLED":<19}  URL')
    print(f'   {"─────":<5}  {"───────────────────":<19}  {"─"*31}')

    for i, (_, url, ts) in enumerate(entries, 1):
        marker = f'  {DIM}← current{RESET}' if url == cur_url else ''
        print(f'   {CYAN}{i:<5}{RESET}  {DIM}{ts:<19}{RESET}  {url}{marker}')

    print()
    if len(entries) > 1:
        print(f'   Roll back with: {BOLD}rack.py -r {name} <index>{RESET}')
    print()

def cmd_rollback(args, archive_mode, dry_run=False):
    if len(args) < 2:
        die('Rollback mode requires a name and index. Usage: rack.py -r <name> <index>')
    name, index_str = args[0], args[1]

    print(f'\n{BOLD}rack — rolling back \'{name}\'{RESET}')
    step('Validating arguments')
    validate_name(name)
    ok(f'Name: {name}')

    try:
        index = int(index_str)
        assert index > 0
    except (ValueError, AssertionError):
        die(f'Index must be a positive integer. Usage: rack.py -r {name} <index>')
    ok(f'Index: {index}')

    step('Reading history')
    if not HISTORY.exists():
        die(f"No history file found. Has '{name}' been installed before?")

    entries = history_for(name)
    if not entries:
        die(f"No history found for '{name}'.")
    ok(f'Found {len(entries)} URL(s) in history')

    if index > len(entries):
        die(f'Index {index} out of range — history has {len(entries)} entry(s). Run: rack.py -H {name}')

    _, target_url, target_ts = entries[index - 1]
    ok(f'Target URL (index {index}): {target_url}')
    info(f'Recorded: {target_ts}')

    cur = registry_lookup(name)
    if cur:
        if target_url == cur[1]:
            warn('This is already the active URL — will re-download and reinstall.')
        else:
            warn(f'Currently active: {cur[1]}')
    else:
        warn(f"'{name}' not currently in registry — will install fresh.")

    archive_mode = archive_mode or is_archive(target_url)
    ok(f"Mode: {'archive extraction' if archive_mode else 'direct binary'}")

    step('Checking install directory')
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    ok(f'Install dir: {INSTALL_DIR}')

    do_install(name, target_url, archive_mode, dry_run)

    if not dry_run:
        print(f'\n{GREEN}{BOLD}✔ Done!{RESET}')
        print(f'   {BOLD}{name}{RESET} rolled back to URL #{index} → {INSTALL_DIR / name}')
        print(f'   Run it with: {BOLD}{name}{RESET}\n')
    else:
        print(f'\n{YELLOW}{BOLD}Dry run complete — no changes made.{RESET}\n')

def cmd_update_single(args, archive_mode, dry_run=False):
    if not args:
        die('Update mode requires a name. Usage: rack.py -u <name> [new-url]')
    name = args[0]
    alt  = args[1] if len(args) > 1 else None

    print(f'\n{BOLD}rack — updating \'{name}\'{RESET}')
    step('Validating arguments')
    validate_name(name)
    ok(f'Name: {name}')

    step('Checking registry')
    info(f'Registry: {REGISTRY}')
    with Spinner(f"Looking up '{name}'"):
        entry = registry_lookup(name)

    if not entry:
        die(f"'{name}' is not managed by rack. Install it first with: rack.py {name} <url>")

    stored_url   = entry[1]
    installed_at = entry[3]
    ok('Registry entry found')
    info(f'Installed: {installed_at}')
    info(f'Recorded URL: {stored_url}')

    if alt:
        url, archive_mode = resolve_slug(alt, archive_mode)
        warn('Using alternative URL (registry will be updated)')
        info(f'Old: {stored_url}')
        info(f'New: {url}')
    else:
        url          = stored_url
        archive_mode = archive_mode or is_archive(url)
        ok('Using recorded URL')

    ok(f"Mode: {'archive extraction' if archive_mode else 'direct binary'}")

    step('Checking install directory')
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    ok(f'Install dir: {INSTALL_DIR}')

    do_install(name, url, archive_mode, dry_run)

    if not dry_run:
        print(f'\n{GREEN}{BOLD}✔ Done!{RESET}')
        print(f'   {BOLD}{name}{RESET} has been updated → {INSTALL_DIR / name}')
        if alt:
            print('   Registry updated with new source URL.')
        print(f'   Run it with: {BOLD}{name}{RESET}\n')
    else:
        print(f'\n{YELLOW}{BOLD}Dry run complete — no changes made.{RESET}\n')

def cmd_global_update(yes_mode=False, dry_run=False):
    print(f'\n{BOLD}rack update — checking managed installs for updates{RESET}')

    step('Loading registry')
    registry_ensure()
    info(f'Registry: {REGISTRY}')

    rows = _reg_rows()
    if not rows:
        warn('Registry is empty — nothing to update.')
        print(); return
    ok(f'Found {len(rows)} managed install(s)')

    step('Checking for updates')
    info('Querying GitHub releases API for each install...')
    print()

    updates  = []
    uptodate = 0
    skipped  = 0

    for name, url, path, ts in rows:
        slug = slug_from_url(url)
        if not slug:
            print(f'   {DIM}~  {name:<20} non-GitHub URL — skipped{RESET}')
            skipped += 1
            continue

        cur_tag = tag_from_url(url)
        if not cur_tag:
            print(f'   {DIM}~  {name:<20} could not parse version — skipped{RESET}')
            skipped += 1
            continue

        with Spinner(f'Checking {name} ({cur_tag})'):
            release = gh_latest_release(slug)

        if not release:
            print(f'   {YELLOW}⚠{RESET}  {name:<20} {DIM}API error — skipped{RESET}')
            skipped += 1
            continue

        latest_tag = release['tag_name']

        if latest_tag == cur_tag:
            print(f'   {GREEN}✔{RESET}  {name:<20} {DIM}{cur_tag} — up to date{RESET}')
            uptodate += 1
        else:
            print(f'   {YELLOW}↑{RESET}  {name:<20} {DIM}{cur_tag}{RESET} → {CYAN}{latest_tag}{RESET}')
            updates.append({'name': name, 'slug': slug, 'old_tag': cur_tag,
                            'new_tag': latest_tag, 'old_url': url, 'release': release})

    print()

    if not updates:
        print(f'{GREEN}{BOLD}✔ All installs are up to date.{RESET}')
        if skipped:
            print(f'   {DIM}({skipped} skipped — non-GitHub or unresolvable){RESET}')
        print(); return

    print(f'{YELLOW}{BOLD}{len(updates)} update(s) available:{RESET}')
    print()
    print(f'   {"BINARY":<20}  {"CURRENT":<15}  LATEST')
    print(f'   {"─"*20}  {"─"*15}  {"─"*15}')
    for u in updates:
        print(f'   {BOLD}{u["name"]:<20}{RESET}  {DIM}{u["old_tag"]:<15}{RESET}  {CYAN}{u["new_tag"]}{RESET}')
    print()

    if uptodate: print(f'   {DIM}{uptodate} already up to date.{RESET}')
    if skipped:  print(f'   {DIM}{skipped} skipped (non-GitHub or unresolvable).{RESET}')
    print()

    if dry_run:
        print(f'{YELLOW}{BOLD}Dry run — the updates above would be applied (use without -n to proceed).{RESET}\n')
        return

    if yes_mode:
        ok('Auto-confirming (-y)')
        proceed = True
    elif not sys.stdin.isatty():
        print(f'   {DIM}Non-interactive — skipping update prompt.{RESET}\n')
        return
    else:
        ans = input(f'   {BOLD}Apply all updates? [y/N]{RESET} ').strip()
        proceed = ans.lower() == 'y'
        if not proceed:
            print(f'\n   {DIM}Aborted. Nothing was changed.{RESET}\n')
            return

    for u in updates:
        name    = u['name']
        slug    = u['slug']
        old_tag = u['old_tag']
        new_tag = u['new_tag']
        old_url = u['old_url']
        release = u['release']

        print(f'\n{BOLD}── {name}  {DIM}{old_tag} → {CYAN}{new_tag}{RESET}{BOLD} ─{RESET}')

        step('Selecting asset')
        assets      = release.get('assets', [])
        old_fname   = old_url.split('/')[-1]
        new_fname   = old_fname.replace(old_tag, new_tag)
        matched     = next((a for a in assets if a['name'] == new_fname), None)

        if len(assets) == 1:
            url = assets[0]['browser_download_url']
            ok(f"Asset: {assets[0]['name']}")
        elif matched:
            url = matched['browser_download_url']
            ok(f'Auto-matched: {matched["name"]}')
        else:
            step(f"Select asset for '{slug}' {new_tag}")
            info('Asset filename changed — please select manually:')
            print()
            chosen = pick_numbered('Asset number:', [a['name'] for a in assets])
            url    = next(a['browser_download_url'] for a in assets if a['name'] == chosen)
            ok(f'Selected: {chosen}')

        archive_mode = is_archive(url)

        step('Checking install directory')
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        ok(f'Install dir: {INSTALL_DIR}')

        do_install(name, url, archive_mode, dry_run=False)  # apply only if we reached here

        print(f'\n   {GREEN}✔{RESET} {BOLD}{name}{RESET} updated  {DIM}{old_tag} → {CYAN}{new_tag}{RESET}')

    print(f'\n{GREEN}{BOLD}✔ All updates applied.{RESET}\n')

# ── usage ─────────────────────────────────────────────────────────────────────
def usage():
    print(f"""{BOLD}rack.py{RESET} — install and remove GitHub binaries by name

{BOLD}Usage:{RESET}
  rack.py {CYAN}[-a] <name> <url|slug>{RESET}  Download and install a binary
  rack.py {CYAN}-u <name> [url|slug]{RESET}    Update a managed binary (optionally from new URL or slug)
  rack.py {CYAN}-r <name> <index>{RESET}       Roll back to a previously recorded URL
  rack.py {CYAN}-H <name>{RESET}              Show URL history for a managed binary
  rack.py {CYAN}-R <name>{RESET}              Remove a managed install
  rack.py {CYAN}-l{RESET}                     List all managed installs
  rack.py {CYAN}update [-y] [-n]{RESET}       Check for and apply updates for all managed installs

{BOLD}Flags:{RESET}
  {CYAN}-a{RESET}   Extract archive and find binary named <name> inside
  {CYAN}-u{RESET}   Update binary from its recorded source URL (or a new one)
  {CYAN}-r{RESET}   Roll back to a URL from history by index (see rack.py -H <name>)
  {CYAN}-H{RESET}   Show numbered URL history for a binary
  {CYAN}-R{RESET}   Remove the named binary from the install dir
  {CYAN}-l{RESET}   List everything managed by rack
  {CYAN}-y{RESET}   Auto-confirm prompts (for rack.py update)
  {CYAN}-n{RESET}   Dry-run: resolve and show plan but do not download or modify anything

  Supported archives: .tar.gz  .tgz  .tar.bz2  .tar.xz  .zip
  GitHub slugs:  {DIM}owner/repo{RESET}  {DIM}(resolves latest release){RESET}
                 {DIM}repo-name{RESET}   {DIM}(searches GitHub, then resolves){RESET}
  Install dir:   {DIM}{INSTALL_DIR}{RESET}
  Registry:      {DIM}{REGISTRY}{RESET}
  History:       {DIM}{HISTORY}{RESET}
  Override dirs: {DIM}export RACK_DIR=/your/path{RESET}
                 {DIM}export RACK_HISTORY=/your/path{RESET}""")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    if not args or args[0] in ('-h', '--help'):
        usage()
        sys.exit(0 if args else 1)

    if args[0] == 'update':
        yes_mode = any(a in ('-y', '--yes') for a in args[1:])
        dry_run = any(a in ('-n', '--dry-run') for a in args[1:])
        cmd_global_update(yes_mode=yes_mode, dry_run=dry_run)
        return

    archive_mode  = False
    remove_mode   = False
    list_mode     = False
    update_mode   = False
    rollback_mode = False
    history_mode  = False
    yes_mode      = False
    dry_run       = False

    i = 0
    while i < len(args) and args[i].startswith('-'):
        flag = args[i]
        if   flag == '-a': archive_mode  = True
        elif flag == '-R': remove_mode   = True
        elif flag == '-l': list_mode     = True
        elif flag == '-u': update_mode   = True
        elif flag == '-r': rollback_mode = True
        elif flag == '-H': history_mode  = True
        elif flag == '-y': yes_mode      = True
        elif flag == '-n': dry_run       = True
        else:
            usage(); sys.exit(1)
        i += 1

    rest = args[i:]

    if   list_mode:     cmd_list()
    elif history_mode:  cmd_history(rest)
    elif rollback_mode: cmd_rollback(rest, archive_mode, dry_run=dry_run)
    elif update_mode:   cmd_update_single(rest, archive_mode, dry_run=dry_run)
    elif remove_mode:   cmd_remove(rest, dry_run=dry_run)
    else:               cmd_install(rest, archive_mode, dry_run=dry_run, yes_mode=yes_mode)

if __name__ == '__main__':
    main()
