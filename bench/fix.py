#!/usr/bin/env python3
"""Corrections: isolate clonefile() from teardown cost, and report target-tree
sizes so the crossover rows are comparable."""
import os, shutil, statistics, subprocess, time

import clonelib

SRC      = clonelib.bench_repo()
BENCH    = clonelib.SCRATCH

def out(*a): return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()

print("=== thread scaling, clonefile(2) syscall time only (teardown excluded) ===")
for j in [1, 2, 4, 8, 16]:
    ts = []
    for _ in range(3):
        d = f"{BENCH}/t{j}"
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        t0 = time.perf_counter()
        clonelib.clone_into(SRC, d, jobs=j)
        ts.append(time.perf_counter() - t0)
        shutil.rmtree(d, ignore_errors=True)
    print(f"  -j{j:<3} {statistics.median(ts):.3f}s   (runs: {', '.join(f'{t:.3f}' for t in ts)})")

print("\n=== target tree sizes (why the HEAD~20000 baseline looked fast) ===")
head = out("git", "-C", SRC, "rev-parse", "HEAD")
for name in ["HEAD", "HEAD~10", "HEAD~200", "HEAD~1000", "HEAD~2500", "HEAD~5000", "HEAD~20000"]:
    sha = out("git", "-C", SRC, "rev-parse", name)
    n   = len(out("git", "-C", SRC, "ls-tree", "-r", "--name-only", sha).splitlines())
    d   = len(out("git", "-C", SRC, "diff", "--name-only", head, sha).splitlines())
    print(f"  {name:>10}  tree has {n:>6} files | {d:>6} differ from HEAD")

print("\n=== teardown cost (rm -rf of a worktree), for reference ===")
import time
for label, mk in [("plain checkout",
                   lambda d: subprocess.run(["git","-C",SRC,"worktree","add","-q","--detach",d,head],
                                            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))]:
    ts = []
    for _ in range(3):
        d = f"{BENCH}/rm-probe"
        shutil.rmtree(d, ignore_errors=True)
        subprocess.run(["git","-C",SRC,"worktree","prune"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mk(d)
        t = time.perf_counter(); shutil.rmtree(d); ts.append(time.perf_counter() - t)
        subprocess.run(["git","-C",SRC,"worktree","prune"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  {label:<18} rm -rf median {statistics.median(ts):.3f}s")
