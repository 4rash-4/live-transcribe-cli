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


SECRET_RE = re.compile(
    r"^(\.env(\..+)?|.*\.(p12|pfx|keystore|jks|kdbx)|id_(rsa|dsa|ecdsa|ed25519)|credentials(\..+)?|"
    r".*secrets?(\..+)?|\.npmrc|\.pypirc|\.netrc|wallet.*\.json|.*private.*key.*)$", re.I)
SECRET_SAFE_RE = re.compile(r"\.(example|sample|template|dist|md|txt)$", re.I)
NOT_SECRET_DIRS = ("node_modules", "site-packages", os.sep + ".git" + os.sep, os.sep + "venv" + os.sep)


def is_secret_file(dp, f):
    """Filename heuristics for credentials that should not be uploaded to a cloud drive."""
    if any(x in dp + os.sep for x in NOT_SECRET_DIRS):
        return False
    if f.lower().endswith((".pem", ".key")):  # PEM private keys vs. e.g. Keynote files
        try:
            with open(os.path.join(dp, f), "rb") as fh:
                return b"PRIVATE KEY" in fh.read(400)
        except OSError:
            return False
    if SECRET_SAFE_RE.search(f):
        return False
    return bool(SECRET_RE.match(f))


def walk_stats(path):
    files = total = 0
    last = 0.0
    readme = False
    secrets = []
    for dp, _dns, fns in os.walk(path):
        for f in fns:
            if f == ".DS_Store":
                continue
            if len(secrets) < 5 and is_secret_file(dp, f):
                secrets.append(os.path.relpath(os.path.join(dp, f), path))
            try:
                st = os.lstat(os.path.join(dp, f))
            except OSError:
                continue
            files += 1
            total += st.st_blocks * 512
            last = max(last, st.st_mtime)
            if f.lower().startswith("readme"):
                readme = True
    return {"files": files, "bytes": total, "last": last, "readme": readme, "secrets": secrets}


def git_ignored(project, path):
    return run(["git", "-C", project, "check-ignore", "-q", "--", path], env=GIT_ENV, timeout=15)[0] == 0


def analyse_project(path, verify):
    p = {"path": path, "files": 0, "src": 0, "last_file": 0.0, "readme": False,
         "artifacts": [], "git": None, "secrets": [], "ignored_local": []}
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
            if len(p["secrets"]) < 5 and is_secret_file(dp, f):
                p["secrets"].append(os.path.relpath(os.path.join(dp, f), path))
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
        arts = [os.path.relpath(x[0], path) for x in p["artifacts"]]
        for e in p["git"].get("ignored", []):
            e = e.rstrip("/")
            if (any(e == x or e.startswith(x + os.sep) for x in arts) or e.endswith((".pyc", ".DS_Store"))
                    or "__pycache__" in e):
                continue
            p["ignored_local"].append(e)
    return p


def git_info(path, verify):
    def g(*a):
        return run(["git", "-C", path] + list(a), env=GIT_ENV, timeout=90)
    info = {"error": None, "dirty": 0, "stash": 0, "unpushed": 0, "remotes": [], "commits": 0,
            "remote_ok": None, "last_commit": 0}
    rc, out = g("status", "--porcelain", "--ignored")
    if rc != 0:
        info["error"] = "git status failed"
        return info
    lines = out.splitlines()
    info["dirty"] = len([l for l in lines if not l.startswith("!!")])
    info["ignored"] = [l[3:] for l in lines if l.startswith("!!")]
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
            if p["ignored_local"]:
                il = p["ignored_local"]
                return "REVIEW", "clean and pushed BUT %d git-ignored local-only item(s) not on the remote: %s" % (
                    len(il), ", ".join(il[:3]))
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
                             idle=idle, reason=reason, secrets=p["secrets"],
                             unsaved=reason.startswith("HAS UNSAVED")))

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
                                 reason="no project markers; junk score %d: %s" % (score, ", ".join(why)),
                                 secrets=s["secrets"], unsaved=False))
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
    ctx = finalize(a, rows, plist, sizes, warnings)
    write_outputs(a, out, ctx["rows"], stats, breakdown, warnings, len(plist), ctx)
    return out


def find_clouds():
    """Locally synced cloud folders: name -> path."""
    found = {}
    icloud = os.path.join(HOME, "Library", "Mobile Documents", "com~apple~CloudDocs")
    if os.path.isdir(icloud):
        found["icloud"] = icloud
    cs = os.path.join(HOME, "Library", "CloudStorage")
    cands = []
    if os.path.isdir(cs):
        cands += [os.path.join(cs, n) for n in sorted(os.listdir(cs)) if n.startswith("OneDrive")]
    cands += [os.path.join(HOME, n) for n in sorted(os.listdir(HOME)) if n.startswith("OneDrive")]
    for c in cands:
        if os.path.isdir(c):
            found.setdefault("onedrive", c)
    return found


def resolve_cloud(spec, warnings):
    clouds = find_clouds()
    if not spec or spec == "none":
        return None
    if spec == "auto":
        for k in ("onedrive", "icloud"):
            if k in clouds:
                return clouds[k]
        warnings.append("no OneDrive or iCloud Drive folder found - sign in and let it sync, or pass --cloud PATH")
        return None
    if spec in clouds:
        return clouds[spec]
    if os.path.isdir(os.path.expanduser(spec)):
        return os.path.realpath(os.path.expanduser(spec))
    warnings.append("cloud folder '%s' not found (detected: %s)" % (spec, ", ".join(clouds) or "none"))
    return None


def finalize(a, rows, plist, sizes, warnings):
    """Apply the keep list, cloud offload routing and the free-space target."""
    keep = [k.lower() for k in a.keep]
    kept_projects = [p for p in plist if any(k in p["path"].lower() for k in keep)]
    kept_paths = [p["path"] for p in kept_projects]

    def protected(path):
        low = path.lower()
        return (any(k in low for k in keep)
                or any(kp.startswith(path + os.sep) or kp == path for kp in kept_paths))
    rows = [r for r in rows if not protected(r["path"])]
    cloud = resolve_cloud(a.cloud, warnings)
    if cloud:
        for r in rows:
            if r["kind"] not in ("project", "folder"):
                continue
            sec = r.get("secrets")
            if sec and not a.include_secrets:
                if r["action"] in ("QUARANTINE", "OFFLOAD"):
                    r["action"] = "REVIEW"
                r["reason"] += " | NOT UPLOADABLE: contains credentials (%s)" % ", ".join(sec[:3])
            elif r["action"] == "QUARANTINE":
                r["action"] = "OFFLOAD"
                r["reason"] += " | archive to cloud"
    shortfall = None
    if a.target_gb:
        target = a.target_gb * 1024 * MB
        got = sum(r["bytes"] for r in rows if r["action"] in ("DELETE", "QUARANTINE", "OFFLOAD"))
        if cloud and got < target:
            idle_rank = lambda r: 3 if r["idle"] >= 365 else 2 if r["idle"] >= 180 else 1 if r["idle"] >= 60 else 0
            cands = [r for r in rows if r["action"] == "REVIEW" and r["kind"] in ("project", "folder")
                     and not (r.get("secrets") and not a.include_secrets)]
            cands.sort(key=lambda r: (r.get("unsaved", False), -idle_rank(r), -r["bytes"]))
            for r in cands:
                if got >= target:
                    break
                r["action"] = "OFFLOAD"
                r["reason"] = "[needed for %g GB target] %s" % (a.target_gb, r["reason"])
                got += r["bytes"]
        if got < target:
            shortfall = target - got
    kept_info = [(p["path"], p["src"] + sum(sizes.get(x[0], 0) for x in p["artifacts"])) for p in kept_projects]
    kept_roots = sorted(set(kept_paths))
    return {"rows": rows, "cloud": cloud, "kept": kept_info, "kept_paths": kept_roots,
            "shortfall": shortfall, "keep": a.keep}


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


ORDER = {"DELETE": 0, "OFFLOAD": 1, "QUARANTINE": 2, "REVIEW": 3}


def volume_line():
    target = "/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/"
    try:
        du = shutil.disk_usage(target)
        return "%s used of %s, %s free" % (fmt(du.used), fmt(du.total), fmt(du.free))
    except OSError:
        return "n/a"


def write_outputs(a, out, rows, stats, breakdown, warnings, nproj, ctx):
    rows.sort(key=lambda r: (ORDER[r["action"]], -r["bytes"]))
    plan = os.path.join(out, "plan.tsv")
    with open(plan, "w", newline="") as f:
        f.write("# macclean plan v1 scanned_at=%d home=%s\n" % (NOW, HOME))
        for k in ctx["keep"]:
            f.write("# keep=%s\n" % k)
        for kp in ctx["kept_paths"]:
            f.write("# keep_path=%s\n" % kp)
        if ctx["cloud"]:
            f.write("# cloud=%s\n" % ctx["cloud"])
        f.write("# Set the first column to DELETE, OFFLOAD, QUARANTINE or KEEP. REVIEW/KEEP rows are ignored by `apply`.\n")
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
    L.append("| OFFLOAD | **%s** | archived (tar.gz) into `%s`, verified, then the local copy is removed |" % (
        fmt(tot["OFFLOAD"]), (ctx["cloud"] or "no cloud folder found").replace(HOME, "~")))
    L.append("| QUARANTINE | **%s** | junk/installers - moved aside, `purge` frees the space later |" % fmt(tot["QUARANTINE"]))
    L.append("| REVIEW | %s | needs your judgement; flip to QUARANTINE/DELETE in `plan.tsv` |\n" % fmt(tot["REVIEW"]))

    if a.target_gb:
        got = tot["DELETE"] + tot["OFFLOAD"] + tot["QUARANTINE"]
        L.append("**Target %g GB: plan reclaims %s%s.**\n" % (
            a.target_gb, fmt(got), "" if not ctx["shortfall"] else
            " - SHORT by %s; see the REVIEW rows (Docker, Ollama, simulators, backups) or lower --review-days" % fmt(ctx["shortfall"])))
    if ctx["keep"]:
        L.append("## Protected (never touched)\n")
        L.append("Anything whose path contains: %s\n" % ", ".join("`%s`" % k for k in ctx["keep"]))
        for p, b in ctx["kept"]:
            L.append("- `%s` (%s incl. build dirs)" % (p.replace(HOME, "~"), fmt(b)))
        L.append("")
    if ctx["cloud"]:
        nsec = [r for r in rows if r["kind"] in ("project", "folder") and r.get("secrets")]
        if nsec:
            L.append("## Not uploaded because they contain credentials\n")
            for r in nsec:
                L.append("- `%s` (%s): %s" % (r["path"].replace(HOME, "~"), fmt(r["bytes"]), ", ".join(r["secrets"][:3])))
            L.append("\nMove the secrets out (or rotate them) and rescan, or pass `--include-secrets` to upload anyway.\n")
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
    print("  OFFLOAD     %10s" % fmt(tot["OFFLOAD"]))
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
    meta = {"keep": [], "keep_path": [], "cloud": None}
    body = []
    with open(path, newline="") as f:
        for l in f:
            if l.startswith("#"):
                m = re.match(r"# (keep|keep_path|cloud)=(.*)$", l.rstrip("\n"))
                if m:
                    if m.group(1) == "cloud":
                        meta["cloud"] = m.group(2)
                    else:
                        meta[m.group(1)].append(m.group(2))
            else:
                body.append(l)
    return meta, list(csv.DictReader(body, delimiter="\t"))


REGISTRY = os.path.join(STATE, "offloads.tsv")


def safe_name(rel):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", rel.replace(os.sep, "__")).strip(" .")
    if len(name) > 120:
        name = name[:100] + "-" + hashlib.sha1(rel.encode()).hexdigest()[:8]
    return name


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_archive(src, cloud, stamp):
    """tar.gz src into <cloud>/MacArchive/<stamp>/, verify it, return (archive, sha256, bytes)."""
    parent, name = os.path.dirname(src), os.path.basename(src)
    info = analyse_project(src, False)
    skip = [os.path.relpath(f, parent) for f, act, _ in info["artifacts"]
            if act == "DELETE" and not re.search(r"[\[\]*?]", f)]
    final_dir = os.path.join(cloud, "MacArchive", stamp)
    arch_name = safe_name(os.path.relpath(src, HOME)) + ".tar.gz"
    stage_dir = os.path.join(STATE, "staging")
    os.makedirs(stage_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)
    stage = os.path.join(stage_dir, arch_name)
    cmd = ["tar", "-czf", stage, "--exclude", ".DS_Store"]
    for e in skip:
        cmd += ["--exclude", e]
    cmd += ["-C", parent, name]
    r = subprocess.run(cmd, stderr=subprocess.PIPE, universal_newlines=True)
    errs = [l for l in r.stderr.splitlines() if l.strip() and "changed as we read" not in l]
    if r.returncode != 0 and (r.returncode != 1 or errs):
        raise OSError("tar failed (exit %d): %s" % (r.returncode, "; ".join(errs[:2])))
    if subprocess.run(["gzip", "-t", stage]).returncode != 0:
        raise OSError("gzip integrity check failed")
    rc, listing = run(["tar", "-tzf", stage], timeout=3600)
    if rc != 0 or not listing.strip():
        raise OSError("archive listing failed")
    sha = sha256_file(stage)
    size = os.path.getsize(stage)
    dest = os.path.join(final_dir, arch_name)
    shutil.move(stage, dest)
    if os.path.getsize(dest) != size:
        raise OSError("size mismatch after moving into cloud folder")
    with open(dest + ".sha256", "w") as f:
        f.write("%s  %s\n" % (sha, arch_name))
    return dest, sha, size


def apply(a):
    meta, rows = read_plan(a.plan)
    cloud = os.path.realpath(os.path.expanduser(a.cloud_dir)) if a.cloud_dir else meta["cloud"]
    keep = [k.lower() for k in meta["keep"]]
    todo = [r for r in rows if (r.get("action") or "").strip().upper() in ("DELETE", "OFFLOAD", "QUARANTINE")]
    rank = {"DELETE": 0, "QUARANTINE": 1, "OFFLOAD": 2}
    todo.sort(key=lambda r: (rank[r["action"].strip().upper()], -len(r["path"]) if r["action"].strip().upper() != "OFFLOAD" else 0))
    ok, refused = [], []
    for r in todo:
        p, act = r["path"], r["action"].strip().upper()
        why = path_problem(p)
        if not why and (any(k in p.lower() for k in keep)
                        or any(kp.startswith(p + os.sep) or kp == p for kp in meta["keep_path"])):
            why = "protected by --keep"
        if not why and cloud and (p == cloud or p.startswith(cloud + os.sep)):
            why = "inside the cloud folder"
        if not why and not os.path.lexists(p):
            why = "already gone"
        if not why and act == "OFFLOAD":
            if not cloud or not os.path.isdir(cloud):
                why = "no cloud folder (plan header or --cloud-dir)"
        if not why and r["kind"] == "project" and act != "OFFLOAD":
            why = recheck_git(p)
        (refused if why else ok).append((r, why))
    total = sum(int(r["bytes"]) for r, _ in ok)
    print("%d actions (%s), %d skipped." % (len(ok), fmt(total), len(refused)))
    for r, why in refused:
        print("  SKIP %s -> %s" % (r["path"].replace(HOME, "~"), why))
    for r, _ in ok:
        print("  %-10s %9s  %s" % (r["action"].strip().upper(), r["size"], r["path"].replace(HOME, "~")))
    if any(r["action"].strip().upper() == "OFFLOAD" for r, _ in ok):
        print("\nOFFLOAD target: %s/MacArchive  (%s the local original afterwards)" % (
            cloud.replace(HOME, "~"), "DELETES" if a.free_now else "quarantines"))
    if not a.yes:
        print("\nDry run. Nothing changed. Quit the apps whose caches are listed, then re-run with --yes.")
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    batch = os.path.join(QDIR, stamp)
    freed = moved = offloaded = 0
    for r, _ in ok:
        p, act = r["path"], r["action"].strip().upper()
        try:
            if act == "DELETE":
                rm(p)
                freed += int(r["bytes"])
                continue
            if act == "OFFLOAD":
                free = shutil.disk_usage(HOME).free
                need = int(r["bytes"]) + (1 << 30)
                if free < need and not a.free_now:
                    print("  SKIP %s: need ~%s free to stage the archive, have %s (try --free-now, or purge first)" % (
                        p.replace(HOME, "~"), fmt(need), fmt(free)))
                    continue
                print("  archiving %s ..." % p.replace(HOME, "~"))
                arch, sha, asz = make_archive(p, cloud, stamp)
                with open(REGISTRY, "a") as reg:
                    reg.write("%d\t%s\t%s\t%s\t%d\n" % (NOW, p, arch, sha, int(r["bytes"])))
                if a.free_now:
                    rm(p)
                    freed += int(r["bytes"])
                    offloaded += int(r["bytes"])
                    continue
            os.makedirs(batch, exist_ok=True)
            dest = os.path.join(batch, os.path.relpath(p, HOME).replace(os.sep, "__"))
            shutil.move(p, dest)
            with open(os.path.join(batch, "MANIFEST.tsv"), "a") as m:
                m.write("%s\t%s\t%s\t%s\n" % (p, dest, arch if act == "OFFLOAD" else "", sha if act == "OFFLOAD" else ""))
            moved += int(r["bytes"])
            if act == "OFFLOAD":
                offloaded += int(r["bytes"])
        except (OSError, subprocess.SubprocessError) as e:
            print("  FAILED %s: %s" % (p, e))
    print("\nDeleted %s. Quarantined %s (space is freed by `purge`). Of this, %s was archived to the cloud folder." % (
        fmt(freed), fmt(moved), fmt(offloaded)))
    if offloaded:
        print("\nNEXT:\n 1. Let OneDrive/iCloud finish uploading (no spinner/pending badge on the MacArchive folder).")
        print("    Check the files in the web UI (onedrive.live.com / icloud.com).")
        if moved:
            print(" 2. python3 %s purge --yes --synced     # deletes the quarantined originals after re-verifying the archives" % sys.argv[0])
        print(" 3. Free the local copy of the archives: Finder > right-click MacArchive >"
              " 'Free Up Space' (OneDrive) / 'Remove Download' (iCloud), or: python3 %s evict" % sys.argv[0])
    elif moved:
        print("Undo with: python3 %s restore --batch %s\nFree it with: python3 %s purge --yes" % (
            sys.argv[0], stamp, sys.argv[0]))


def batches():
    return sorted(d for d in os.listdir(QDIR)) if os.path.isdir(QDIR) else []


def manifest(batch):
    man = os.path.join(QDIR, batch, "MANIFEST.tsv")
    out = []
    if os.path.exists(man):
        for line in open(man):
            parts = (line.rstrip("\n").split("\t") + ["", ""])[:4]
            out.append(parts)
    return out


def purge(a):
    cutoff = NOW - a.older_than * DAY
    tot = 0
    for b in batches():
        p = os.path.join(QDIR, b)
        if os.path.getmtime(p) > cutoff:
            continue
        archives = [(m[2], m[3]) for m in manifest(b) if m[2]]
        if archives and not a.synced:
            print("SKIP %s: contains items archived to the cloud. Confirm the upload finished, then use --synced." % b)
            continue
        bad = []
        if archives and a.yes:
            for arch, sha in archives:
                try:
                    if sha256_file(arch) != sha:
                        bad.append(arch)
                except OSError:
                    bad.append(arch)
        if bad:
            print("REFUSING to purge %s: archive missing or corrupt:\n  %s" % (b, "\n  ".join(bad)))
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
        for orig, dest, _arch, _sha in manifest(b):
            if not os.path.lexists(dest):
                continue
            if os.path.lexists(orig):
                print("SKIP (exists) %s" % orig)
                continue
            os.makedirs(os.path.dirname(orig), exist_ok=True)
            shutil.move(dest, orig)
            print("restored %s" % orig.replace(HOME, "~"))


def read_registry():
    if not os.path.exists(REGISTRY):
        return []
    return [l.rstrip("\n").split("\t") for l in open(REGISTRY) if l.count("\t") >= 4]


def evict(a):
    """Ask iCloud to drop the local copy of uploaded archives (OneDrive: use Finder 'Free Up Space')."""
    n = 0
    for _ts, _src, arch, _sha, _b in read_registry():
        if "/Mobile Documents/" in arch and os.path.exists(arch):
            if shutil.which("brctl") and run(["brctl", "evict", arch])[0] == 0:
                n += 1
    print("Asked iCloud to evict %d archive(s). Items not yet uploaded are kept by iCloud." % n)
    print("For OneDrive: Finder > right-click the MacArchive folder > 'Free Up Space'.")


def unarchive(a):
    arch = os.path.abspath(os.path.expanduser(a.archive))
    side = arch + ".sha256"
    if os.path.exists(side):
        want = open(side).read().split()[0]
        if sha256_file(arch) != want:
            sys.exit("checksum mismatch - archive is damaged, refusing to extract")
    dest = os.path.abspath(os.path.expanduser(a.to or HOME))
    os.makedirs(dest, exist_ok=True)
    top = run(["tar", "-tzf", arch], timeout=3600)[1].splitlines()
    if not top:
        sys.exit("cannot read archive")
    root = os.path.join(dest, top[0].split("/")[0])
    if os.path.exists(root):
        sys.exit("%s already exists; use --to another directory" % root)
    if subprocess.run(["tar", "-xzf", arch, "-C", dest]).returncode != 0:
        sys.exit("extract failed")
    print("restored to %s (rebuild node_modules/venvs with the usual install commands)" % root)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="read-only scan")
    s.add_argument("--out")
    s.add_argument("--keep", action="append", default=None,
                   help="extra case-insensitive path substring that is NEVER touched (repeatable; 'nobitex' is always kept)")
    s.add_argument("--cloud", default="auto",
                   help="where to offload: auto | onedrive | icloud | none | /path (default auto, OneDrive first)")
    s.add_argument("--target-gb", type=float, help="free at least this much; escalates stale projects to OFFLOAD until met")
    s.add_argument("--include-secrets", action="store_true", help="allow uploading projects that contain .env/keys/credentials")
    s.add_argument("--stale-days", type=int, help="clean+pushed repos idle this long are candidates (default 180; 30 with --target-gb)")
    s.add_argument("--review-days", type=int, help="list projects/folders idle at least this long (default 60; 14 with --target-gb)")
    s.add_argument("--artifact-days", type=int, help="remove node_modules/venv/etc. from projects idle this long (default 30; 14 with --target-gb)")
    s.add_argument("--min-mb", type=int, default=20, help="ignore items smaller than this")
    s.add_argument("--big-mb", type=int, default=500, help="flag single files/bundles this big")
    s.add_argument("--verify-remotes", action="store_true", help="git ls-remote each repo (network) before trusting its remote")
    p = sub.add_parser("apply", help="act on a reviewed plan")
    p.add_argument("plan")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--cloud-dir", help="override the cloud folder recorded in the plan")
    p.add_argument("--free-now", action="store_true",
                   help="delete originals right after their archive is verified (no quarantine copy; for tight disks)")
    q = sub.add_parser("purge", help="permanently delete quarantined items")
    q.add_argument("--older-than", type=int, default=0, help="days (default: everything)")
    q.add_argument("--yes", action="store_true")
    q.add_argument("--synced", action="store_true", help="I confirmed the cloud archives finished uploading")
    r = sub.add_parser("restore", help="move quarantined items back")
    r.add_argument("--batch")
    sub.add_parser("evict", help="ask iCloud to drop local copies of uploaded archives")
    u = sub.add_parser("unarchive", help="restore a cloud archive")
    u.add_argument("archive")
    u.add_argument("--to", help="destination directory (default: your home)")
    a = ap.parse_args()
    if a.cmd == "scan":
        a.keep = ["nobitex"] + [k for k in (a.keep or []) if k.lower() != "nobitex"]
        t = a.target_gb is not None
        a.stale_days = a.stale_days if a.stale_days is not None else (30 if t else 180)
        a.review_days = a.review_days if a.review_days is not None else (14 if t else 60)
        a.artifact_days = a.artifact_days if a.artifact_days is not None else (14 if t else 30)
    {"scan": scan, "apply": apply, "purge": purge, "restore": restore, "evict": evict,
     "unarchive": unarchive}[a.cmd](a)


if __name__ == "__main__":
    main()
