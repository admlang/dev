# adm.dev.kubernetes

A Kubernetes API client in ADM over `std.net.http`: the API server, its resources typed or
unstructured, and the credentials a kubeconfig or a pod carries.

```bash
adm get admlang:adm.dev.kubernetes
```

```adm
use adm.dev.kubernetes

let cluster = try kubernetes.connect()            // KUBECONFIG or ~/.kube/config, else in-cluster
let pods = cluster.pods("shop")
for let pod in try pods.list(labels = "app=web") {
	print("{pod.name} {pod.status.phase}")
}
for let event in try pods.watch(labels = "app=web") {
	print("{event.kind} {event.object.name}")
}
let logs = try pods.logs("web-7f9d", container = "web", tail = 100)
try cluster.deployments("shop").scale("web", 3)
```

## Tests

`adm test dev/kubernetes` runs against a loopback server that answers as an API server does.
`ADM_TEST_KUBECONFIG=<file>` adds calls to a real cluster, in a namespace named `adm-test-*`
the suite creates and deletes.
