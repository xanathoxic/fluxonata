"""Hidden built-in actions backing 'script task' nodes.

The user's code is NOT stored in an action version: it travels as an execution input (and lives, immutable, in the
published workflow version). The built-in wrapper only runs it inside the same container sandbox as any action
(no network, non-root, read-only rootfs, limits) and returns the declared outputs.
"""

from catalog.models import Action, ActionVersion

PY_WRAPPER = '''\
import sys


class _System:
    """Logging helper: lines go to the execution logs (stderr), never into the result."""

    def log(self, *a): print("[INFO]", *a, file=sys.stderr)
    def warn(self, *a): print("[WARN]", *a, file=sys.stderr)
    def error(self, *a): print("[ERROR]", *a, file=sys.stderr)


def run(inputs):
    ns = {"System": _System(), "inputs": inputs["inputs"], "vars": inputs["vars"], **inputs["bindings"]}
    exec(compile(inputs["script"], "<script>", "exec"), ns)  # the sandbox, not this wrapper, is the security boundary
    out = {}
    for name in inputs["outputs"]:
        if name not in ns:
            raise NameError(f"La sortie « {name} » n'a pas été définie par le script")
        out[name] = ns[name]
    return out
'''

JS_WRAPPER = """\
export async function run(inp) {
  const System = {
    log: (...a) => console.error("[INFO]", ...a),
    warn: (...a) => console.error("[WARN]", ...a),
    error: (...a) => console.error("[ERROR]", ...a),
  };
  const names = Object.keys(inp.bindings);
  const body = `${inp.script}\\n;return {${inp.outputs.join(",")}};`;
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const fn = new AsyncFunction("System", "inputs", "vars", ...names, body);
  return await fn(System, inp.inputs, inp.vars, ...names.map((n) => inp.bindings[n]));
}
"""

LANGUAGES = {
    "python": ("python", PY_WRAPPER),
    "javascript": ("node", JS_WRAPPER),
}


def get_script_version(workspace, language: str) -> ActionVersion:
    runtime, source = LANGUAGES[language]
    slug = f"__script_{language}__"
    action, _ = Action.objects.get_or_create(
        workspace=workspace, slug=slug, defaults={"name": f"Tâche script ({language})", "description": "Intégré"}
    )
    v = action.versions.filter(version="1.0.0").first()
    if v is None:
        v = ActionVersion.objects.create(
            action=action,
            version="1.0.0",
            runtime=runtime,
            source=source,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effects="none",
            idempotent=True,
            network_policy="none",
            limits={"timeout_seconds": 60, "memory_mb": 128},
        )
    return v
