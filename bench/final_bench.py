#!/usr/bin/env python3
"""Final comparison: git worktree add vs git-wtclone (both modes).

Reports time-to-USABLE (create + first index-refreshing command), not just
creation, and verifies every worktree against a reference checkout.
"""
import hashlib, os, shutil, statistics, subprocess, time

import clonelib

SRC   = clonelib.bench_repo()
BENCH = os.path.join(clonelib.BENCH_DIR, "final.noindex")
WTC   = clonelib.WTCLONE

def sh(*a, **k):
    return subprocess.run(a, check=k.pop("check", True), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **k)
def out(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()
def rev(r):     return out("git", "-C", SRC, "rev-parse", r)
def free_kb():  return int(out("df", "-k", BENCH).splitlines()[1].split()[3])
def teardown(d):
    shutil.rmtree(d, ignore_errors=True); sh("git", "-C", SRC, "worktree", "prune")

def manifest(root):
    h, n = hashlib.sha256(), 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(x for x in dn if x != ".git")
        for f in sorted(fn):
            p = os.path.join(dp, f); rel = os.path.relpath(p, root)
            if rel == ".git": continue
            st = os.lstat(p)
            h.update(rel.encode()); h.update(str(st.st_mode).encode())
            if os.path.islink(p): h.update(os.readlink(p).encode())
            else:
                with open(p, "rb") as fh:
                    for c in iter(lambda: fh.read(1 << 20), b""): h.update(c)
            n += 1
    return h.hexdigest(), n

VARIANTS = {
    "git worktree add": lambda d, t: sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, t),
    "wtclone":          lambda d, t: sh(WTC, "-q", "--detach", d, t, cwd=SRC),
    "wtclone --exact":  lambda d, t: sh(WTC, "-q", "--exact", "--detach", d, t, cwd=SRC),
}

os.makedirs(BENCH, exist_ok=True)
while float(out("sysctl", "-n", "vm.loadavg").split()[1]) > 2.0:
    time.sleep(5)

targets = [("same", rev("HEAD"), 0), ("HEAD~10", rev("HEAD~10"), 946),
           ("HEAD~200", rev("HEAD~200"), 9669), ("HEAD~1000", rev("HEAD~1000"), 23517)]

ref = {}
for name, sha, _ in targets:
    d = f"{BENCH}/ref-{name}"; teardown(d)
    sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, sha)
    ref[name] = manifest(d); teardown(d)

print(f"{'delta':>10} {'variant':<18} {'create':>8} {'1st status':>11} "
      f"{'usable':>8} {'disk':>9}  ok")
print("-" * 76)
for tname, sha, nfiles in targets:
    for vname, fn in VARIANTS.items():
        creates, statuses, disk = [], [], None
        for i in range(4):
            d = f"{BENCH}/{vname.replace(' ','_').replace('-','')}-{tname}"
            teardown(d)                     # outside the clock
            time.sleep(0.5)
            before = free_kb()
            t = time.perf_counter(); fn(d, sha); c = time.perf_counter() - t
            t = time.perf_counter(); sh("git", "-C", d, "status"); s = time.perf_counter() - t
            if i == 1:
                after = free_kb(); disk = (before - after) / 1024
                ok = (out("git","-C",d,"rev-parse","HEAD") == sha
                      and out("git","-C",d,"status","--porcelain") == ""
                      and manifest(d) == ref[tname])
            if i >= 1:
                creates.append(c); statuses.append(s)
            teardown(d)
        c, s = statistics.median(creates), statistics.median(statuses)
        print(f"{nfiles:>10} {vname:<18} {c:7.3f}s {s:10.3f}s {c+s:7.3f}s "
              f"{disk:8.1f}M  {'OK' if ok else 'FAIL'}")

sh("git", "-C", SRC, "config", "--unset", "extensions.worktreeConfig", check=False)
shutil.rmtree(BENCH, ignore_errors=True)
