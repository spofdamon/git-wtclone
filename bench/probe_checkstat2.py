#!/usr/bin/env python3
"""core.checkStat=minimal, properly isolated from git's racy-index logic.

Files are back-dated well before the index timestamp, otherwise git content-
compares every entry as "racily clean" and the checkStat setting never shows.
"""
import os, shutil, subprocess, tempfile, time

ROOT = tempfile.mkdtemp(prefix="cs2-")
OLD  = time.time() - 3600          # 1h ago: safely outside the racy window

def sh(*a, check=True):
    return subprocess.run(a, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def out(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()

def fresh(mode):
    d = os.path.join(ROOT, f"{mode}{len(os.listdir(ROOT))}")
    os.makedirs(d)
    sh("git", "init", "-q", d)
    for k, v in [("user.email","b@b"),("user.name","b"),("core.checkStat",mode)]:
        sh("git", "-C", d, "config", k, v)
    p = os.path.join(d, "f.txt")
    open(p, "wb").write(b"A" * 64)
    os.utime(p, (OLD, OLD))                       # back-date BEFORE indexing
    sh("git", "-C", d, "add", "f.txt")
    sh("git", "-C", d, "commit", "-qm", "init")
    sh("git", "-C", d, "status")                  # settle stat cache
    assert out("git","-C",d,"status","--porcelain") == "", "should start clean"
    return d, p

def dirty(d): return out("git", "-C", d, "status", "--porcelain") != ""

def edit_restore_mtime(p):
    """In-place rewrite, same size, mtime restored. Only ctime changes."""
    open(p, "wb").write(b"B" * 64)
    os.utime(p, (OLD, OLD))

def replace_inode(p):
    """Swapped for a different inode, mtime restored. ctime AND ino change."""
    tmp = p + ".n"; open(tmp, "wb").write(b"C" * 64); os.utime(tmp, (OLD, OLD))
    os.replace(tmp, p)

def natural_edit(p):
    """Ordinary edit; mtime advances to now."""
    open(p, "wb").write(b"E" * 64)

def size_change(p):
    open(p, "wb").write(b"F" * 65)

SCEN = [("ordinary edit (mtime advances)",              natural_edit),
        ("size change",                                  size_change),
        ("rewrite + mtime restored  (only ctime moves)", edit_restore_mtime),
        ("replaced inode + mtime restored (ctime+ino)",  replace_inode)]

print(f"git: {out('git','--version')}\n")
print(f"{'scenario':<46} {'default':>9} {'minimal':>9}")
print("-" * 68)
missed = []
for name, fn in SCEN:
    row = {}
    for mode in ("default", "minimal"):
        d, p = fresh(mode); fn(p)
        row[mode] = "detected" if dirty(d) else "MISSED"
        if row[mode] == "MISSED": missed.append((name, mode, d, p))
    print(f"{name:<46} {row['default']:>9} {row['minimal']:>9}")

print("\n=== consequence of a MISS ===")
for name, mode, d, p in missed:
    sh("git", "-C", d, "commit", "-aqm", "attempt", check=False)
    committed = out("git", "-C", d, "show", "HEAD:f.txt")
    disk = open(p).read()
    print(f"  [{mode}] {name}")
    print(f"     on disk {disk[:4]!r}... / committed {committed[:4]!r}... -> "
          f"{'STALE CONTENT SILENTLY COMMITTED' if disk != committed else 'ok'}")

print("\n=== recovery from a missed change (checkStat=minimal repo) ===")
d, p = fresh("minimal"); edit_restore_mtime(p)
print(f"  missed initially:                          dirty={dirty(d)}")
for label, cmd in [("update-index --refresh",        ["git","-C",d,"update-index","--refresh"]),
                   ("update-index --really-refresh", ["git","-C",d,"update-index","--really-refresh"]),
                   ("checkStat=default status",      ["git","-C",d,"-c","core.checkStat=default","status"]),
                   ("git add --renormalize .",       ["git","-C",d,"add","--renormalize","."])]:
    sh(*cmd, check=False)
    print(f"  after {label:<32} dirty={dirty(d)}")

shutil.rmtree(ROOT, ignore_errors=True)
