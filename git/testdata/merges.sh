#!/usr/bin/env bash
# Builds merges.tar.gz: base/ours/theirs texts and what `git merge-file -p
# --diff-algorithm=histogram` makes of them in each conflict style and with
# each favor, with its conflict count. Run from this folder; the output is
# committed.
set -euo pipefail
cd "$(dirname "$0")"
# Scratch space inside this folder, removed by its literal name.
rm -rf .work && mkdir .work
work="$PWD/.work"
python3 - "$work" <<'PY'
import os, random, subprocess, sys
out = sys.argv[1]
random.seed(31337)
words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
def text(n):
    lines = []
    for i in range(n):
        r = random.random()
        if r < 0.1: lines.append("\n")
        elif r < 0.2: lines.append("}\n")
        else: lines.append("\t" * random.randint(0, 2) + " ".join(random.choice(words) for _ in range(random.randint(1, 4))) + " %d\n" % i)
    return lines
def mutate(lines, ops):
    lines = lines[:]
    for _ in range(ops):
        if not lines: lines.append("x\n"); continue
        i = random.randrange(len(lines))
        n = random.randint(1, 4)
        op = random.choice(["del", "ins", "edit", "dup"])
        if op == "del": del lines[i:i+n]
        elif op == "ins": lines[i:i] = ["added %d\n" % random.randint(0, 999) for _ in range(n)]
        elif op == "edit": lines[i] = lines[i].rstrip("\n") + " edited\n"
        elif op == "dup": lines[i:i] = lines[i:i+n]
    return lines
for k in range(80):
    base = text(random.randint(5, 60))
    shared = mutate(base, random.randint(0, 2))
    ours = mutate(shared, random.randint(1, 5))
    theirs = mutate(shared, random.randint(1, 5))
    if k % 7 == 0:
        ours = [l.replace("\n", "\r\n") for l in ours]
        theirs = [l.replace("\n", "\r\n") for l in theirs]
        base = [l.replace("\n", "\r\n") for l in base]
    if k % 9 == 0 and theirs:
        theirs[-1] = theirs[-1].rstrip("\n")
    d = os.path.join(out, "%02d" % k)
    os.makedirs(d)
    for name, lines in [("base", base), ("ours", ours), ("theirs", theirs)]:
        open(os.path.join(d, name), "w", newline="").write("".join(lines))
    for variant, flags in [("merge", []), ("diff3", ["--diff3"]), ("zdiff3", ["--zdiff3"]), ("ours", ["--ours"]),
                           ("theirs", ["--theirs"]), ("union", ["--union"])]:
        r = subprocess.run(["git", "merge-file", "-p", "--diff-algorithm=histogram", *flags,
                            "-L", "ours", "-L", "base", "-L", "theirs", "ours", "base", "theirs"], cwd=d, capture_output=True)
        open(os.path.join(d, variant + ".out"), "wb").write(r.stdout)
        open(os.path.join(d, variant + ".code"), "w").write(str(r.returncode))
PY
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work" . | gzip -9n > merges.tar.gz
rm -rf .work
