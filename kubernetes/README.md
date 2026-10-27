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

## Connecting

`kubernetes.connect()` takes the current context of the kubeconfig files `KUBECONFIG` lists
(merged as kubectl merges them), else of `~/.kube/config`, else the pod the program runs in.
Name a context or a file, or build a `Config` yourself:

```adm
let staging = try kubernetes.connect(context = "staging")
let other = try kubernetes.connect(kubeconfig = "/etc/ci/kubeconfig")
let inside = try kubernetes.connect(try kubernetes.inCluster())
let config = try (try kubernetes.loadKubeconfig()).config("prod")
config.timeout = 10s
config.impersonate = kubernetes.Impersonation{user: "auditor", groups: ["read-only"]}
let prod = try kubernetes.connect(config)
```

Credentials are what the context says: client certificates, a bearer token or token file (read
again as a pod's token is renewed), basic credentials, an exec plugin (`aws eks get-token`,
`gke-gcloud-auth-plugin`, `kubelogin`; run again when its credential expires or the server
refuses it) or the `oidc` auth provider (its id token renewed with the refresh token). The
server's certificate is checked against the context's CA, and a `proxy-url` (`http`, `https`,
`socks5`) is used when set.

## Typed resources

Each kind of the core, apps, batch, networking, rbac, storage, autoscaling, policy and
coordination groups has a type (`Pod`, `Deployment`, `Ingress`, `Role`, `Lease`, ...) and an
accessor on the cluster: `pods(namespace)` for a kind that lives in namespaces (the context's
namespace when none is named), `nodes()` for one of the whole cluster.

```adm
let maps = cluster.configMaps("shop")
let settings = new kubernetes.ConfigMap()
settings.name = "settings"
settings.data = {"mode": "fast"}
try maps.create(settings)

let current = try maps.get("settings")
current.data["mode"] = "slow"
try maps.update(current)                          // fails with Conflict when it changed since
try maps.patch("settings", {"data": {"extra": "1"}})
try maps.delete("settings")

let running = try cluster.pods().inAllNamespaces().list(fields = "status.phase=Running")
let first = try cluster.pods("shop").page(limit = 50)
let rest = try cluster.pods("shop").page(limit = 50, continueToken = first.continueToken())
```

Server-side apply sends only the fields a program means to own:

```adm
let web = new kubernetes.Deployment()
web.name = "web"
web.spec.replicas = 2
web.spec.selector = kubernetes.LabelSelector{matchLabels: {"app": "web"}}
web.spec.template.metadata.labels = {"app": "web"}
web.spec.template.spec.containers = [kubernetes.Container{name: "web", image: "nginx:1.27"}]
try cluster.deployments("shop").apply(web, fieldManager = "shop-deployer")
```

`patch` takes a merge patch (the default), a strategic merge patch (`PatchKind.Strategic`) or a
JSON patch (`PatchKind.Json`); `updateStatus`, `patchStatus` and `applyStatus` write the
`status` subresource; `delete` takes the propagation, a grace period and preconditions.
Deployments, stateful sets and replica sets `scale`, pods `evict`, service accounts hand out a
`token`.

## Watching

`watch` reports changes as they happen and asks again from the last version it saw when the
connection drops. An informer keeps a local copy in step and lists again when a watch can go on
no longer:

```adm
let informer = cluster.pods("shop").informer(labels = "app=web")
informer.onAdd = def (pod kubernetes.Pod) none {
	print("up: {pod.name}")
}
informer.onUpdate = def (old kubernetes.Pod, pod kubernetes.Pod) none {
	print("{pod.name}: {old.status.phase} -> {pod.status.phase}")
}
informer.onDelete = def (pod kubernetes.Pod) none {
	print("gone: {pod.name}")
}
informer.start()
try informer.waitForSync()
print("{informer.list().len()} pods")
```

## Custom resources and discovery

Any kind the server serves is reached as `Unstructured`, its fields by dotted path:

```adm
let tabs = try cluster.objects("stable.example.com/v1", "CronTab", "shop")
let tab = try tabs.get("nightly")
print(tab.text("spec.cronSpec"))
tab.set("spec.replicas", 2)
try tabs.update(tab)

let groups = try cluster.groups()                 // the core group first, named ""
let served = try cluster.resources("apps/v1")
let schema = try cluster.schema("apps/v1", "Deployment")
```

## Pods: logs, exec, port-forward

Exec, attach and port-forward go over a WebSocket, or SPDY/3.1 for a server that takes none.

```adm
for let line in try pods.follow("web-7f9d", tail = 10) {
	print(line)
}

let result = try pods.run("web-7f9d", ["sh", "-c", "cat /etc/hostname"])
print("{result.exitCode}: {result.stdout}")

let shell = try pods.exec("web-7f9d", ["sh"], stdin = true, tty = true)
try shell.stdin.write("ls\n".bytes())
try shell.resize(120, 40)

let forward = try pods.portForward("web-7f9d")
let local = try forward.listen(8080, localPort = 18080)  // http://127.0.0.1:18080 reaches the pod
let stream = try forward.dial(5432)                     // one connection, as a net.Stream
```

## Leader election

```adm
let election = cluster.leaderElection("shop-controller", identity = "pod-a", namespace = "shop")
election.onStartedLeading = def () none {
	print("leading")
}
election.onStoppedLeading = def () none {
	print("no longer leading")
}
try election.run()                                // returns once leadership is lost or close() is called
```

## Manifests

```adm
let objects = try kubernetes.readManifests(text)  // YAML documents or JSON, Lists expanded
let deployment = new kubernetes.Deployment()
try objects[0].decode(deployment)
print(kubernetes.writeManifests([deployment]))
try cluster.apply(text, fieldManager = "shop-deployer", namespace = "shop")
```

## Errors

Calls fail with a `KubernetesError`: its `kind` says what went wrong (`NotFound`,
`AlreadyExists`, `Conflict`, `Invalid`, `Denied`, `Expired`, `Throttled`, `TimedOut`,
`Connection`, ...), `status` the HTTP status, `reason` and `details` what the server's Status
said. A request the server throttles (429) is sent again after its `Retry-After`.

## Tests

`adm test dev/kubernetes` runs against a loopback server that answers as an API server does.
`ADM_TEST_KUBECONFIG=<file>` adds calls to a real cluster, in a namespace named `adm-test-*`
the suite creates and deletes.

The typed resources (`types.adm`, `kinds.adm`, `kinds_test.adm`) are generated by
`tools/generate.py` from the OpenAPI document and the Go types of a Kubernetes release.
