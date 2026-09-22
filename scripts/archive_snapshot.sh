#!/usr/bin/env bash
# Append the newest raw snapshot to the orphan `data-raw` branch.
#
# Raw snapshots are kept out of `main` so a normal clone stays small, but they
# remain fully versioned and diffable -- which is also how we detect that dawum
# has silently corrected a past survey, since the API only exposes current state.
#
# Creates the branch on first use. Safe to run repeatedly: re-archiving a
# snapshot that is already on the branch is a no-op, and the work happens on a
# uniquely named temporary branch so nothing is left behind to collide with the
# next run in the same clone.
set -euo pipefail

BRANCH="data-raw"
RAW_DIR="${1:-data/raw}"

shopt -s nullglob
snapshots=("$RAW_DIR"/*.json.gz)
if [ ${#snapshots[@]} -eq 0 ]; then
  echo "archive_snapshot: no snapshot in $RAW_DIR, nothing to do"
  exit 0
fi

# Portable last-element access: bash 3.2 (macOS) rejects [-1].
snapshot="${snapshots[$((${#snapshots[@]} - 1))]}"
name="$(basename "$snapshot")"
# Snapshot names start with an ISO-ish UTC stamp: 20260922T113117Z-<sha12>.json.gz
year="${name:0:4}"
month="${name:4:2}"
target="snapshots/$year/$month/$name"

worktree="$(mktemp -d)"
tmp_branch="archive-snapshot-$$-${RANDOM}"
cleanup() {
  git worktree remove --force "$worktree" >/dev/null 2>&1 || true
  git branch -D "$tmp_branch" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch --depth=1 origin "$BRANCH"
  git worktree add --detach "$worktree" FETCH_HEAD >/dev/null
  git -C "$worktree" switch -c "$tmp_branch" >/dev/null
else
  echo "archive_snapshot: creating orphan branch $BRANCH"
  git worktree add --detach "$worktree" HEAD >/dev/null
  git -C "$worktree" checkout --orphan "$tmp_branch" >/dev/null
  git -C "$worktree" rm -rqf . >/dev/null 2>&1 || true
  cat > "$worktree/README.md" <<'README'
# Raw snapshots

Verbatim, gzipped copies of what each source returned, one per observed change.
This branch shares no history with `main`, so cloning the code does not pull
this data.

Layout: `snapshots/<year>/<month>/<UTC timestamp>-<sha256 prefix>.json.gz`

The file name's hash is of the *uncompressed* bytes. Data here is from
dawum.de and is licensed ODbL; see `LICENSE-DATA` on `main`.
README
  git -C "$worktree" add README.md
fi

if [ -e "$worktree/$target" ]; then
  echo "archive_snapshot: $target already archived, nothing to do"
  exit 0
fi

mkdir -p "$worktree/$(dirname "$target")"
cp "$snapshot" "$worktree/$target"
git -C "$worktree" add "$target"

if git -C "$worktree" diff --cached --quiet; then
  echo "archive_snapshot: nothing staged"
  exit 0
fi

git -C "$worktree" commit -q -m "Archive snapshot $name"
# Push the temporary branch onto the real one; never create a local `data-raw`.
git -C "$worktree" push -q origin "HEAD:refs/heads/$BRANCH"
echo "archive_snapshot: pushed $target"
