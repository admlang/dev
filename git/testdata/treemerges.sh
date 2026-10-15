#!/usr/bin/env bash
# Builds treemerges.tar.gz: repositories whose two branches change a common
# base in random ways (edits, adds, deletes, renames, folder renames, mode
# and type changes, binary files, criss-cross histories), and what `git
# merge-tree --write-tree` makes of each: the tree, the conflicted stages and
# the messages. Run from this folder; the output is committed.
set -euo pipefail
cd "$(dirname "$0")"
# Scratch space inside this folder, removed by its literal name.
rm -rf .work && mkdir .work
work="$PWD/.work"
export GIT_CONFIG_NOSYSTEM=1 HOME="$PWD/.work/.home"
mkdir -p "$HOME"
git config --global user.name "Ana Lima"
git config --global user.email "ana@example.com"
git config --global init.defaultBranch main
git config --global gc.auto 0
python3 - "$work" <<'PY'
import os, random, subprocess, sys, json, shutil
out = sys.argv[1]
random.seed(int(os.environ.get("SEED", "9001")))
words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
def git(repo, *a, check=True, env=None):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, env=env)
    if check and r.returncode not in (0,):
        raise SystemExit(" ".join(a) + ": " + r.stderr)
    return r
tick = [1700000000]
def commit(repo, msg):
    tick[0] += 60
    env = dict(os.environ, GIT_AUTHOR_DATE="%d +0000" % tick[0], GIT_COMMITTER_DATE="%d +0000" % tick[0])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg, env=env)
def text(n, seed):
    rnd = random.Random(seed)
    return "".join("%s %s %d\n" % (rnd.choice(words), rnd.choice(words), i) for i in range(n))
def files(repo):
    res = []
    for root, dirs, fs in os.walk(repo):
        if ".git" in root.split(os.sep): continue
        for f in fs:
            p = os.path.relpath(os.path.join(root, f), repo)
            res.append(p)
    return sorted(res)
def mutate(repo, ops):
    for _ in range(ops):
        fs = files(repo)
        op = random.choice(["edit", "edit", "add", "del", "ren", "renedit", "dirren", "mode", "typ", "bin"])
        if not fs: op = "add"
        if op == "edit":
            f = random.choice(fs); p = os.path.join(repo, f)
            if os.path.islink(p): continue
            lines = open(p, errors="replace").read().splitlines(True)
            if lines:
                i = random.randrange(len(lines)); lines[i] = "changed %d %s" % (random.randint(0, 99), lines[i])
            open(p, "w").write("".join(lines))
        elif op == "add":
            d = random.choice(["", "src/", "docs/", "src/deep/", "lib/"])
            p = os.path.join(repo, d + "new%d.txt" % random.randint(0, 9))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write(text(random.randint(3, 20), random.random()))
        elif op == "del":
            os.remove(os.path.join(repo, random.choice(fs)))
        elif op in ("ren", "renedit"):
            f = random.choice(fs)
            d = random.choice(["", "src/", "moved/", "docs/"])
            dst = os.path.join(repo, d + "r_" + os.path.basename(f))
            if os.path.exists(dst): continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(os.path.join(repo, f), dst)
            if op == "renedit" and not os.path.islink(dst):
                open(dst, "a").write("tail %d\n" % random.randint(0, 99))
        elif op == "dirren":
            dirs = sorted(set(os.path.dirname(f) for f in fs if os.path.dirname(f)))
            if not dirs: continue
            d = random.choice(dirs)
            new = d + "_v2"
            if os.path.exists(os.path.join(repo, new)): continue
            os.rename(os.path.join(repo, d), os.path.join(repo, new))
        elif op == "mode":
            p = os.path.join(repo, random.choice(fs))
            if os.path.islink(p): continue
            os.chmod(p, os.stat(p).st_mode ^ 0o111)
        elif op == "typ":
            f = random.choice(fs); p = os.path.join(repo, f)
            os.remove(p); os.symlink("target-" + os.path.basename(f), p)
        elif op == "bin":
            p = os.path.join(repo, "data%d.bin" % random.randint(0, 2))
            open(p, "wb").write(bytes([0, 1, 2]) + os.urandom(8))
for k in range(int(os.environ.get("CASES", "60"))):
    repo = os.path.join(out, "%02d" % k)
    os.makedirs(repo)
    git(repo, "init", "-q")
    for d in ["src", "src/deep", "docs", "lib"]:
        os.makedirs(os.path.join(repo, d), exist_ok=True)
    for i in range(random.randint(3, 9)):
        d = random.choice(["", "src/", "src/deep/", "docs/", "lib/"])
        open(os.path.join(repo, d + "f%d.txt" % i), "w").write(text(random.randint(5, 40), i * 31 + k))
    commit(repo, "base")
    git(repo, "branch", "side")
    mutate(repo, random.randint(1, 5)); commit(repo, "ours")
    if k % 10 == 9:
        # Criss-cross: each side merges the other's first commit.
        git(repo, "checkout", "-q", "side"); mutate(repo, 2); commit(repo, "theirs 1")
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "main1")
        git(repo, "merge", "-q", "-s", "ort", "-X", "ours", "--no-edit", "side", check=False)
        if os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD")):
            git(repo, "merge", "--abort", check=False); continue
        git(repo, "checkout", "-q", "side")
        git(repo, "merge", "-q", "-s", "ort", "-X", "theirs", "--no-edit", "main1", check=False)
        if os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD")):
            git(repo, "merge", "--abort", check=False); continue
        git(repo, "checkout", "-q", "main")
    else:
        git(repo, "checkout", "-q", "side")
    mutate(repo, random.randint(1, 5)); commit(repo, "theirs")
    git(repo, "checkout", "-q", "main")
    results = {}
    for style in ["merge", "diff3"]:
        r = subprocess.run(["git", "-C", repo, "-c", "merge.conflictStyle=" + style, "merge-tree", "--write-tree", "main", "side"], capture_output=True, text=True)
        results[style] = {"code": r.returncode, "out": r.stdout}
    json.dump({"results": [{"key": k2, "values": [str(v["code"]), v["out"]]} for k2, v in results.items()]},
              open(os.path.join(repo, ".git", "expected.json"), "w"), indent=1)
    shutil.rmtree(os.path.join(repo, ".git", "hooks"))
    # The working tree is not needed: the merge is of commits.
    for name in os.listdir(repo):
        if name != ".git":
            p = os.path.join(repo, name)
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    git(repo, "gc", "-q")
PY
rm -rf .work/.home
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work" . | gzip -9n > treemerges.tar.gz
rm -rf .work
