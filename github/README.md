# adm.dev.github

A GitHub API client in ADM over `std.net.http`: REST and GraphQL, webhooks, and the three ways
to be someone on GitHub (a token, an OAuth app, a GitHub App). GitHub Enterprise Server works
through a base URL.

```bash
adm get admlang:adm.dev.github
```

```adm
use adm.dev.github

let gh = try github.connect(token)                // GITHUB_TOKEN when left out
let repo = gh.repo("admlang", "examples")
for let issue in try repo.issues(state = github.State.Open).all() {
	print("#{issue.number} {issue.title}")
}
let pr = try repo.pullRequests.create("feature", "main", title = "Add geo examples")
try pr.comment("Ready for review")

// a webhook endpoint
let hooks = new github.Webhooks(secret)
server.handle("/github", hooks.handler((event github.Event) => print(event.name)))
```

## Tests

`adm test dev/github` runs against a loopback server that answers as GitHub does.
`ADM_TEST_GITHUB_TOKEN` and `ADM_TEST_GITHUB_REPO=owner/name` add calls to the real API in a
scratch repository the token may write to.
