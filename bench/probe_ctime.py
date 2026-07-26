#!/usr/bin/env python3
"""Airtight: which stat fields does each checkStat mode actually compare?

Uses a FIXED mtime constant (not a recomputed now-3600, which silently shifted
mtime between writes in earlier attempts) and prints the index-vs-disk field
diff alongside the detection result, so the cause of any detection is visible.
"""
import os, re, shutil, subprocess, tempfile, time

ROOT = tempfile.mkdtemp(prefix="ctime-")
OLD  = 1700000000          # fixed absolute epoch; never recomputed

def sh(*a, check=True):
    return subprocess.run(a, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def out(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()

def index_fields(d, f):
    t = out("git", "-C", d, "ls-files", "--debug", f)
    g = lambda k: re.search(rf"{k}: (\d+)", t).group(1)
    return {"ctime": g("ctime"), "mtime": g("mtime"), "ino": g("ino"), "size": g("size")}

def disk_fields(p):
    st = os.lstat(p)
    return {"ctime": str(int(st.st_ctime)), "mtime": str(int(st.st_mtime)),
            "ino": str(st.st_ino), "size": str(st.st_size)}

def run(checkstat, trustctime, mutate, label):
    d = os.path.join(ROOT, f"{checkstat}-{trustctime}-{len(os.listdir(ROOT))}")
    os.makedirs(d)
    sh("git", "init", "-q", d)
    for k, v in [("user.email","b@b"), ("user.name","b"),
                 ("core.checkStat",checkstat), ("core.trustctime",str(trustctime).lower())]:
        sh("git", "-C", d, "config", k, v)
    p = os.path.join(d, "f.txt")
    open(p, "wb").write(b"A" * 64)
    os.utime(p, (OLD, OLD))
    sh("git", "-C", d, "add", "f.txt"); sh("git", "-C", d, "commit", "-qm", "i")
    sh("git", "-C", d, "status")
    assert out("git","-C",d,"status","--porcelain") == "", "must start clean"

    time.sleep(2)                       # guarantee the ctime SECOND moves
    mutate(p)

    idx, dsk = index_fields(d, "f.txt"), disk_fields(p)
    diff = [k for k in idx if idx[k] != dsk[k]]
    det  = out("git", "-C", d, "status", "--porcelain") != ""
    print(f"  checkStat={checkstat:<8} trustctime={str(trustctime):<5} "
          f"fields differing: {','.join(diff) or 'none':<12} -> "
          f"{'DETECTED' if det else 'MISSED'}")
    return det

def inplace(p):                          # same size, mtime restored exactly
    open(p, "wb").write(b"B" * 64); os.utime(p, (OLD, OLD))

def replaced(p):                         # new inode, mtime restored exactly
    t = p + ".n"; open(t, "wb").write(b"C" * 64); os.utime(t, (OLD, OLD)); os.replace(t, p)

print(f"git: {out('git','--version')}   (fixed mtime constant = {OLD})\n")
print("--- in-place rewrite: only ctime moves --------------------------------")
for cs in ("default", "minimal"):
    for tc in (True, False):
        run(cs, tc, inplace, "inplace")

print("\n--- replaced inode: ctime AND ino move --------------------------------")
for cs in ("default", "minimal"):
    for tc in (True, False):
        run(cs, tc, replaced, "replaced")

shutil.rmtree(ROOT, ignore_errors=True)
