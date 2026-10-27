#!/usr/bin/env python3
"""Generate the REST layer of adm.dev.github from GitHub's OpenAPI 3.1
description (github/rest-api-description,
descriptions-next/api.github.com/api.github.com.json).

Usage: python3 tools/generate.py <api.github.com.json> <library folder>

Writes types.adm (every schema the operations in scope answer with and
every webhook payload), operations.adm (a method for every operation of
the groups in scope, on the object its path names: the client, a
repository, an organization, a team, a user, the authenticated user, and
the collections under them), events.adm (the typed payloads of webhook
events and their accessors on WebhookEvent) and operations_test.adm (a call of
every operation against the loopback server of github_test.adm),
formatted with `adm fmt`. The client, authentication, paging, rate
limits, GraphQL and the webhook handler are written by hand.
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

# The groups (OpenAPI tags) in scope.
TAGS = {"repos", "issues", "pulls", "checks", "git", "actions", "orgs", "teams", "users",
        "gists", "search", "packages", "apps", "rate-limit", "reactions", "meta", "activity"}

# The objects a path's leading parameters name, longest prefix first:
# (prefix, holder type, its fields in path order).
HOLDERS = [
    ("/repos/{owner}/{repo}", "Repo", ["owner", "repo"]),
    ("/orgs/{org}/teams/{team_slug}", "OrgTeam", ["org", "team_slug"]),
    ("/orgs/{org}", "Org", ["org"]),
    ("/users/{username}", "User", ["username"]),
    ("/user", "Me", []),
]

# Holder fields as the holder types name them.
HOLDER_FIELDS = {"owner": "owner", "repo": "name", "org": "org", "team_slug": "slug", "username": "login"}

# Collection fields named otherwise than their path segment.
NODE_NAMES = {"pulls": "pullRequests"}

# A collection with more operations than this is split by the next segment
# of its paths (`repo.actions.runs`, `repo.actions.secrets`).
SPLIT_ABOVE = 40

# A segment with at most this many operations is no collection: its
# operations are the holder's own methods.
HOIST_UPTO = 2

# Collections whose operations are the holder's own methods, named with
# the collection's noun, by holder and segment; and the names some of
# those take.
HOISTED = {}
HOISTED_NAMES = {}

# Context suffixes an operation's name carries that its holder already
# says.
CONTEXT_SUFFIXES = {
    "Repo": ["-for-repo", "-for-repository", "-in-repo", "-in-a-repository", "-for-a-repository", "-repository"],
    "Org": ["-for-org", "-in-org", "-for-organization", "-in-organization", "-for-an-organization", "-from-org", "-organization"],
    "OrgTeam": ["-in-org", "-for-team", "-legacy"],
    "User": ["-for-user"],
    "Me": ["-for-authenticated-user", "-authenticated-user", "-for-the-authenticated-user", "-for-authenticated", "-by-authenticated", "-authenticated"],
    "Client": [],
}

# Schemas the hand-written part binds to the repository they came from,
# so their own methods can call the API (`pr.comment(...)`).
BOUND = {"PullRequest", "Issue"}

# Schemas named otherwise than their key, where the name is taken: the
# activity feed's event (the webhook Event is the library's own).
SCHEMA_NAMES = {"event": "ActivityEvent", "link": "HypermediaLink", "rate-limit": "RateLimitResource"}

# Query parameters `Pages` sets itself.
PAGING = {"per_page", "page"}

# The hand-written enum of `state` parameters that take open, closed, all.
STATE_VALUES = {"open", "closed", "all"}


def pascal(name):
    parts = re.split(r"[-_. /]+", name)
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    out = re.sub(r"[^A-Za-z0-9]", "", out)
    if out[:1].isdigit():
        out = "N" + out
    return out


def camel(name):
    special = {"+1": "plusOne", "-1": "minusOne"}
    if name in special:
        return special[name]
    out = pascal(name)
    out = out[:1].lower() + out[1:]
    if out == "type":
        return "typeName"
    if out in KEYWORDS or out == "":
        return out + "Value"
    return out


def first_sentence(text):
    text = " ".join((text or "").split())
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    match = re.match(r"(.+?[a-z0-9)\]'\"`]\.)(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence.replace("{", "(").replace("}", ")")


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


class Model:
    """The ADM types of the schemas: their declarations, by name, and the
    type each schema reference maps to."""

    def __init__(self, document):
        self.document = document
        self.schemas = document["components"]["schemas"]
        self.declared = {}       # name -> (kind, body lines, doc)
        self.order = []
        self.by_ref = {}         # schema key -> adm type (without ?)
        self.in_progress = set()

    def resolve(self, schema):
        """A schema with its $ref followed, and whether it was nullable."""
        nullable = False
        seen = 0
        while isinstance(schema, dict) and "$ref" in schema and seen < 20:
            key = schema["$ref"].split("/")[-1]
            schema = self.schemas.get(key, {})
            seen += 1
        return schema

    def is_nullable(self, schema):
        kinds = schema.get("type")
        if isinstance(kinds, list) and "null" in kinds:
            return True
        if schema.get("nullable"):
            return True
        for key in ("anyOf", "oneOf"):
            for alt in schema.get(key, []):
                if alt.get("type") == "null":
                    return True
        return False

    def type_of(self, schema, owner, field):
        """(adm type, nullable) of a property schema."""
        if "$ref" in schema:
            key = schema["$ref"].split("/")[-1]
            nullable = False
            if key.startswith("nullable-") and key[len("nullable-"):] in self.schemas:
                key = key[len("nullable-"):]
                nullable = True
            target = self.schemas.get(key, {})
            nullable = nullable or self.is_nullable(target)
            return self.ref_type(key), nullable
        nullable = self.is_nullable(schema)
        for key in ("anyOf", "oneOf"):
            alts = [a for a in schema.get(key, []) if a.get("type") != "null"]
            if alts:
                if len(alts) == 1:
                    inner, inner_null = self.type_of(alts[0], owner, field)
                    return inner, nullable or inner_null
                resolved = [self.resolve(a) for a in alts]
                if all(self.kind(r) == "object" and r.get("properties") for r in resolved):
                    return self.merged_type(alts, owner + pascal(field)), nullable
                kinds = {self.kind(r) for r in resolved}
                if len(kinds) == 1 and next(iter(kinds)) in ("string", "integer", "number", "boolean"):
                    return self.scalar(resolved[0]), nullable
                return "any", True
        if "allOf" in schema:
            return self.merged_type(schema["allOf"], owner + pascal(field)), nullable
        kind = self.kind(schema)
        if kind == "array":
            inner, _ = self.type_of(schema.get("items", {}), owner, field + "Item" if schema.get("items", {}).get("properties") else field)
            return inner + "[]", False
        if kind == "object":
            if schema.get("properties"):
                name = self.unique(owner + pascal(field))
                self.declare_object(name, schema)
                return name, nullable
            values = schema.get("additionalProperties")
            if isinstance(values, dict) and values:
                inner, _ = self.type_of(values, owner, field + "Value")
                return f"map<string, {inner}>", False
            return "map<string, any>", False
        if kind in ("string", "integer", "number", "boolean"):
            return self.scalar(schema), nullable
        return "any", True

    def kind(self, schema):
        kinds = schema.get("type")
        if isinstance(kinds, list):
            kinds = [k for k in kinds if k != "null"]
            return kinds[0] if len(kinds) == 1 else ("any" if kinds else "null")
        if kinds:
            return kinds
        if schema.get("properties"):
            return "object"
        return "any"

    def scalar(self, schema):
        kind = self.kind(schema)
        if kind == "string":
            return "time.Time" if schema.get("format") == "date-time" else "string"
        return {"integer": "int", "number": "float", "boolean": "bool"}.get(kind, "any")

    def ref_type(self, key):
        if key in self.by_ref:
            return self.by_ref[key]
        schema = self.schemas.get(key, {})
        kind = self.kind(schema)
        if kind in ("string", "integer", "number", "boolean"):
            out = self.scalar(schema)
            self.by_ref[key] = out
            return out
        if kind == "array":
            inner, _ = self.type_of(schema.get("items", {}), pascal(key), "Item")
            self.by_ref[key] = inner + "[]"
            return inner + "[]"
        name = self.unique(SCHEMA_NAMES.get(key, pascal(key)))
        self.by_ref[key] = name
        if any(k in schema for k in ("allOf", "anyOf", "oneOf")) and not schema.get("properties"):
            parts = schema.get("allOf") or [a for a in schema.get("anyOf", []) + schema.get("oneOf", []) if a.get("type") != "null"]
            resolved = [self.resolve(p) for p in parts]
            if parts and all(self.kind(r) == "object" for r in resolved):
                self.declare_merged(name, parts, schema)
                return name
            self.by_ref[key] = "any"
            return "any"
        if kind == "object" and (schema.get("properties") or not schema.get("additionalProperties")):
            self.declare_object(name, schema)
            return name
        values = schema.get("additionalProperties")
        if isinstance(values, dict) and values:
            inner, _ = self.type_of(values, name, "Value")
            self.by_ref[key] = f"map<string, {inner}>"
            return self.by_ref[key]
        self.by_ref[key] = "map<string, any>"
        return "map<string, any>"

    def unique(self, name):
        base = name or "Value"
        out = base
        count = 2
        while out in self.declared or out in self.in_progress:
            out = f"{base}{count}"
            count += 1
        self.in_progress.add(out)
        return out

    def merged_type(self, parts, name):
        name = self.unique(name)
        self.declare_merged(name, parts, {})
        return name

    def declare_merged(self, name, parts, schema):
        properties, required = {}, None
        for part in parts:
            resolved = self.resolve(part)
            for sub in resolved.get("allOf", []):
                inner = self.resolve(sub)
                for k, v in inner.get("properties", {}).items():
                    properties.setdefault(k, v)
            for k, v in resolved.get("properties", {}).items():
                properties.setdefault(k, v)
            req = set(resolved.get("required", []))
            required = req if required is None else (required & req if "allOf" not in schema else required | req)
        merged = {"type": "object", "properties": properties, "required": sorted(required or []),
                  "description": schema.get("description") or (self.resolve(parts[0]).get("description") if parts else "")}
        self.declare_object(name, merged)

    def declare_object(self, name, schema):
        self.in_progress.add(name)
        lines = []
        required = set(schema.get("required", []))
        used = set()
        for prop, sub in schema.get("properties", {}).items():
            adm, nullable = self.type_of(sub, name, prop)
            local = camel(prop)
            while local in used:
                local += "Value"
            used.add(local)
            options = []
            if local != prop:
                options.append(f'"{prop}"')
            options.append("omitempty = true")
            optional = nullable or prop not in required
            if adm.endswith("[]"):
                declared = f"{adm} = []"
            elif adm.startswith("map<"):
                declared = f"{adm} = {{}}"
            elif adm == "any":
                declared = "any"
            elif adm == "time.Time":
                declared = "?time.Time" if optional else "time.Time = time.Time{}"
            elif adm in ("int", "float", "string", "bool"):
                if nullable:
                    declared = f"?{adm}"
                else:
                    declared = f"{adm} = {ZERO[adm]}"
            else:
                declared = f"?{adm}" if optional else f"{adm} = {adm}{{}}"
            description = sub.get("description") or self.resolve(sub).get("description", "")
            doc = comment(description, "\t\t") if description else ""
            lines.append(doc + f"\t\t@json({', '.join(options)})\n\t\t{local} {declared}\n")
        if not lines:
            lines.append("\t\t@json(omitempty = true)\n\t\tvalue any\n")
        self.declared[name] = ("partial type" if name in BOUND else "struct", lines, schema.get("description") or schema.get("title") or name)
        self.order.append(name)
        self.in_progress.discard(name)


ZERO = {"int": "0", "float": "0.0", "string": '""', "bool": "false"}


def write_types(model, folder):
    out = ["// Generated by tools/generate.py from GitHub's OpenAPI description; do not edit.\n",
           "partial module adm.dev.github {\n",
           "\tuse (\n\t\tstd.data.encoding::(json)\n\t\tstd.time\n\t)\n"]
    for name in sorted(model.order):
        kind, lines, doc = model.declared[name]
        out.append("\n" + comment(doc, "\t"))
        out.append(f"\t{kind} {name} {{\n")
        out.extend(lines)
        if kind == "partial type":
            out.append("\t\t// The repository the object came from, which its own methods\n"
                       "\t\t// call the API about.\n"
                       "\t\t@json(ignore = true)\n\t\tinternal origin ?RepoRef = none\n")
        out.append("\t}\n")
    out.append("}\n")
    open(os.path.join(folder, "types.adm"), "w").write("".join(out))


def parameters_of(document, operation):
    out = []
    for param in operation.get("parameters", []):
        if "$ref" in param:
            param = document["components"]["parameters"][param["$ref"].split("/")[-1]]
        out.append(param)
    return out


class Operation:
    pass


def holder_of(path):
    for prefix, name, fields in HOLDERS:
        if path == prefix or path.startswith(prefix + "/"):
            return name, fields, path[len(prefix):]
    return "Client", [], path


def strip_suffix(tail, holder):
    for suffix in CONTEXT_SUFFIXES.get(holder, []):
        if tail.endswith(suffix):
            return tail[: -len(suffix)]
    return tail


def singular(word):
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def noun_phrases(segment, tails):
    """The phrases that name a collection in its operations' names, longest
    first: `workflow-runs` and `workflow-run` for `runs`."""
    words = segment.replace("_", "-").split("-")
    last = words[-1]
    nouns = {last, singular(last)}
    phrases = set()
    for tail in tails:
        tokens = tail.split("-")
        for i, token in enumerate(tokens):
            if token in nouns:
                start = i
                while start > 0 and tokens[start - 1] in words[:-1] + ["workflow", "check", "pull", "review", "release", "repository", "repo", "org", "deploy", "commit", "issue"] and tokens[start - 1] not in ("list", "get", "create", "update", "delete", "set", "add", "remove"):
                    start -= 1
                phrases.add("-".join(tokens[start:i + 1]))
    return sorted(phrases, key=lambda p: -len(p))


def method_name(tail, phrases):
    """The method a collection's operation is: its name without the
    collection's noun (`list-releases` is `list`), camel case."""
    name = tail
    for phrase in phrases:
        for pattern in (f"-for-a-{phrase}", f"-for-{phrase}", f"-of-{phrase}", f"-from-{phrase}", f"-on-{phrase}", f"-{phrase}", f"{phrase}-"):
            if pattern in name or name == phrase:
                if name == phrase:
                    name = ""
                elif name.startswith(pattern) and pattern.endswith("-"):
                    name = name[len(pattern):]
                elif pattern in name:
                    name = name.replace(pattern, "", 1)
                break
        else:
            continue
        break
    name = name.strip("-")
    return camel(name) if name else ""


def adm_param_type(schema, name):
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = [k for k in kind if k != "null"][0] if [k for k in kind if k != "null"] else "string"
    if kind == "string":
        if schema.get("format") == "date-time":
            return "time.Time"
        if name == "state" and set(schema.get("enum", [])) <= STATE_VALUES and schema.get("enum"):
            return "State"
        return "string"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "array":
        inner = adm_param_type(schema.get("items", {}), name)
        return inner + "[]" if inner in ("string", "int", "float", "bool") else "any"
    return "any"


def collect_operations(document, model):
    out = []
    for path, methods in document["paths"].items():
        for verb, operation in methods.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            tag, tail = operation["operationId"].split("/", 1)
            if tag not in TAGS:
                continue
            op = Operation()
            op.path, op.verb, op.raw, op.tag, op.tail = path, verb, operation, tag, tail
            op.holder, op.holder_params, op.rest = holder_of(path)
            segments = [s for s in op.rest.split("/") if s]
            literals = [s for s in segments if not s.startswith("{")]
            op.node = literals[0] if literals else ""
            op.subnode = literals[1] if len(literals) > 1 else ""
            out.append(op)
    return out


def place(operations):
    """Decides where each operation goes: (holder, collection path, method
    name)."""
    counts = {}
    for op in operations:
        counts.setdefault((op.holder, op.node), []).append(op)
    sub_counts = {}
    for op in operations:
        sub_counts.setdefault((op.holder, op.node, op.subnode), []).append(op)
    for op in operations:
        group = counts[(op.holder, op.node)]
        tail = strip_suffix(op.tail, op.holder)
        if op.tag == "reactions":
            # `react`, `reactions`, `deleteReaction`, each with the thing
            # reacted to: reactToReviewComment(commentId, content).
            verb, _, target = tail.partition("-for-")
            target = target or ""
            for prefix in ("pull-request-", "team-discussion-comment-in-", "team-discussion-in-"):
                if target.startswith(prefix) and prefix.startswith("pull"):
                    target = target[len(prefix):]
            target = camel(target)
            target = target[:1].upper() + target[1:]
            op.collection = [] if (op.holder, op.node) in HOISTED or len(counts[(op.holder, op.node)]) <= HOIST_UPTO else [op.node]
            if op.collection and len(counts[(op.holder, op.node)]) > SPLIT_ABOVE and op.subnode and len(sub_counts[(op.holder, op.node, op.subnode)]) > HOIST_UPTO:
                op.collection = [op.node, op.subnode]
            op.method = {"create": f"reactTo{target}", "list": f"reactionsTo{target}", "delete": f"delete{target}Reaction"}.get(verb.split("-")[0], camel(tail))
            continue
        if (op.holder, op.node) in HOISTED:
            op.collection = []
            noun = HOISTED[(op.holder, op.node)]
            name = HOISTED_NAMES.get((op.holder, op.raw["operationId"]))
            if not name:
                tokens = tail.split("-")
                if noun not in tokens and singular(noun) not in tokens:
                    tokens.insert(1, noun)
                name = camel("-".join(tokens))
            op.method = name
            continue
        if op.node == "" or len(group) <= HOIST_UPTO:
            op.collection = []
            op.method = camel(tail)
            if op.node and tail.split("-")[0] == tail:
                # A bare verb names what it acts on: getRateLimit.
                noun = op.node if tail == "list" else singular(op.node)
                op.method = camel(f"{tail}-{noun}")
            continue
        collection = [op.node]
        phrases_tails = [strip_suffix(o.tail, op.holder) for o in group]
        if len(group) > SPLIT_ABOVE and op.subnode:
            sub = sub_counts[(op.holder, op.node, op.subnode)]
            if len(sub) > HOIST_UPTO:
                collection = [op.node, op.subnode]
                phrases_tails = [strip_suffix(o.tail, op.holder) for o in sub]
        op.collection = collection
        phrases = noun_phrases(collection[-1], phrases_tails)
        name = method_name(tail, phrases)
        op.method = name or ("list" if op.verb == "get" and is_list(op) else op.verb if op.verb != "patch" else "update")
        if op.method == "post":
            op.method = "create"
        if op.method == "put":
            op.method = "set"
    # ADM names readers without a `get` prefix: `latest`, `byTag`; a bare
    # `get` stays.
    for op in operations:
        if op.method.startswith("get") and op.method[3:4].isupper():
            op.method = op.method[3].lower() + op.method[4:]
    # Names taken twice in one place with the same leading parameters keep
    # their whole operation name; with other parameters they are overloads.
    seen = {}
    for op in operations:
        key = (op.holder, tuple(op.collection), op.method, positional_shape(op))
        seen.setdefault(key, []).append(op)
    for key, group in seen.items():
        if len(group) > 1:
            for op in group[1:]:
                op.method = camel(strip_suffix(op.tail, op.holder))
            again = {}
            for op in group:
                again.setdefault(op.method, []).append(op)
            for name, same in again.items():
                for i, op in enumerate(same[1:], 2):
                    op.method = camel(op.tail) if camel(op.tail) != name else f"{name}{i}"
    for op in operations:
        if op.method in KEYWORDS or op.method in ("new", "dispose", "str"):
            op.method += "Value"


def positional_shape(op):
    """The types of an operation's leading (path) parameters, which tell
    overloads apart."""
    names = [p.strip("{}") for p in op.rest.split("/") if p.startswith("{")]
    return tuple("int" if n.endswith(("_id", "_number")) or n in ("id", "number") else "string" for n in names)


def success_schema(document, op):
    """The JSON schema of the 2xx answer and its status; none for no body."""
    for code in sorted(op.raw.get("responses", {})):
        if not code.startswith("2"):
            continue
        response = op.raw["responses"][code]
        if "$ref" in response:
            response = document["components"]["responses"][response["$ref"].split("/")[-1]]
        content = response.get("content", {})
        for media in ("application/json", "application/vnd.github+json", "application/vnd.github.v3+json", "application/scim+json"):
            if media in content and "schema" in content[media]:
                return content[media]["schema"], code
        for media, value in content.items():
            if "json" in media and "schema" in value:
                return value["schema"], code
        return None, code
    return None, "204"


def is_list(op):
    return any(p.get("name") == "per_page" for p in op.raw.get("parameters", []) if "name" in p) or \
        any(p.get("$ref", "").endswith("/per-page") for p in op.raw.get("parameters", []))


def list_shape(model, schema):
    """(item schema, wrapper key) of a list answer: a JSON array, or an
    object holding one (`{"total_count": 2, "workflow_runs": [...]}`)."""
    resolved = model.resolve(schema)
    if model.kind(resolved) == "array":
        return resolved.get("items", {}), ""
    arrays = [(k, v) for k, v in resolved.get("properties", {}).items() if model.kind(model.resolve(v)) == "array"]
    if len(arrays) == 1:
        return model.resolve(arrays[0][1]).get("items", {}), arrays[0][0]
    return None, ""


def write_operations(document, model, operations, folder):
    by_place = {}
    for op in operations:
        by_place.setdefault((op.holder, tuple(op.collection)), []).append(op)
    out = ["// Generated by tools/generate.py from GitHub's OpenAPI description; do not edit.\n",
           "partial module adm.dev.github {\n",
           "\tuse (\n\t\tstd.net.http\n\t\tstd.time\n\t\tadm.dev.github.api::*\n\t)\n"]
    holders = {name: fields for _, name, fields in HOLDERS}
    holders["Client"] = []
    tests = []
    # Collection types and the fields that hold them.
    children = {}
    for (holder, collection) in by_place:
        for depth in range(1, len(collection) + 1):
            parent = (holder, collection[:depth - 1])
            children.setdefault(parent, set()).add(collection[:depth])
    def type_name(holder, collection):
        if not collection:
            return holder
        nouns = "".join(pascal(NODE_NAMES.get(c, c)) for c in collection)
        return nouns + "Api" if holder == "Client" else holder + nouns
    places = set(by_place) | set(children)
    for key in sorted(places, key=lambda k: (k[0], k[1])):
        holder, collection = key
        name = type_name(holder, collection)
        fields = holders[holder]
        out.append("\n")
        if collection:
            noun = " ".join(NODE_NAMES.get(c, c).replace("_", " ").replace("-", " ") for c in collection)
            out.append(f"\t// The {noun} of {'GitHub' if holder == 'Client' else 'a ' + holder.lower() if holder != 'Me' else 'the authenticated user'}.\n")
            out.append(f"\ttype {name} {{\n")
            out.append("\t\tinternal link Link\n")
            for field in fields:
                out.append(f"\t\tinternal {HOLDER_FIELDS[field]} string\n")
        else:
            out.append(f"\tpartial type {name} {{\n")
        for child in sorted(children.get(key, [])):
            child_name = type_name(holder, child)
            field = camel(NODE_NAMES.get(child[-1], child[-1]))
            out.append(f"\t\t{field} {child_name} {{\n\t\t\tdef get() {child_name} {{\n")
            args = ", ".join(["link"] + [HOLDER_FIELDS[f] for f in fields])
            out.append(f"\t\t\t\treturn new {child_name}({args})\n\t\t\t}}\n\t\t}}\n")
        if collection:
            params = ", ".join(["link Link"] + [f"{HOLDER_FIELDS[f]} string" for f in fields])
            out.append(f"\n\t\tdef new({params}) {{\n\t\t\tself.link = link\n")
            for field in fields:
                out.append(f"\t\t\tself.{HOLDER_FIELDS[field]} = {HOLDER_FIELDS[field]}\n")
            out.append("\t\t}\n")
        for op in sorted(by_place.get(key, []), key=lambda o: o.method):
            text, test = method_text(document, model, op, holder, fields)
            out.append("\n" + text)
            tests.append((holder, collection, op, test))
        out.append("\t}\n")
    out.append("}\n")
    open(os.path.join(folder, "operations.adm"), "w").write("".join(out))
    write_operations_test(tests, folder)


def method_text(document, model, op, holder, holder_fields):
    params = parameters_of(document, op.raw)
    path_params = [p for p in params if p["in"] == "path" and p["name"] not in holder_fields]
    query_params = [p for p in params if p["in"] == "query" and not (p["name"] in PAGING and is_list(op))]
    body = op.raw.get("requestBody")
    body_props, body_required, raw_body = [], set(), None
    if body:
        content = body.get("content", {})
        json_media = next((m for m in content if "json" in m), None)
        if json_media:
            schema = model.resolve(content[json_media].get("schema", {}))
            if model.kind(schema) == "object" and schema.get("properties"):
                body_props = list(schema["properties"].items())
                body_required = set(schema.get("required", []))
            elif schema:
                raw_body = ("json", json_media)
        else:
            raw_body = ("bytes", next(iter(content), "application/octet-stream"))
    signature, names = [], set()
    positional, named = [], []

    def local(name):
        out = camel(name)
        # The generated locals (sentQuery, sentBody, answer, answered)
        # take names no parameter has.
        while out in names or out in ("link", "self", "sentQuery", "sentBody", "answer", "answered"):
            out = out + "Value"
        names.add(out)
        return out

    for p in path_params:
        positional.append((local(p["name"]), adm_param_type(p.get("schema", {}), p["name"]), p, "path"))
    for p in query_params:
        entry = (local(p["name"]), adm_param_type(p.get("schema", {}), p["name"]), p, "query")
        (positional if p.get("required") else named).append(entry)
    for prop, schema in body_props:
        entry = (local(prop), adm_param_type(model.resolve(schema), prop), {"name": prop, "schema": schema, "description": schema.get("description", "")}, "body")
        (positional if prop in body_required else named).append(entry)
    if raw_body:
        positional.append(("data", "byte[]" if raw_body[0] == "bytes" else "any", {"name": "data"}, "raw"))
    for name, kind, _, _ in positional:
        signature.append(f"{name} {kind}")
    for name, kind, _, _ in named:
        signature.append(f"{name} ?{kind} = none" if not kind.endswith("[]") and kind != "any" else f"{name} {kind} = []" if kind.endswith("[]") else f"{name} any = none")
    schema, status = success_schema(document, op)
    listing = is_list(op) and op.verb == "get" and schema is not None
    result = "none"
    item = None
    key = ""
    if listing:
        item_schema, key = list_shape(model, schema)
        if item_schema is None:
            listing = False
        else:
            item, _ = model.type_of(item_schema, pascal(op.raw["operationId"]), "Item")
            result = f"Pages<{item}>"
    if not listing and schema is not None:
        result, _ = model.type_of(schema, pascal(op.raw["operationId"].replace("/", "-")), "Response")
        if result in ("int", "float", "string", "bool", "time.Time") or (result.startswith("map<") and result != "map<string, any>"):
            result = "any"
    summary = op.raw.get("summary", "")
    description = op.raw.get("description", "")
    lines = []
    lines.append(comment(f"{op.method} — {summary}." if summary else op.method, "\t\t").replace(" — ", ": "))
    docs = (op.raw.get("externalDocs") or {}).get("url", "")
    lines.append(f"\t\t// {op.verb.upper()} {op.path}" + (f" ({docs})" if docs else "") + "\n")
    if listing:
        lines.append("\t\t// Fails when the first page is read: see Pages.\n")
    else:
        lines.append("\t\t// Fails as the type's calls do.\n")
    path_expr = op.path
    for field in holder_fields:
        path_expr = path_expr.replace("{" + field + "}", "{segment(self." + HOLDER_FIELDS[field] + ")}")
    for name, _, p, _ in positional:
        if p.get("name") and "{" + p["name"] + "}" in path_expr:
            path_expr = path_expr.replace("{" + p["name"] + "}", "{segment(" + name + ")}")
    signature_text = ", ".join(signature)
    returns = result if listing else (f"!{result}" if result != "none" else "!none")
    lines.append(f"\t\tdef {op.method}({signature_text}) {returns} {{\n")
    lines.append("\t\t\tlet sentQuery = new Params()\n")
    for name, kind, p, where in positional + named:
        if where == "query":
            lines.append(f"\t\t\tsentQuery.put(\"{p['name']}\", {name})\n")
    has_body = body_props or raw_body
    if body_props:
        lines.append("\t\t\tlet sentBody = new Params()\n")
        for name, kind, p, where in positional + named:
            if where == "body":
                lines.append(f"\t\t\tsentBody.put(\"{p['name']}\", {name})\n")
    servers = op.raw.get("servers")
    base = "uploads" if servers else ""
    payload = "sentBody.json()" if body_props else ("data" if raw_body and raw_body[0] == "bytes" else "encodeBody(data)" if raw_body else "none")
    media = raw_body[1] if raw_body and raw_body[0] == "bytes" else "application/json"
    path_literal = path_expr.replace('"', '\\"')
    if listing:
        bind = ""
        if item in BOUND and holder == "Repo":
            bind = ", def (made " + item + ") none {\n\t\t\t\tmade.origin = RepoRef{link: link, owner: self.owner, name: self.name}\n\t\t\t}"
        lines.append(f"\t\t\treturn new Pages<{item}>(link, \"{path_literal}\", sentQuery, \"{key}\"{bind})\n")
    else:
        verb = op.verb.upper()
        if result == "none":
            lines.append(f"\t\t\ttry link.call(http.Method.{verb}, \"{path_literal}\", sentQuery, {payload}, none, \"{media}\", \"{base}\")\n")
            lines.append("\t\t\treturn none\n")
        elif result.endswith("[]"):
            lines.append(f"\t\t\tlet answer {result} = []\n")
            lines.append(f"\t\t\ttry link.call(http.Method.{verb}, \"{path_literal}\", sentQuery, {payload}, answer, \"{media}\", \"{base}\")\n")
            lines.append("\t\t\treturn answer\n")
        elif result in ("int", "float", "string", "bool", "any", "time.Time") or result.startswith("map<"):
            lines.append(f"\t\t\tlet answered = try link.fetch(http.Method.{verb}, \"{path_literal}\", sentQuery, {payload}, \"{media}\", \"{base}\")\n")
            lines.append(f"\t\t\treturn try {'parseObject' if result == 'map<string, any>' else 'parseValue'}(answered)\n")
        else:
            lines.append(f"\t\t\tlet answer = {'new ' + result + '()' if result in BOUND else result + '{}'}\n")
            lines.append(f"\t\t\ttry link.call(http.Method.{verb}, \"{path_literal}\", sentQuery, {payload}, answer, \"{media}\", \"{base}\")\n")
            if result in BOUND and holder == "Repo":
                lines.append("\t\t\tanswer.origin = RepoRef{link: link, owner: self.owner, name: self.name}\n")
            lines.append("\t\t\treturn answer\n")
    lines.append("\t\t}\n")
    answer = "[]" if (listing and key == "") or result.endswith("[]") else "{}"
    test = (op.method, [(n, k) for n, k, _, _ in positional], op.verb.upper(), ("/uploads" if servers else "") + op.path, listing, answer)
    return "".join(lines), test


DUMMY = {"string": '"x"', "int": "1", "float": "1.0", "bool": "true", "byte[]": "byte[1]", "any": "none",
         "time.Time": "time.now()", "State": "State.Open"}


def write_operations_test(tests, folder):
    def access(holder, collection):
        base = {"Client": "gh", "Repo": "gh.repo(\"o\", \"r\")", "Org": "gh.org(\"o\")", "OrgTeam": "gh.org(\"o\").team(\"t\")",
                "User": "gh.user(\"u\")", "Me": "gh.me"}[holder]
        for c in collection:
            base += "." + camel(NODE_NAMES.get(c, c))
        return base
    out = ["// Generated by tools/generate.py from GitHub's OpenAPI description; do not edit.\n",
           "partial module adm.dev.github {\n",
           "\tuse (\n\t\tstd.testing\n\t\tstd.time as _\n\t)\n\n" if not any(k == "time.Time" for _, _, _, t in tests for _, k in t[1]) else "\tuse (\n\t\tstd.testing\n\t\tstd.time\n\t)\n\n",
           "\tcheck \"Operations\" {\n"]
    chunk = 60
    for start in range(0, len(tests), chunk):
        out.append("\t\t@test()\n")
        out.append(f"\t\tdef TestOperations{start // chunk + 1}() !none {{\n")
        out.append("\t\t\tlet fake = new FakeGitHub()\n\t\t\tlet server = try fakeGitHub(fake)\n\t\t\tdefer server.close()\n")
        out.append("\t\t\tlet gh = try fakeClient(server)\n")
        for holder, collection, op, test in tests[start:start + chunk]:
            method, args, verb, path, listing, answer = test
            values = ", ".join(DUMMY.get(kind, DUMMY.get(kind.rstrip("[]"), "none")) if not kind.endswith("[]") or kind == "byte[]" else "[]" for _, kind in args)
            call = f"{access(holder, collection)}.{method}({values})"
            expected = re.sub(r"\{[^}]+\}", "*", path)
            out.append(f"\t\t\tfake.body = \"{answer}\"\n")
            if listing:
                out.append(f"\t\t\ttry {call}.page(1)\n")
            else:
                out.append(f"\t\t\ttry {call}\n")
            out.append(f"\t\t\tassert fake.last(\"{verb}\", \"{expected}\")\n")
        out.append("\t\t\treturn none\n\t\t}\n\n")
    out.append("\t}\n}\n")
    open(os.path.join(folder, "operations_test.adm"), "w").write("".join(out))


def write_events(document, model, folder):
    """One payload type per event name, the properties of all its actions
    merged, and an accessor on Event for it."""
    by_name = {}
    for key, entry in document.get("webhooks", {}).items():
        post = entry.get("post", {})
        event = None
        for param in post.get("parameters", []):
            if param.get("name", "").lower() == "x-github-event":
                event = param.get("example")
        schema = post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if not event or not schema:
            continue
        by_name.setdefault(event, []).append((post, schema))
    out = ["// Generated by tools/generate.py from GitHub's OpenAPI description; do not edit.\n",
           "partial module adm.dev.github {\n",
           "\tpartial type WebhookEvent {\n"]
    for event in sorted(by_name):
        variants = by_name[event]
        name = pascal(event) + "Event"
        merged = model.merged_type([schema for _, schema in variants], name)
        summary = variants[0][0].get("summary", "")
        actions = sorted({ (model.resolve(s).get("properties", {}).get("action", {}).get("enum") or [""])[0] for _, s in variants} - {""})
        accessor = camel(event)
        if accessor in ("new", "name", "action", "delivery", "payload", "json", "decode"):
            accessor += "Event"
        out.append(comment(summary or event, "\t\t"))
        out.append(f"\t\t// `{event}`" + (f", action {', '.join(actions)}" if actions else "") + ": the payload, none for another event.\n")
        out.append("\t\t// Fails when the payload does not read as the event's.\n")
        out.append(f"\t\tdef {accessor}() !?{merged} {{\n\t\t\treturn none unless name == \"{event}\"\n")
        out.append(f"\t\t\tlet out = {merged}{{}}\n\t\t\ttry decode(out)\n\t\t\treturn out\n\t\t}}\n\n")
    out.append("\t}\n}\n")
    open(os.path.join(folder, "events.adm"), "w").write("".join(out))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    document = json.load(open(sys.argv[1]))
    folder = sys.argv[2]
    model = Model(document)
    operations = collect_operations(document, model)
    place(operations)
    write_operations(document, model, operations, folder)
    write_events(document, model, folder)
    write_types(model, folder)
    written = [os.path.join(folder, name) for name in ("types.adm", "operations.adm", "events.adm", "operations_test.adm")]
    subprocess.run(["adm", "fmt", *written], check=False, stdout=subprocess.DEVNULL)


main()
