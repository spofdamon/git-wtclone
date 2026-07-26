"""Shared clonefile(2) helper for the benchmark scripts.

ctypes into libSystem; the benchmarks measured this at 0.442s against 0.431s for
an equivalent compiled helper, so there is no reason to build anything.
"""
import ctypes
import os
import threading

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
_libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
_libc.clonefile.restype = ctypes.c_int

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.join(BENCH_DIR, "run.noindex")
WTCLONE = os.path.join(os.path.dirname(BENCH_DIR), "git-wtclone")


def bench_repo():
    """The checkout to benchmark against.

    $WTCLONE_BENCH_REPO, else ~/git/rust. The published numbers come from
    rust-lang/rust; any large repo works, though the win tracks file count.
    """
    path = os.environ.get("WTCLONE_BENCH_REPO") or "~/git/rust"
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(os.path.join(path, ".git")):
        raise SystemExit(
            f"{path} is not a git checkout — set WTCLONE_BENCH_REPO to one "
            f"(e.g. a clone of https://github.com/rust-lang/rust)")
    return path


def clonefile(src, dst):
    """clonefile(2). Destination must not exist; directories recurse in-kernel."""
    if _libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), src, None, dst)


def clone_into(src, dst, exclude=(".git",), jobs=1):
    """Clone each top-level entry of src into an existing dst.

    jobs>1 spreads the entries over threads; ctypes releases the GIL across the
    foreign call, so this is a real parallelism test. It does not help — the
    kernel already saturates the APFS metadata path from a single thread.
    """
    names = [n for n in os.listdir(src) if n not in exclude]
    if jobs <= 1:
        for n in names:
            clonefile(os.path.join(src, n), os.path.join(dst, n))
        return len(names)

    lock, idx, err = threading.Lock(), [0], []

    def worker():
        while True:
            with lock:
                if idx[0] >= len(names):
                    return
                n = names[idx[0]]
                idx[0] += 1
            try:
                clonefile(os.path.join(src, n), os.path.join(dst, n))
            except OSError as e:
                err.append(e)

    threads = [threading.Thread(target=worker) for _ in range(min(jobs, len(names)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if err:
        raise err[0]
    return len(names)
