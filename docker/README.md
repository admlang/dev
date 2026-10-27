# adm.dev.docker

A Docker Engine API client in ADM over `std.net.http`, on a Unix socket or TCP with TLS. It
also talks to Podman through its Docker-compatible API.

```bash
adm get admlang:adm.dev.docker
```

```adm
use adm.dev.docker

let engine = try docker.connect()                 // DOCKER_HOST, else the local socket
try engine.images.pull("postgres:17")
let pg = try engine.containers.create("postgres:17", name = "db", env = {"POSTGRES_PASSWORD": "secret"}, ports = {"5432/tcp": 5432})
try pg.start()
for let line in try pg.logs(follow = true) {
	print(line.text)
}
let result = try pg.exec("pg_isready")
try pg.stop()
try pg.remove(volumes = true)
```

## Connecting

`docker.connect(host)` takes `unix:///var/run/docker.sock`, `tcp://host:2375`,
`tcp://host:2376` or `https://...` for TLS. Without a host it looks where the Docker CLI looks:

1. `DOCKER_HOST`, with `DOCKER_TLS_VERIFY` and `DOCKER_CERT_PATH` (`ca.pem`, `cert.pem`,
   `key.pem`) for TLS,
2. the context `DOCKER_CONTEXT` names, else the CLI's current context (`docker context use`),
3. the local sockets of Docker, Docker Desktop (`~/.docker/run/docker.sock`) and Podman
   (`$XDG_RUNTIME_DIR/podman/podman.sock`, `/run/podman/podman.sock`).

The client speaks API 1.47, or the engine's version when it is older, down to 1.41 (Docker
20.10, Podman). `engine.apiVersion` says which; set it before `connect` to pin one. Calls that
do not stream time out after `engine.timeout` (2 minutes); logs, events, pulls, builds and waits
run as long as they take. An Engine is safe to share between tasks.

## Containers

```adm
let web = try engine.containers.create(docker.ContainerSpec{
	image: "nginx:1.27",
	ports: {"80/tcp": 8080},
	mounts: [docker.Mount{kind: docker.MountKind.Bind, source: "/srv/site", target: "/usr/share/nginx/html", readOnly: true}],
	restart: docker.RestartPolicy{name: "unless-stopped"},
	healthcheck: docker.HealthCheck{test: ["CMD", "curl", "-f", "localhost"], interval: 10s},
	memory: 256 * 1024 * 1024,
	cpus: 0.5,
}, name = "web")
try web.start()
let info = try web.inspect()                      // state, health, config, networks
let all = try engine.containers.list(all = true, filters = {"label": ["app=web"]})
```

`ContainerSpec` holds everything a container is created with: command, environment, labels,
ports, mounts, networks, restart policy, health check, resources (memory, CPUs, PIDs, ulimits,
devices, GPUs through `deviceRequests`), capabilities and security options, DNS, tmpfs,
logging. A `Container` then starts, stops (with a timeout and a signal), restarts, kills,
pauses, unpauses, renames, updates its resources, resizes its terminal, waits for its exit
code, and is removed. `top`, `changes` and `stats` (once, or `statsStream` each second) report
on it; `cpuPercent()` and `memoryUsed()` read a reading as `docker stats` does.

### Output, exec and attach

```adm
for let line in try web.logs(follow = true, timestamps = true, tail = 100) {
	print("{line.time} {line.stream.value}: {line.text}")
}

let tested = try web.exec("nginx", "-t")          // exitCode, stdout, stderr
let piped = try web.exec(["sh", "-c", "wc -c"], input = data)

let shell = try web.startExec(["sh"], tty = true)
try shell.write("ls /\nexit\n")
for let piece in shell {
	print(piece.text())
}
let code = try shell.wait()
```

A container without a terminal sends stdout and stderr apart; each `LogLine` and `Output` says
which it came on. `attach` connects to the main process the same way, its standard input
included when the container was created with `openStdin`. `closeInput` ends what a session
writes while its output goes on.

### Files

```adm
try web.copyTo("site/", "/usr/share/nginx/html")  // a local file or folder into a folder there
try web.copyFrom("/var/log/nginx", "logs/")
let tar = try web.readArchive("/etc/nginx")       // as tar bytes; writeArchive puts some back
```

## Images

```adm
try engine.images.pull("ghcr.io/acme/api:1.4", progress = def (p docker.Progress) {
	print("{p.id} {p.status} {p.current}/{p.total}")
})
let id = try engine.images.build("./api", tags = ["acme/api:dev"], buildArgs = {"VERSION": "1.4"}, output = def (line string) {
	print(line)
})
try engine.images.tag(id, "registry.acme.com/api", "dev")
try engine.images.push("registry.acme.com/api:dev")
```

`build` sends the folder less what its `.dockerignore` leaves out, as the CLI does, and runs the
classic builder. `list`, `inspect`, `history`, `tag`, `remove`, `prune`, `save` (to a writer)
and `load` cover the rest.

Pulls and pushes take their registry credentials from the `auth` argument, else
`engine.credentials` (by registry host: `ghcr.io`, `docker.io` for Docker Hub), else the Docker
CLI's `config.json`: its `auths`, and the credential helpers `credsStore` and `credHelpers`
name (`docker-credential-desktop`, `-pass`, `-secretservice`, `-ecr-login`, ...). A build gets
the credentials the engine and `config.json` hold for its base images. `engine.login(auth)`
checks credentials as `docker login` does.

## Volumes, networks and the engine

```adm
let data = try engine.volumes.create("pgdata", labels = {"app": "db"})
let back = try engine.networks.create("back", subnets = [docker.Subnet{subnet: "10.20.0.0/16"}])
try engine.networks.connect("back", "db", aliases = ["postgres"])

for let event in try engine.events(filters = {"type": ["container"], "event": ["die"]}) {
	print("{event.actor.attributes["name"]} exited with {event.actor.attributes["exitCode"]}")
}
```

`engine.info()`, `version()`, `diskUsage()` and `ping()` report on the engine itself; every
`prune` returns what it removed and the space it freed.

## Errors

Every failure is a `docker.DockerError` with a `kind`: `Connection`, `TimedOut`, `Closed`,
`Argument` (400), `Denied` (401, 403, or a registry refusing credentials), `NotFound` (404),
`Conflict` (409: a name taken, a container still running), `Protocol`, `Server`. `status` holds
the engine's HTTP status and `message` its words. A pull, push, build or load that fails part
way reports the engine's message the same way.

## Tests

`adm test dev/docker` runs against a loopback server that answers as the engine does.
`ADM_TEST_DOCKER=<host>` adds calls to a real engine, with containers, images, volumes and
networks named `adm-test-*` that the suite creates and removes; it pulls `alpine:3.20`.

```bash
ADM_TEST_DOCKER=unix:///var/run/docker.sock adm test dev/docker
ADM_TEST_DOCKER=unix://$XDG_RUNTIME_DIR/podman/podman.sock adm test dev/docker
```
