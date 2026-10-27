#!/usr/bin/env python3
"""Generate the typed resources of adm.dev.kubernetes from the Kubernetes
OpenAPI v2 document and the Go sources of the API types.

Usage: python3 tools/generate.py <swagger.json> <go folder> <library folder>

<swagger.json> is api/openapi-spec/swagger.json of a Kubernetes release.
<go folder> holds the types.go of each group in scope, named after the
group and version (core_v1.go, apps_v1.go, ..., autoscaling_v2.go), and
meta_types.go of k8s.io/apimachinery/pkg/apis/meta/v1; they say which
fields Go declares as pointers and which it leaves out when empty, which
the OpenAPI document does not.

Writes types.adm (every object of the groups in scope), kinds.adm (a
Cluster accessor for every kind with a list endpoint, and the table of
those kinds `Cluster.kind` looks in before asking the server) and
kinds_test.adm (a call of every accessor against the loopback server of
protocol_test.adm), formatted with `adm fmt`. The rest of the library is
written by hand.
"""
import json
import os
import re
import subprocess
import sys

KEYWORDS = set("""application library plugin module check view style component datatype
service type union interface enum struct partial internal let const def use atomic weak
async cuda sql infix meta return fail expects provides begin rollback transaction defer
yield try onerror await if else when unless as is in for forall every empty break finish
switch match select where case continue default assert self none true false""".split())

# The groups in scope, each at the version the library speaks.
GROUPS = {
    "core.v1": ("", "v1"),
    "apps.v1": ("apps", "v1"),
    "batch.v1": ("batch", "v1"),
    "networking.v1": ("networking.k8s.io", "v1"),
    "rbac.v1": ("rbac.authorization.k8s.io", "v1"),
    "storage.v1": ("storage.k8s.io", "v1"),
    "autoscaling.v2": ("autoscaling", "v2"),
    "policy.v1": ("policy", "v1"),
    "coordination.v1": ("coordination.k8s.io", "v1"),
}

# autoscaling/v1 is in scope only for the scale subresource.
EXTRA = {
    "io.k8s.api.autoscaling.v1.Scale",
    "io.k8s.api.autoscaling.v1.ScaleSpec",
    "io.k8s.api.autoscaling.v1.ScaleStatus",
}

# authentication/v1 is in scope for the token subresource of service
# accounts.
EXTRA |= {
    "io.k8s.api.authentication.v1.TokenRequest",
    "io.k8s.api.authentication.v1.TokenRequestSpec",
    "io.k8s.api.authentication.v1.TokenRequestStatus",
    "io.k8s.api.authentication.v1.BoundObjectReference",
}

# Definitions that share a name with another in scope, by the name they
# take.
TYPE_NAMES = {
    "io.k8s.api.storage.v1.TokenRequest": "CSITokenRequest",
    # Kinds named like a type of the prelude, which a library does not
    # declare: the kind on the wire stays theirs.
    "io.k8s.api.core.v1.Secret": "SecretResource",
    "io.k8s.api.core.v1.Service": "ServiceResource",
    "io.k8s.api.core.v1.Event": "EventResource",
    "io.k8s.api.rbac.v1.Role": "RoleResource",
}

# OpenAPI definitions the library maps to ADM types of its own.
MAPPED = {
    "io.k8s.apimachinery.pkg.apis.meta.v1.Time": "time.Time",
    "io.k8s.apimachinery.pkg.apis.meta.v1.MicroTime": "MicroTime",
    "io.k8s.apimachinery.pkg.util.intstr.IntOrString": "IntOrString",
    "io.k8s.apimachinery.pkg.api.resource.Quantity": "string",
    "io.k8s.apimachinery.pkg.runtime.RawExtension": "any",
    "io.k8s.apimachinery.pkg.apis.meta.v1.FieldsV1": "any",
    "io.k8s.apimachinery.pkg.apis.meta.v1.Patch": "any",
}

# Definitions written by hand: a watch event is generic over its object.
EXCLUDED = {
    "io.k8s.apimachinery.pkg.apis.meta.v1.WatchEvent",
}

# Field names that are ADM keywords, by type and field; `type` is renamed
# everywhere.
RENAMED = {
    ("GRPCAction", "service"): "serviceName",
    ("IngressBackend", "service"): "serviceBackend",
    ("EventSource", "component"): "componentName",
    ("LimitRangeItem", "default"): "defaults",
    ("ListMeta", "continue"): "continueToken",
}

# Group and version as the Go file of their types is named.
GO_FILES = ["core_v1", "apps_v1", "authentication_v1", "batch_v1", "networking_v1", "rbac_v1", "storage_v1",
            "autoscaling_v1", "autoscaling_v2", "policy_v1", "coordination_v1", "meta_types"]


def in_scope(name):
    if name in EXTRA:
        return True
    if name in MAPPED or name in EXCLUDED:
        return False
    if name.startswith("io.k8s.apimachinery.pkg.apis.meta.v1.") or name == "io.k8s.apimachinery.pkg.version.Info":
        return True
    parts = name.split(".")
    return name.startswith("io.k8s.api.") and ".".join(parts[-3:-1]) in GROUPS


def go_key(name):
    """The Go file a definition's struct lives in."""
    if "apimachinery" in name:
        return "meta_types"
    parts = name.split(".")
    return parts[-3] + "_" + parts[-2]


class GoField:
    def __init__(self, gotype, options):
        self.pointer = gotype.startswith("*")
        self.omitempty = "omitempty" in options or "omitzero" in options


def parse_go(folder):
    """Struct fields by file, struct and JSON name, inline embeddings
    resolved."""
    structs = {}
    for key in GO_FILES:
        path = os.path.join(folder, key + ".go")
        if not os.path.exists(path):
            continue
        text = open(path).read()
        found = {}
        for match in re.finditer(r"^type (\w+) struct \{\n(.*?)^\}", text, re.S | re.M):
            fields, inline = {}, []
            for line in match.group(2).split("\n"):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                field = re.match(r'(\w+)\s+(\S+)\s+`json:"([^"]*)"', line)
                # An embedded struct: inline, or a field under its JSON name
                # (`metav1.ObjectMeta `json:"metadata"``).
                embedded = re.match(r'(\*?)(?:\w+\.)?(\w+)\s+`json:"([^"]*)"', line)
                if field:
                    tag = field.group(3).split(",")
                    gotype = field.group(2)
                elif embedded:
                    tag = embedded.group(3).split(",")
                    gotype = embedded.group(1) + embedded.group(2)
                    if tag[0] == "" and "inline" in tag:
                        inline.append(embedded.group(2))
                        continue
                else:
                    continue
                if tag[0] and tag[0] != "-":
                    fields[tag[0]] = GoField(gotype, tag[1:])
            found[match.group(1)] = (fields, inline)
        structs[key] = found
    every = {}
    for found in structs.values():
        for name, entry in found.items():
            every.setdefault(name, entry)

    def fields_of(found, name, seen=()):
        # An embedded struct of another file (metav1.TypeMeta) is looked
        # up among all of them.
        fields, inline = found.get(name) or every.get(name, ({}, []))
        out = dict(fields)
        for inner in inline:
            if inner not in seen:
                out.update(fields_of(found, inner, seen + (name,)))
        return out

    return {key: {name: fields_of(found, name) for name in found} for key, found in structs.items()}


def short(name):
    return TYPE_NAMES.get(name, name.split(".")[-1])


def adm_type(schema):
    """The ADM type of a property schema and whether it is a struct of the
    library (which a field holds by value with a default instance)."""
    ref = schema.get("$ref")
    if ref:
        target = ref.split("/")[-1]
        if target in MAPPED:
            return MAPPED[target], False
        return short(target), True
    kind = schema.get("type")
    if kind == "array":
        inner, _ = adm_type(schema.get("items", {}))
        return inner + "[]", False
    if kind == "object":
        values = schema.get("additionalProperties")
        if values:
            inner, _ = adm_type(values)
            return f"map<string, {inner}>", False
        return "map<string, any>", False
    return {"string": "string", "integer": "int", "number": "float", "boolean": "bool"}.get(kind, "any"), False


def first_sentence(text):
    text = " ".join(text.split())
    match = re.match(r"(.+?[a-z0-9)\]'\"`]\.)(\s|$)", text)
    return match.group(1) if match else text


def comment(text, indent):
    words = first_sentence(text).split()
    lines, line = [], ""
    for word in words:
        if line and len(line) + len(word) + 1 > 96 - len(indent) * 4:
            lines.append(line)
            line = word
        else:
            line = word if not line else line + " " + word
    if line:
        lines.append(line)
    return "".join(f"{indent}// {l}\n" for l in lines)


def field_name(owner, name):
    if (owner, name) in RENAMED:
        return RENAMED[(owner, name)]
    if name[:1].isupper():
        # The API writes a few names capitalised (DaemonEndpoint.Port).
        return name[0].lower() + name[1:]
    if name == "type":
        return "typeName"
    if name in KEYWORDS or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        sys.exit(f"generate: field {owner}.{name} needs a name")
    return name


ZERO = {"int": "0", "float": "0.0", "string": '""', "bool": "false"}


def kind_of(definition):
    """(group, version, kind) of a definition that is an API kind."""
    # A kind of meta every group serves (DeleteOptions, Status) is written
    # as the core group's.
    entries = definition.get("x-kubernetes-group-version-kind", [])
    for gvk in sorted(entries, key=lambda e: e["group"] != ""):
        return gvk["group"], gvk["version"], gvk["kind"]
    return None


def write_types(definitions, go, folder, kinds):
    names = sorted((n for n in definitions if in_scope(n)), key=short)
    seen = {}
    for name in names:
        if short(name) in seen:
            sys.exit(f"generate: {name} and {seen[short(name)]} share a name")
        seen[short(name)] = name
    missing = []
    out = ["// Generated by tools/generate.py from the Kubernetes OpenAPI document; do not edit.\n",
           "partial module adm.dev.kubernetes {\n",
           "\tuse (\n\t\tstd.data.encoding::(json)\n\t\tstd.time\n\t)\n"]
    for name in names:
        definition = definitions[name]
        local = short(name)
        go_fields = go.get(go_key(name), {}).get(name.split(".")[-1])
        if go_fields is None:
            missing.append(name)
            go_fields = {}
        properties = definition.get("properties", {})
        required = set(definition.get("required", []))
        gvk = kind_of(definition)
        is_kind = gvk is not None and gvk[2] in kinds and (kinds[gvk[2]]["group"], kinds[gvk[2]]["version"]) == (gvk[0], gvk[1])
        out.append("\n" + comment(definition.get("description", local), "\t"))
        out.append(f"\t{'type' if is_kind else 'struct'} {local} {{\n")
        for prop, schema in properties.items():
            adm, is_struct = adm_type(schema)
            goinfo = go_fields.get(prop)
            pointer = goinfo.pointer if goinfo else (prop not in required)
            omit = goinfo.omitempty if goinfo else (prop not in required)
            options = []
            local_name = field_name(local, prop)
            if local_name != prop:
                options.append(f'"{prop}"')
            default = None
            if prop == "apiVersion" and gvk:
                group, version, _ = gvk
                default = '"' + (f"{group}/{version}" if group else version) + '"'
                omit = True
            elif prop == "kind" and gvk:
                default = f'"{gvk[2]}"'
                omit = True
            if default is not None:
                declared = f"{adm} = {default}"
            elif adm in ("time.Time", "MicroTime"):
                # Go writes a zero time as null, whatever the field's tag.
                declared = f"?{adm}"
                omit = True
            elif is_struct and not pointer:
                declared = f"{adm} = {adm}{{}}"
            elif adm == "IntOrString" and not pointer:
                declared = "IntOrString = IntOrString{}"
            elif pointer and (is_struct or adm in ZERO or adm == "IntOrString"):
                declared = f"?{adm}"
                omit = True
            elif adm.endswith("[]"):
                declared = f"{adm} = []"
            elif adm.startswith("map<"):
                declared = f"{adm} = {{}}"
            elif adm == "any":
                declared = "any"
                omit = True
            else:
                declared = f"{adm} = {default or ZERO[adm]}"
            if omit:
                options.append("omitempty = true")
            description = schema.get("description", "")
            if schema.get("format") == "byte" or (schema.get("additionalProperties") or {}).get("format") == "byte":
                description = "Base64 text: " + description
            if description:
                out.append(comment(description, "\t\t"))
            if options:
                out.append(f"\t\t@json({', '.join(options)})\n")
            out.append(f"\t\t{local_name} {declared}\n")
        if is_kind:
            out.append(KIND_MEMBERS.format(kind=local))
        if not properties and not is_kind:
            # An object the API leaves open, which travels as its JSON.
            out.append("\t\t@json(omitempty = true)\n\t\tvalue any\n")
        out.append("\t}\n")
    out.append("}\n")
    open(os.path.join(folder, "types.adm"), "w").write("".join(out))
    return missing


KIND_MEMBERS = """
		// The object's `metadata.name`.
		name string {{
			def get() string {{
				return metadata.name
			}}

			def set(given string) {{
				metadata.name = given
			}}
		}}

		// The object's `metadata.namespace`; empty for a kind of the
		// whole cluster.
		namespace string {{
			def get() string {{
				return metadata.namespace
			}}

			def set(given string) {{
				metadata.namespace = given
			}}
		}}

		// Implements Object.
		def objectMeta() ObjectMeta {{
			return metadata
		}}
"""


def accessor_name(kind):
    # A leading acronym goes lower case whole: CSIDriver is csiDrivers.
    lead = re.match(r"[A-Z]+(?=[A-Z][a-z]|$)", kind)
    cut = len(lead.group(0)) if lead and len(lead.group(0)) > 1 else 1
    name = kind[:cut].lower() + kind[cut:]
    if kind.endswith("Endpoints"):
        return name
    if re.search(r"[^aeiou]y$", name):
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "ch", "sh")):
        return name + "es"
    return name + "s"


def find_kinds(document):
    """Kinds with a list endpoint, by name: group, version, plural, whether
    namespaced, subresources."""
    wanted = {group: version for group, version in GROUPS.values()}
    kinds = {}
    for path, operations in document["paths"].items():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            gvk = operation.get("x-kubernetes-group-version-kind")
            if not gvk or wanted.get(gvk["group"]) != gvk["version"]:
                continue
            if operation.get("x-kubernetes-action") != "list" or "{name}" in path or "/watch/" in path:
                continue
            entry = kinds.setdefault(gvk["kind"], {"group": gvk["group"], "version": gvk["version"], "namespaced": False, "subresources": set()})
            entry["plural"] = path.rstrip("/").split("/")[-1]
            entry["namespaced"] |= "{namespace}" in path
    for path in document["paths"]:
        for entry in kinds.values():
            marker = "/" + entry["plural"] + "/{name}/"
            if marker in path:
                entry["subresources"].add(path.split(marker)[1].split("/")[0])
    return kinds


def kind_info(entry, kind):
    namespaced = "true" if entry["namespaced"] else "false"
    return (f'ResourceKind{{group: "{entry["group"]}", version: "{entry["version"]}", kind: "{kind}", '
            f'plural: "{entry["plural"]}", namespaced: {namespaced}}}')


def type_of_kind(kind, entry, definitions):
    """The ADM type of a kind: its definition's name."""
    for name, definition in definitions.items():
        if kind_of(definition) == (entry["group"], entry["version"], kind):
            return short(name)
    return kind


def write_kinds(kinds, definitions, folder):
    out = ["// Generated by tools/generate.py from the Kubernetes OpenAPI document; do not edit.\n",
           "partial module adm.dev.kubernetes {\n",
           "\tuse adm.dev.kubernetes.api::*\n\n"]
    out.append("\tpartial type Cluster {\n")
    first = True
    for kind in sorted(kinds):
        entry = kinds[kind]
        definition = next(d for n, d in definitions.items() if kind_of(d) == (entry["group"], entry["version"], kind))
        accessor = accessor_name(kind)
        adm = type_of_kind(kind, entry, definitions)
        api = (entry["group"] + "/" if entry["group"] else "") + entry["version"]
        if accessor.lower() != entry["plural"]:
            sys.exit(f"generate: {kind} would be {accessor}, its plural is {entry['plural']}")
        out.append("" if first else "\n")
        first = False
        out.append(comment(f"The {kind} objects of {api}" + (" in a namespace, the context's when none is named." if entry["namespaced"] else ", which belong to the whole cluster."), "\t\t"))
        out.append(comment(definition.get("description", ""), "\t\t"))
        if entry["namespaced"]:
            out.append(f"\t\tdef {accessor}(namespace string = \"\") Resources<{adm}> {{\n")
            out.append(f"\t\t\treturn new Resources<{adm}>(core, {kind_info(entry, kind)}, namespaceOr(namespace))\n")
        else:
            out.append(f"\t\tdef {accessor}() Resources<{adm}> {{\n")
            out.append(f"\t\t\treturn new Resources<{adm}>(core, {kind_info(entry, kind)}, \"\")\n")
        out.append("\t\t}\n")
    out.append("\t}\n\n")
    out.append("\t// Every kind the library has a type for, as the API serves it.\n")
    out.append("\tinternal def knownKinds() ResourceKind[] {\n\t\treturn [\n")
    for kind in sorted(kinds):
        out.append(f"\t\t\t{kind_info(kinds[kind], kind)},\n")
    out.append("\t\t]\n\t}\n}\n")
    open(os.path.join(folder, "kinds.adm"), "w").write("".join(out))
    write_kinds_test(kinds, folder)


def write_kinds_test(kinds, folder):
    """A test that lists every kind through its accessor against the
    loopback server of protocol_test.adm and checks the path."""
    out = ["// Generated by tools/generate.py from the Kubernetes OpenAPI document; do not edit.\n",
           "partial module adm.dev.kubernetes {\n",
           "\tuse std.testing\n\n",
           "\tcheck \"Kinds\" {\n",
           "\t\t@test()\n",
           "\t\tdef TestEveryAccessor() !none {\n",
           "\t\t\tlet fake = new FakeServer()\n",
           "\t\t\tlet server = try fakeServer(fake)\n",
           "\t\t\tdefer server.close()\n",
           "\t\t\tlet cluster = try fakeCluster(server)\n"]
    for kind in sorted(kinds):
        entry = kinds[kind]
        base = f"/apis/{entry['group']}/{entry['version']}" if entry["group"] else f"/api/{entry['version']}"
        path = f"{base}/namespaces/shop/{entry['plural']}" if entry["namespaced"] else f"{base}/{entry['plural']}"
        call = f"cluster.{accessor_name(kind)}()"
        out.append(f"\t\t\ttry {call}.page()\n")
        out.append(f"\t\t\tassert fake.seen[fake.seen.len() - 1] == \"GET {path}\"\n")
    out.append("\t\t\treturn none\n\t\t}\n\t}\n}\n")
    open(os.path.join(folder, "kinds_test.adm"), "w").write("".join(out))


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    document = json.load(open(sys.argv[1]))
    go = parse_go(sys.argv[2])
    definitions = document["definitions"]
    kinds = find_kinds(document)
    missing = write_types(definitions, go, sys.argv[3], kinds)
    write_kinds(kinds, definitions, sys.argv[3])
    # The files as `adm fmt` lays them out, columns aligned.
    written = [os.path.join(sys.argv[3], name) for name in ("types.adm", "kinds.adm", "kinds_test.adm")]
    subprocess.run(["adm", "fmt", *written], check=True, stdout=subprocess.DEVNULL)
    for name in missing:
        print(f"generate: no Go struct for {name}; fields follow the OpenAPI required list", file=sys.stderr)


main()
