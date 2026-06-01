# rack

The Linux ecosystem is full of useful utilities that never make it into mainstream package managers — tools that live solely as binaries or archives on GitHub, quietly doing their thing for the people who know to look for them. Installing one is never hard, but it's never fast either: find the release page, grab the right asset for your arch, extract it, move it somewhere on your PATH, and remember where you got it from for next time. Repeat that often enough and it starts to feel like exactly the kind of thing a computer should be doing for you.

rack is the result of that frustration. It's a single Bash script that installs any binary from a GitHub release by a name you choose, tracks where it came from, and lets you update or remove it later — without a daemon, a package database, or anything beyond what ships on a base Linux install.

```
rack bat sharkdp/bat          # resolve latest release, pick an asset
rack fd fd-find/fd            # same for fd
rack -u bat                   # update bat from its recorded source
rack update                   # check and apply updates for everything
rack -l                       # list all managed installs
```

## Install

```sh
curl -Lo ~/.local/bin/rack https://raw.githubusercontent.com/W4RM1ND/rack/main/rack
chmod +x ~/.local/bin/rack
```

Make sure `~/.local/bin` is in your PATH:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
```

## Usage

```
rack [-a] <name> <url|slug>   Install a binary
rack -u  <name> [url|slug]    Update a managed binary (optionally from a new URL or slug)
rack -r  <name> <index>       Roll back to a previously recorded URL
rack -H  <name>               Show URL history for a managed binary
rack -R  <name>               Remove a managed install
rack -l                       List all managed installs
rack update                   Check for and apply updates for all managed installs
```

### Installing a binary

Pass a full release URL or a GitHub slug — rack fetches the latest release and lets you pick an asset if there are multiple:

```sh
rack bat sharkdp/bat                          # owner/repo slug
rack rg ripgrep                               # bare repo name, searches GitHub
rack hx https://github.com/helix-editor/helix/releases/download/24.07.1/helix-24.07.1-x86_64-linux.tar.xz
```

Use `-a` if the URL points to an archive and rack can't auto-detect it (it usually can):

```sh
rack -a mytool https://example.com/mytool-linux.tar.gz
```

### Updating

```sh
rack -u bat           # re-download from the recorded URL
rack -u bat cli/cli   # re-download from a new slug instead
rack update           # check all managed installs and apply available updates
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

When given a GitHub slug, rack calls the GitHub releases API to resolve the latest release and presents a numbered asset picker. `rack update` queries the API for every managed install, compares the tag in the stored URL against the latest release tag, and applies updates with automatic asset matching (falls back to the picker if the upstream filename format changed).

## Configuration

| Variable        | Default                               | Purpose                  |
|-----------------|---------------------------------------|--------------------------|
| `RACK_DIR`      | `~/.local/bin`                        | Binary install directory |
| `RACK_REGISTRY` | `~/.local/share/rack/registry.tsv`   | Registry file path       |
| `RACK_HISTORY`  | `~/.local/share/rack/history.tsv`    | History file path        |

## Limitations

- Only works with GitHub release assets. Arbitrary download pages are supported as direct URLs but won't benefit from slug resolution or update checks.
- Update checks require the source URL to contain a versioned release tag (e.g. `v1.2.3`). Binaries installed from tag-less URLs are skipped by `rack update`.
- The GitHub API is queried unauthenticated (60 requests/hour). Running `rack update` with many managed installs may hit this limit.

## Dependencies

`bash`, `wget` or `curl`, `tar`, `unzip`, `find`, `du`, `date`, `mktemp`, `grep`, `cut`, `mv`, `sed` — all present on a base Arch/Linux install.

## License

MIT
