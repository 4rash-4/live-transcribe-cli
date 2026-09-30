# macclean — find and clear the "death by a thousand cuts" on a Mac

Stdlib-only Python 3 (uses the system `du` and `git`). Nothing is modified until you run `apply --yes`.

```bash
# 1. Read-only scan (a few minutes; add --verify-remotes to confirm each repo's remote still exists)
python3 macclean.py scan

# 2. Read ~/.mac-cleanup/<timestamp>/report.md, then edit plan.tsv:
#    set the first column to DELETE, QUARANTINE, or KEEP for rows you disagree with

# 3. Dry run, then for real
python3 macclean.py apply ~/.mac-cleanup/<timestamp>/plan.tsv
python3 macclean.py apply ~/.mac-cleanup/<timestamp>/plan.tsv --yes

# 4. After a week with nothing missed
python3 macclean.py purge --yes            # or: restore --batch <id> to undo
```

Grant your terminal **Full Disk Access** first (System Settings → Privacy & Security), or `~/Library` and the Trash can't be measured.

## How it decides

| Bucket | What goes in | What `apply` does |
|---|---|---|
| **DELETE** | Caches, logs, Trash, Xcode DerivedData, and `node_modules` / venvs / `target` / `Pods` / git-ignored build dirs inside projects idle 30+ days *and* rebuildable from a manifest | real delete |
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
