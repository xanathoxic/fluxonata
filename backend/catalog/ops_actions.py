"""Useful everyday actions (fictional-data free, no account needed), each with a TEST mode.

Every action takes `mode`: « reel » does the work; « test » simulates it: nothing outside the action is consulted or
changed, the main input may be left empty (a built-in sample is used so the action can be tried in one click), and the
result says `simulated: true` so a workflow can tell a rehearsal from the real thing. These actions only compute (the
sandbox has no network), so « test » never hides a side effect: it is the same contract the future integrations will
follow, where « test » will mean « build the request, do not send it »."""

MODE = {
    "type": "string",
    "title": "Mode",
    "description": "« reel » : traite les données fournies. « test » : simulation sans effet ; si l'entrée principale est vide, un exemple intégré est utilisé ; le résultat porte simulated = true.",
    "enum": ["reel", "test"],
    "default": "reel",
}
SIMULATED = {
    "type": "boolean",
    "title": "Simulé",
    "description": "Vrai si l'action a tourné en mode test (résultat de simulation), faux en mode réel.",
}
LIMITS = {"timeout_seconds": 20, "memory_mb": 128}


def _spec(slug, name, description, tags, source, inputs, outputs, example, required=()):
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "domain": "Opérations",
        "tags": ["opérations", *tags],
        "version": "1.0.0",
        "source": source,
        "input_schema": {
            "type": "object",
            "required": list(required),
            "properties": {**inputs, "mode": MODE},
        },
        "output_schema": {
            "type": "object",
            "required": [*outputs, "simulated"],
            "properties": {**outputs, "simulated": SIMULATED},
        },
        "limits": LIMITS,
        "examples": [example],
        "side_effects": "none",
        "idempotent": True,
        "network_policy": "none",
    }


def _s(title, description, **extra):
    return {"type": "string", "title": title, "description": description, **extra}


def _n(title, description, kind="integer", **extra):
    return {"type": kind, "title": title, "description": description, **extra}


def _l(title, description, items="string"):
    return {"type": "array", "title": title, "description": description, "items": {"type": items}}


PARSE_HOSTS = _spec(
    "parse-hosts",
    "Extraire une liste de machines",
    "Transforme un texte libre (une machine par ligne, ou séparées par des virgules, points-virgules ou espaces) en liste propre de noms de machines valides, sans doublon, et signale les valeurs rejetées.",
    ["texte", "inventaire"],
    """\
import re

SAMPLE = "web-01, web-02\\ndb-01;web-01 bad_host!"
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def run(inputs):
    test = inputs.get("mode") == "test"
    text = inputs.get("text") or (SAMPLE if test else "")
    if not text.strip():
        raise ValueError("aucun texte à analyser")
    seen, hosts, rejected = set(), [], []
    for token in re.split(r"[\\s,;]+", text.strip()):
        if not token:
            continue
        name = token.lower()
        if not HOST.match(name):
            rejected.append(token)
        elif name not in seen:
            seen.add(name)
            hosts.append(name)
    return {"hosts": hosts, "count": len(hosts), "rejected": rejected, "simulated": test}
""",
    {
        "text": _s(
            "Texte",
            "Les machines, séparées par des retours à la ligne, virgules, points-virgules ou espaces.",
            maxLength=20000,
        )
    },
    {
        "hosts": _l("Machines", "Noms de machines valides, en minuscules, sans doublon, dans l'ordre d'apparition."),
        "count": _n("Nombre", "Nombre de machines valides retenues."),
        "rejected": _l(
            "Rejetées", "Valeurs qui ne sont pas des noms de machine valides (liste vide si tout est valide)."
        ),
    },
    {"text": "web-01, web-02\ndb-01", "mode": "reel"},
)

CHECK_VM = _spec(
    "check-vm-request",
    "Contrôler une demande de machine virtuelle",
    "Vérifie une demande de machine virtuelle (processeurs, mémoire, disque, environnement) contre des plafonds par environnement ; renvoie si elle est acceptable, la liste des écarts et une taille indicative (petite, moyenne, grande).",
    ["machines virtuelles", "contrôle"],
    """\
LIMITS = {"dev": (8, 32, 500), "recette": (16, 64, 1000), "prod": (32, 128, 2000)}


def run(inputs):
    test = inputs.get("mode") == "test"
    cpu = inputs.get("cpu", 2 if test else None)
    ram = inputs.get("ram_gb", 4 if test else None)
    disk = inputs.get("disk_gb", 50 if test else None)
    env = inputs.get("environment") or ("dev" if test else None)
    if None in (cpu, ram, disk) or not env:
        raise ValueError("cpu, ram_gb, disk_gb et environment sont nécessaires")
    if env not in LIMITS:
        raise ValueError("environnement inconnu : " + str(env))
    max_cpu, max_ram, max_disk = LIMITS[env]
    problems = []
    for label, value, cap, unit in (("processeurs", cpu, max_cpu, ""), ("mémoire", ram, max_ram, " Go"), ("disque", disk, max_disk, " Go")):
        if value < 1:
            problems.append(label + " : au moins 1 est nécessaire")
        elif value > cap:
            problems.append(label + " : " + str(value) + unit + " dépasse le plafond de " + str(cap) + unit + " pour « " + env + " »")
    score = cpu + ram / 4 + disk / 100
    size = "petite" if score <= 6 else "moyenne" if score <= 20 else "grande"
    return {"acceptable": not problems, "problems": problems, "size": size, "simulated": test}
""",
    {
        "cpu": _n("Processeurs", "Nombre de processeurs virtuels demandés.", minimum=1),
        "ram_gb": _n("Mémoire (Go)", "Mémoire demandée, en gigaoctets.", minimum=1),
        "disk_gb": _n("Disque (Go)", "Espace disque demandé, en gigaoctets.", minimum=1),
        "environment": _s("Environnement", "Environnement cible de la machine.", enum=["dev", "recette", "prod"]),
    },
    {
        "acceptable": {
            "type": "boolean",
            "title": "Acceptable",
            "description": "Vrai si la demande respecte tous les plafonds.",
        },
        "problems": _l("Écarts", "Un message par plafond dépassé ; liste vide si la demande est acceptable."),
        "size": _s(
            "Taille",
            "Taille indicative de la machine : petite, moyenne ou grande.",
            enum=["petite", "moyenne", "grande"],
        ),
    },
    {"cpu": 4, "ram_gb": 16, "disk_gb": 100, "environment": "prod", "mode": "reel"},
)

CHECK_WINDOW = _spec(
    "check-maintenance-window",
    "Vérifier une fenêtre de maintenance",
    "Indique si un instant donné tombe dans une fenêtre de maintenance (plage d'heures, éventuellement à cheval sur minuit, et jours autorisés) et calcule le début de la prochaine fenêtre.",
    ["maintenance", "planification"],
    """\
from datetime import datetime, timedelta

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _in(moment, start, end, days):
    hour = moment.hour + moment.minute / 60
    if start <= end:
        return start <= hour < end and DAYS[moment.weekday()] in days
    if hour >= start:
        return DAYS[moment.weekday()] in days
    return hour < end and DAYS[(moment.weekday() - 1) % 7] in days


def run(inputs):
    test = inputs.get("mode") == "test"
    raw = inputs.get("now") or ("2026-01-03T23:30:00" if test else None)
    if not raw:
        raise ValueError("now (date et heure ISO) est nécessaire")
    now = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    start, end = inputs.get("start_hour", 22), inputs.get("end_hour", 6)
    days = inputs.get("days") or DAYS
    unknown = [d for d in days if d not in DAYS]
    if unknown:
        raise ValueError("jours inconnus : " + ", ".join(unknown))
    inside = _in(now, start, end, days)
    probe = now.replace(minute=0, second=0, microsecond=0)
    nxt = None
    for _ in range(24 * 8):
        probe += timedelta(hours=1)
        if _in(probe, start, end, days) and not _in(probe - timedelta(hours=1), start, end, days):
            nxt = probe
            break
    return {"in_window": inside, "next_start": nxt.isoformat() if nxt else "", "simulated": test}
""",
    {
        "now": _s("Instant testé", "Date et heure à contrôler, au format ISO (ex. 2026-03-14T23:30:00)."),
        "start_hour": _n(
            "Heure de début", "Heure d'ouverture de la fenêtre (0 à 23).", minimum=0, maximum=23, default=22
        ),
        "end_hour": _n(
            "Heure de fin",
            "Heure de fermeture (0 à 23) ; si elle est avant le début, la fenêtre passe minuit.",
            minimum=0,
            maximum=23,
            default=6,
        ),
        "days": _l("Jours autorisés", "Jours où la fenêtre s'ouvre (lundi … dimanche) ; tous les jours si vide."),
    },
    {
        "in_window": {
            "type": "boolean",
            "title": "Dans la fenêtre",
            "description": "Vrai si l'instant testé est dans la fenêtre de maintenance.",
        },
        "next_start": _s(
            "Prochaine ouverture", "Début de la prochaine fenêtre (ISO) dans les 8 jours ; vide s'il n'y en a pas."
        ),
    },
    {"now": "2026-01-03T23:30:00", "start_hour": 22, "end_hour": 6, "mode": "reel"},
)

AGGREGATE = _spec(
    "aggregate-results",
    "Synthétiser des résultats",
    "Compte les résultats d'une liste d'opérations (chacune avec un statut « ok » ou « echec » et un nom) : totaux, taux de réussite en pourcentage et noms des opérations en échec.",
    ["synthèse", "rapport"],
    """\
SAMPLE = [{"name": "web-01", "status": "ok"}, {"name": "web-02", "status": "echec"}, {"name": "db-01", "status": "ok"}]


def run(inputs):
    test = inputs.get("mode") == "test"
    items = inputs.get("results") or (SAMPLE if test else [])
    if not items:
        raise ValueError("aucun résultat à synthétiser")
    failed = [str(i.get("name", "?")) for i in items if i.get("status") != "ok"]
    total = len(items)
    ok = total - len(failed)
    return {"total": total, "succeeded": ok, "failed": len(failed), "success_rate": round(100 * ok / total), "failed_names": failed, "simulated": test}
""",
    {
        "results": {
            "type": "array",
            "title": "Résultats",
            "description": "Liste d'objets {name, status} ; status vaut « ok » pour une réussite, toute autre valeur compte comme un échec.",
            "items": {"type": "object"},
        }
    },
    {
        "total": _n("Total", "Nombre d'opérations examinées."),
        "succeeded": _n("Réussies", "Nombre d'opérations au statut « ok »."),
        "failed": _n("En échec", "Nombre d'opérations dont le statut n'est pas « ok »."),
        "success_rate": _n("Taux de réussite", "Pourcentage de réussite arrondi, de 0 à 100.", minimum=0, maximum=100),
        "failed_names": _l("Noms en échec", "Noms des opérations en échec ; liste vide si tout a réussi."),
    },
    {"results": [{"name": "web-01", "status": "ok"}, {"name": "web-02", "status": "echec"}], "mode": "reel"},
)

RENDER = _spec(
    "render-message",
    "Composer un message",
    "Remplit un modèle de message contenant des repères {{nom}} avec des valeurs ; renvoie le texte final et la liste des repères restés sans valeur. Sert à préparer des courriels ou notifications.",
    ["message", "notification"],
    """\
import re

SAMPLE = "Bonjour {{prenom}}, la machine {{machine}} est prête ({{environnement}})."
SAMPLE_VALUES = {"prenom": "Alice", "machine": "web-01"}


def run(inputs):
    test = inputs.get("mode") == "test"
    template = inputs.get("template") or (SAMPLE if test else "")
    values = inputs.get("values") or (SAMPLE_VALUES if test and not inputs.get("template") else {})
    if not template:
        raise ValueError("aucun modèle fourni")
    missing = []

    def fill(match):
        key = match.group(1)
        if key in values and values[key] is not None:
            return str(values[key])
        if key not in missing:
            missing.append(key)
        return match.group(0)

    return {"message": re.sub(r"\\{\\{\\s*([A-Za-z0-9_]+)\\s*\\}\\}", fill, template), "missing": missing, "simulated": test}
""",
    {
        "template": _s("Modèle", "Texte avec des repères {{nom}}.", maxLength=10000),
        "values": {
            "type": "object",
            "title": "Valeurs",
            "description": "Valeurs des repères, sous la forme {nom: valeur}.",
        },
    },
    {
        "message": _s("Message", "Le texte final ; les repères sans valeur y restent tels quels."),
        "missing": _l("Repères manquants", "Noms des repères sans valeur ; liste vide si tous sont remplis."),
    },
    {"template": "Bonjour {{prenom}}", "values": {"prenom": "Alice"}, "mode": "reel"},
)

SCAN_LOG = _spec(
    "scan-log",
    "Repérer les erreurs dans un journal",
    "Parcourt un journal texte, compte les lignes par niveau (ERROR, WARN, INFO) et extrait les premières lignes d'erreur ou d'alerte pour un diagnostic rapide.",
    ["journal", "diagnostic"],
    """\
SAMPLE = "INFO démarrage\\nWARN disque à 85%\\nERROR connexion refusée\\nINFO fin"


def run(inputs):
    test = inputs.get("mode") == "test"
    text = inputs.get("text") or (SAMPLE if test else "")
    if not text.strip():
        raise ValueError("journal vide")
    limit = inputs.get("max_lines", 5)
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    notable = []
    for line in text.splitlines():
        upper = line.upper()
        level = "ERROR" if ("ERROR" in upper or "FATAL" in upper) else "WARN" if "WARN" in upper else "INFO" if "INFO" in upper else None
        if level:
            counts[level] += 1
        if level in ("ERROR", "WARN") and len(notable) < limit:
            notable.append(line.strip()[:300])
    worst = "ERROR" if counts["ERROR"] else "WARN" if counts["WARN"] else "INFO"
    return {"errors": counts["ERROR"], "warnings": counts["WARN"], "infos": counts["INFO"], "worst": worst, "notable": notable, "simulated": test}
""",
    {
        "text": _s("Journal", "Le contenu du journal, une entrée par ligne.", maxLength=200000),
        "max_lines": _n(
            "Lignes à extraire",
            "Nombre maximal de lignes d'erreur ou d'alerte renvoyées.",
            minimum=1,
            maximum=50,
            default=5,
        ),
    },
    {
        "errors": _n("Erreurs", "Nombre de lignes d'erreur (ERROR ou FATAL)."),
        "warnings": _n("Alertes", "Nombre de lignes d'alerte (WARN)."),
        "infos": _n("Informations", "Nombre de lignes d'information (INFO)."),
        "worst": _s("Pire niveau", "Niveau le plus grave trouvé.", enum=["ERROR", "WARN", "INFO"]),
        "notable": _l("Lignes notables", "Premières lignes d'erreur ou d'alerte, tronquées à 300 caractères."),
    },
    {"text": "INFO ok\nERROR disque plein", "mode": "reel"},
)

REDACT = _spec(
    "redact-text",
    "Masquer les données sensibles",
    "Remplace dans un texte les adresses électroniques, adresses IPv4 et jetons secrets évidents (suites longues de caractères hexadécimaux ou « token=… ») par des marques, pour pouvoir partager un journal sans fuite.",
    ["sécurité", "texte"],
    """\
import re

SAMPLE = "contact alice@example.org depuis 10.1.2.3 avec token=abcd1234secret"
RULES = (
    ("email", re.compile(r"[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+")),
    ("ip", re.compile(r"\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b")),
    ("secret", re.compile(r"(?i)\\b(token|secret|password|mot_de_passe)=\\S+")),
    ("secret", re.compile(r"\\b[0-9a-fA-F]{32,}\\b")),
)


def run(inputs):
    test = inputs.get("mode") == "test"
    text = inputs.get("text") or (SAMPLE if test else "")
    if not text:
        raise ValueError("aucun texte à traiter")
    total = 0
    for kind, rule in RULES:
        text, n = rule.subn("[" + kind + " masqué]", text)
        total += n
    return {"redacted": text, "replacements": total, "simulated": test}
""",
    {"text": _s("Texte", "Le texte à nettoyer.", maxLength=200000)},
    {
        "redacted": _s("Texte masqué", "Le texte où chaque donnée sensible est remplacée par « [… masqué] »."),
        "replacements": _n("Remplacements", "Nombre de données masquées ; 0 si le texte n'en contenait pas."),
    },
    {"text": "écrire à alice@example.org", "mode": "reel"},
)

DEADLINE = _spec(
    "compute-deadline",
    "Calculer une échéance",
    "Ajoute un délai en heures à une date de départ, renvoie l'échéance (ISO), et indique si elle est dépassée par rapport à un instant donné, avec le nombre d'heures de retard ou de marge.",
    ["planification", "délai"],
    """\
from datetime import datetime, timedelta


def _parse(raw):
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def run(inputs):
    test = inputs.get("mode") == "test"
    start = inputs.get("start") or ("2026-01-05T09:00:00" if test else None)
    hours = inputs.get("hours", 24 if test else None)
    if not start or hours is None:
        raise ValueError("start et hours sont nécessaires")
    due = _parse(start) + timedelta(hours=hours)
    now = _parse(inputs["now"]) if inputs.get("now") else (_parse(start) + timedelta(hours=hours + 2) if test else datetime.utcnow())
    margin = round((due - now).total_seconds() / 3600, 2)
    return {"due": due.isoformat(), "overdue": margin < 0, "hours_left": margin, "simulated": test}
""",
    {
        "start": _s("Départ", "Date et heure de départ, au format ISO."),
        "hours": _n("Délai (heures)", "Nombre d'heures accordées (peut être décimal).", kind="number", minimum=0),
        "now": _s("Instant de référence", "Date et heure ISO à comparer à l'échéance ; l'heure courante si vide."),
    },
    {
        "due": _s("Échéance", "Date et heure limite, au format ISO."),
        "overdue": {
            "type": "boolean",
            "title": "Dépassée",
            "description": "Vrai si l'échéance est passée à l'instant de référence.",
        },
        "hours_left": _n(
            "Marge (heures)", "Heures restantes avant l'échéance ; négatif si elle est dépassée.", kind="number"
        ),
    },
    {"start": "2026-01-05T09:00:00", "hours": 24, "now": "2026-01-05T12:00:00", "mode": "reel"},
)

COMPARE = _spec(
    "compare-lists",
    "Comparer deux listes",
    "Compare une liste attendue et une liste constatée (noms de machines, comptes, paquets…) : renvoie les éléments manquants, les éléments en trop et les éléments communs, pour détecter un écart de configuration.",
    ["inventaire", "écart"],
    """\
def run(inputs):
    test = inputs.get("mode") == "test"
    expected = inputs.get("expected") or (["web-01", "web-02", "db-01"] if test else [])
    actual = inputs.get("actual") or (["web-01", "db-01", "cache-01"] if test else [])
    if not expected and not actual:
        raise ValueError("les deux listes sont vides")
    e, a = [str(x) for x in expected], [str(x) for x in actual]
    missing = [x for x in dict.fromkeys(e) if x not in a]
    extra = [x for x in dict.fromkeys(a) if x not in e]
    common = [x for x in dict.fromkeys(e) if x in a]
    return {"missing": missing, "extra": extra, "common": common, "identical": not missing and not extra, "simulated": test}
""",
    {
        "expected": _l("Liste attendue", "Les éléments qui devraient être présents."),
        "actual": _l("Liste constatée", "Les éléments réellement présents."),
    },
    {
        "missing": _l("Manquants", "Éléments attendus mais absents de la liste constatée."),
        "extra": _l("En trop", "Éléments constatés mais non attendus."),
        "common": _l("Communs", "Éléments présents dans les deux listes."),
        "identical": {
            "type": "boolean",
            "title": "Identiques",
            "description": "Vrai si aucune différence n'a été trouvée.",
        },
    },
    {"expected": ["a", "b"], "actual": ["b", "c"], "mode": "reel"},
)

OPS_ACTIONS = [PARSE_HOSTS, CHECK_VM, CHECK_WINDOW, AGGREGATE, RENDER, SCAN_LOG, REDACT, DEADLINE, COMPARE]
OPS_FOLDER = "Opérations"
