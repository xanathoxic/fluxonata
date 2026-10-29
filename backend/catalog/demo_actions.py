"""Demo actions seeded locally (fictional data, no external account needed)."""

TEXT_STATS_SOURCE = """\
def run(inputs):
    text = inputs["text"]
    words = [w for w in text.split() if w]
    print("analyse de", len(text), "caractères")  # logs go to stderr, never to the result
    return {
        "characters": len(text),
        "words": len(words),
        "longest_word": max(words, key=len) if words else "",
        "uppercase": text.upper() if inputs.get("shout") else None,
    }
"""

TEXT_STATS = {
    "slug": "text-stats",
    "name": "Statistiques de texte",
    "description": "Analyse un texte : compte ses caractères et ses mots, trouve le mot le plus long et peut renvoyer le texte en majuscules. Ne modifie rien et n'accède à aucun service.",
    "domain": "Démonstration",
    "tags": ["texte", "démo"],
    "version": "1.0.0",
    "source": TEXT_STATS_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "title": "Texte",
                "description": "Le texte à analyser.",
                "minLength": 1,
                "maxLength": 10000,
                "examples": ["Bonjour le monde orchestré"],
            },
            "shout": {
                "type": "boolean",
                "title": "Majuscules",
                "description": "Renvoyer aussi le texte en majuscules.",
                "default": False,
            },
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["characters", "words", "longest_word"],
        "properties": {
            "characters": {
                "type": "integer",
                "title": "Caractères",
                "description": "Nombre de caractères du texte, espaces compris.",
            },
            "words": {
                "type": "integer",
                "title": "Mots",
                "description": "Nombre de mots (suites de caractères séparées par des espaces).",
            },
            "longest_word": {
                "type": "string",
                "title": "Mot le plus long",
                "description": "Le mot le plus long du texte ; vide si le texte ne contient aucun mot.",
            },
            "uppercase": {
                "type": ["string", "null"],
                "title": "Texte en majuscules",
                "description": "Le texte converti en majuscules si « Majuscules » est activé, sinon vide (null).",
            },
        },
    },
    "limits": {"timeout_seconds": 20, "memory_mb": 128},
    "examples": [{"text": "Bonjour le monde orchestré", "shout": True}],
}

SLOW_SOURCE = """\
import time


def run(inputs):
    for i in range(inputs["seconds"]):
        print("étape", i + 1)
        time.sleep(1)
    return {"slept": inputs["seconds"]}
"""

SLOW = {
    "slug": "slow-wait",
    "name": "Attente simulée",
    "description": "Attend le nombre de secondes demandé puis se termine : une pause simulée, utile pour observer une exécution en cours. Ne fait rien d'autre.",
    "domain": "Démonstration",
    "tags": ["démo"],
    "version": "1.0.0",
    "source": SLOW_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["seconds"],
        "properties": {
            "seconds": {
                "type": "integer",
                "title": "Secondes",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
                "description": "Durée d'attente.",
            }
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["slept"],
        "properties": {
            "slept": {
                "type": "integer",
                "title": "Secondes attendues",
                "description": "Durée réellement attendue, en secondes (égale à l'entrée « Secondes »).",
            }
        },
    },
    "limits": {"timeout_seconds": 30, "memory_mb": 64},
    "examples": [{"seconds": 3}],
}

NOTIFY_SOURCE = """\
import time


def run(inputs):
    time.sleep(inputs.get("seconds", 2))
    print("notification simulée pour", inputs["recipient"])
    return {"delivered": True, "recipient": inputs["recipient"]}
"""

NOTIFY = {
    "slug": "send-notification",
    "name": "Envoyer une notification (simulé)",
    "description": "Simule l'envoi d'un message à un destinataire : rien n'est réellement envoyé, mais l'action se comporte comme un effet externe NON idempotent (elle n'est jamais relancée à l'aveugle si son issue est incertaine).",
    "domain": "Démonstration",
    "tags": ["effet externe", "démo"],
    "version": "1.0.0",
    "source": NOTIFY_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["recipient"],
        "properties": {
            "recipient": {
                "type": "string",
                "title": "Destinataire",
                "description": "Personne ou adresse à prévenir (texte libre, 100 caractères au plus).",
                "minLength": 1,
                "maxLength": 100,
            },
            "seconds": {
                "type": "integer",
                "title": "Durée simulée (s)",
                "description": "Temps que dure l'envoi simulé, en secondes (0 à 60 ; 2 par défaut).",
                "minimum": 0,
                "maximum": 60,
                "default": 2,
            },
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["delivered"],
        "properties": {
            "delivered": {"type": "boolean", "title": "Remis", "description": "Vrai quand l'envoi simulé est terminé."},
            "recipient": {
                "type": "string",
                "title": "Destinataire",
                "description": "Le destinataire à qui le message a été « remis » (repris de l'entrée).",
            },
        },
    },
    "limits": {"timeout_seconds": 90, "memory_mb": 64},
    "side_effects": "external",
    "idempotent": False,
    "examples": [{"recipient": "equipe@example.org", "seconds": 2}],
}

JSON_SOURCE = """\
def run(inputs):
    data, fields = inputs["data"], inputs["fields"]
    picked = {k: data[k] for k in fields if k in data}
    return {"result": picked, "kept": len(picked), "dropped": len(data) - len(picked)}
"""

JSON_TRANSFORM = {
    "slug": "json-transform",
    "name": "Transformation JSON",
    "description": "Filtre un objet JSON : ne garde que les champs dont vous donnez le nom et compte ceux qui sont conservés et écartés. Ne modifie pas l'objet d'origine (connecteur de démonstration local).",
    "domain": "Démonstration",
    "tags": ["json", "connecteur", "démo"],
    "version": "1.0.0",
    "source": JSON_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["data", "fields"],
        "properties": {
            "data": {
                "type": "object",
                "title": "Objet source",
                "description": "L'objet JSON à filtrer : ses champs sont examinés un à un.",
            },
            "fields": {
                "type": "array",
                "title": "Champs à conserver",
                "items": {"type": "string"},
                "description": "Noms des champs à conserver ; les autres champs de l'objet source sont écartés.",
            },
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["result"],
        "properties": {
            "result": {
                "type": "object",
                "title": "Objet filtré",
                "description": "Un nouvel objet qui ne contient que les champs demandés présents dans l'objet source.",
            },
            "kept": {
                "type": "integer",
                "title": "Champs conservés",
                "description": "Nombre de champs gardés dans l'objet filtré.",
            },
            "dropped": {
                "type": "integer",
                "title": "Champs écartés",
                "description": "Nombre de champs de l'objet source qui n'ont pas été gardés.",
            },
        },
    },
    "limits": {"timeout_seconds": 20, "memory_mb": 64},
    "examples": [{"data": {"a": 1, "b": 2, "c": 3}, "fields": ["a", "c"]}],
}

REPORT_SOURCE = """\
def run(inputs):
    lines = "\\n".join(f"- {line}" for line in inputs["lines"])
    body = f"# {inputs['title']}\\n\\n{lines}\\n"
    return {"lines": len(inputs["lines"]), "__artifacts__": [{"name": "rapport.md", "content_type": "text/markdown", "text": body}]}
"""

MAKE_REPORT = {
    "slug": "make-report",
    "name": "Créer un rapport (artefact)",
    "description": "Produit un rapport : un fichier Markdown téléchargeable, construit à partir d'un titre et de lignes de texte. Le fichier est conservé comme artefact de l'exécution ; rien n'est envoyé ailleurs.",
    "domain": "Démonstration",
    "tags": ["artefact", "connecteur", "démo"],
    "version": "1.0.0",
    "source": REPORT_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["title", "lines"],
        "properties": {
            "title": {
                "type": "string",
                "title": "Titre",
                "description": "Titre du rapport, affiché en tête du fichier (100 caractères au plus).",
                "minLength": 1,
                "maxLength": 100,
            },
            "lines": {
                "type": "array",
                "title": "Lignes",
                "description": "Le contenu du rapport : une ligne de texte par élément de la liste (100 lignes au plus).",
                "items": {"type": "string"},
                "maxItems": 100,
            },
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["lines"],
        "properties": {
            "lines": {
                "type": "integer",
                "title": "Lignes écrites",
                "description": "Nombre de lignes écrites dans le fichier du rapport. Le fichier lui-même est l'artefact de l'exécution.",
            }
        },
    },
    "limits": {"timeout_seconds": 20, "memory_mb": 64},
    "examples": [{"title": "Bilan", "lines": ["premier point", "second point"]}],
}

HTTP_SOURCE = """\
import json
import re
import urllib.request


def run(inputs):
    path = inputs["path"]
    if not re.fullmatch(r"/[A-Za-z0-9/_?=&.-]{0,200}", path):  # fixed host, constrained path: no SSRF surface
        raise ValueError("Chemin non autorisé")
    with urllib.request.urlopen("http://testsvc:8080" + path, timeout=5) as resp:  # noqa: S310 - fixed internal host
        return {"status": resp.status, "body": json.loads(resp.read())}
"""

HTTP_TEST = {
    "slug": "http-test-service",
    "name": "Appel HTTP (service de test local)",
    "description": "Appelle le service HTTP de test fourni avec la plate-forme (GET sur le chemin demandé) et renvoie le code de réponse et son contenu. Politique réseau : service de test uniquement, jamais Internet.",
    "domain": "Démonstration",
    "tags": ["http", "connecteur", "démo"],
    "version": "1.0.0",
    "source": HTTP_SOURCE,
    "input_schema": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "title": "Chemin",
                "default": "/echo?x=1",
                "description": "Chemin sur le service de test (ex. /echo?x=1).",
            }
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {
                "type": "integer",
                "title": "Code HTTP",
                "description": "Code de réponse du service (200 = succès).",
            },
            "body": {
                "type": "object",
                "title": "Réponse",
                "description": "Contenu de la réponse du service, décodé en objet JSON.",
            },
        },
    },
    "limits": {"timeout_seconds": 20, "memory_mb": 64},
    "network_policy": "test-service",
    "examples": [{"path": "/echo?x=1"}],
}

DEMO_ACTIONS = [TEXT_STATS, SLOW, NOTIFY, JSON_TRANSFORM, MAKE_REPORT, HTTP_TEST]
