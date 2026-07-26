#!/usr/bin/env python3
"""Regression tests for git-wtclone's index patcher.

The patcher rewrites bytes git depends on, so the property that matters is:
anything it cannot handle must raise Unsupported (which makes the tool fall
back to `git worktree add`), never a traceback and never a silent success on
an index it misparsed.

Run: python3 bench/test_patch_index.py
"""
import importlib.machinery
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "git-wtclone")

_loader = importlib.machinery.SourceFileLoader("wtc", TOOL)
_spec = importlib.util.spec_from_loader("wtc", _loader)
wtc = importlib.util.module_from_spec(_spec)
_loader.exec_module(wtc)

TMP = tempfile.mkdtemp(prefix="wtc-test-")
fails = []


def header(version=2, count=0):
    return b"DIRC" + struct.pack(">II", version, count)


def entry(path=b"f.txt", mode=0o100644, extended=False, xflags=0):
    """A v2/v3 index entry: 10 stat ints, 20-byte oid, flags, padded path."""
    stat_block = struct.pack(">10I", 1, 0, 1, 0, 1, 1, mode, 501, 20, 4)
    flags = len(path) | (0x4000 if extended else 0)
    body = stat_block + b"\x11" * 20 + struct.pack(">H", flags)
    if extended:
        body += struct.pack(">H", xflags)
    body += path + b"\0"
    return body + b"\0" * (-len(body) % 8)


def ext(sig, payload=b""):
    return sig + struct.pack(">I", len(payload)) + payload


def rejects(name, blob, expect=None):
    """The patcher must refuse blob with Unsupported."""
    p = os.path.join(TMP, "idx")
    open(p, "wb").write(blob + b"\0" * 20)
    try:
        wtc.patch_index(p, TMP, 20)
    except wtc.Unsupported as e:
        if expect and expect not in str(e):
            fails.append(f"{name}: refused, but for the wrong reason: {e}")
        else:
            print(f"  ok    {name:<38} -> {str(e)[:44]}")
        return
    except Exception as e:
        fails.append(f"{name}: raised {type(e).__name__} instead of Unsupported ({e})")
        print(f"  FAIL  {name:<38} -> {type(e).__name__}")
        return
    fails.append(f"{name}: accepted an index it should have refused")
    print(f"  FAIL  {name:<38} -> accepted silently")


open(os.path.join(TMP, "f.txt"), "wb").write(b"data")

print("malformed / unsupported indexes must all raise Unsupported\n")
rejects("empty file", b"")
rejects("three bytes", b"DIR")
rejects("bad signature", b"XXXX" + struct.pack(">II", 2, 0))
rejects("index version 4", header(4, 1))
rejects("count larger than data", header(2, 5))
rejects("count = 2^31", header(2, 2**31))
rejects("truncated mid-entry", header(2, 1) + b"\0" * 30)
rejects("unterminated long path",
        header(2, 1) + b"\0" * 60 + b"\x0f\xff" + b"A" * 8)
rejects("extension size overflows",
        header(2, 0) + b"TREE" + struct.pack(">I", 2**31))
rejects("non-alpha extension signature",
        header(2, 0) + bytes([0, 1, 2, 3]) + struct.pack(">I", 0))
rejects("split index", header(2, 0) + ext(b"link", b"\0" * 8))
rejects("fsmonitor extension", header(2, 0) + ext(b"FSMN", b"\0" * 8))
rejects("sparse directory entry",
        header(2, 1) + entry(b"sub/", mode=0o040000))
rejects("EOIE disagrees with the walk",
        header(2, 1) + entry() + ext(b"EOIE", struct.pack(">I", 9999) + b"\0" * 20),
        expect="EOIE")
rejects("trailing junk after extensions",
        header(2, 0) + ext(b"TREE") + b"\x00\x01")

# A well-formed index whose file is absent: every entry missing trips the
# ratio guard rather than writing a wholly stale index.
rejects("all entries missing from worktree",
        header(2, 1) + entry(b"absent.txt"), expect="missing")

print("\nwell-formed index is accepted and only ctime/dev/ino/uid/gid change")
p = os.path.join(TMP, "good")
blob = header(2, 1) + entry() + b"\0" * 20
open(p, "wb").write(blob)
before = open(p, "rb").read()
total, patched, missing, skipped = wtc.patch_index(p, TMP, 20)
after = open(p, "rb").read()
print(f"  total={total} patched={patched} missing={missing} skipped={skipped}")

changed = {i for i in range(12, 12 + 40) if before[i] != after[i]}
allowed = set(range(12, 20)) | set(range(28, 36)) | set(range(40, 48))
mtime_size = set(range(20, 28)) | set(range(48, 52))
if changed - allowed:
    fails.append(f"patched bytes outside ctime/dev/ino/uid/gid: "
                 f"{sorted(changed - allowed)}")
if changed & mtime_size:
    fails.append("mtime or size was modified — this must never happen, it is "
                 "what keeps --dirty worktrees honest")
print(f"  byte offsets changed within the entry: {sorted(changed)}")
print(f"  mtime/size untouched: {not (changed & mtime_size)}")

# git itself must still accept a patched real index
print("\ngit accepts a patched index from a real repo")
repo = os.path.join(TMP, "repo")
sh = lambda *a: subprocess.run(a, cwd=repo, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.makedirs(repo)
subprocess.run(["git", "init", "-q", repo], check=True)
sh("git", "config", "user.email", "t@t"); sh("git", "config", "user.name", "t")
for n in range(20):
    open(os.path.join(repo, f"f{n}.txt"), "w").write(f"content {n}\n")
sh("git", "add", "-A"); sh("git", "commit", "-qm", "init")
total, patched, missing, skipped = wtc.patch_index(
    os.path.join(repo, ".git", "index"), repo, 20)
listed = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                        text=True, check=True).stdout.split()
status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                        capture_output=True, text=True, check=True).stdout
print(f"  patched={patched}/{total}  ls-files={len(listed)}  status={status.strip()!r}")
if len(listed) != total:
    fails.append("git could not read back all entries from the patched index")
if status.strip():
    fails.append(f"patched index made a clean tree look dirty: {status!r}")

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
