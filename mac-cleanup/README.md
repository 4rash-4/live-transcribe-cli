# macclean — find and clear the "death by a thousand cuts" on a Mac

Stdlib-only Python 3 (uses the system `du` and `git`). Nothing is modified until you run `apply --yes`.

```bash
# 1. Read-only scan (a few minutes; add --verify-remotes to confirm each repo's remote still exists)
python3 macclean.py scan

# 2. Read ~/.mac-cleanup/<timestamp>/report.md, then edit plan.tsv:
#    set the first column to DELETE, OFFLOAD, QUARANTINE, or KEEP for rows you disagree with

# 3. Dry run, then for real
python3 macclean.py apply ~/.mac-cleanup/<timestamp>/plan.tsv
python3 macclean.py apply ~/.mac-cleanup/<timestamp>/plan.tsv --yes

# 4. After a week with nothing missed
python3 macclean.py purge --yes            # or: restore --batch <id> to undo
```

Grant your terminal **Full Disk Access** first (System Settings → Privacy & Security), or `~/Library` and the Trash can't be measured.

## Freeing 20-30 GB and parking old work in OneDrive / iCloud

```bash
# Protect nobitex_trading (always kept; add --keep NAME for anything it depends on), aim for 25 GB
python3 macclean.py scan --target-gb 25 --cloud onedrive      # or --cloud icloud / auto / /path
# read the report, edit plan.tsv, then:
python3 macclean.py apply <plan.tsv>                          # dry run
python3 macclean.py apply <plan.tsv> --yes                    # add --free-now if the disk is nearly full
# let the cloud finish uploading, check the web UI, then:
python3 macclean.py purge --yes --synced
# Finder > right-click MacArchive > Free Up Space (OneDrive) / Remove Download (iCloud)   (or: macclean.py evict)
python3 macclean.py unarchive <path/to/archive.tar.gz>        # bring a project back
```

- **`--target-gb`** switches to back-seat mode: projects idle 14+ days are listed, and the oldest are promoted to OFFLOAD until the target is met.
- **OFFLOAD** = `tar.gz` into `<cloud>/MacArchive/<timestamp>/` (without `node_modules`/venvs, which are rebuildable), `gzip -t` + listing + sha256 sidecar, then the original is quarantined (or deleted with `--free-now`). Unsaved/uncommitted work is preserved inside the archive.
- **Projects with credentials** (`.env`, private keys, `credentials*`, wallet/keystore files…) are *not* uploaded unless you pass `--include-secrets`. Secrets already committed to git history are not detected.
- Repos that look "pushed" but hold git-ignored local files (`.env`, databases, data) are no longer treated as re-clonable.
- Nothing is freed locally until the cloud copy is evicted. Check that your plan has room: iCloud/OneDrive quota must hold the archives (compressed, up to the OFFLOAD total in the report).
- Space is only freed by `purge`/`--free-now` *and* evicting the local archive. `purge --synced` re-verifies every archive's sha256 first and refuses on a mismatch, but it cannot see the cloud's upload state — you confirm that.

## How it decides

| Bucket | What goes in | What `apply` does |
|---|---|---|
| **DELETE** | Caches, logs, Trash, Xcode DerivedData, and `node_modules` / venvs / `target` / `Pods` / git-ignored build dirs inside projects idle 30+ days *and* rebuildable from a manifest | real delete |
| **OFFLOAD** | (cloud mode) stale projects and folders, archived to OneDrive/iCloud | archive, verify, then remove the local copy |
| **QUARANTINE** | Repos that are clean, fully pushed and idle 180+ days (re-clonable); scratch-looking projects/folders with a high junk score; old Downloads installers; old screenshots | move to `~/.mac-cleanup/quarantine/` (undo with `restore`, free with `purge`) |
| **REVIEW** | Anything with unsaved work, no remote, unclear value, large files, duplicates, Docker/Ollama/iOS backups | ignored unless you change it |

Junk score (projects without a remote, and marker-less folders): idle time, scratch-like name (`test`, `tmp`, `copy`, `untitled`…), very few files, no README, no git history; big commit histories subtract. Every row says why.

## Safety

- The scan writes only to `~/.mac-cleanup/` and runs git with `GIT_OPTIONAL_LOCKS=0`.
- `apply` re-checks git state at run time and skips repos that gained uncommitted, stashed or unpushed work since the scan.
- `apply` refuses paths outside `$HOME`, `~/Documents`/`Desktop`/etc. themselves, `.ssh`, Photos libraries, iCloud/CloudStorage, Keychains, Preferences, Messages, and Mail — even if a plan row says otherwise.
- Cloud-sync folders (Dropbox, Google Drive, OneDrive…) are skipped entirely; deleting there would delete remotely.
- If Desktop & Documents sync to iCloud, deletions propagate to your other devices.
- Quit apps whose caches are listed before applying.
- Not automated on purpose: `docker system prune`, `brew cleanup`, `xcrun simctl delete unavailable`, Time Machine snapshot thinning, conda, ML model caches. The report lists the commands that apply to your machine.
