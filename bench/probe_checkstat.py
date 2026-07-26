#!/usr/bin/env python3
"""What does core.checkStat=minimal actually stop detecting?

Each scenario mutates a tracked file so that whole-second mtime and size are
preserved -- the only two fields `minimal` still compares -- and asks whether
git notices, under both settings. Also checks whether `git commit -a` would
silently commit stale content.
"""
import os, shutil, subprocess, tempfile

ROOT = tempfile.mkdtemp(prefix="checkstat-")

def sh(*a, cwd=None, check=True):
    return subprocess.run(a, cwd=cwd, check=check, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
def out(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()

def fresh(mode):
    """New repo with one tracked 64-byte file, committed. mode = default|minimal."""
    d = os.path.join(ROOT, mode + str(len(os.listdir(ROOT))))
    os.makedirs(d)
    sh("git", "init", "-q", d)
    sh("git", "-C", d, "config", "user.email", "b@b"); sh("git", "-C", d, "config", "user.name", "b")
    sh("git", "-C", d, "config", "core.checkStat", mode)
    p = os.path.join(d, "f.txt")
    open(p, "wb").write(b"A" * 64)
    sh("git", "-C", d, "add", "f.txt"); sh("git", "-C", d, "commit", "-qm", "init")
    sh("git", "-C", d, "status")               # settle the index stat cache
    return d, p

def dirty(d):
    return out("git", "-C", d, "status", "--porcelain") != ""

def scenario_edit_restore_mtime(d, p):
    """Same size, content changed, mtime put back (rsync -t / cp -p / touch -r)."""
    st = os.stat(p)
    open(p, "wb").write(b"B" * 64)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))

def scenario_replace_inode(d, p):
    """File swapped for a *different inode* with mtime preserved (mv/rsync)."""
    st = os.stat(p)
    tmp = p + ".new"
    open(tmp, "wb").write(b"C" * 64)
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, p)

def scenario_same_second(d, p):
    """Content changed within the same wall-clock second, no timestamp forging."""
    st = os.stat(p)
    open(p, "wb").write(b"D" * 64)
    # keep the whole second, advance only the sub-second part
    os.utime(p, ns=(st.st_atime_ns, (st.st_mtime_ns // 10**9) * 10**9 + 500_000_000))

def scenario_natural_edit(d, p):
    """Ordinary edit, same size, mtime allowed to advance normally."""
    open(p, "wb").write(b"E" * 64)
    os.utime(p, ns=(os.stat(p).st_atime_ns, os.stat(p).st_mtime_ns + 3 * 10**9))

SCENARIOS = [
    ("ordinary edit (mtime advances)", scenario_natural_edit),
    ("edit + mtime restored  (cp -p, rsync -t)", scenario_edit_restore_mtime),
    ("replaced inode + mtime restored (mv/rsync)", scenario_replace_inode),
    ("edit within the same second", scenario_same_second),
]

print(f"git: {out('git','--version')}\n")
print(f"{'scenario':<44} {'default':>9} {'minimal':>9}")
print("-" * 66)
miss = []
for name, fn in SCENARIOS:
    row = {}
    for mode in ("default", "minimal"):
        d, p = fresh(mode)
        fn(d, p)
        row[mode] = "detected" if dirty(d) else "MISSED"
        if row[mode] == "MISSED":
            miss.append((name, mode, d, p))
    print(f"{name:<44} {row['default']:>9} {row['minimal']:>9}")

print("\n=== consequence of a MISS: does `git commit -a` capture the new content? ===")
for name, mode, d, p in miss:
    sh("git", "-C", d, "commit", "-aqm", "attempt", check=False)
    committed = out("git", "-C", d, "show", "HEAD:f.txt", cwd=d)
    onfile = open(p).read()
    print(f"  [{mode}] {name}")
    print(f"      on disk={onfile[:8]!r}  committed={committed[:8]!r}  "
          f"-> {'STALE CONTENT COMMITTED' if onfile != committed else 'ok'}")

print("\n=== recovery: which commands re-detect a missed change? ===")
for name, mode, d, p in miss[:1]:
    pass
d, p = fresh("minimal"); scenario_edit_restore_mtime(d, p)
print(f"  baseline (missed):                      dirty={dirty(d)}")
sh("git", "-C", d, "update-index", "--refresh", check=False)
print(f"  after `update-index --refresh`:          dirty={dirty(d)}")
sh("git", "-C", d, "update-index", "--really-refresh", check=False)
print(f"  after `update-index --really-refresh`:   dirty={dirty(d)}")
sh("git", "-C", d, "add", "--renormalize", ".", check=False)
print(f"  after `git add --renormalize .`:         dirty={dirty(d)}")
sh("git", "-C", d, "-c", "core.checkStat=default", "update-index", "--really-refresh", check=False)
print(f"  after really-refresh w/ checkStat=default: dirty={dirty(d)}")

shutil.rmtree(ROOT, ignore_errors=True)
