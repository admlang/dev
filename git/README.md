# adm.dev.git

Git in ADM: repositories on disk, their objects and references, the working tree, merging, and
the smart protocol over HTTP and SSH, client and server. There is no libgit2 and no `git` binary
to call; what it writes git reads, and what git writes it reads.

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
let readme = try (try tree.file("README.md")).text()

try repo.add("notes.md")
let id = try repo.commit("Add notes", author = new git.Signature("Ana", "ana@example.com"))
try repo.push("origin", "main", credentials = token)
```

`token` is a `git.Credentials` (a user name and a password or access token).

## Repositories

`git.open(path)` opens a repository (a working tree or a bare one); `git.discover(path)` looks
upwards for one; `git.init(path, InitOptions{...})` makes one, bare or not, SHA-1 or SHA-256,
with files or reftable references, with a separate git folder. `git.clone(url, path)`
clones: everything, a single branch (`branch`, `singleBranch`), a shallow history (`depth`,
`shallowSince`, `shallowExclude`), a partial clone (`filter = "blob:none"`, `"blob:limit=1m"`,
`"tree:0"`; what it left out is fetched when read), bare or a mirror, with or without its
submodules. The
configuration (`repo.config`) reads every file git reads, with includes and `includeIf`, and
writes the repository's own file in place.

## Objects and references

Objects come from loose files, packs (deltas, multi-pack indexes, reachability bitmaps) and
alternates: `repo.object(rev)`, `commitAt`, `tree`, `blob`, `tag`, `raw`, `info`. They are
written loose (`write`, `writeBlob`, `writeTree`) and packed by `repo.repack()`, which also
writes a bitmap for a bare repository; `repo.gc()` packs references and objects and prunes
unreachable loose objects older than two weeks.

References live in loose files and `packed-refs`, or in a reftable stack: `ref`, `refs(prefix)`,
`setRef` (with an expected old value), `setSymbolicRef`, `deleteRef`, `reflog`. Revision
expressions are git's: `main~3`, `HEAD^2`, `v1.0^\{commit\}`, `@\{upstream\}`, `main@\{2.days.ago\}`,
`:/message`, `rev:path`.

## History

`repo.log(rev, limit, paths)` lists commits newest first. `RevWalk` walks commits in date or topological order, reversed, first-parent only, limited to
paths and dates, with ranges (`pushRange("a..b")`, `"a...b"`); the commit-graph and its Bloom
filters make it fast where they exist. `mergeBases`, `isAncestor`, and `repo.blame(path)`
(following renames) complete it.

## Working tree

The index (versions 2 to 4, split and sparse), `status` (and `porcelain`, as `git status
--porcelain` prints it), `add`, `remove`, `move`, `commit` (with signing), `checkout`, branches, `reset`, `restore`.
Diffs are git's: `repo.diff(from, to)`, `diffIndex`, `diffWorktree` with rename detection, as
patches (`.patch()`) or numbers (`.stats()`), Myers, patience, histogram and minimal, the
whitespace options; `repo.apply(patch)` applies them, binary patches too. Ignore rules,
attributes, line endings (`core.autocrlf`, `eol`, `text`), `ident`, `working-tree-encoding`
and filter drivers (including long-running filter processes) are applied as git applies them.

## Merging

`repo.merge(rev)` merges as `merge-ort` does (renames, directory renames, conflict markers in
the merge, diff3 and zdiff3 styles), fast-forwards when it can, and stops at conflicts with git's
`MERGE_*` files; `cherryPick`, `revert` and `rebase` (of a linear range, with `rebaseContinue`
and `rebaseAbort`, in git's `rebase-merge` state) share its machinery. `git.mergeCommits` and
`git.mergeText` merge without touching the working tree.

## Remotes

```adm
let connection = git.RemoteOptions{
	ask: def (req git.CredentialRequest) !?git.Credentials {
		return git.Credentials{username: "ana", password: new Secret<string>(readToken(req.host))}
	},
}
let fetched = try repo.fetch("origin", prune = true, connection = connection)
for let c in fetched.changes {
	print("{c.name}: {c.kind}")
}
let pushed = try repo.push("origin", "HEAD:refs/heads/topic", force = true, connection = connection)
let refs = try git.lsRemote("git@github.com:admlang/examples.git", ["refs/tags/"])
```

- Transports: smart HTTP and HTTPS (protocol 2, and 0 for older servers), SSH through
  `std.net.ssh` (the agent and `~/.ssh/id_*` keys, or a `ssh.Client` the program sets up), and
  repositories on this machine.
- Credentials: given by the program (`credentials`), asked for with `ask` when the server refuses, or taken from
  the credential helpers the configuration names (`store` is built in; others run as git runs
  them); helpers learn which worked and forget those refused.
- Fetch: refspecs, negotiation with git's default negotiator, tags followed or all,
  `FETCH_HEAD`, pruning, forced and rejected updates reported per reference, shallow deepening,
  filters. A partial clone fetches missing objects when they are read.
- Push: refspecs (patterns, deletions, `matching`), fast-forward checks, `force`, leases
  (`--force-with-lease`), all-or-nothing, push options, tags, thin packs, the remote's report per
  reference, remote-tracking references updated and the upstream set.
- Submodules: `repo.submodules()` lists them; `repo.updateSubmodules()` initializes, clones or
  fetches and checks out each at the commit the superproject records.

## Signing

`OpenPGPSigner` (with `std.crypto.openpgp`) and `SSHSigner` (any `std.net.ssh` signer) sign
commits (`CommitOptions{signer}`) and tags; `verifyCommit` and `verifyTag` check them with an
`OpenPGPVerifier` or an `SSHVerifier` over git's allowed-signers file.

## Hosting

`git.Server` is a `std.net.http` handler for the smart protocol (versions 2 and 0): clones,
fetches (shallow, filtered, thin) and pushes, with the program deciding which repository a path
names and who may read or push:

```adm
let server = new git.Server(def (path string) !?git.Repository {
	return try git.open("/srv/git/" + path)
})
server.authorize = def (ctx http.Context, path string, push bool) !none {
	fail http.HttpError{message: "log in to push", status: 401} when push && !ctx.authenticated
	return none
}
```

Without `authorize` every fetch is served and every push refused.

## Tests

`adm test dev/git` runs on fixtures git made (`testdata/*.sh` rebuilds them with git) and on
repositories it creates. Diffs, merges, ignore rules, attributes, blame, patches, status, packs
and bitmaps are compared with git's own answers; clones, fetches and pushes run against the
library's server over HTTP, SSH and in-process, in protocol 2 and 0.

- `ADM_TEST_GIT_BINARY=git` adds the interoperability suite: the client against git's own
  `upload-pack` and `receive-pack`, git's client against `git.Server`, `git fsck`, `verify-pack`
  and `rev-list --test-bitmap` on what the library writes.
- `ADM_TEST_GIT_KEEP=<dir>` keeps the repositories the tests make, to inspect with git.
- `ADM_TEST_GIT_PACK=<pack>` indexes a large pack and compares the index with git's.
- `ADM_TEST_GIT_DIFF_CORPUS` and `ADM_TEST_GIT_MERGE_CORPUS` add generated diff and merge cases.
