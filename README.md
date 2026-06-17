# rack

The Linux ecosystem is full of useful utilities that never make it into mainstream package managers — tools that live solely as binaries or archives on GitHub, quietly doing their thing for the people who know to look for them. Installing one is never hard, but it's never fast either: find the release page, grab the right asset for your arch, extract it, move it somewhere on your PATH, and remember where you got it from for next time. Repeat that often enough and it starts to feel like exactly the kind of thing a computer should be doing for you.

rack is the result of that frustration. It's a single Bash script that installs any binary from a GitHub release by a name you choose, tracks where it came from, and lets you update or remove it later — without a daemon, a package database, or anything beyond what ships on a base Linux install.

```
rack bat sharkdp/bat                    # resolve latest release, auto-select asset for current platform
rack fd fd-find/fd                      # same for fd
rack nvm https://github.com/nvm-sh/nvm # no binary assets? falls back to source archive + runs installer
rack -u bat                             # update bat from its recorded source
rack update -y                          # check and apply all updates, no prompt
rack update -n                          # dry-run to preview available updates
rack -l                                 # list all managed installs
```

## Install

```sh
curl -Lo ~/.local/bin/rack https://raw.githubusercontent.com/Arrowstorm-Technologies-LLC/rack/main/rack
chmod +x ~/.local/bin/rack
```

Make sure `~/.local/bin` is in your PATH:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
```

## Usage

```
rack [-a] [-n] <name> <url|slug>   Install a binary (use -n for dry-run)
rack -u  <name> [url|slug]         Update a managed binary (optionally from a new URL or slug)
rack -r  <name> <index>            Roll back to a previously recorded URL
rack -H  <name>                    Show URL history for a managed binary
rack -R  <name>                    Remove a managed install
rack -l                            List all managed installs
rack update [-y] [-n]              Check for and apply updates ( -y = no prompt, -n = dry-run)
```

### Installing a binary

Pass a GitHub slug, a full repo URL, or a direct release download URL — rack fetches the latest release and auto-selects an asset for the current platform. If more than one asset matches after filtering, an interactive picker is shown.

```sh
rack bat sharkdp/bat                          # owner/repo slug — auto-selects asset for your arch/OS
rack rg ripgrep                               # bare repo name, searches GitHub
rack hx https://github.com/helix-editor/helix # full repo URL (same as owner/repo slug)
rack hx https://github.com/helix-editor/helix/releases/download/24.07.1/helix-24.07.1-x86_64-linux.tar.xz
```

Use `-A <pattern>` to override the auto-detected architecture filter:

```sh
rack -A arm64 bat sharkdp/bat    # force arm64 asset selection
```

Use `-a` if the URL points to an archive and rack can't auto-detect it (it usually can):

```sh
rack -a mytool https://example.com/mytool-linux.tar.gz
```

Use `-n` / `--dry-run` (or `-n` with `rack update`) to preview actions without downloading or writing anything:

```sh
rack -n bat sharkdp/bat
rack update -n -y
```

Tool names must start with an alphanumeric character and can only contain letters, numbers, dots, dashes, and underscores. The characters `.`, `..`, names starting with `-`, or containing `/` are rejected early with a clear error.

### Source archive fallback and installer scripts

Some tools don't ship pre-built binaries as release assets — they distribute a shell installer that handles the installation itself (nvm, oh-my-zsh, and similar tools). rack handles these automatically.

When a release has no downloadable binary assets, rack falls back to the GitHub-generated source tarball for that release tag and searches it for a recognisable installer script (`install.sh`, `setup.sh`, `bootstrap.sh`, etc.). If found, rack prompts you to run it:

```sh
rack nvm https://github.com/nvm-sh/nvm
# → no release assets found, falls back to source archive
# → finds install.sh, classifies it as installer
# → Run installer? [Y/n]
# → runs bash install.sh from its own directory
```

When the source archive contains a plain script (shebang, but no installer behaviour) or an ELF binary, rack installs it to the install dir as usual.

**Classification heuristics** (in priority order):
1. **Filename** — `install.sh`, `setup.sh`, `bootstrap.sh`, `installer.sh`, `install`, `setup` → installer
2. **Magic bytes** — ELF header (`7f 45 4c 46`) → binary
3. **Content** — shebang present and file modifies shell profiles or installs into `$HOME` → installer; otherwise → script (installed to PATH)

Because installer scripts control their own destination, installs run this way are **not registered** in rack's registry and cannot be updated or rolled back via rack.

### Updating

```sh
rack -u bat           # re-download from the recorded URL
rack -u bat cli/cli   # re-download from a new slug instead
rack update           # check all managed installs and apply available updates
rack update -y        # same, but skip the confirmation prompt (scriptable)
rack update -n        # dry-run: show what would be updated
```

### Rollback

```sh
rack -H bat           # show numbered URL history
rack -r bat 2         # reinstall from history entry #2
```

### Removing

```sh
rack -R bat
```

## How it works

rack keeps a tab-separated registry at `~/.local/share/rack/registry.tsv` and a URL history file at `~/.local/share/rack/history.tsv`. Each install records the binary name, source URL, install path, and timestamp.

When given a GitHub slug or repo URL, rack calls the GitHub releases API to resolve the latest release. Before presenting the asset picker, it filters candidates to those matching the current platform (`uname -m` / `uname -s`), preferring musl over gnu builds on Linux and portable archives (`.tar.gz`, `.zip`, etc.) over package formats (`.deb`, `.rpm`). If filtering leaves exactly one asset, it is selected automatically; otherwise the picker is shown with the filtered list. Use `-A <pattern>` to override arch detection, or pass a direct download URL to bypass resolution entirely.

If the release has no binary assets, rack falls back to the release's source tarball, extracts it, and classifies the candidate file to decide whether to install it to PATH or run it as an installer script.

Downloads are performed with the best available tool (curl or wget for Bash; urllib for Python). After a successful download, the SHA256 of the payload is computed and displayed to aid verification.

`rack update` queries the API for every managed install, compares the tag in the stored URL against the latest release tag, and applies updates with automatic asset matching (falls back to the picker if the upstream filename format changed).

## Configuration

| Variable        | Default                               | Purpose                  |
|-----------------|---------------------------------------|--------------------------|
| `RACK_DIR`      | `~/.local/bin`                        | Binary install directory |
| `RACK_REGISTRY` | `~/.local/share/rack/registry.tsv`   | Registry file path       |
| `RACK_HISTORY`  | `~/.local/share/rack/history.tsv`    | History file path        |

## Limitations

- Arbitrary (non-GitHub) download URLs are supported but won't benefit from slug resolution, source archive fallback, or `rack update` checks.
- Update checks require the source URL to contain a versioned release tag (e.g. `v1.2.3`). Binaries installed from tag-less URLs are skipped by `rack update`.
- Installs performed by external installer scripts are not registered in rack's registry and cannot be updated or rolled back via rack.
- The GitHub API is queried unauthenticated (60 requests/hour). Running `rack update` with many managed installs may hit this limit.

## Comparison to similar tools

rack is a lightweight personal binary registry with tracking, history, and rollback.

| Tool       | Focus                          | Tracking | Updates/Rollback | Source Fallback | Notes |
|------------|--------------------------------|----------|------------------|-----------------|-------|
| rack      | GitHub binaries + management  | Full registry + history | Yes (all + per, rollback) | Yes (tar + installers) | Bash + Py, dry-run, platform filters |
| eget      | Simple fetch/extract          | None     | Basic upgrade    | Limited         | Fast one-off, good for CI |
| bin       | Multi-provider manager        | Config   | Yes (update/remove/pin) | No              | Docker support too |
| aqua      | Declarative version manager   | YAML registry | Yes (lazy, per-project) | Limited      | Team/CI focused, heavier |

rack stays simple for personal use while adding the management layer the others lack. No central registry, no version pinning (unlike aqua), just ad-hoc tracking for your GitHub binaries.

## Shell vs Python

rack ships as two functionally identical scripts that share the same registry and history files — you can use either interchangeably, or switch between them at any point without losing your install history.

**`rack`** (Bash) is the original. It has no runtime requirements beyond what ships on a base Linux system and starts in under 5ms. The tradeoff is that parsing JSON from the GitHub API is done with `grep -o` pattern matching — reliable against GitHub's consistently formatted responses, but not a full parser.

**`rack.py`** (Python) was written to address that tradeoff directly. It uses Python's `json` module for proper API response parsing, `urllib` for HTTP (no `curl` or `wget` needed), and the standard `tarfile`/`zipfile` modules for archive extraction (no `tar` or `unzip` needed). This makes it more resilient to unexpected API responses and removes the dependency on external download tools entirely. The cost is Python 3.8+ as a requirement and a slightly slower startup (~50–150ms of import overhead vs. <5ms for Bash).

In practice, both feel identical to use. They now have full flag parity, including `-y` for non-interactive updates and `-n` for dry-runs. The Python version is the better choice if you want robustness and easier extensibility; the Bash version is the better choice if you value a zero-dependency install or are on a minimal system.

Both versions perform stricter name validation upfront, log the SHA256 of every downloaded payload for verification, and support `rack update -y` (and the equivalent with `rack.py`).

### Installing rack.py

```sh
curl -Lo ~/.local/bin/rack.py https://raw.githubusercontent.com/Arrowstorm-Technologies-LLC/rack/main/rack.py
chmod +x ~/.local/bin/rack.py
```

Usage is identical — just substitute `rack.py` for `rack` in any command.

## Dependencies

**rack** (Bash): `bash`, `wget` or `curl`, `tar`, `unzip`, `find`, `du`, `date`, `mktemp`, `grep`, `cut`, `mv`, `sed` — all present on a base Arch/Linux install.

**rack.py** (Python): `python3` (3.8+) — HTTP, JSON parsing, and archive extraction are handled by the standard library. No external tools required.

## License

MIT
