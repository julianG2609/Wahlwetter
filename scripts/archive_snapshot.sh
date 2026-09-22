#!/usr/bin/env bash
# Append the newest raw snapshot to the orphan `data-raw` branch.
#
# Raw snapshots are kept out of `main` so a normal clone stays small, but they
# remain fully versioned and diffable -- which is also how we detect that dawum
# has silently corrected a past survey, since the API only exposes current state.
#
# Creates the branch on first use. Safe to run when there is nothing to do.
set -euo pipefail

BRANCH="data-raw"
RAW_DIR="${1:-data/raw}"

shopt -s nullglob
snapshots=("$RAW_DIR"/*.json.gz)
if [ ${#snapshots[@]} -eq 0 ]; then
  echo "archive_snapshot: no snapshot in $RAW_DIR, nothing to do"
  exit 0
fi

snapshot="${snapshots[-1]}"
name="$(basename "$snapshot")"
# Snapshot names start with an ISO-ish UTC stamp: 20260922T113117Z-<sha12>.json.gz
year="${name:0:4}"
month="${name:4:2}"
target="snapshots/$year/$month/$name"

worktree="$(mktemp -d)"
cleanup() { git worktree remove --force "$worktree" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch --depth=1 origin "$BRANCH"
  git worktree add --detach "$worktree" FETCH_HEAD
  git -C "$worktree" switch -c "$BRANCH"
else
  echo "archive_snapshot: creating orphan branch $BRANCH"
  git worktree add --detach "$worktree" HEAD
  git -C "$worktree" checkout --orphan "$BRANCH"
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

mkdir -p "$worktree/$(dirname "$target")"
if [ -e "$worktree/$target" ]; then
  echo "archive_snapshot: $target already archived, nothing to do"
  exit 0
fi
cp "$snapshot" "$worktree/$target"

git -C "$worktree" add "$target"
if git -C "$worktree" diff --cached --quiet; then
  echo "archive_snapshot: nothing staged"
  exit 0
fi

git -C "$worktree" commit -q -m "Archive snapshot $name"
git -C "$worktree" push -q origin "$BRANCH"
echo "archive_snapshot: pushed $target"
