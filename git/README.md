# adm.dev.git

Git in ADM: repositories on disk, their objects and references, and the smart protocol over
HTTP and SSH. There is no libgit2 and no `git` binary to call.

```bash
adm get admlang:adm.dev.git
```

```adm
use adm.dev.git

let repo = try git.clone("https://github.com/admlang/examples.git", "examples", depth = 1)
for let commit in try repo.log("main", limit = 10) {
	print("{commit.id.short()} {commit.summary}")
}
let tree = try repo.tree("main")
let readme = try tree.file("README.md").text()

try repo.add("notes.md")
let id = try repo.commit("Add notes", author = new git.Signature("Ana", "ana@example.com"))
try repo.push("origin", "main", credentials = token)
```

## Tests

`adm test dev/git` runs on repositories it creates under a scratch folder and on fixtures with
packfiles of every feature. `ADM_TEST_GIT_REMOTE=<url>` adds fetch and push against a remote the
suite may write a throwaway branch to.
