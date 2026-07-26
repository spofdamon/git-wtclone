# git-wtclone

Create git worktrees with APFS `clonefile(2)` instead of a full checkout.

Rather than materialising every tracked file from the object database,
`git-wtclone` clones an existing worktree's directory tree — one `clonefile(2)`
per top-level entry, which the kernel recurses — and lets git apply only the
commit delta. The cost model goes from `O(whole tree)` to `constant + O(diff)`.

Single Python file, no dependencies, no build step. macOS + APFS only; it falls
back to `git worktree add` everywhere else.

## Install

```sh
cp git-wtclone /usr/local/bin/     # anywhere on PATH
git wtclone --help
```

## Usage

```sh
git wtclone ../feature-x                  # new branch "feature-x" off HEAD
git wtclone ../hotfix main                # check out an existing branch
git wtclone -b exp ../exp origin/main     # new branch from a start point
git wtclone --detach ../poke HEAD~5
git wtclone --exact ../feature-x          # no config change (see below)
git wtclone --clean ../feature-x          # tracked files only
git wtclone -v ../feature-x               # per-step timings
```

Untracked and ignored files (`target/`, `node_modules/`, `.venv/`) come along by
default at zero disk cost — something git worktree can't do, since those files
aren't in the object database. Use `--clean` to opt out. Note this also carries
`.env` files and stale build artifacts.

## The two modes

Cloned files get new inodes and ctimes, which the copied index doesn't expect.
Left alone, the first git command that refreshes the index re-hashes all of it —
on the rust repo that's a 4-second penalty wipes out the savings.

By default wtclone persists `core.checkStat=minimal` into the new worktree (per-worktree
via `extensions.worktreeConfig`, so other worktrees keep git's default). Git then
compares only whole-second mtime and size.

Passing `--exact` rewrites the copied index's ctime/dev/ino/uid/gid from a fresh
`lstat` pass and recomputes the trailing checksum. No config change, no relaxed
checking. Costs ~0.2s more.

### Downside of `core.checkStat=minimal`

It stops comparing ctime, inode, uid/gid, and sub-second mtime. Any change that
preserves **whole-second mtime and size** becomes invisible in that worktree —
`rsync -t`, `cp -p`, `tar -p`, cache restoration. Consequences, in severity
order: `git checkout` silently overwrites the change with no warning;
`git commit -a` commits stale content; `git diff` shows nothing.

Git's racy-index logic still catches the common accidental case (a write landing
in the same second the index was written). You can recover from a missed change with
`git add --renormalize .`. (`update-index --refresh` doesn't work.)

Note the git man page states `minimal` keeps whole-second ctime "if
core.trustCtime is set". It does not; ctime is dropped entirely regardless.
That discrepancy is why this technique works.

## Benchmarks

Run against a checkout of [rust-lang/rust](https://github.com/rust-lang/rust) at
`5d4886964b0`: 61,309 tracked files, 212 MiB of working tree (~430 MB allocated,
since 4 KB blocks round up hard across files averaging 3.6 KB), 1.0 GB of git
objects, 1.4 GB total. M5 Max, APFS, git 2.50.1.

Time-to-usable = create + first `git status`. Median of 3, every result verified
against a reference checkout by SHA-256 content manifest.

| changed files | `git worktree add` | `wtclone` | `wtclone --exact` |
|--------------:|-------------------:|----------:|------------------:|
| 0             | 3.87 s / 432 MB    | **1.24 s / 23 MB** | 1.40 s / 25 MB |
| 946           | 3.65 s / 432 MB    | **1.43 s / 43 MB** | 1.63 s / 43 MB |
| 9,669         | 3.65 s / 424 MB    | **2.48 s / 147 MB** | 2.77 s / 152 MB |
| 23,517        | 3.68 s / 397 MB    | **3.28 s / 203 MB** | 3.47 s / 202 MB |

Measured crossover is ~32,000 changed files (~50% of the tree); past that a plain
checkout is faster and wtclone falls back automatically.

Supporting measurements:

- `clonefile(2)` on the whole tree: **0.45 s**, flat from `-j1` to `-j16` — it
  does not parallelise, the kernel already saturates the APFS metadata path.
- BSD `cp -c -R` for the same work: **5.66 s** (12.6× slower). It walks the tree
  in userspace and clones file-by-file, which throws away the advantage.
- `git worktree add` is 91% system time (`user 0.69s / sys 7.51s`), so checkout
  is metadata-bound, not zlib-bound. `checkout.workers=0` is a 5–12%
  *pessimisation* here.
- `rm -rf` of a worktree is 1.87 s, so with this tool deleting a worktree costs
  4× more than creating one.

## Falls back to `git worktree add` when

Not macOS · destination on a different volume · destination exists · parent
missing · source worktree has uncommitted changes (`--dirty` to override) ·
initialised submodules (their `.git` files hold absolute paths into the source
repo) · delta exceeds 50% of the tree · unresolvable commit-ish · index is v4 or
a split index (`--exact` only) · `core.worktree` set or `core.bare` true in
shared config (default mode only) · `--no-clone`.

The tool never reports an error itself. It cleans up any partial worktree and
hands off to git so git emits the authoritative diagnostic.

## Layout

`git-wtclone` is the tool — a single file, and the only thing you need to
install. `bench/` holds the investigation that produced it:

| | |
|---|---|
| `bench.py`, `results.json` | main matrix: baseline vs clone across delta sizes |
| `final_bench.py` | head-to-head, both modes, time-to-usable |
| `extra.py` | crossover, disk cost, untracked build artifacts |
| `fix.py` | thread scaling, target tree sizes, teardown cost |
| `probe_design.py`, `probe_stat.py` | index repair and time-to-usable design probes |
| `probe_checkstat*.py`, `probe_ctime.py` | what `core.checkStat=minimal` stops checking |
| `clonelib.py` | shared `clonefile(2)` ctypes helper |

To reproduce, point `WTCLONE_BENCH_REPO` at a large checkout — it defaults to
`~/git/rust` — and run any script in `bench/`:

```sh
WTCLONE_BENCH_REPO=~/src/rust python3 bench/final_bench.py
```

## License

MIT — see [LICENSE](LICENSE).
