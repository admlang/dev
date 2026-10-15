#!/usr/bin/env bash
# Builds rules.tar.gz: working trees with nested .gitignore and
# .gitattributes files (and info/exclude, info/attributes), and what
# `git check-ignore --no-index` and `git check-attr -a` answer for every file
# and folder in them. Run from this folder; the output is committed.
set -euo pipefail
cd "$(dirname "$0")"
# Scratch space inside this folder, removed by its literal name.
rm -rf .work && mkdir .work
work="$PWD/.work"
export GIT_CONFIG_NOSYSTEM=1 HOME="$PWD/.work/.home"
mkdir -p "$HOME"
python3 - "$work" <<'PY'
import os, random, subprocess, sys, json
out = sys.argv[1]
random.seed(4242)
names = ["a", "b", "build", "docs", "src", "lib", "test", "x.c", "y.h", "readme.md", "log.txt", "deep", "Foo", "foo", ".hidden", "data.bin", "k[1].txt", "sp ace", "z!.txt"]
ignore_pats = ["*.c", "!x.c", "build/", "/docs", "deep/**/z*", "**/test", "*.txt", "!log.txt", "src/*.h", "[ab]", "?oo", "lib/", "\\!z*", "*.bin", "a/**", "!a/keep", "#comment", "", "sp\\ ace", "Foo/", "**/deep/", "/b/x.c", "x.c ", "*.md\\ "]
attr_lines = ["*.c text eol=crlf", "*.txt -text", "*.bin binary", "src/** diff=cpp", "readme.md text=auto", "[attr]mine text -diff myattr=1", "*.h mine", "deep/**/* !text", "docs/* eol=lf", "test filter=lfs -merge", "Foo* ident", "*.md -ident", "a/** linguist-generated", "\"sp ace\" whitespace=-trailing"]
for case in range(60):
    root = os.path.join(out, "%02d" % case)
    os.makedirs(root)
    subprocess.run(["git", "init", "-q", root], check=True)
    paths = []
    def build(d, depth):
        for n in random.sample(names, random.randint(3, 8)):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if depth < 3 and random.random() < 0.4 and "." not in n:
                os.makedirs(p, exist_ok=True)
                paths.append(rel + "/")
                build(p, depth + 1)
            elif not os.path.exists(p):
                open(p, "w").write("x\n")
                paths.append(rel)
        if random.random() < 0.8:
            open(os.path.join(d, ".gitignore"), "w").write("\n".join(random.sample(ignore_pats, random.randint(1, 6))) + "\n")
        if random.random() < 0.8:
            open(os.path.join(d, ".gitattributes"), "w").write("\n".join(random.sample(attr_lines, random.randint(1, 5))) + "\n")
    build(root, 0)
    open(os.path.join(root, ".git", "info", "exclude"), "w").write("\n".join(random.sample(ignore_pats, 2)) + "\n")
    open(os.path.join(root, ".git", "info", "attributes"), "w").write(random.choice(attr_lines) + "\n")
    rels = sorted(set(p for p in paths))
    ign = subprocess.run(["git", "-C", root, "check-ignore", "--no-index", "--stdin"], input="\n".join(p.rstrip("/") for p in rels) + "\n", capture_output=True, text=True).stdout.split("\n")
    ignored = sorted(set(p for p in ign if p))
    attrs = {}
    files = [p for p in rels if not p.endswith("/")]
    out_attr = subprocess.run(["git", "-C", root, "check-attr", "-a", "--stdin"], input="\n".join(files) + "\n", capture_output=True, text=True).stdout
    for line in out_attr.splitlines():
        path, name, value = line.split(": ", 2)
        attrs.setdefault(path, []).append(name + "=" + value)
    json.dump({"paths": rels, "ignored": ignored, "attributes": [{"key": k, "values": v} for k, v in sorted(attrs.items())]},
              open(os.path.join(root, "expected.json"), "w"), indent=1)
    subprocess.run(["rm", "-rf", os.path.join(root, ".git", "hooks"), os.path.join(root, ".git", "objects")])
    os.makedirs(os.path.join(root, ".git", "objects"))
PY
rm -rf .work/.home
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work" . | gzip -9n > rules.tar.gz
rm -rf .work
