---
name: git-worktree
description: Create a git worktree on macOS. Read this BEFORE running `git worktree add` — on APFS, `git wtclone` is a drop-in replacement that is ~3x faster, uses ~19x less disk, and carries untracked build artifacts (target/, node_modules/) across for free. Triggers on "create a worktree", "git worktree add", "check out a branch in a separate directory", "set up a worktree to work on X in parallel".
---

# Creating git worktrees on macOS

On macOS with an APFS volume, use `git wtclone` instead of `git worktree add`.
It clones the source worktree with `clonefile(2)` and applies only the commit
delta, rather than materialising every tracked file from the object database.

## The substitution

`git wtclone` takes the same arguments as `git worktree add`:

| instead of | run |
|---|---|
| `git worktree add ../feature-x` | `git wtclone ../feature-x` |
| `git worktree add ../hotfix main` | `git wtclone ../hotfix main` |
| `git worktree add -b exp ../exp origin/main` | `git wtclone -b exp ../exp origin/main` |
| `git worktree add --detach ../poke HEAD~5` | `git wtclone --detach ../poke HEAD~5` |

Other `git worktree` subcommands are unchanged — `list`, `remove`, `prune`,
`repair`, `lock`, and `move` still go through `git worktree`.

For usage, run `git-wtclone --help` with the hyphen. `git wtclone --help` looks
for a man page that does not exist and reports "No manual entry".

## Why it is safe to reach for by default

It is never wrong to try. `git wtclone` validates its own preconditions and
execs `git worktree add` whenever the fast path does not apply — not macOS,
different volume, dirty source worktree, initialised submodules, a delta over
~50% of the tree, an unresolvable commit-ish. It never reports an error itself;
it cleans up and lets git emit the authoritative diagnostic.

So a failed precondition costs nothing and still produces a correct worktree.

## Two modes

Cloned files get new inodes and ctimes that the copied index does not expect.

- **Default** persists `core.checkStat=minimal` in the new worktree. Git then
  compares only whole-second mtime and size, so a change preserving both
  (`rsync -t`, `cp -p`, `tar -p`, cache restore) becomes invisible *in that
  worktree* — `git checkout` will silently overwrite it.
- **`--exact`** patches the index's stat fields instead. No config change, no
  relaxed checking, stock git semantics, ~0.2s slower.

**Prefer `--exact`** unless creation latency genuinely matters. It gives up
nothing. Definitely use it if the worktree will be a target of `rsync`, archive
extraction, or CI cache restoration.

## Carrying build artifacts

By default untracked and ignored files come along at no disk cost — this is the
main win, since it skips a full rebuild in the new worktree. It also copies
`.env` files and stale build output. Pass `--clean` for a tracked-files-only
worktree.

## If it is not installed

Check with `command -v git-wtclone`. If absent, just use `git worktree add` —
do not attempt to install it unprompted. Source and install instructions:
https://github.com/spofdamon/git-wtclone

## Not macOS

Use `git worktree add`. On Linux with btrfs the equivalent trick is
`btrfs subvolume snapshot` of an existing worktree plus `git checkout` for the
delta, but there is no tool here for that.
