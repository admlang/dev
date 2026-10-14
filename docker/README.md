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

## Tests

`adm test dev/docker` runs against a loopback server that answers as the engine does.
`ADM_TEST_DOCKER=<host>` adds calls to a real engine, with containers, images, volumes and
networks named `adm-test-*` that the suite creates and removes.
