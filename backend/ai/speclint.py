"""What the request states in plain words, checked against the graph without asking a model: names given to inputs,
outputs and variables, quoted messages and titles, durations, loop bounds, strict comparisons, workflows to call.
Conservative on purpose: a finding is only raised for a pattern that leaves no doubt, because each one costs a
correction round. Returns findings shaped like the reviewer's: {step, severity, message}."""

import json
import re
import unicodedata

TYPE_WORDS = r"(?:liste|texte|nombre|entier|booléen|booleen|objet|chaîne|chaine|décimal|decimal|tableau)"
# « machines (liste de noms) », « seuil (nombre, défaut 3) »
NAME_TYPED = re.compile(rf"\b([a-z_][a-z0-9_]{{2,40}})\s*\(\s*{TYPE_WORDS}\b", re.I)
# « sortie moyenne », « sorties : total (…) », « champ de sortie « resume » »
NAME_AFTER_NOUN = re.compile(
    r"\b(?:sorties?|champs?(?:\s+de\s+sortie)?|variable(?:\s+du\s+workflow)?)\s*:?\s*"
    r"(?:[«\"]\s*([a-z_][a-z0-9_]{2,40})\s*[»\"]|([a-z_][a-z0-9_]{2,40})\s*[,(])",
    re.I,
)
QUOTED = re.compile(r"[«\"]\s*([^»\"\n]{8,100}?)\s*[»\"]")
LABELLED = re.compile(
    r"(?:exception|approbation|message|titre|intitulé|libellé|note|nommée?|appelée?|étiquette)\W*(?:\w+\W+){0,3}$", re.I
)
WAIT = re.compile(r"\b(?:attente|attendre|pause|patiente)\b[^.;\d]{0,25}?(\d+)\s*(seconde|minute|heure)s?", re.I)
ITERATIONS = re.compile(r"(\d+)\s*itérations?", re.I)
PARALLEL = re.compile(r"(\d+)\s*(?:en\s+)?parall[eè]le", re.I)
STRICT = re.compile(r"strictement\s+(supérieur|inférieur)e?\s+à\s+(\d+)", re.I)
INCLUSIVE = re.compile(r"(supérieur|inférieur)e?\s+ou\s+égale?\s+à\s+(\d+)", re.I)
REMOVAL = re.compile(r"\b(supprim|retire|enlève|enleve|efface|remplace|annule)", re.I)
STOP = {
    "texte", "liste", "nombre", "entier", "objet", "chaîne", "chaine", "variable", "sortie", "sorties", "entrée",
    "entrées", "champ", "champs", "type", "valeur", "valeurs_de", "workflow", "étape", "etape", "script", "python",
}  # fmt: skip
UNIT = {"seconde": 1, "minute": 60, "heure": 3600}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c)))


def _names_in_graph(graph: dict, ctx: dict) -> set[str]:
    names = set((graph.get("inputs") or {}).get("properties") or {})
    names |= {v.get("name") for v in graph.get("variables") or [] if isinstance(v, dict)}
    skills = {s["slug"]: s for s in ctx.get("skills") or []}
    catalog = {a["slug"]: a for a in ctx.get("catalog") or []}
    for n in graph.get("nodes", []):
        names |= {o.get("name") for o in n.get("outputs") or [] if isinstance(o, dict)}
        names |= set((n.get("output_schema") or {}).get("properties") or {})
        names |= set((skills.get(n.get("skill")) or {}).get("outputs") or {})
        names |= set((catalog.get(n.get("action")) or {}).get("outputs") or {})
        names.add(n.get("id"))
    return {x for x in names if isinstance(x, str)}


def _on_error_exit(graph: dict, text: str, owns=None) -> bool:
    """An exception carrying `text` is reached from an error exit (a refusal or a failure leads to it); `owns(node)` limits
    it to the error exits of the step the request is talking about, when it names one."""
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    todo = [e["to"] for e in edges if e.get("label") == "error" and (owns is None or owns(nodes.get(e["from"], {})))]
    seen: set = set()
    while todo:
        nid = todo.pop()
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        n = nodes[nid]
        if n.get("type") == "throw" and text in _norm(f"{n.get('message', '')} {n.get('name', '')}"):
            return True
        todo += [e["to"] for e in edges if e["from"] == nid]
    return False


def _owner_of(request: str, upto: int, context: str, ctx: dict):
    """Which step the « si elle est refusée / si elle échoue » clause is about: the approval or the catalog action named
    closest before it in the request. Returns a predicate on nodes (None: any step that can fail, an approval only for a refusal)."""
    refusal = "refus" in context
    named = ""
    kind = ""
    for q in QUOTED.finditer(request[:upto]):
        before = request[max(0, q.start() - 40) : q.start()]
        if re.search(r"approbation", before, re.I):
            named, kind = _norm(q.group(1).strip()), "approval"
        else:
            for a in ctx.get("catalog") or []:
                if _norm(a.get("name", "")) == _norm(q.group(1).strip()):
                    named, kind = a["slug"], "action"
    if kind == "approval":
        return lambda n: n.get("type") == "approval" and named in _norm(f"{n.get('title', '')} {n.get('name', '')}")
    if kind == "action":
        return lambda n: n.get("action") == named
    return (lambda n: n.get("type") == "approval") if refusal else (lambda n: n.get("type") != "approval")


def spec_lint(request: str, graph: dict, ctx: dict, *, additions_only: bool = False) -> list[dict]:
    if additions_only and REMOVAL.search(request):
        return []  # « supprime l'attente de 2 minutes » must not demand a 2 minute wait
    out: list[dict] = []
    nodes = graph.get("nodes", [])

    def add(message, step=None):
        out.append({"step": step, "severity": "error", "message": message})

    declared = {v.get("name") for v in graph.get("variables") or [] if isinstance(v, dict)}
    for n in nodes:  # a variable of the workflow exists once: a script must not recreate it as an output
        for o in n.get("outputs") or []:
            if isinstance(o, dict) and o.get("name") in declared:
                add(
                    f"La variable du workflow « {o['name']} » est déjà déclarée : lis-la par vars.{o['name']} (dans une "
                    "expression ou un binding) au lieu de la recréer comme sortie d'un script.",
                    n.get("id"),
                )

    present = _names_in_graph(graph, ctx)
    wanted = {g for rx in (NAME_TYPED, NAME_AFTER_NOUN) for m in rx.finditer(request) for g in m.groups() if g}
    for name in sorted(w for w in wanted if w.lower() not in STOP and w not in present):
        add(
            f"Le nom « {name} » est demandé tel quel (entrée, sortie ou variable) mais n'existe nulle part : utilise "
            "exactement ce nom, sans le traduire ni le renommer. Pour une étape IA, un nom de sortie précis exige un "
            "prompt avec output_schema (un skill impose les sorties du skill)."
        )
    blob = _norm(json.dumps(graph, ensure_ascii=False))

    def pool(kind: str) -> str:
        """Where a quoted text must appear: an exception's text on an exception step, an approval's on an approval."""
        wanted = {"exception": "throw", "approbation": "approval"}.get(kind)
        picked = [n for n in nodes if n["type"] == wanted] if wanted else []
        return _norm(json.dumps(picked, ensure_ascii=False)) if picked else blob

    for m in QUOTED.finditer(request):
        text = m.group(1).strip()
        before = request[max(0, m.start() - 40) : m.start()]
        label = LABELLED.search(before)
        # only a text introduced as a label to reproduce: « pour chaque » names a kind of step, not a message
        kind = re.match(r"(exception|approbation)", label.group(0), re.I) if label else None
        context = _norm(request[max(0, m.start() - 110) : m.start()])
        on_error = bool(
            kind and kind.group(1).lower() == "exception" and re.search(r"refus|echou|erreur|echec", context)
        )
        present = _norm(text) in pool(kind.group(1).lower() if kind else "")
        if (
            present and on_error
        ):  # « si elle est refusée / échoue, une exception… »: it must hang on that step's error exit
            present = _on_error_exit(graph, _norm(text), _owner_of(request, m.start(), context, ctx))
        if " " in text and label and not present:
            where = {
                "exception": " Il manque une étape exception (throw) portant ce texte ; si la demande la place « si elle est "
                "refusée / si l'étape échoue », accroche-la à la sortie d'erreur de cette étape (add_branch after=<étape> "
                "label=error, avec la notification éventuelle avant l'exception).",
                "approbation": " Il manque une étape approbation (approval) dont le titre est ce texte.",
            }.get(kind.group(1).lower() if kind else "", "")
            excerpt = " ".join(request[max(0, m.start() - 110) : m.end() + 10].split())
            add(
                f"Le texte demandé « {text} » n'apparaît dans aucune étape adaptée (titre, message ou nom) : reprends-le "
                f"tel quel.{where} Extrait de la demande : « …{excerpt} »."
            )
    waits = {n.get("seconds") for n in nodes if n["type"] == "wait"}
    for m in WAIT.finditer(request):
        seconds = int(m.group(1)) * UNIT[m.group(2).lower()]
        if seconds not in waits:
            add(
                f"Une attente de {m.group(1)} {m.group(2)}(s) est demandée : aucune étape d'attente ne dure {seconds} s."
            )
    loops = [n for n in nodes if n["type"] == "foreach"]
    for m in ITERATIONS.finditer(request):
        if loops and not any(n.get("max_iterations") == int(m.group(1)) for n in loops):
            add(
                f"{m.group(1)} itérations au maximum sont demandées : max_iterations de la boucle doit valoir {m.group(1)}."
            )
    for m in PARALLEL.finditer(request):
        if loops and not any(n.get("concurrency") == int(m.group(1)) for n in loops):
            add(f"{m.group(1)} en parallèle est demandé : concurrency de la boucle doit valoir {m.group(1)}.")
    exprs = [n.get("expr", "") for n in nodes if n["type"] == "condition"]
    exprs += [
        c.get("expr", "") for n in nodes if n["type"] == "switch" for c in n.get("cases") or [] if isinstance(c, dict)
    ]
    for m in STRICT.finditer(request):
        op, n = (">" if m.group(1).lower() == "supérieur" else "<"), m.group(2)
        if (
            exprs
            and not any(re.search(rf"(?<![<>=]){op}\s*{n}\b", e) for e in exprs)
            and any(f"{op}= {n}" in e or f"{op}={n}" in e for e in exprs)
        ):
            add(f"« strictement {m.group(1)} à {n} » demande l'opérateur {op} et non {op}=.")
    for m in INCLUSIVE.finditer(request):
        op, n = (">=" if m.group(1).lower() == "supérieur" else "<="), m.group(2)
        if exprs and not any(re.search(rf"{op}\s*{n}\b", e) for e in exprs):
            add(f"« {m.group(1)} ou égal à {n} » demande l'opérateur {op}.")
    called = {n.get("workflow") for n in nodes if n["type"] in ("subworkflow", "foreach")}
    for wf in ctx.get("workflows") or []:
        slug = wf["slug"]
        if re.search(rf"(?<![\w-]){re.escape(slug)}(?![\w-])", request) and slug not in called:
            add(f"Le workflow « {slug} » est cité dans la demande mais aucune étape ne l'appelle.")
    return out
