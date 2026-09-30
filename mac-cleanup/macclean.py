#!/usr/bin/env python3
"""macclean - find, rank and (optionally) remove disk hogs on macOS.

  macclean.py scan      read-only. Writes report.md + plan.tsv to ~/.mac-cleanup/<timestamp>/
  macclean.py apply P   acts on a plan.tsv you have reviewed (dry run unless --yes)
  macclean.py purge     permanently deletes quarantined items (dry run unless --yes)
  macclean.py restore   moves quarantined items back where they came from

Only Python 3.8+ stdlib and the system `du` / `git` are used. See README.md.
"""
import argparse
import csv
import datetime
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections import defaultdict

HOME = os.path.realpath(os.environ.get("MACCLEAN_HOME") or os.path.expanduser("~"))
STATE = os.path.join(HOME, ".mac-cleanup")
QDIR = os.path.join(STATE, "quarantine")
NOW = time.time()
DAY = 86400.0
MB = 1024 * 1024

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
GIT_ENV.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def fmt(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%d B" % n if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def run(cmd, env=None, timeout=60):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, env=env, universal_newlines=True)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def du_bytes(paths):
    """Real disk usage (bytes) per path via `du -sk -x`; unreadable parts count as 0."""
    out = {}
    paths = [p for p in paths if os.path.lexists(p)]
    for i in range(0, len(paths), 40):
        _, txt = run(["du", "-sk", "-x"] + paths[i:i + 40], timeout=3600)
        for line in txt.splitlines():
            kb, _, p = line.partition("\t")
            if kb.isdigit():
                out[p] = int(kb) * 1024
    return out


def idle_days(ts):
    return max(0, int((NOW - ts) / DAY)) if ts else None


# ----------------------------------------------------------------------------
# Known regenerable / reviewable locations under $HOME
# (relpath, expand_children, default_action, why, options)
# ----------------------------------------------------------------------------
TARGETS = [
    (".Trash", True, "DELETE", "already in the Trash", {}),
    ("Library/Caches", True, "DELETE", "app cache, rebuilt on demand",
     {"skip_prefix": "com.apple."}),
    ("Library/Logs", True, "DELETE", "logs and crash reports", {}),
    ("Library/Developer/Xcode/DerivedData", True, "DELETE", "Xcode build output", {}),
    ("Library/Developer/Xcode/iOS DeviceSupport", True, "DELETE",
     "device symbols, re-downloaded when a device is attached", {}),
    ("Library/Developer/Xcode/watchOS DeviceSupport", True, "DELETE", "device symbols", {}),
    ("Library/Developer/Xcode/Archives", True, "REVIEW", "holds dSYMs for shipped builds", {}),
    ("Library/Developer/CoreSimulator/Caches", True, "DELETE", "simulator caches", {}),
    ("Library/Developer/CoreSimulator/Devices", True, "REVIEW",
     "simulator devices; prefer `xcrun simctl delete unavailable`", {}),
    ("Library/Containers/com.apple.mail/Data/Library/Mail Downloads", True, "DELETE",
     "copies of attachments you opened from Mail", {}),
    ("Library/Application Support/MobileSync/Backup", True, "REVIEW",
     "iPhone/iPad backups - delete stale devices only", {}),
    ("Library/Application Support/Code/CachedData", False, "DELETE", "VS Code cache", {}),
    ("Library/Application Support/Code/Cache", False, "DELETE", "VS Code cache", {}),
    ("Library/Application Support/Code/CachedExtensionVSIXs", False, "DELETE", "VS Code cache", {}),
    ("Library/Application Support/Code/logs", False, "DELETE", "VS Code logs", {}),
    ("Library/Application Support/Slack/Cache", False, "DELETE", "Slack cache", {}),
    ("Library/Application Support/Slack/Service Worker/CacheStorage", False, "DELETE",
     "Slack cache", {}),
    ("Library/Application Support/discord/Cache", False, "DELETE", "Discord cache", {}),
    ("Library/Containers/com.docker.docker/Data", False, "REVIEW",
     "Docker VM disk; run `docker system prune -a --volumes` (removes ALL unused images/volumes)", {}),
    ("Library/Messages/Attachments", False, "REVIEW",
     "iMessage attachments - irreplaceable; clean up inside Messages, not here", {}),
    ("Library/pnpm/store", False, "DELETE", "pnpm content store, re-fetched on install", {}),
    (".npm/_cacache", False, "DELETE", "npm cache", {}),
    (".yarn/berry/cache", False, "DELETE", "yarn cache", {}),
    (".cache", True, "DELETE", "generic user cache",
     {"review": {"huggingface", "torch", "whisper", "lm-studio", "pypoetry", "puppeteer",
                 "ms-playwright"}}),
    (".cargo/registry", False, "DELETE", "cargo crate cache", {}),
    (".cargo/git", False, "DELETE", "cargo git checkouts", {}),
    (".gradle/caches", False, "DELETE", "gradle cache", {}),
    (".m2/repository", False, "DELETE", "maven cache", {}),
    (".cocoapods/repos", False, "DELETE", "CocoaPods spec repos", {}),
    (".nuget/packages", False, "DELETE", "NuGet cache", {}),
    (".ollama/models", False, "REVIEW", "local LLM weights - big, re-downloadable", {}),
    (".conda/pkgs", False, "REVIEW", "conda package cache; `conda clean -a`", {}),
    ("miniconda3/pkgs", False, "REVIEW", "conda package cache; `conda clean -a`", {}),
    ("anaconda3/pkgs", False, "REVIEW", "conda package cache; `conda clean -a`", {}),
    (".android/avd", True, "REVIEW", "Android emulator images", {}),
    (".rustup/toolchains", True, "REVIEW", "Rust toolchains - keep the one you use", {}),
    (".pyenv/versions", True, "REVIEW", "Python versions - keep the ones you use", {}),
]

# ----------------------------------------------------------------------------
# Project discovery
# ----------------------------------------------------------------------------
PROJECT_FILES = {
    ".git", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Pipfile",
    "Cargo.toml", "go.mod", "Package.swift", "Gemfile", "pom.xml", "build.gradle",
    "build.gradle.kts", "CMakeLists.txt", "Makefile", "deno.json", "composer.json", "mix.exs",
    "pubspec.yaml", "Podfile", "environment.yml", "tsconfig.json", "Dockerfile",
}
PROJECT_SUFFIXES = (".xcodeproj", ".xcworkspace", ".sln", ".csproj", ".ipynb")
CONTAINERS = {HOME} | {os.path.join(HOME, d) for d in ("Desktop", "Documents", "Downloads")}
SKIP_TOP = {"Library", "Applications", "Movies", "Music", "Pictures", "Public", "Sites"}
CLOUD_PREFIXES = ("Dropbox", "Google Drive", "GoogleDrive", "OneDrive", "Box", "iCloud",
                  "Creative Cloud", "Sync", "pCloud", "Nextcloud", "MEGA")
BUNDLE_EXT = (".app", ".photoslibrary", ".pvm", ".vmwarevm", ".sparsebundle", ".fcpbundle",
              ".imovielibrary", ".tmbackup", ".xcarchive", ".pkg")
MAX_DEPTH = 6

# Directories that are safe to remove *when the project can rebuild them*.
ALWAYS_ARTIFACT = {
    "node_modules": "npm/yarn/pnpm install", "__pycache__": "regenerated on run",
    ".pytest_cache": "regenerated", ".mypy_cache": "regenerated", ".ruff_cache": "regenerated",
    ".tox": "tox recreates", ".next": "next build", ".nuxt": "nuxt build",
    ".parcel-cache": "regenerated", ".turbo": "regenerated", ".svelte-kit": "svelte build",
    ".gradle": "gradle recreates", ".dart_tool": "flutter pub get", ".terraform": "terraform init",
    ".angular": "regenerated",
}
NEEDS_SIBLING = {  # dir name -> (sibling files that prove it is a build dir, rebuild hint)
    "target": (("Cargo.toml", "pom.xml"), "cargo/mvn build"),
    "Pods": (("Podfile",), "pod install"),
    ".build": (("Package.swift",), "swift build"),
}
IGNORED_ONLY = {"build": "rebuild", "dist": "rebuild", "out": "rebuild", ".output": "rebuild",
                "coverage": "rerun tests", "DerivedData": "rebuild"}
VENV_NAMES = {"venv", ".venv", "env"}
PY_MANIFESTS = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "environment.yml",
                "poetry.lock", "uv.lock")
SCRATCH_RE = re.compile(
    r"(^|[-_. ])(test|tests|tmp|temp|untitled|scratch|playground|demo|sample|copy|old|backup|bak|"
    r"foo|bar|baz|experiment|exp|try|draft|hello|learn|learning|tutorial|practice|trial|misc|"
    r"stuff|new|final)([-_. 0-9]|$)|\b(copy|\d)\)?$", re.I)
INSTALLER_EXT = (".dmg", ".pkg", ".iso", ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".xz", ".ipsw")


def is_project(names):
    return any(n in PROJECT_FILES or n.endswith(PROJECT_SUFFIXES) for n in names)


def discover(warnings):
    projects, loose, bundles = [], [], []

    def walk(path, depth):
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            if depth <= 2:
                warnings.append("cannot read %s (%s) - grant your terminal Full Disk Access" % (path, e.strerror))
            return
        if path not in CONTAINERS and is_project([e.name for e in entries]):
            projects.append(path)
            return
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    n = e.name
                    if n.startswith(".") or n == "node_modules":
                        continue
                    if depth == 0 and n in SKIP_TOP:
                        continue
                    if n.startswith(CLOUD_PREFIXES):
                        continue
                    if n.lower().endswith(BUNDLE_EXT):
                        bundles.append(e.path)
                        continue
                    if depth + 1 <= MAX_DEPTH:
                        walk(e.path, depth + 1)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    size = st.st_blocks * 512
                    if size >= 5 * MB or e.name.lower().endswith(INSTALLER_EXT):
                        loose.append((e.path, size, st.st_mtime, (st.st_dev, st.st_ino)))
            except OSError:
                continue

    walk(HOME, 0)
    return projects, loose, bundles


def walk_stats(path):
    files = total = 0
    last = 0.0
    readme = False
    for dp, _dns, fns in os.walk(path):
        for f in fns:
            if f == ".DS_Store":
                continue
            try:
                st = os.lstat(os.path.join(dp, f))
            except OSError:
                continue
            files += 1
            total += st.st_blocks * 512
            last = max(last, st.st_mtime)
            if f.lower().startswith("readme"):
                readme = True
    return {"files": files, "bytes": total, "last": last, "readme": readme}


def git_ignored(project, path):
    return run(["git", "-C", project, "check-ignore", "-q", "--", path], env=GIT_ENV, timeout=15)[0] == 0


def analyse_project(path, verify):
    p = {"path": path, "files": 0, "src": 0, "last_file": 0.0, "readme": False,
         "artifacts": [], "git": None}
    is_git = os.path.exists(os.path.join(path, ".git"))
    top_names = set(os.listdir(path)) if os.path.isdir(path) else set()
    has_manifest = any(m in top_names for m in PY_MANIFESTS)
    for dp, dns, fns in os.walk(path):
        keep = []
        for d in dns:
            full = os.path.join(dp, d)
            if d == ".git":
                continue
            if d in ALWAYS_ARTIFACT and (d != "node_modules" or "package.json" in fns or is_git):
                p["artifacts"].append((full, "DELETE", ALWAYS_ARTIFACT[d]))
                continue
            if d in NEEDS_SIBLING and any(s in fns for s in NEEDS_SIBLING[d][0]):
                p["artifacts"].append((full, "DELETE", NEEDS_SIBLING[d][1]))
                continue
            if d in VENV_NAMES and os.path.exists(os.path.join(full, "pyvenv.cfg")):
                if has_manifest:
                    p["artifacts"].append((full, "DELETE", "recreate venv from requirements"))
                else:
                    p["artifacts"].append((full, "REVIEW", "venv but no requirements file - cannot be rebuilt"))
                continue
            if d in IGNORED_ONLY and is_git and git_ignored(path, full):
                p["artifacts"].append((full, "DELETE", IGNORED_ONLY[d] + " (git-ignored)"))
                continue
            keep.append(d)
        dns[:] = keep
        for f in fns:
            if f == ".DS_Store":
                continue
            try:
                st = os.lstat(os.path.join(dp, f))
            except OSError:
                continue
            p["files"] += 1
            p["src"] += st.st_blocks * 512
            p["last_file"] = max(p["last_file"], st.st_mtime)
            if dp == path and f.lower().startswith("readme"):
                p["readme"] = True
    p["last"] = p["last_file"]
    if is_git:
        p["git"] = git_info(path, verify)
        p["last"] = max(p["last"], p["git"].get("last_commit") or 0)
    return p


def git_info(path, verify):
    def g(*a):
        return run(["git", "-C", path] + list(a), env=GIT_ENV, timeout=90)
    info = {"error": None, "dirty": 0, "stash": 0, "unpushed": 0, "remotes": [], "commits": 0,
            "remote_ok": None, "last_commit": 0}
    rc, out = g("status", "--porcelain")
    if rc != 0:
        info["error"] = "git status failed"
        return info
    info["dirty"] = len(out.splitlines())
    info["remotes"] = g("remote")[1].split()
    rc, out = g("rev-list", "--count", "HEAD")
    info["commits"] = int(out) if rc == 0 and out.strip().isdigit() else 0
    info["stash"] = len(g("stash", "list")[1].splitlines())
    info["unpushed"] = len(g("log", "--branches", "--not", "--remotes", "--format=%h")[1].split())
    out = g("log", "-1", "--all", "--format=%ct")[1].strip()
    info["last_commit"] = int(out) if out.isdigit() else 0
    if verify and info["remotes"]:
        info["remote_ok"] = any(
            run(["git", "-C", path, "ls-remote", "--exit-code", "-q", r], env=GIT_ENV, timeout=45)[0] == 0
            for r in info["remotes"])
    return info


def junk_score(p, idle):
    s, why = 0, []
    if idle >= 730:
        s += 35; why.append("untouched %dy" % (idle // 365))
    elif idle >= 365:
        s += 25; why.append("untouched %dy" % (idle // 365))
    elif idle >= 180:
        s += 15; why.append("untouched %dmo" % (idle // 30))
    else:
        s += 5
    name = os.path.basename(p["path"])
    if SCRATCH_RE.search(name):
        s += 20; why.append("scratch-like name")
    if p["files"] <= 10:
        s += 20; why.append("only %d file(s)" % p["files"])
    elif p["files"] <= 30:
        s += 10; why.append("%d files" % p["files"])
    if not p["readme"]:
        s += 10; why.append("no README")
    g = p["git"]
    if g is None:
        s += 10; why.append("no git history")
    elif g["commits"] <= 3:
        s += 10; why.append("%d commit(s)" % g["commits"])
    elif g["commits"] >= 50:
        s -= 25; why.append("%d commits invested" % g["commits"])
    return s, why


def classify(p, a):
    idle = idle_days(p["last"]) or 0
    g = p["git"]
    if g:
        if g["error"]:
            return "REVIEW", "git state unreadable - check by hand"
        risk = []
        if g["dirty"]:
            risk.append("%d uncommitted/untracked file(s)" % g["dirty"])
        if g["stash"]:
            risk.append("%d stash(es)" % g["stash"])
        if g["remotes"] and g["unpushed"]:
            risk.append("%d unpushed commit(s)" % g["unpushed"])
        if risk:
            return "REVIEW", "HAS UNSAVED WORK: " + ", ".join(risk)
        if g["remotes"]:
            if a.verify_remotes and not g["remote_ok"]:
                return "REVIEW", "remote unreachable/deleted - this may be the only copy"
            if idle >= a.stale_days:
                note = "" if g["remote_ok"] else " (remote not verified; use --verify-remotes)"
                return "QUARANTINE", "clean, fully pushed to '%s', idle %dd - re-clone anytime%s" % (
                    g["remotes"][0], idle, note)
            return "REVIEW", "clean and pushed, but touched %dd ago" % idle
    score, why = junk_score(p, idle)
    threshold = 70 if g else 60
    label = "junk score %d/%d: %s" % (score, threshold, ", ".join(why))
    return ("QUARANTINE" if score >= threshold else "REVIEW"), label


# ----------------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------------
def cache_rows(a, warnings):
    rows = []
    for rel, expand, action, why, opts in TARGETS:
        base = os.path.join(HOME, rel)
        if not os.path.exists(base):
            continue
        if expand:
            try:
                kids = [os.path.join(base, n) for n in os.listdir(base)
                        if not n.startswith(opts.get("skip_prefix", "\0"))]
            except OSError as e:
                warnings.append("cannot read %s (%s) - grant Full Disk Access" % (base, e.strerror))
                continue
        else:
            kids = [base]
        sizes = du_bytes(kids)
        for k in kids:
            b = sizes.get(k, 0)
            if b < a.min_mb * MB:
                continue
            act = "REVIEW" if os.path.basename(k) in opts.get("review", ()) else action
            rows.append(dict(action=act, kind="cache", path=k, bytes=b, idle="", reason=why))
    return rows


def scan(a):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.out or os.path.join(STATE, ts)
    os.makedirs(out, exist_ok=True)
    warnings, rows = [], []
    if sys.platform != "darwin":
        warnings.append("not running on macOS - results are best-effort")

    log("[1/5] known cache and tool locations ...")
    rows += cache_rows(a, warnings)

    log("[2/5] discovering projects and loose files under %s ..." % HOME)
    projects, loose, bundles = discover(warnings)
    log("      %d projects, %d large/installer files, %d bundles" % (len(projects), len(loose), len(bundles)))

    log("[3/5] analysing projects (git state, artifacts, last activity) ...")
    plist = []
    for i, path in enumerate(projects, 1):
        if i % 25 == 0:
            log("      %d/%d" % (i, len(projects)))
        plist.append(analyse_project(path, a.verify_remotes))
    du_targets = [x[0] for p in plist for x in p["artifacts"]]
    du_targets += [os.path.join(p["path"], ".git") for p in plist if p["git"]]
    sizes = du_bytes(du_targets)

    stats = []  # every project/folder, for the age histogram
    for p in plist:
        idle = idle_days(p["last"])
        art_bytes = 0
        for full, act, why in p["artifacts"]:
            b = sizes.get(full, 0)
            art_bytes += b
            if b >= a.min_mb * MB and idle is not None and idle >= a.artifact_days:
                rows.append(dict(action=act, kind="artifact", path=full, bytes=b, idle=idle,
                                 reason="%s in %s - %s" % (os.path.basename(full),
                                                           os.path.basename(p["path"]), why)))
        p["bytes"] = p["src"] + sizes.get(os.path.join(p["path"], ".git"), 0)
        stats.append((p["bytes"] + art_bytes, idle, "project"))
        if idle is not None and idle >= a.review_days:
            act, reason = classify(p, a)
            rows.append(dict(action=act, kind="project", path=p["path"], bytes=p["bytes"],
                             idle=idle, reason=reason))

    log("[4/5] loose folders, files, bundles, duplicates ...")
    proj_set = set(projects)
    for c in ("Desktop", "Documents", "Downloads"):
        base = os.path.join(HOME, c)
        try:
            kids = [e.path for e in os.scandir(base) if e.is_dir(follow_symlinks=False)
                    and not e.name.startswith(".") and not e.name.lower().endswith(BUNDLE_EXT)]
        except OSError:
            continue
        for k in kids:
            if k in proj_set or any(pp.startswith(k + os.sep) for pp in projects):
                continue
            s = walk_stats(k)
            idle = idle_days(s["last"])
            stats.append((s["bytes"], idle, "folder"))
            if idle is not None and idle >= a.review_days:
                score, why = junk_score({"path": k, "files": s["files"], "readme": s["readme"], "git": None}, idle)
                scratch = bool(SCRATCH_RE.search(os.path.basename(k)))
                act = "QUARANTINE" if scratch and score >= 80 else "REVIEW"
                rows.append(dict(action=act, kind="folder", path=k, bytes=s["bytes"], idle=idle,
                                 reason="no project markers; junk score %d: %s" % (score, ", ".join(why))))
    bsz = du_bytes(bundles)
    for b in bundles:
        if bsz.get(b, 0) >= a.big_mb * MB:
            rows.append(dict(action="REVIEW", kind="bundle", path=b, bytes=bsz[b], idle="",
                             reason="large bundle (%s)" % os.path.splitext(b)[1]))
    dl = os.path.join(HOME, "Downloads") + os.sep
    seen_dup = defaultdict(list)
    for path, size, mtime, _ino in loose:
        idle = idle_days(mtime)
        if path.startswith(dl) and path.lower().endswith(INSTALLER_EXT) and idle >= 30 and size >= MB:
            rows.append(dict(action="QUARANTINE", kind="file", path=path, bytes=size, idle=idle,
                             reason="installer/archive in Downloads, idle %dd" % idle))
        elif size >= a.big_mb * MB:
            rows.append(dict(action="REVIEW", kind="file", path=path, bytes=size, idle=idle,
                             reason="large file (%s)" % fmt(size)))
        if size >= 5 * MB:
            seen_dup[size].append((path, mtime))
    planned = {r["path"] for r in rows}
    shot = re.compile(r"^(Screenshot|Screen Shot|Screen Recording|CleanShot)", re.I)
    for c, min_idle in (("Desktop", a.review_days), ("Downloads", 90)):
        base = os.path.join(HOME, c)
        try:
            entries = [e for e in os.scandir(base) if e.is_file(follow_symlinks=False) and e.name != ".DS_Store"]
        except OSError:
            continue
        for e in entries:
            if e.path in planned:
                continue
            st = e.stat(follow_symlinks=False)
            idle = idle_days(st.st_mtime)
            if idle < min_idle:
                continue
            if shot.match(e.name) and idle >= 90:
                act, why = "QUARANTINE", "old screenshot/recording"
            else:
                act, why = "REVIEW", "loose file in %s, idle %dd" % (c, idle)
            rows.append(dict(action=act, kind="file", path=e.path, bytes=st.st_blocks * 512, idle=idle, reason=why))
            planned.add(e.path)
    for size, group in seen_dup.items():
        if len(group) < 2:
            continue
        by_hash = defaultdict(list)
        for path, mtime in group:
            h = hash_file(path)
            if h:
                by_hash[h].append((path, mtime))
        for dupes in by_hash.values():
            if len(dupes) < 2:
                continue
            dupes.sort(key=lambda x: (len(x[0]), x[1]))
            keep = dupes[0][0]
            for path, mtime in dupes[1:]:
                if path in planned:
                    continue
                rows.append(dict(action="REVIEW", kind="dup", path=path, bytes=size,
                                 idle=idle_days(mtime), reason="identical to %s" % keep))

    log("[5/5] measuring your home directory for the report ...")
    breakdown = home_breakdown()
    write_outputs(a, out, rows, stats, breakdown, warnings, len(plist))
    return out


def hash_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def home_breakdown():
    kids = [os.path.join(HOME, n) for n in os.listdir(HOME)]
    sizes = du_bytes(kids)
    top = sorted(sizes.items(), key=lambda x: -x[1])[:20]
    lib = os.path.join(HOME, "Library")
    lib_top = []
    if os.path.isdir(lib):
        ls = du_bytes([os.path.join(lib, n) for n in os.listdir(lib)])
        lib_top = sorted(ls.items(), key=lambda x: -x[1])[:12]
    return {"home": top, "library": lib_top}


ORDER = {"DELETE": 0, "QUARANTINE": 1, "REVIEW": 2}


def volume_line():
    target = "/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/"
    try:
        du = shutil.disk_usage(target)
        return "%s used of %s, %s free" % (fmt(du.used), fmt(du.total), fmt(du.free))
    except OSError:
        return "n/a"


def write_outputs(a, out, rows, stats, breakdown, warnings, nproj):
    rows.sort(key=lambda r: (ORDER[r["action"]], -r["bytes"]))
    plan = os.path.join(out, "plan.tsv")
    with open(plan, "w", newline="") as f:
        f.write("# macclean plan v1 scanned_at=%d home=%s\n" % (NOW, HOME))
        f.write("# Set the first column to DELETE, QUARANTINE or KEEP. REVIEW/KEEP rows are ignored by `apply`.\n")
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["action", "size", "bytes", "kind", "idle_days", "path", "reason"])
        for r in rows:
            w.writerow([r["action"], fmt(r["bytes"]), r["bytes"], r["kind"], r["idle"], r["path"], r["reason"]])

    tot = defaultdict(int)
    for r in rows:
        tot[r["action"]] += r["bytes"]
    L = []
    L.append("# Mac storage report - %s\n" % datetime.date.today().isoformat())
    L.append("Volume: **%s**\n" % volume_line())
    L.append("| Bucket | Reclaimable | Meaning |\n|---|---|---|")
    L.append("| DELETE | **%s** | regenerable caches/build output - safe to remove for real |" % fmt(tot["DELETE"]))
    L.append("| QUARANTINE | **%s** | stale projects/installers - moved aside, `purge` frees the space later |" % fmt(tot["QUARANTINE"]))
    L.append("| REVIEW | %s | needs your judgement; flip to QUARANTINE/DELETE in `plan.tsv` |\n" % fmt(tot["REVIEW"]))

    L.append("## Where your space is\n")
    L.append("| Size | Home item |\n|---:|---|")
    for p, b in breakdown["home"]:
        L.append("| %s | `%s` |" % (fmt(b), p.replace(HOME, "~")))
    if breakdown["library"]:
        L.append("\n**Inside ~/Library:**\n\n| Size | Item |\n|---:|---|")
        for p, b in breakdown["library"]:
            L.append("| %s | `%s` |" % (fmt(b), p.replace(HOME, "~")))

    L.append("\n## Death by a thousand cuts\n")
    buckets = [("< 3 months", 0, 90), ("3-12 months", 90, 365), ("1-2 years", 365, 730), ("> 2 years", 730, 10 ** 9)]
    L.append("| Last touched | Projects/folders | Total size |\n|---|---:|---:|")
    for name, lo, hi in buckets:
        sel = [s for s in stats if s[1] is not None and lo <= s[1] < hi]
        L.append("| %s | %d | %s |" % (name, len(sel), fmt(sum(s[0] for s in sel))))
    small = [s for s in stats if s[1] is not None and s[1] >= a.review_days and s[0] < 100 * MB]
    L.append("\n%d stale projects/folders are each under 100 MB; together they weigh %s.\n" % (
        len(small), fmt(sum(s[0] for s in small))))

    L.append("## Biggest opportunities (top 40 by size)\n")
    L.append("| Action | Size | Idle | Kind | Path | Why |\n|---|---:|---:|---|---|---|")
    for r in sorted(rows, key=lambda r: -r["bytes"])[:40]:
        L.append("| %s | %s | %s | %s | `%s` | %s |" % (
            r["action"], fmt(r["bytes"]), ("%sd" % r["idle"]) if r["idle"] != "" else "", r["kind"],
            r["path"].replace(HOME, "~"), r["reason"]))

    L.append("\n## Tool-managed cleanups (not automated - run what applies)\n")
    for tool, cmd in (("brew", "brew cleanup -s --prune=all"),
                      ("docker", "docker system prune -a --volumes   # removes ALL unused images/volumes"),
                      ("xcrun", "xcrun simctl delete unavailable"),
                      ("conda", "conda clean -a"),
                      ("npm", "npm cache clean --force"),
                      ("pip3", "pip3 cache purge")):
        if shutil.which(tool):
            L.append("- `%s`" % cmd)
    if sys.platform == "darwin":
        rc, txt = run(["tmutil", "listlocalsnapshots", "/"])
        n = len([l for l in txt.splitlines() if "com.apple" in l])
        if n:
            L.append("- %d local Time Machine snapshot(s) hold space: `sudo tmutil thinlocalsnapshots / 99999999999 4`" % n)
    if warnings:
        L.append("\n## Warnings\n")
        for w in sorted(set(warnings)):
            L.append("- " + w)
    L.append("\n_Scanned %d projects. Nothing was modified by the scan._" % nproj)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write("\n".join(L) + "\n")

    print("\nDone. Nothing has been changed.")
    print("  DELETE      %10s" % fmt(tot["DELETE"]))
    print("  QUARANTINE  %10s" % fmt(tot["QUARANTINE"]))
    print("  REVIEW      %10s" % fmt(tot["REVIEW"]))
    print("\nReport: %s/report.md\nPlan:   %s\n" % (out, plan))
    print("Next: open the plan, flip rows you disagree with, then:\n  python3 %s apply %s          # dry run\n  python3 %s apply %s --yes" % (
        sys.argv[0], plan, sys.argv[0], plan))


# ----------------------------------------------------------------------------
# apply / purge / restore
# ----------------------------------------------------------------------------
PROTECTED = {os.path.join(HOME, d) for d in (
    "Desktop", "Documents", "Downloads", "Library", "Applications", "Movies", "Music", "Pictures",
    "Public", ".ssh", ".gnupg", ".config", ".aws", ".kube")} | {HOME}
PROTECTED_SUBSTR = ("/Library/Mobile Documents/", "/Library/CloudStorage/", "/Library/Keychains/",
                    "/Library/Preferences/", "/Library/Messages/", "/Library/Mail/", "/.ssh/", "/.gnupg/")


def path_problem(path):
    if not os.path.isabs(path):
        return "not absolute"
    parent = os.path.realpath(os.path.dirname(path))
    real = os.path.join(parent, os.path.basename(path))
    if not real.startswith(HOME + os.sep):
        return "outside home"
    if real in PROTECTED or any(p.startswith(real + os.sep) for p in PROTECTED):
        return "protected location"
    if any(s in real + "/" for s in PROTECTED_SUBSTR):
        return "protected location (use the owning app instead)"
    if ".photoslibrary" in real:
        return "Photos library"
    rel = os.path.relpath(real, HOME)
    if rel.count(os.sep) == 0 and rel.startswith("."):
        return "top-level dotfile/dir"
    return None


def recheck_git(path):
    if not os.path.exists(os.path.join(path, ".git")):
        return None
    g = git_info(path, False)
    if g["error"]:
        return "git state unreadable"
    if g["dirty"] or g["stash"] or (g["remotes"] and g["unpushed"]):
        return "changed since scan: %d uncommitted, %d stashes, %d unpushed" % (g["dirty"], g["stash"], g["unpushed"] if g["remotes"] else 0)
    return None


def rm(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
        return

    def onerr(func, p, _exc):
        os.chmod(p, stat.S_IRWXU)
        func(p)
    shutil.rmtree(path, onerror=onerr)


def read_plan(path):
    with open(path, newline="") as f:
        lines = [l for l in f if not l.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def apply(a):
    rows = read_plan(a.plan)
    todo = [r for r in rows if (r.get("action") or "").strip().upper() in ("DELETE", "QUARANTINE")]
    todo.sort(key=lambda r: (0 if r["action"].strip().upper() == "DELETE" else 1, -len(r["path"])))
    ok, refused = [], []
    for r in todo:
        p = r["path"]
        why = path_problem(p)
        if not why and not os.path.lexists(p):
            why = "already gone"
        if not why and r["kind"] == "project":
            why = recheck_git(p)
        (refused if why else ok).append((r, why))
    total = sum(int(r["bytes"]) for r, _ in ok)
    print("%d actions (%s), %d skipped." % (len(ok), fmt(total), len(refused)))
    for r, why in refused:
        print("  SKIP %s -> %s" % (r["path"].replace(HOME, "~"), why))
    for r, _ in ok:
        print("  %-10s %9s  %s" % (r["action"].strip().upper(), r["size"], r["path"].replace(HOME, "~")))
    if not a.yes:
        print("\nDry run. Nothing changed. Quit the apps whose caches are listed, then re-run with --yes.")
        return
    batch = os.path.join(QDIR, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    freed = moved = 0
    for r, _ in ok:
        p, act = r["path"], r["action"].strip().upper()
        try:
            if act == "DELETE":
                rm(p)
                freed += int(r["bytes"])
            else:
                os.makedirs(batch, exist_ok=True)
                dest = os.path.join(batch, os.path.relpath(p, HOME).replace(os.sep, "__"))
                shutil.move(p, dest)
                with open(os.path.join(batch, "MANIFEST.tsv"), "a") as m:
                    m.write("%s\t%s\n" % (p, dest))
                moved += int(r["bytes"])
        except OSError as e:
            print("  FAILED %s: %s" % (p, e))
    print("\nDeleted %s. Quarantined %s (space is freed by `purge`)." % (fmt(freed), fmt(moved)))
    if moved:
        print("Undo with: python3 %s restore --batch %s\nFree it with: python3 %s purge --yes" % (
            sys.argv[0], os.path.basename(batch), sys.argv[0]))


def batches():
    return sorted(d for d in os.listdir(QDIR)) if os.path.isdir(QDIR) else []


def purge(a):
    cutoff = NOW - a.older_than * DAY
    tot = 0
    for b in batches():
        p = os.path.join(QDIR, b)
        if os.path.getmtime(p) > cutoff:
            continue
        size = du_bytes([p]).get(p, 0)
        tot += size
        print("%s  %s" % (fmt(size), p.replace(HOME, "~")))
        if a.yes:
            shutil.rmtree(p, ignore_errors=True)
    print("%s %s." % ("Purged" if a.yes else "Would free", fmt(tot)) + ("" if a.yes else " Re-run with --yes."))


def restore(a):
    for b in batches():
        if a.batch and b != a.batch:
            continue
        man = os.path.join(QDIR, b, "MANIFEST.tsv")
        if not os.path.exists(man):
            continue
        for line in open(man):
            orig, dest = line.rstrip("\n").split("\t")
            if not os.path.lexists(dest):
                continue
            if os.path.lexists(orig):
                print("SKIP (exists) %s" % orig)
                continue
            os.makedirs(os.path.dirname(orig), exist_ok=True)
            shutil.move(dest, orig)
            print("restored %s" % orig.replace(HOME, "~"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="read-only scan")
    s.add_argument("--out")
    s.add_argument("--stale-days", type=int, default=180, help="clean+pushed repos idle this long are quarantine candidates")
    s.add_argument("--review-days", type=int, default=60, help="list projects/folders idle at least this long")
    s.add_argument("--artifact-days", type=int, default=30, help="remove node_modules/venv/etc. from projects idle this long")
    s.add_argument("--min-mb", type=int, default=20, help="ignore items smaller than this")
    s.add_argument("--big-mb", type=int, default=500, help="flag single files/bundles this big")
    s.add_argument("--verify-remotes", action="store_true", help="git ls-remote each repo (network) before trusting its remote")
    p = sub.add_parser("apply", help="act on a reviewed plan")
    p.add_argument("plan")
    p.add_argument("--yes", action="store_true")
    q = sub.add_parser("purge", help="permanently delete quarantined items")
    q.add_argument("--older-than", type=int, default=0, help="days (default: everything)")
    q.add_argument("--yes", action="store_true")
    r = sub.add_parser("restore", help="move quarantined items back")
    r.add_argument("--batch")
    a = ap.parse_args()
    {"scan": scan, "apply": apply, "purge": purge, "restore": restore}[a.cmd](a)


if __name__ == "__main__":
    main()
