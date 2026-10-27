#!/usr/bin/env python3
"""Generate the REST layer of adm.dev.gitlab from GitLab's OpenAPI document
(doc/api/openapi/openapi_v2.yaml of gitlab-org/gitlab) and
tools/supplement.yaml, the endpoints that document does not describe yet.

Usage: python3 tools/generate.py <openapi_v2.yaml> <library folder>

Writes types.adm (every entity the operations in scope answer with),
operations.adm (a method for every operation in scope, on the object its
path names: the client, a project, a group, a user, the authenticated user,
and the collections under them) and operations_test.adm (a call of every
operation against the loopback server of gitlab_test.adm), formatted with
`adm fmt`. The client, tokens, paging, rate limits, GraphQL and webhooks
are written by hand.
"""
import os
import re
import subprocess
import sys

import yaml

KEYWORDS = set("""application library plugin module check view style component datatype
service type union interface enum struct partial internal let const def use atomic weak
async cuda sql infix meta return fail expects provides begin rollback transaction defer
yield try onerror await if else when unless as is in for forall every empty break finish
switch match select where case continue default assert self none true false""".split())

# Types of the prelude, which a library does not declare: entities of
# these names take another.
PRELUDE = {"Event", "Service", "Role", "Secret", "Error", "Task", "Duration", "Iterator", "Result"}

# The objects a path's leading parameters name: (prefix, holder, its field).
HOLDERS = [
    ("/projects/{id}", "Project", "id"),
    ("/groups/{id}", "Group", "id"),
    ("/users/{id}", "User", "id"),
    ("/users/{user_id}", "User", "id"),
    ("/user", "Me", ""),
]

# The segments of each holder in scope; the client's are the first segment
# of the path.
SCOPE = {
    "Project": {"", "merge_requests", "issues", "repository", "statuses", "protected_branches",
                "protected_tags", "pipeline", "pipelines", "pipeline_schedules", "jobs", "variables",
                "releases", "packages", "registry", "snippets", "search", "hooks", "labels",
                "milestones", "members", "access_tokens", "environments", "deployments", "fork",
                "forks", "star", "unstar", "archive", "unarchive", "share", "uploads", "triggers",
                "approvals", "approval_rules", "issues_statistics", "languages", "starrers"},
    "Group": {"", "projects", "subgroups", "descendant_groups", "members", "variables", "issues",
              "merge_requests", "labels", "milestones", "search", "access_tokens", "registry",
              "packages", "hooks", "epics_disabled"},
    "User": {"", "projects", "starred_projects", "keys", "gpg_keys", "memberships", "status",
             "events", "snippets"},
    "Me": {"", "keys", "gpg_keys", "emails", "status", "preferences", "personal_access_tokens"},
    "Client": {"projects", "groups", "users", "snippets", "search", "version", "metadata",
               "personal_access_tokens", "markdown"},
}

# Package-manager protocols under `packages`, which are not the packages API.
PACKAGE_PROTOCOLS = {"maven", "npm", "nuget", "conan", "pypi", "composer", "rubygems", "helm",
                     "debian", "terraform", "go", "ml_models", "rpm", "pub", "cargo"}

# Methods named otherwise than the rules name them, by verb and path.
NAMES = {
    ("get", "/projects/{id}/repository/archive"): "repositoryArchive",
    ("get", "/projects/{id}/repository/archive.{format}"): "repositoryArchiveAs",
}

# Segments named otherwise as collections: `project.pipelines` takes
# `POST /pipeline` too.
NODE_ALIASES = {"pipeline": "pipelines"}

# Segments whose own segments are the collections: `repository/commits`
# is `project.commits`.
FLATTENED = {"repository", "registry"}

# Words that name what a request does rather than what it lists or makes.
ACTIONS = {"merge", "approve", "unapprove", "rebase", "subscribe", "unsubscribe", "cancel",
           "retry", "play", "erase", "star", "unstar", "fork", "archive", "unarchive", "transfer",
           "share", "reset_approvals", "cherry_pick", "revert", "todo", "time_estimate",
           "reset_time_estimate", "add_spent_time", "reset_spent_time", "move", "clone",
           "promote", "take_ownership", "unprotect", "protect", "rotate", "revoke",
           "merge_when_pipeline_succeeds", "cancel_merge_when_pipeline_succeeds", "reorder",
           "restore", "keep", "unshare", "housekeeping", "block", "unblock", "activate",
           "deactivate", "ban", "unban", "approve_all"}

# Nouns that are the same for one and many.
UNCOUNTABLE = {"award_emoji", "metadata", "time_stats"}

# The hand-written enum of `state` parameters.
STATE_VALUES = {"opened", "closed", "merged", "locked", "all"}

# Query parameters `Pages` sets itself.
PAGING = {"per_page", "page"}

ZERO = {"int": "0", "float": "0.0", "string": '""', "bool": "false"}


def pascal(name):
    parts = re.split(r"[^A-Za-z0-9]+", name)
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if out[:1].isdigit():
        out = "N" + out
    return out


def camel(name):
    out = pascal(name)
    out = out[:1].lower() + out[1:]
    if out == "type":
        return "typeName"
    if out in KEYWORDS or out == "":
        return out + "Value"
    return out


def singular(word):
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


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


# Names the hand-written part declares: an entity of a holder's name is
# its details (`ProjectDetails`), of another such name an entity.
HAND_WRITTEN = {"Project", "Group", "User", "Me", "Client", "Pages", "PageStream", "Link", "Params",
                "State", "GitLabError", "ErrorKind", "WebhookEvent", "Webhooks", "WebhookHandler",
                "RateLimit", "TokenKind", "UploadFile", "EventHead", "HookUser", "HookProject",
                "HookCommit", "HookCommitAuthor", "HookLabel", "PushEvent", "MergeRequestEvent",
                "MergeRequestAttributes", "IssueEvent", "IssueAttributes", "NoteEvent",
                "NoteAttributes", "PipelineEvent", "PipelineAttributes", "PipelineJob", "JobEvent",
                "DeploymentEvent", "ReleaseEvent", "GraphQLPages", "GraphQLStream", "GraphQLProblem",
                "GraphQLAnswer", "JsonValue"}


# Entities named for what they are to a reader: a project as `get` answers
# with it, as lists do.
TYPE_RENAMES = {
    "API_Entities_Projects_WithAccessAndCatalogSetting": "ProjectDetails",
    "API_Entities_Project": "ProjectInfo",
    "API_Entities_GroupDetail": "GroupDetails",
    "API_Entities_Group": "GroupInfo",
    "API_Entities_User": "UserDetails",
}


def type_name(key):
    if key in TYPE_RENAMES:
        return TYPE_RENAMES[key]
    name = pascal(re.sub(r"^API_Entities_", "", key))
    if name in ("Project", "Group", "User"):
        return name + "Details"
    return name + "Entity" if name in PRELUDE or name in HAND_WRITTEN else name


class Model:
    def __init__(self, document):
        self.definitions = document.get("definitions", {})
        self.declared = {}
        self.order = []
        self.by_ref = {}
        self.taken = set()

    def unique(self, name):
        out, count = name, 2
        while out in self.taken:
            out = f"{name}{count}"
            count += 1
        self.taken.add(out)
        return out

    def type_of(self, schema, owner, field):
        if "$ref" in schema:
            return self.ref_type(schema["$ref"].split("/")[-1])
        kind = schema.get("type")
        if kind == "array":
            inner = self.type_of(schema.get("items", {}), owner, field + "Item")
            return inner + "[]"
        if kind == "object" or (kind is None and schema.get("properties")):
            if schema.get("properties"):
                name = self.unique(owner + pascal(field))
                self.declare(name, schema)
                return name
            values = schema.get("additionalProperties")
            if isinstance(values, dict) and values:
                return f"map<string, {self.type_of(values, owner, field + 'Value')}>"
            return "map<string, any>"
        return self.scalar(schema)

    def scalar(self, schema):
        kind = schema.get("type")
        if kind == "string":
            return "time.Time" if schema.get("format") == "date-time" else "string"
        return {"integer": "int", "number": "float", "boolean": "bool"}.get(kind, "any")

    def ref_type(self, key):
        if key in self.by_ref:
            return self.by_ref[key]
        schema = self.definitions.get(key, {})
        kind = schema.get("type")
        if kind in ("string", "integer", "number", "boolean"):
            self.by_ref[key] = self.scalar(schema)
            return self.by_ref[key]
        if kind == "array":
            inner = self.type_of(schema.get("items", {}), type_name(key), "Item")
            self.by_ref[key] = inner + "[]"
            return self.by_ref[key]
        name = self.unique(type_name(key))
        self.by_ref[key] = name
        self.declare(name, schema)
        return name

    def declare(self, name, schema):
        properties = dict(schema.get("properties", {}))
        for part in schema.get("allOf", []):
            if "$ref" in part:
                properties.update(self.definitions.get(part["$ref"].split("/")[-1], {}).get("properties", {}))
            properties.update(part.get("properties", {}))
        lines, used = [], set()
        for prop, sub in properties.items():
            adm = self.type_of(sub, name, prop)
            local = camel(prop)
            while local in used:
                local += "Value"
            used.add(local)
            options = [f'"{prop}"'] if local != prop else []
            options.append("omitempty = true")
            if adm.endswith("[]"):
                declared = f"{adm} = []"
            elif adm.startswith("map<"):
                declared = f"{adm} = {{}}"
            elif adm == "any":
                declared = "any"
            elif adm in ZERO:
                declared = f"{adm} = {ZERO[adm]}"
            else:
                # GitLab writes null for what it does not have: an object
                # or a time that may be missing is optional.
                declared = f"?{adm}"
            doc = comment(sub.get("description", ""), "\t\t") if sub.get("description") else ""
            lines.append(doc + f"\t\t@json({', '.join(options)})\n\t\t{local} {declared}\n")
        if not lines:
            lines.append("\t\t@json(omitempty = true)\n\t\tvalue any\n")
        self.declared[name] = (lines, schema.get("description") or name)
        self.order.append(name)


def write_types(model, folder):
    out = ["// Generated by tools/generate.py from GitLab's OpenAPI document; do not edit.\n",
           "partial module adm.dev.gitlab {\n",
           "\tuse (\n\t\tstd.data.encoding::(json)\n\t\tstd.time\n\t)\n"]
    for name in sorted(model.order):
        lines, doc = model.declared[name]
        out.append("\n" + comment(doc, "\t"))
        out.append(f"\tstruct {name} {{\n")
        out.extend(lines)
        out.append("\t}\n")
    out.append("}\n")
    open(os.path.join(folder, "types.adm"), "w").write("".join(out))


class Operation:
    pass


def parameters_of(document, operation):
    out = []
    for param in operation.get("parameters", []):
        if "$ref" in param:
            param = document["parameters"][param["$ref"].split("/")[-1]]
        out.append(param)
    return out


def holder_of(path):
    for prefix, name, field in HOLDERS:
        if path == prefix or path.startswith(prefix + "/"):
            return name, field, path[len(prefix):]
    return "Client", "", path


def in_scope(op):
    segments = [s for s in op.rest.split("/") if s]
    first = segments[0] if segments else ""
    if op.holder == "Client":
        return first in SCOPE["Client"]
    if first not in SCOPE[op.holder]:
        return False
    if first == "packages" and len(segments) > 1 and segments[1] in PACKAGE_PROTOCOLS:
        return False
    return True


def collect(document):
    out = []
    for raw_path, methods in document["paths"].items():
        path = raw_path[len("/api/v4"):] if raw_path.startswith("/api/v4") else raw_path
        # A path written as a pattern (`(ref/{ref}/)trigger/pipeline`) has a
        # plain twin.
        if "(" in path:
            continue
        for verb, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            op = Operation()
            op.raw_path, op.path, op.verb, op.raw = raw_path, path, verb, operation
            op.holder, op.holder_field, op.rest = holder_of(path)
            if not in_scope(op):
                continue
            out.append(op)
    return out


def is_param(segment):
    return segment.startswith("{")


def place(operations):
    for op in operations:
        segments = [s for s in op.rest.split("/") if s]
        literal = [s for s in segments if not is_param(s)]
        node = NODE_ALIASES.get(literal[0], literal[0]) if literal else ""
        rest = segments[1:] if segments else []
        if node in FLATTENED and len(rest) >= 1 and not is_param(rest[0]):
            node = rest[0]
            rest = rest[1:]
        op.node = node
        op.after = rest
    counts = {}
    for op in operations:
        counts.setdefault((op.holder, op.node), []).append(op)
    for op in operations:
        group = counts[(op.holder, op.node)]
        if op.node == "" or len(group) <= 2:
            op.collection = []
            op.method = holder_method(op)
        else:
            op.collection = [op.node]
            op.method = node_method(op)
    # A name taken twice in one place is no overload: optional parameters
    # take positional arguments, so a call could pick the wrong one. The
    # later operations take a word of their path: the parameter they end
    # with (`createForkedFrom`), else their last segment.
    seen = {}
    for op in operations:
        seen.setdefault((op.holder, tuple(op.collection), op.method), []).append(op)
    for key, group in seen.items():
        group.sort(key=lambda o: (len(o.path), o.path, o.verb))
        taken = {o.method for o in operations if (o.holder, tuple(o.collection)) == key[:2]}
        for i, op in enumerate(group[1:], 2):
            segments = [s for s in op.path.split("/") if s]
            last = segments[-1]
            word = pascal(re.sub(r"_(id|iid)$", "", last.strip("{}"))) if is_param(last) else pascal(last)
            name = op.method + word if word.lower() not in op.method.lower() else f"{op.method}{i}"
            while name in taken:
                name += str(i)
            taken.add(name)
            op.method = name
    for op in operations:
        op.method = NAMES.get((op.verb, op.path), op.method)
        if op.method in KEYWORDS or op.method in ("new", "dispose", "str", "get" if op.collection == [] and op.holder == "Client" else ""):
            op.method += "Value"


def shape(op):
    return tuple(s for s in op.after if is_param(s))


def holder_method(op):
    noun = op.node
    verb = op.verb
    if noun == "":
        return {"get": "get", "put": "update", "delete": "delete", "post": "create", "head": "exists"}.get(verb, verb)
    if noun in ACTIONS:
        return camel(noun)
    if verb == "get":
        return camel(noun)
    if verb == "post":
        return camel("create-" + singular(noun))
    if verb in ("put", "patch"):
        return camel("update-" + noun)
    if verb == "delete":
        return camel("delete-" + noun)
    return camel(f"{verb}-{noun}")


def node_method(op):
    after = op.after
    item = bool(after) and is_param(after[0])
    subs = [s for s in after if not is_param(s)]
    ends_param = bool(after) and is_param(after[-1])
    verb = op.verb
    if not subs:
        if verb == "get":
            return "get" if ends_param else "list"
        if verb == "post":
            return "create"
        if verb in ("put", "patch"):
            return "update" if ends_param or not item else "update"
        if verb == "delete":
            return "delete" if ends_param else "deleteAll"
        if verb == "head":
            return "exists"
        return verb
    noun = "-".join(subs)
    last = subs[-1]
    if last in UNCOUNTABLE:
        # One word for one and many: the verb says which.
        verb_word = {"get": "" if ends_param else "list", "post": "create", "put": "update", "delete": "delete"}.get(verb, verb)
        return camel(f"{verb_word}-{noun}") if verb_word else camel(noun)
    if ends_param:
        if verb == "get":
            return camel(singular(noun))
        if verb in ("put", "patch"):
            return camel("update-" + singular(noun))
        if verb == "delete":
            return camel("delete-" + singular(noun))
        if verb == "post":
            return camel(noun)
        return camel(f"{verb}-{singular(noun)}")
    if last in ACTIONS:
        return camel(noun)
    if verb == "get":
        return camel(noun)
    if verb == "post":
        return camel("create-" + singular(noun)) if last.endswith("s") else camel(noun)
    if verb in ("put", "patch"):
        return camel("update-" + noun) if last.endswith("s") else camel(noun) if last in ACTIONS else camel("update-" + noun)
    if verb == "delete":
        return camel("delete-" + noun)
    if verb == "head":
        return camel("check-" + noun)
    return camel(f"{verb}-{noun}")


def param_type(schema, name):
    kind = schema.get("type")
    if kind == "string":
        if schema.get("format") == "date-time":
            return "time.Time"
        if name == "state" and schema.get("enum") and set(schema["enum"]) <= STATE_VALUES:
            return "State"
        return "string"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "file":
        return "byte[]"
    if kind == "array":
        inner = param_type(schema.get("items", {}), name)
        return inner + "[]" if inner in ("string", "int", "bool", "float") else "any"
    return "any"


def success_of(op):
    for code in sorted(op.raw.get("responses", {})):
        if code.startswith("2"):
            return op.raw["responses"][code].get("schema"), code
    return None, "204"


def list_item(model, schema):
    """The item schema of a list answer. The document writes some lists as
    their item (`GET /projects/{id}/issues` answering an Issue): a paged
    GET answering an object answers a list of them."""
    if schema is None:
        return None
    if "$ref" in schema:
        target = model.definitions.get(schema["$ref"].split("/")[-1], {})
        if target.get("type") == "array":
            return target.get("items", {})
        return schema
    if schema.get("type") == "array":
        return schema.get("items", {})
    return None


def method_text(document, model, op):
    params = parameters_of(document, op.raw)
    path_params = [p for p in params if p["in"] == "path" and p["name"] != (op.holder_field or "-")]
    if op.holder == "User":
        path_params = [p for p in path_params if p["name"] not in ("id", "user_id")]
    item_schema = list_item(model, success_of(op)[0])
    listing = op.verb == "get" and item_schema is not None and any(p["name"] in PAGING for p in params)
    query_params = [p for p in params if p["in"] in ("query",) and not (listing and p["name"] in PAGING)]
    body_props, required_body, files = [], set(), []
    for p in params:
        if p["in"] == "body":
            schema = p.get("schema", {})
            if "$ref" in schema:
                schema = model.definitions.get(schema["$ref"].split("/")[-1], {})
            body_props.extend(schema.get("properties", {}).items())
            required_body |= set(schema.get("required", []))
        elif p["in"] == "formData":
            if p.get("type") == "file":
                files.append(p)
            else:
                body_props.append((p["name"], p))
                if p.get("required"):
                    required_body.add(p["name"])
    names = set()

    def local(name):
        out = camel(name)
        while out in names or out in ("wire", "self", "sentQuery", "sentBody", "answer", "answered"):
            out += "Value"
        names.add(out)
        return out

    positional, named = [], []
    for p in path_params:
        positional.append((local(p["name"]), param_type(p, p["name"]), p["name"], "path"))
    for p in query_params:
        entry = (local(p["name"]), param_type(p, p["name"]), p["name"], "query")
        (positional if p.get("required") else named).append(entry)
    for prop, schema in body_props:
        kind = param_type(schema, prop)
        if kind == "byte[]":
            files.append({"name": prop})
            continue
        if prop == "variables" and kind == "any":
            # [{key, value}] of a pipeline, a trigger or a schedule: given
            # as a map.
            kind = "map<string, string>"
        entry = (local(prop), kind, prop, "body")
        (positional if prop in required_body else named).append(entry)
    for p in files:
        positional.append((local(p["name"]), "byte[]", p["name"], "file"))
        positional.append((local(p["name"] + "_name"), "string", p["name"], "filename"))
    signature = [f"{n} {k}" for n, k, _, _ in positional]
    for n, k, _, _ in named:
        if k.endswith("[]"):
            signature.append(f"{n} {k} = []")
        elif k.startswith("map<"):
            signature.append(f"{n} {k} = {{}}")
        elif k == "any":
            signature.append(f"{n} any = none")
        else:
            signature.append(f"{n} ?{k} = none")
    schema, code = success_of(op)
    if listing:
        item = model.type_of(item_schema, pascal(op.method), "Item")
        # An untyped object reads itself (JsonValue): a list of maps does
        # not decode.
        item = "JsonValue" if item.startswith("map<") or item == "any" else item
        result = f"Pages<{item}>"
    elif schema is not None:
        result = model.type_of(schema, pascal(op.holder + "-" + op.method), "Response")
        if result in ("int", "float", "string", "bool", "time.Time") or (result.startswith("map<") and result != "map<string, any>"):
            result = "any"
    else:
        result = "any" if code != "204" and op.verb != "delete" else "none"
    lines = []
    summary = op.raw.get("summary") or op.raw.get("description") or op.method
    lines.append(comment(f"{op.method}: {summary.rstrip('.')}.", "\t\t"))
    lines.append(f"\t\t// {op.verb.upper()} {op.raw_path}\n")
    lines.append("\t\t// Fails when the first page is read: see Pages.\n" if listing else "\t\t// Fails as Client's calls do.\n")
    path_expr = op.path
    if op.holder_field:
        path_expr = path_expr.replace("{" + op.holder_field + "}", "{segment(self.id)}")
    if op.holder == "User":
        path_expr = path_expr.replace("{user_id}", "{segment(self.id)}")
    for n, _, raw, where in positional:
        if where == "path":
            path_expr = path_expr.replace("{" + raw + "}", "{segment(" + n + ")}")
    returns = result if listing else (f"!{result}" if result != "none" else "!none")
    lines.append(f"\t\tdef {op.method}({', '.join(signature)}) {returns} {{\n")
    lines.append("\t\t\tlet sentQuery = new Params()\n")
    for n, k, raw, where in positional + named:
        if where == "query":
            lines.append(f"\t\t\tsentQuery.put(\"{raw}\", {n})\n")
    payload = "none"
    media = "application/json"
    if body_props or files:
        lines.append("\t\t\tlet sentBody = new Params()\n")
        for n, k, raw, where in positional + named:
            if where == "body":
                if k == "map<string, string>" and raw == "variables":
                    lines.append(f"\t\t\tsentBody.putVariables(\"{raw}\", {n})\n")
                else:
                    lines.append(f"\t\t\tsentBody.put(\"{raw}\", {n})\n")
        for n, k, raw, where in positional:
            if where == "file":
                filename = [m for m, _, r, w in positional if w == "filename" and r == raw][0]
                lines.append(f"\t\t\tsentBody.putFile(\"{raw}\", {n}, {filename})\n")
        payload = "sentBody"
    verb = op.verb.upper()
    target = f"\"{path_expr}\""
    if listing:
        lines.append(f"\t\t\treturn new Pages<{item}>(self.wire, {target}, sentQuery)\n")
    elif result == "none":
        lines.append(f"\t\t\ttry self.wire.call(http.Method.{verb}, {target}, sentQuery, {payload}, none)\n")
        lines.append("\t\t\treturn none\n")
    elif result.endswith("[]"):
        lines.append(f"\t\t\tlet answer {result} = []\n")
        lines.append(f"\t\t\ttry self.wire.call(http.Method.{verb}, {target}, sentQuery, {payload}, answer)\n")
        lines.append("\t\t\treturn answer\n")
    elif result == "any" or result.startswith("map<"):
        lines.append(f"\t\t\tlet answered = try self.wire.fetch(http.Method.{verb}, {target}, sentQuery, {payload})\n")
        lines.append(f"\t\t\treturn try {'parseObject' if result.startswith('map<') else 'parseValue'}(answered)\n")
    else:
        lines.append(f"\t\t\tlet answer = {result}{{}}\n")
        lines.append(f"\t\t\ttry self.wire.call(http.Method.{verb}, {target}, sentQuery, {payload}, answer)\n")
        lines.append("\t\t\treturn answer\n")
    lines.append("\t\t}\n")
    answer = "[]" if listing or result.endswith("[]") else "{}"
    test = (op.method, [(n, k) for n, k, _, w in positional], verb, op.path, listing, answer)
    return "".join(lines), test


HOLDER_TYPES = {"Project", "Group", "User", "Me", "Client"}


def write_operations(document, model, operations, folder):
    by_place = {}
    for op in operations:
        by_place.setdefault((op.holder, tuple(op.collection)), []).append(op)
    out = ["// Generated by tools/generate.py from GitLab's OpenAPI document; do not edit.\n",
           "partial module adm.dev.gitlab {\n",
           "\tuse (\n\t\tstd.net.http\n\t\tstd.time\n\t\tadm.dev.gitlab.api::*\n\t)\n"]
    tests = []

    def collection_type(holder, collection):
        if not collection:
            return holder
        noun = pascal(collection[0])
        return noun + "Api" if holder == "Client" else holder + noun

    children = {}
    for holder, collection in by_place:
        if collection:
            children.setdefault(holder, set()).add(collection)
    for key in sorted(set(by_place) | {(h, ()) for h in children}):
        holder, collection = key
        name = collection_type(holder, collection)
        has_id = holder in ("Project", "Group", "User")
        out.append("\n")
        if collection:
            out.append(f"\t// The {collection[0].replace('_', ' ')} of {'GitLab' if holder == 'Client' else 'the authenticated user' if holder == 'Me' else 'a ' + holder.lower()}.\n")
            out.append(f"\ttype {name} {{\n\t\tinternal wire Link\n")
            if has_id:
                out.append("\t\tinternal id   string\n")
        else:
            out.append(f"\tpartial type {name} {{\n")
        for child in sorted(children.get(holder, [])) if not collection else []:
            child_name = collection_type(holder, child)
            field = camel(child[0])
            args = "wire, id" if has_id else "wire"
            out.append(f"\t\t{field} {child_name} {{\n\t\t\tdef get() {child_name} {{\n\t\t\t\treturn new {child_name}({args})\n\t\t\t}}\n\t\t}}\n")
        if collection:
            if has_id:
                out.append("\n\t\tdef new(wire Link, id string) {\n\t\t\tself.wire = wire\n\t\t\tself.id = id\n\t\t}\n")
            else:
                out.append("\n\t\tdef new(wire Link) {\n\t\t\tself.wire = wire\n\t\t}\n")
        for op in sorted(by_place.get(key, []), key=lambda o: (o.method, o.path)):
            text, test = method_text(document, model, op)
            out.append("\n" + text)
            tests.append((holder, collection, test))
        out.append("\t}\n")
    out.append("}\n")
    open(os.path.join(folder, "operations.adm"), "w").write("".join(out))
    write_tests(tests, folder)


DUMMY = {"string": '"x"', "int": "1", "float": "1.0", "bool": "true", "byte[]": "byte[1]", "any": "none",
         "time.Time": "time.now()", "State": "State.Opened"}


def write_tests(tests, folder):
    def access(holder, collection):
        base = {"Client": "gl", "Project": "gl.project(\"g/p\")", "Group": "gl.group(\"g\")",
                "User": "gl.user(1)", "Me": "gl.me"}[holder]
        for c in collection:
            base += "." + camel(c)
        return base
    uses_time = any(k == "time.Time" for _, _, t in tests for _, k in t[1])
    out = ["// Generated by tools/generate.py from GitLab's OpenAPI document; do not edit.\n",
           "partial module adm.dev.gitlab {\n",
           "\tuse (\n\t\tstd.testing\n" + ("\t\tstd.time\n" if uses_time else "") + "\t)\n\n",
           "\tcheck \"Operations\" {\n"]
    chunk = 60
    for start in range(0, len(tests), chunk):
        out.append("\t\t@test()\n")
        out.append(f"\t\tdef TestOperations{start // chunk + 1}() !none {{\n")
        out.append("\t\t\tlet fake = new FakeGitLab()\n\t\t\tlet server = try fakeGitLab(fake)\n\t\t\tdefer server.close()\n")
        out.append("\t\t\tlet gl = try fakeClient(server)\n")
        for holder, collection, test in tests[start:start + chunk]:
            method, args, verb, path, listing, answer = test
            values = ", ".join("[]" if k.endswith("[]") and k != "byte[]" else DUMMY.get(k, "none") for _, k in args)
            call = f"{access(holder, collection)}.{method}({values})"
            expected = "/api/v4" + re.sub(r"\{[^}]+\}", "*", path)
            out.append(f"\t\t\tfake.body = \"{answer}\"\n")
            out.append(f"\t\t\ttry {call}.page(1)\n" if listing else f"\t\t\ttry {call}\n")
            out.append(f"\t\t\tassert fake.last(\"{verb}\", \"{expected}\")\n")
        out.append("\t\t\treturn none\n\t\t}\n\n")
    out.append("\t}\n}\n")
    open(os.path.join(folder, "operations_test.adm"), "w").write("".join(out))


def merge(document, supplement):
    for path, methods in supplement.get("paths", {}).items():
        document["paths"].setdefault(path, {})
        for verb, operation in methods.items():
            document["paths"][path][verb] = operation
    document.setdefault("parameters", {}).update(supplement.get("parameters", {}))
    document.setdefault("definitions", {}).update(supplement.get("definitions", {}))
    for name, fields in supplement.get("corrections", {}).items():
        for field, schema in fields.items():
            # `assets.links`: a field of an object the entity holds inline.
            target = document["definitions"][name]
            steps = field.split(".")
            for step in steps[:-1]:
                target = target["properties"][step]
            target.setdefault("properties", {})[steps[-1]] = schema


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    document = yaml.safe_load(open(sys.argv[1]))
    folder = sys.argv[2]
    merge(document, yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "supplement.yaml"))))
    model = Model(document)
    model.taken |= HAND_WRITTEN
    operations = collect(document)
    place(operations)
    write_operations(document, model, operations, folder)
    write_types(model, folder)
    written = [os.path.join(folder, name) for name in ("types.adm", "operations.adm", "operations_test.adm")]
    subprocess.run(["adm", "fmt", *written], check=False, stdout=subprocess.DEVNULL)


main()
