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
for let issue in try repo.issues.list(state = github.State.Open).all() {
	print("#{issue.number} {issue.title}")
}
let pr = try repo.pullRequests.create("feature", "main", title = "Add geo examples")
try pr.comment("Ready for review")

// a webhook endpoint
let hooks = new github.Webhooks(secret)
try server.handle("/github", hooks.handler((event github.WebhookEvent) => print(event.name)))
```

## Signing in

```adm
let mine = try github.connect()                   // GITHUB_TOKEN, then GH_TOKEN; anonymous without
let ghes = try github.connect(token, url = "https://ghe.example.com")

let app = try github.connectApp("123456", privateKeyPem)
let installed = try app.installationFor("admlang", "examples")   // its token renewed before it expires
let scoped = app.asInstallation(42, repositories = ["examples"], permissions = {"contents": "read"})

let flow = try github.deviceFlow(clientId, scopes = ["repo"])
print("Open {flow.verificationUri} and enter {flow.userCode}")
let user = try flow.poll()
```

In Actions, `GITHUB_TOKEN`, `GITHUB_API_URL` and `GITHUB_GRAPHQL_URL` are read when nothing
else is given. `github.oauthClient(clientId, clientSecret)` is the OAuth client of the web flow.

## REST

Every operation of the repositories, issues, pull requests, checks, git, actions, organizations,
teams, users, gists, search, packages, apps, reactions and activity groups is a method on the
object its path names: the client (`gh.search`, `gh.gists`, `gh.app`), a repository
(`gh.repo(owner, name)`), an organization (`gh.org(name)`, `.team(slug)`), a user
(`gh.user(login)`) or the signed-in user (`gh.me`), and the collections under them. Required
parameters come first, the others are named and left out when not given.

```adm
let release = try repo.releases.create("v1.0.0", name = "1.0", generateReleaseNotes = true)
try repo.releases.uploadAsset(release.id, "adm-linux.tar.gz", archive)
let runs = try repo.actions.runs.list(branch = "main", status = "failure").all(limit = 20)
for let run in runs {
	print("{run.name}: {run.htmlUrl}")
}
try repo.pullRequests.merge(7, mergeMethod = "squash")
let found = try gh.search.repos("language:adm stars:>10").page(1)
```

Lists are `Pages`: `all()` walks every page as the iteration needs it (at most `limit` objects
when given), `page(n, size)` reads one. GETs are conditional on the ETag of the last answer, so
an unchanged answer costs no rate limit. The rate limit is read from every answer
(`gh.limits()`); a request refused for it waits until it resets, at most `rateLimitWait`
(15 minutes), and a secondary limit's `Retry-After` is honoured.

## GraphQL

```adm
struct Viewer { login string = "" }
struct ViewerData { viewer Viewer = Viewer{} }

let me = ViewerData{}
try gh.graphql(`query { viewer { login } }`, target = me)

struct IssueNode { number int = 0  title string = "" }
let query = `query($cursor: String) { repository(owner: "admlang", name: "adm") { issues(first: 100, after: $cursor) { pageInfo { hasNextPage endCursor } nodes { number title } } } }`
for let issue in try gh.graphqlPages<IssueNode>(query, "repository.issues").all() {
	print(issue.title)
}
```

## Webhooks

`Webhooks.handler` checks each delivery's `X-Hub-Signature-256` against the secret and hands it
out as a `WebhookEvent`; its accessor for the event decodes the typed payload:

```adm
let hooks = new github.Webhooks(secret)
try server.handle("/github", hooks.handler(def (event github.WebhookEvent) !none {
	let opened = try event.pullRequest()
	if !(opened is none) && event.action == "opened" {
		print("PR #{opened.number} opened")
	}
	return none
}))
```

The deliveries of a hook are REST operations (`repo.hooks.listDeliveries`,
`repo.hooks.redeliverDelivery`, `gh.app.listWebhookDeliveries`).

## Errors

Calls fail with a `GitHubError`: its `kind` (`NotFound`, `Denied`, `Invalid`, `RateLimited`,
`Conflict`, `Query`, ...), the HTTP `status`, the `problems` of a 422 and GitHub's `requestId`.

## Tests

`adm test dev/github` runs against a loopback server that answers as GitHub does.
`ADM_TEST_GITHUB_TOKEN` and `ADM_TEST_GITHUB_REPO=owner/name` add calls to the real API in a
scratch repository the token may write to.

The REST layer (`types.adm`, `operations.adm`, `events.adm`, `operations_test.adm`) is generated
by `tools/generate.py` from GitHub's OpenAPI description.
