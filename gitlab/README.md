# adm.dev.gitlab

A GitLab API client in ADM over `std.net.http`: REST and GraphQL, webhooks, and the tokens
GitLab issues to people, projects, groups and CI jobs. Self-managed instances work through a
base URL.

```bash
adm get admlang:adm.dev.gitlab
```

```adm
use adm.dev.gitlab

let gl = try gitlab.connect(token, url = "https://gitlab.example.com")   // GITLAB_TOKEN, gitlab.com when left out
let project = gl.project("group/app")
for let mr in try project.mergeRequests(state = gitlab.State.Opened).all() {
	print("!{mr.iid} {mr.title}")
}
let pipeline = try project.pipelines.create("main", variables = {"DEPLOY": "staging"})
```

## Tests

`adm test dev/gitlab` runs against a loopback server that answers as GitLab does.
`ADM_TEST_GITLAB_TOKEN`, `ADM_TEST_GITLAB_URL` and `ADM_TEST_GITLAB_PROJECT` add calls to a
real instance in a scratch project the token may write to.
