#!/usr/bin/env python3
"""Design probe: can `update-index --refresh` permanently repair the stale
inode/ctime data in an index copied from another worktree? (It cannot.)"""
import os, shutil, subprocess, time

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

HEAD = out("git", "-C", SRC, "rev-parse", "HEAD")

# --------------------------------------------- index repair after a clone
print("=== can the stale-inode index be permanently repaired? ===")

def build(dst):
    teardown(dst)
    sh("git", "-C", SRC, "worktree", "add", "--no-checkout", "--detach", dst, HEAD)
    clonelib.clone_into(SRC, dst)
    admin = out("git", "-C", dst, "rev-parse", "--absolute-git-dir")
    sh("cp", "-c", f"{SRC}/.git/index", f"{admin}/index")
    sh("git", "-C", dst, "-c", "core.checkStat=minimal",
       "checkout", "--no-progress", "--detach", HEAD)

def timed(*a):
    t = time.perf_counter(); sh(*a); return time.perf_counter() - t

d = f"{BENCH}/repair"

build(d)
print(f"  (a) no repair, first `git status` (default checkStat): {timed('git','-C',d,'status'):.3f}s")
print(f"      second `git status`:                               {timed('git','-C',d,'status'):.3f}s")
teardown(d)

build(d)
r = timed("git", "-C", d, "-c", "core.checkStat=minimal", "update-index", "--refresh")
print(f"  (b) update-index --refresh (checkStat=minimal):        {r:.3f}s")
print(f"      then `git status` (default checkStat):             {timed('git','-C',d,'status'):.3f}s")
print(f"      and again:                                         {timed('git','-C',d,'status'):.3f}s")
print(f"      still clean? '{out('git','-C',d,'status','--porcelain')}'")
teardown(d)

# baseline for reference: what does status cost on a normal worktree?
d2 = f"{BENCH}/normal"; teardown(d2)
sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d2, HEAD)
sh("git", "-C", d2, "status")
print(f"\n  reference: `git status` on a plain checkout:           {timed('git','-C',d2,'status'):.3f}s")
teardown(d2)
