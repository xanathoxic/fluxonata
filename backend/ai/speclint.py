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


def spec_lint(request: str, graph: dict, ctx: dict, *, additions_only: bool = False) -> list[dict]:
    if additions_only and REMOVAL.search(request):
        return []  # « supprime l'attente de 2 minutes » must not demand a 2 minute wait
    out: list[dict] = []
    nodes = graph.get("nodes", [])

    def add(message, step=None):
        out.append({"step": step, "severity": "error", "message": message})

    present = _names_in_graph(graph, ctx)
    wanted = {g for rx in (NAME_TYPED, NAME_AFTER_NOUN) for m in rx.finditer(request) for g in m.groups() if g}
    for name in sorted(w for w in wanted if w.lower() not in STOP and w not in present):
        add(
            f"Le nom « {name} » est demandé tel quel (entrée, sortie ou variable) mais n'existe nulle part : utilise "
            "exactement ce nom, sans le traduire ni le renommer. Pour une étape IA, un nom de sortie précis exige un "
            "prompt avec output_schema (un skill impose les sorties du skill)."
        )
    blob = _norm(json.dumps(graph, ensure_ascii=False))
    for m in QUOTED.finditer(request):
        text = m.group(1).strip()
        before = request[max(0, m.start() - 40) : m.start()]
        # only a text introduced as a label to reproduce: « pour chaque » names a kind of step, not a message
        if " " in text and LABELLED.search(before) and _norm(text) not in blob:
            add(
                f"Le texte demandé « {text} » n'apparaît dans aucune étape (titre, message ou nom) : reprends-le tel quel."
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
