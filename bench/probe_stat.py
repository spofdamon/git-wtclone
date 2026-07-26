#!/usr/bin/env python3
"""The deferred-rehash problem: creation speed is not the whole story.
Measure time-to-USABLE worktree (create + first index-refreshing command)."""
import os, shutil, statistics, subprocess, time

import clonelib

SRC   = clonelib.bench_repo()
BENCH = clonelib.SCRATCH

def sh(*a, **kw):
    return subprocess.run(a, check=kw.pop("check", True), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **kw)
def out(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()
def teardown(d):
    shutil.rmtree(d, ignore_errors=True); sh("git", "-C", SRC, "worktree", "prune")
def timed(*a):
    t = time.perf_counter(); sh(*a); return time.perf_counter() - t

HEAD = out("git", "-C", SRC, "rev-parse", "HEAD")
TGT  = out("git", "-C", SRC, "rev-parse", "HEAD~10")

def build_clone(dst, persist_checkstat):
    # NB: caller must tear down BEFORE starting the clock — rm -rf is ~1.9s and
    # would otherwise be charged to the clone but not to the baseline.
    sh("git", "-C", SRC, "worktree", "add", "--no-checkout", "--detach", dst, HEAD)
    clonelib.clone_into(SRC, dst)
    admin = out("git", "-C", dst, "rev-parse", "--absolute-git-dir")
    sh("cp", "-c", f"{SRC}/.git/index", f"{admin}/index")
    if persist_checkstat:
        # per-worktree config, so other worktrees keep git's default behaviour
        sh("git", "-C", SRC, "config", "extensions.worktreeConfig", "true")
        sh("git", "-C", dst, "config", "--worktree", "core.checkStat", "minimal")
    sh("git", "-C", dst, "-c", "core.checkStat=minimal",
       "checkout", "--no-progress", "--detach", TGT)

print("=== time to a USABLE worktree (create + first `git status`) ===\n")

d = f"{BENCH}/u"

# --- baseline
ts = []
for _ in range(3):
    teardown(d)
    t = time.perf_counter()
    sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, TGT)
    c = time.perf_counter() - t
    s = timed("git", "-C", d, "status")
    ts.append((c, s))
teardown(d)
c, s = [statistics.median(x) for x in zip(*ts)]
print(f"  baseline worktree add      create {c:.3f}s + first status {s:.3f}s = {c+s:.3f}s")

# --- clone, no persistent config
ts = []
for _ in range(3):
    teardown(d)
    t = time.perf_counter(); build_clone(d, False); c = time.perf_counter() - t
    s = timed("git", "-C", d, "status")
    ts.append((c, s))
teardown(d)
c, s = [statistics.median(x) for x in zip(*ts)]
print(f"  clone, default checkStat   create {c:.3f}s + first status {s:.3f}s = {c+s:.3f}s")

# --- clone, persistent per-worktree checkStat=minimal
ts = []
for _ in range(3):
    teardown(d)
    t = time.perf_counter(); build_clone(d, True); c = time.perf_counter() - t
    s = timed("git", "-C", d, "status")
    s2 = timed("git", "-C", d, "status")
    ts.append((c, s, s2))
c, s, s2 = [statistics.median(x) for x in zip(*ts)]
print(f"  clone, checkStat=minimal   create {c:.3f}s + first status {s:.3f}s = {c+s:.3f}s"
      f"   (2nd status {s2:.3f}s)")
print(f"  correctness: status clean? '{out('git','-C',d,'status','--porcelain')}'  "
      f"HEAD={out('git','-C',d,'rev-parse','--short','HEAD')}")
teardown(d)

# does minimal miss a real edit?
teardown(d); build_clone(d, True)
p = os.path.join(d, "README.md")
data = open(p, "rb").read()
st = os.stat(p)
open(p, "wb").write(data.replace(b"Rust", b"RUST", 1))   # same size, new mtime
print(f"\n  same-size edit, natural mtime -> detected? "
      f"{'YES' if out('git','-C',d,'status','--porcelain') else 'NO'}")
os.utime(p, (st.st_atime, st.st_mtime))                  # forge mtime back
print(f"  same-size edit, mtime forged back -> detected? "
      f"{'YES' if out('git','-C',d,'status','--porcelain') else 'NO'}  <- the tradeoff")
teardown(d)
sh("git", "-C", SRC, "config", "--unset", "extensions.worktreeConfig", check=False)
