# ADM developer platform libraries

Official [ADM](https://github.com/admlang/adm) libraries for the tools around a codebase: version
control, containers, clusters and the hosting platforms. Each is written in ADM over `std.net`,
published as `adm.dev.<name>` and signed by the `admlang` publisher. They live outside the
standard library because these APIs change with every release of the product behind them.

| Library | Talks to | How |
|---|---|---|
| [`adm.dev.git`](git/) | Git repositories, local and remote | the object store and packfiles in ADM, smart HTTP and SSH transports |
| [`adm.dev.docker`](docker/) | Docker Engine, Podman | the Engine API over a Unix socket or TCP with TLS |
| [`adm.dev.kubernetes`](kubernetes/) | Kubernetes clusters | the API server over HTTPS, kubeconfig and in-cluster credentials |
| [`adm.dev.github`](github/) | GitHub, GitHub Enterprise | REST, GraphQL, webhooks, GitHub Apps |
| [`adm.dev.gitlab`](gitlab/) | GitLab, self-managed GitLab | REST, GraphQL, webhooks |

## Tests

Each suite runs without a server where it can (repositories on disk, loopback servers that
answer as the platform does), and against the real thing when told where it is:

```bash
ADM_TEST_DOCKER=unix:///var/run/docker.sock adm test dev/docker
ADM_TEST_KUBECONFIG=~/.kube/config adm test dev/kubernetes     # a cluster the suite may create a namespace in
ADM_TEST_GITHUB_TOKEN=... adm test dev/github                  # a token with access to a scratch repository
ADM_TEST_GITLAB_TOKEN=... adm test dev/gitlab
```
