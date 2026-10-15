#!/usr/bin/env bash
# Builds diffs.tar.gz: pairs of code-like texts with random edits (moved and
# duplicated blocks, blank-line and indentation changes, a missing final
# newline) and the hunks `git diff --no-index` prints for each algorithm and
# option the suite checks. Run from this folder; the output is committed.
set -euo pipefail
cd "$(dirname "$0")"
# Scratch space inside this folder, removed by its literal name.
rm -rf .work && mkdir .work
work="$PWD/.work"
python3 - "$work" <<'PY'
import os, random, subprocess, sys
out = sys.argv[1]
random.seed(20261015)
words = "let fail return for if else switch case def type struct value count index data buffer node".split()
def block(depth):
    lines = []
    for _ in range(random.randint(2, 7)):
        ind = "\t" * depth
        r = random.random()
        if r < 0.15 and depth < 3:
            lines.append(ind + "if %s {\n" % random.choice(words))
            lines += block(depth + 1)
            lines.append(ind + "}\n")
        elif r < 0.25:
            lines.append("\n")
        else:
            lines.append(ind + " ".join(random.choice(words) for _ in range(random.randint(1, 5))) + "\n")
    return lines
def text():
    lines = []
    for f in range(random.randint(3, 12)):
        lines.append("def f%d() {\n" % f)
        lines += block(1)
        lines.append("}\n\n")
    return lines
def mutate(lines, ops):
    lines = lines[:]
    for _ in range(ops):
        i = random.randrange(len(lines) or 1)
        n = random.randint(1, 5)
        op = random.choice(["del", "ins", "dup", "move", "blank", "indent", "edit", "space"])
        if op == "del": del lines[i:i+n]
        elif op == "ins": lines[i:i] = ["\tadded %d\n" % random.randint(0, 9) for _ in range(n)]
        elif op == "dup": lines[i:i] = lines[i:i+n]
        elif op == "move":
            b = lines[i:i+n]; del lines[i:i+n]; j = random.randrange(len(lines) + 1); lines[j:j] = b
        elif op == "blank": lines.insert(i, "\n")
        elif op == "indent" and lines: lines[i] = "    " + lines[i]
        elif op == "edit" and lines: lines[i] = lines[i].rstrip("\n") + " x\n"
        elif op == "space" and lines: lines[i] = lines[i].rstrip("\n") + " \n"
    return lines
variants = [("myers", []), ("patience", ["--patience"]), ("histogram", ["--histogram"]), ("minimal", ["--minimal"]),
            ("w", ["-w"]), ("b", ["-b"]), ("eol", ["--ignore-space-at-eol"]), ("blank", ["--ignore-blank-lines"]),
            ("u1", ["-U1"]), ("noindent", ["--no-indent-heuristic"])]
for k in range(24):
    old = text()
    new = mutate(old, random.choice([2, 5, 12, 60]))
    if k % 5 == 0 and new: new[-1] = new[-1].rstrip("\n")
    d = os.path.join(out, "%02d" % k)
    os.makedirs(d)
    open(os.path.join(d, "old"), "w").write("".join(old))
    open(os.path.join(d, "new"), "w").write("".join(new))
    for name, flags in variants:
        r = subprocess.run(["git", "diff", "--no-index", "--no-color", *flags, "old", "new"], cwd=d, capture_output=True)
        t = r.stdout
        at = t.find(b"\n@@")
        open(os.path.join(d, name + ".diff"), "wb").write(t[at+1:] if at >= 0 else b"")
PY
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work" . | gzip -9n > diffs.tar.gz
rm -rf .work
