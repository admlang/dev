#!/usr/bin/env bash
# Builds the fixture repositories of the test suite with git, and what git
# answers about them (expected/<name>.json). Run from this folder; the
# output is committed, so the suite never needs git itself.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf repos expected
mkdir -p repos expected

export GIT_CONFIG_NOSYSTEM=1 HOME="$PWD/.home" TZ=UTC
rm -rf .home && mkdir -p .home
git config --global user.name "Ana Lima"
git config --global user.email "ana@example.com"
git config --global init.defaultBranch main
git config --global gc.auto 0
git config --global maintenance.auto false

tick=1700000000
commit() { # message [date offset]
	tick=$((tick + 3600))
	GIT_AUTHOR_DATE="$tick +0130" GIT_COMMITTER_DATE="$tick +0130" git commit -q -m "$1"
}

# A history with merges, tags, every entry mode and enough similar content for
# deltas.
build_history() {
	local dir=$1
	cd "$dir"
	seq 1 600 | sed 's/^/line /' > numbers.txt
	printf 'hello\n' > hello.txt
	mkdir -p src/deep
	printf 'int main() { return 0; }\n' > src/main.c
	printf 'deep\n' > src/deep/file.txt
	printf '#!/bin/sh\necho hi\n' > run.sh && chmod +x run.sh
	ln -s hello.txt link
	git add -A && commit "Initial commit"
	git tag -a v1.0 -m "Version 1.0"
	for i in 1 2 3 4 5; do
		sed -i "s/^line $((i * 100))\$/line $((i * 100)) changed $i/" numbers.txt
		git add numbers.txt && commit "Change line $((i * 100))"
	done
	git checkout -q -b feature HEAD~3
	printf 'feature\n' > feature.txt
	git add feature.txt && commit "Add feature"
	printf 'more\n' >> src/main.c
	git add src/main.c && commit "Extend main"
	git checkout -q main
	GIT_AUTHOR_DATE="$((tick + 100)) +0130" GIT_COMMITTER_DATE="$((tick + 100)) +0130" git merge -q --no-ff feature -m "Merge feature"
	tick=$((tick + 200))
	git tag light
	git tag -a v2.0 -m "Version 2.0"
	git -c advice.nestedTag=false tag -a v2.0-signed-wrapper -m "Tag of a tag" v2.0
	git checkout -q -b side HEAD~2
	printf 'side\n' > side.txt
	git add side.txt && commit "Side work"
	git checkout -q main
	git mv src/deep/file.txt src/deep/moved.txt && commit "Move a file"
	printf 'GIF89a\0\1\2binary\0data\n' > logo.bin && git add logo.bin && commit "Add binary"
	printf 'GIF89a\0\1\3binary\0data and more\n' > logo.bin && chmod -x run.sh && git add -A && commit "Change binary, drop exec bit"
	git mv src/main.c src/app.c && printf 'int extra;\n' >> src/app.c && git add -A && commit "Rename and edit"
	git rm -q --cached hello.txt && commit "Untrack hello" && git add hello.txt && commit "Track hello again"
	cd - >/dev/null
}

expect() { # name gitdir
	local name=$1 dir=$2
	python3 - "$dir" "expected/$name.json" <<'PY'
import json, subprocess, sys
d, out = sys.argv[1], sys.argv[2]
def git(*a, check=True):
    r = subprocess.run(["git", "--git-dir", d, *a], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(r.stderr)
    return r.stdout
revs = ["HEAD", "main", "feature", "side", "v1.0", "v2.0", "light", "v2.0-signed-wrapper",
        "v2.0^{}", "v2.0-signed-wrapper^{}", "v2.0^{tag}", "main~1", "main~3", "main^2", "main^2~1",
        "main^{tree}", "main:src/main.c", "main:src", "HEAD@{1}", "main@{2}", "refs/heads/feature",
        "v1.0^{commit}", ":/Extend", "main^{/Change line 300}", "main~2^2"]
resolved = {}
for r in revs:
    v = git("rev-parse", "--verify", "-q", r, check=False).strip()
    if v:
        resolved[r] = v
objects = []
for line in git("cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype) %(objectsize)").splitlines():
    i, t, s = line.split()
    objects.append({"id": i, "kind": t, "size": int(s)})
refs = []
for line in git("for-each-ref", "--format=%(refname) %(objectname) %(*objectname)").splitlines():
    p = line.split()
    refs.append({"name": p[0], "id": p[1], "peeled": p[2] if len(p) > 2 else ""})
logs = {}
for args in [["main"], ["--topo-order", "main"], ["--date-order", "main"], ["--reverse", "main"],
             ["main", "^v1.0"], ["v1.0..side"], ["main...side"], ["--first-parent", "main"],
             ["--no-merges", "main"], ["--merges", "main"], ["main", "--", "src"], ["main", "--", "numbers.txt"],
             ["main", "--", "src/deep/moved.txt"], ["--all"], ["main", "--", "*.c"]]:
    logs[" ".join(args)] = git("rev-list", *args).split()
bases = {}
for a, b in [("main", "side"), ("feature", "side"), ("main", "feature"), ("v1.0", "main")]:
    bases[a + " " + b] = git("merge-base", "--all", a, b).split()
reflogs = {}
for r in ["HEAD", "refs/heads/main"]:
    out_ = git("reflog", "show", "--format=%H %gs", r, check=False)
    reflogs[r] = [l for l in out_.splitlines()]
idx = []
try:
    for line in git("ls-files", "-s").splitlines():
        meta, path = line.split("\t", 1)
        mode, oid, stage = meta.split()
        idx.append({"path": path, "mode": int(mode, 8), "id": oid, "stage": int(stage)})
except SystemExit:
    pass
patches = {}
for a, b, extra in [("v1.0", "main", []), ("main~3", "main", []), ("v1.0", "side", []), ("main~2", "main", ["--binary"]),
                    ("main~5", "main~3", ["-U1", "--histogram"])]:
    patches[" ".join([a, b] + extra)] = [git("diff", "--no-color", "-M", *extra, a, b, check=False)]
blames = {}
for rev, path in [("main", "numbers.txt"), ("main", "src/app.c"), ("feature", "src/main.c"), ("main", "src/deep/moved.txt")]:
    out_ = git("blame", "--line-porcelain", rev, "--", path, check=False)
    rows = []
    for line in out_.splitlines():
        parts = line.split(" ")
        if len(parts) >= 3 and len(parts[0]) in (40, 64) and all(c in "0123456789abcdef" for c in parts[0]):
            rows.append(" ".join(parts[:3]))
        elif line.startswith("filename "):
            rows[-1] += " " + line[9:]
    blames[rev + " " + path] = rows
json.dump({"resolved": resolved, "objects": objects, "refs": refs,
           "blames": [{"key": k, "values": v} for k, v in blames.items()],
           "patches": [{"key": k, "values": v} for k, v in patches.items()],
           "logs": [{"key": k, "values": v} for k, v in logs.items()],
           "bases": [{"key": k, "values": v} for k, v in bases.items()],
           "reflogs": [{"key": k, "values": v} for k, v in reflogs.items()], "index": idx,
           "format": git("rev-parse", "--show-object-format").strip()}, open(out, "w"), indent=1, sort_keys=True)
PY
}

# basic: packed with deltas, some loose objects left, packed and loose refs,
# a commit-graph with changed-path Bloom filters.
# Scratch space inside this folder, removed by its literal name.
rm -rf .work && mkdir .work
work="$PWD/.work"
git init -q "$work/basic"
build_history "$work/basic"
git -C "$work/basic" gc -q --aggressive
git -C "$work/basic" pack-refs --all
(cd "$work/basic" && printf 'loose\n' > loose.txt && git add loose.txt && commit "Loose commit")
git -C "$work/basic" commit-graph write --reachable --changed-paths
git clone -q --bare --no-local "$work/basic" repos/basic.git
# Keep what the clone dropped: reflogs, loose objects, the commit-graph.
rm -rf repos/basic.git && cp -r "$work/basic/.git" repos/basic.git
git --git-dir repos/basic.git config core.bare true
expect basic repos/basic.git
# thin.pack: the two newest commits of main as a thin pack against
# basic.git (their delta bases left out), and the objects it holds.
printf 'main\n^main~2\n' | git --git-dir repos/basic.git pack-objects -q --revs --thin --stdout > repos/thin.pack
git --git-dir repos/basic.git rev-list --objects main ^main~2 | cut -d' ' -f1 | sort > expected/thin.txt

# twopacks: two packs under a multi-pack-index, the second a thin-free
# incremental pack; version 1 pack indexes.
git init -q "$work/twopacks"
build_history "$work/twopacks"
git -C "$work/twopacks" -c pack.indexVersion=1 repack -q -a -d
(cd "$work/twopacks" && printf 'two\n' > two.txt && git add two.txt && commit "Second pack")
git -C "$work/twopacks" -c pack.indexVersion=1 repack -q
git -C "$work/twopacks" multi-pack-index write
cp -r "$work/twopacks/.git" repos/twopacks.git
git --git-dir repos/twopacks.git config core.bare true
expect twopacks repos/twopacks.git

# sha256: the same history in a SHA-256 repository.
git init -q --object-format=sha256 "$work/sha256"
build_history "$work/sha256"
git -C "$work/sha256" gc -q
cp -r "$work/sha256/.git" repos/sha256.git
git --git-dir repos/sha256.git config core.bare true
expect sha256 repos/sha256.git

# reftable: references and logs in a reftable stack, with enough branches for
# several blocks, an index and a compaction.
git init -q --ref-format=reftable "$work/reftable"
build_history "$work/reftable"
for i in $(seq 1 300); do git -C "$work/reftable" branch "many/branch-with-a-long-name-$i" main~1; done
git -C "$work/reftable" gc -q
git -C "$work/reftable" branch -D many/branch-with-a-long-name-7
cp -r "$work/reftable/.git" repos/reftable.git
git --git-dir repos/reftable.git config core.bare true
expect reftable repos/reftable.git

# bitmaps / midxbitmaps: reachability bitmaps, one for a pack (with the
# hash cache and the lookup table, and a commit made after it) and one for
# a multi-pack index over two packs; expected/<name>.txt holds what `git
# rev-list --objects` lists for each spec, sorted.
bitmap_answers() { # name gitdir
	for spec in "main" "main ^main~3" "--all" "side ^main" "v2.0"; do
		printf '%s\t' "$spec"
		# shellcheck disable=SC2086
		git --git-dir "$2" rev-list --objects $spec | cut -c1-40 | sort | tr '\n' ' '
		printf '\n'
	done > "expected/$1.txt"
}
git init -q "$work/bitmaps"
build_history "$work/bitmaps"
git -C "$work/bitmaps" -c pack.writeBitmapLookupTable=true -c pack.writeBitmapHashCache=true repack -q -adb
(cd "$work/bitmaps" && printf 'after\n' > after.txt && git add after.txt && commit "After the bitmap")
cp -r "$work/bitmaps/.git" repos/bitmaps.git
git --git-dir repos/bitmaps.git config core.bare true
bitmap_answers bitmaps repos/bitmaps.git
git init -q "$work/midxbitmaps"
build_history "$work/midxbitmaps"
git -C "$work/midxbitmaps" repack -q -ad
(cd "$work/midxbitmaps" && printf 'two\n' > two.txt && git add two.txt && commit "Second pack")
git -C "$work/midxbitmaps" repack -q
git -C "$work/midxbitmaps" multi-pack-index write --bitmap
cp -r "$work/midxbitmaps/.git" repos/midxbitmaps.git
git --git-dir repos/midxbitmaps.git config core.bare true
bitmap_answers midxbitmaps repos/midxbitmaps.git

# index4 / splitindex: index formats (the repositories keep their index).
git init -q "$work/index4"
build_history "$work/index4"
git -C "$work/index4" gc -q
git -C "$work/index4" update-index --index-version 4
git -C "$work/index4" update-index --skip-worktree run.sh
cp -r "$work/index4/.git" repos/index4.git
expect index4 repos/index4.git
git init -q "$work/split"
build_history "$work/split"
git -C "$work/split" gc -q
git -C "$work/split" update-index --split-index
(cd "$work/split" && printf 'x\n' > extra.txt && git add extra.txt && git rm -q --cached side.txt 2>/dev/null || true)
cp -r "$work/split/.git" repos/split.git
expect split repos/split.git

rm -rf .work .home
# Hooks samples are not part of a fixture.
find repos -path '*/hooks/*' -delete

# One archive for the suite to embed: the repositories and git's answers.
mkdir -p repos/expected && mv expected/* repos/expected/ && rmdir expected
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C repos . | gzip -9n > fixtures.tar.gz
rm -rf repos
