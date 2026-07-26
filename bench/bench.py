#!/usr/bin/env python3
"""Benchmark: APFS clonefile(2) as a substitute for git's worktree checkout.

Compares `git worktree add` against "clone a sibling worktree, then let git
apply only the commit delta", across a range of delta sizes.
"""
import hashlib, json, os, shutil, subprocess, sys, time

import clonelib

SRC      = clonelib.bench_repo()
BENCH    = clonelib.SCRATCH
WARMUP, TIMED = 1, 3

def sh(*args, **kw):
    return subprocess.run(args, check=kw.pop("check", True),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

def out(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()

def rev(r):        return out("git", "-C", SRC, "rev-parse", r)
def admin_dir(d):  return out("git", "-C", d, "rev-parse", "--absolute-git-dir")
def free_kb():     return int(out("df", "-k", BENCH).splitlines()[1].split()[3])
def loadavg():     return float(out("sysctl", "-n", "vm.loadavg").split()[1])

def wait_idle(threshold=2.0, limit=180):
    """Don't start timing while something else is hammering the machine."""
    t0 = time.time()
    while loadavg() > threshold and time.time() - t0 < limit:
        time.sleep(5)
    print(f"[idle] 1-min load {loadavg():.2f}", flush=True)

def teardown(dst):
    shutil.rmtree(dst, ignore_errors=True)
    sh("git", "-C", SRC, "worktree", "prune")

# ---------------------------------------------------------------- variants

def v_baseline(dst, target, workers=None):
    cmd = ["git", "-C", SRC]
    if workers is not None:
        cmd += ["-c", f"checkout.workers={workers}"]
    cmd += ["worktree", "add", "-q", "--detach", dst, target]
    t = time.perf_counter(); sh(*cmd)
    return {"checkout": time.perf_counter() - t}

def v_clone(dst, target, mode="syscall", jobs=1, copy_index=True, checkstat="minimal"):
    steps, src_ref = {}, rev("HEAD")

    t = time.perf_counter()
    sh("git", "-C", SRC, "worktree", "add", "--no-checkout", "--detach", dst, src_ref)
    steps["worktree_add"] = time.perf_counter() - t

    t = time.perf_counter()
    if mode == "syscall":
        clonelib.clone_into(SRC, dst, jobs=jobs)
    else:  # BSD cp -c, per-entry userspace walk
        entries = [os.path.join(SRC, e) for e in os.listdir(SRC) if e != ".git"]
        sh("cp", "-c", "-R", *entries, dst + "/")
    steps["clone_tree"] = time.perf_counter() - t

    t = time.perf_counter()
    if copy_index:
        sh("cp", "-c", f"{SRC}/.git/index", f"{admin_dir(dst)}/index")
    steps["copy_index"] = time.perf_counter() - t

    t = time.perf_counter()
    sh("git", "-C", dst, "-c", f"core.checkStat={checkstat}",
       "checkout", "--no-progress", "--detach", target)
    steps["delta_checkout"] = time.perf_counter() - t
    return steps

VARIANTS = {
    "baseline_seq":       lambda d, t: v_baseline(d, t),
    "baseline_par":       lambda d, t: v_baseline(d, t, workers=0),
    "clone_cp":           lambda d, t: v_clone(d, t, mode="cp"),
    "clone_syscall":      lambda d, t: v_clone(d, t, mode="syscall"),
    "clone_par":          lambda d, t: v_clone(d, t, mode="syscall", jobs=8),
    "clone_fullstat":     lambda d, t: v_clone(d, t, jobs=8, checkstat="default"),
    # NB: copy_index=False is not a slower variant, it is a broken one — git sees
    # every cloned file as untracked and refuses the checkout. Demonstrated
    # separately; excluded here because it cannot produce a timing.
}

# ---------------------------------------------------------------- checking

def manifest(root):
    """Independent content digest of a working tree, ignoring .git."""
    h, n = hashlib.sha256(), 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            p   = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if rel == ".git":
                continue
            st = os.lstat(p)
            h.update(rel.encode()); h.update(str(st.st_mode).encode())
            if os.path.islink(p):
                h.update(os.readlink(p).encode())
            else:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            n += 1
    return h.hexdigest(), n

def verify(dst, target):
    head  = out("git", "-C", dst, "rev-parse", "HEAD")
    dirty = out("git", "-C", dst, "status", "--porcelain")
    return {"head_ok": head == target, "clean": dirty == "", "dirty_sample": dirty[:400]}

# ---------------------------------------------------------------- driver

def main():
    os.makedirs(BENCH, exist_ok=True)
    wait_idle()
    targets = [("same",     rev("HEAD")),
               ("HEAD~10",  rev("HEAD~10")),
               ("HEAD~200", rev("HEAD~200")),
               ("HEAD~1000",rev("HEAD~1000"))]

    # Reference manifest from a plain git checkout, per target.
    ref = {}
    for name, sha in targets:
        dst = f"{BENCH}/ref-{name}"
        teardown(dst)
        sh("git", "-C", SRC, "worktree", "add", "-q", "--detach", dst, sha)
        ref[name] = manifest(dst)
        print(f"[ref] {name} {sha[:12]}  files={ref[name][1]}  {ref[name][0][:16]}", flush=True)
        teardown(dst)

    results = []
    for tname, sha in targets:
        for vname, fn in VARIANTS.items():
            times, steps_all, checked = [], [], None
            load_at_start = loadavg()
            for i in range(WARMUP + TIMED):
                dst = f"{BENCH}/{vname}-{tname}-{i}"
                teardown(dst)
                t0 = time.perf_counter()
                try:
                    steps = fn(dst, sha)
                except subprocess.CalledProcessError as e:
                    print(f"{tname:>10} {vname:<16} ERROR: {e}", flush=True)
                    times, steps_all = [], []
                    break
                total = time.perf_counter() - t0
                if i == WARMUP:                       # verify one real run
                    checked = verify(dst, sha)
                    dig, n = manifest(dst)
                    checked["content_match"] = (dig == ref[tname][0])
                    checked["files"] = n
                if i >= WARMUP:
                    times.append(total); steps_all.append(steps)
                teardown(dst)
            if not times:
                results.append({"target": tname, "variant": vname, "error": True})
                continue
            med = sorted(times)[len(times) // 2]
            agg = {k: sorted(s[k] for s in steps_all)[len(steps_all) // 2]
                   for k in steps_all[0]}
            row = {"target": tname, "variant": vname, "median": med,
                   "min": min(times), "max": max(times), "steps": agg,
                   "check": checked, "load": load_at_start}
            results.append(row)
            ok = "OK " if (checked["clean"] and checked["head_ok"]
                           and checked["content_match"]) else "FAIL"
            print(f"{tname:>10} {vname:<16} {med:7.3f}s  "
                  f"[{min(times):.3f}-{max(times):.3f}]  {ok} load={load_at_start:.1f}",
                  flush=True)

    with open(os.path.join(clonelib.BENCH_DIR, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("\nwrote results.json")

if __name__ == "__main__":
    main()
