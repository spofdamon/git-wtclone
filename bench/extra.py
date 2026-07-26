#!/usr/bin/env python3
"""Follow-up measurements: crossover point, thread scaling, disk cost, and the
untracked-build-artifact case that plain `git worktree add` cannot do at all."""
import json, os, random, shutil, subprocess, time

import clonelib

SRC      = clonelib.bench_repo()
BENCH    = clonelib.SCRATCH
ART      = f"{SRC}/target"          # gitignored in rust's .gitignore

def sh(*a, **kw):
    return subprocess.run(a, check=kw.pop("check", True), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **kw)
def out(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout.strip()
def rev(r):       return out("git", "-C", SRC, "rev-parse", r)
def admin(d):     return out("git", "-C", d, "rev-parse", "--absolute-git-dir")
def free_kb():    return int(out("df", "-k", BENCH).splitlines()[1].split()[3])
def du_mb(p):     return int(out("du", "-sk", p).split()[0]) / 1024

def teardown(d):
    shutil.rmtree(d, ignore_errors=True); sh("git", "-C", SRC, "worktree", "prune")

def clone_worktree(dst, target, jobs=8):
    src_ref = rev("HEAD")
    sh("git", "-C", SRC, "worktree", "add", "--no-checkout", "--detach", dst, src_ref)
    clonelib.clone_into(SRC, dst, jobs=jobs)
    sh("cp", "-c", f"{SRC}/.git/index", f"{admin(dst)}/index")
    sh("git", "-C", dst, "-c", "core.checkStat=minimal",
       "checkout", "--no-progress", "--detach", target)

def timeit(fn, reps=3):
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return sorted(ts)[len(ts) // 2]

# ---------------------------------------------------------------- 1. crossover
print("=== 1. crossover: does the clone approach lose on very large deltas? ===", flush=True)
for name in ["HEAD~2500", "HEAD~5000", "HEAD~20000"]:
    sha = rev(name)
    n = len(out("git", "-C", SRC, "diff", "--name-only", "HEAD", sha).splitlines())
    def base():
        d = f"{BENCH}/x-base"; teardown(d)
        sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, sha); teardown(d)
    def clone():
        d = f"{BENCH}/x-clone"; teardown(d)
        clone_worktree(d, sha); teardown(d)
    b, c = timeit(base), timeit(clone)
    print(f"{name:>10} {n:>6} files  baseline={b:.3f}s  clone={c:.3f}s  "
          f"{'CLONE WINS' if c < b else 'BASELINE WINS'} ({b/c:.2f}x)", flush=True)

# ---------------------------------------------------------------- 2. threads
print("\n=== 2. clonefile thread scaling (uncontaminated) ===", flush=True)
for j in [1, 2, 4, 8, 16]:
    def f(j=j):
        d = f"{BENCH}/t{j}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        clonelib.clone_into(SRC, d, jobs=j); shutil.rmtree(d, ignore_errors=True)
    print(f"  -j{j:<3} {timeit(f):.3f}s", flush=True)

# ---------------------------------------------------------------- 3. disk cost
print("\n=== 3. disk cost of a worktree (df delta vs what du reports) ===", flush=True)
for label, mk in [("baseline_checkout",
                   lambda d: sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, rev("HEAD"))),
                  ("clonefile", lambda d: clone_worktree(d, rev("HEAD")))]:
    d = f"{BENCH}/space-{label}"; teardown(d)
    time.sleep(2); before = free_kb()
    mk(d)
    time.sleep(2); after = free_kb()
    print(f"  {label:<18} du reports {du_mb(d):7.1f} MiB | "
          f"actual disk consumed {(before-after)/1024:7.1f} MiB", flush=True)
    teardown(d)

# ------------------------------------------------ 4. untracked build artifacts
print("\n=== 4. untracked build artifacts (what git cannot carry over at all) ===", flush=True)
shutil.rmtree(ART, ignore_errors=True)
os.makedirs(f"{ART}/debug/deps", exist_ok=True)
rnd = random.Random(1)
blob = os.urandom(1 << 20)
tot = 0
for i in range(12000):                                   # many small artifacts
    p = f"{ART}/debug/deps/unit-{i}.{'d' if i%3 else 'rmeta'}"
    n = rnd.randint(1024, 48*1024)
    with open(p, "wb") as f: f.write(blob[:n]); tot += n
for i in range(150):                                     # medium rlibs
    with open(f"{ART}/debug/deps/lib{i}.rlib", "wb") as f:
        for _ in range(4): f.write(blob)
        tot += 4 << 20
for i in range(16):                                      # large binaries
    with open(f"{ART}/debug/bin{i}", "wb") as f:
        for _ in range(32): f.write(blob)
        tot += 32 << 20
print(f"  synthesized {tot/(1<<20):.0f} MiB across "
      f"{sum(len(fs) for _,_,fs in os.walk(ART))} files in {ART}", flush=True)
assert out("git", "-C", SRC, "status", "--porcelain") == "", "artifacts must stay ignored"

def base_plus(copyflag):
    d = f"{BENCH}/art"; teardown(d)
    sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", d, rev("HEAD"))
    sh("cp", *( ["-c"] if copyflag else [] ), "-R", ART, d + "/")
    teardown(d)
def clone_all():
    d = f"{BENCH}/art"; teardown(d)
    clone_worktree(d, rev("HEAD")); teardown(d)

print(f"  worktree add + cp -R  (real copy)   {timeit(lambda: base_plus(False)):.3f}s", flush=True)
print(f"  worktree add + cp -c -R (clone cp)  {timeit(lambda: base_plus(True)):.3f}s", flush=True)
print(f"  clonefile worktree (artifacts free) {timeit(clone_all):.3f}s", flush=True)

d = f"{BENCH}/art-verify"; teardown(d); clone_worktree(d, rev("HEAD"))
got = sum(len(fs) for _, _, fs in os.walk(f"{d}/target"))
print(f"  verify: cloned worktree contains {got} artifact files under target/", flush=True)
teardown(d)

shutil.rmtree(ART, ignore_errors=True)
print("\ncleaned up synthetic artifacts; src status: "
      f"'{out('git','-C',SRC,'status','--porcelain')}'", flush=True)
