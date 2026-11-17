"""Corpus de scripts Python rejoué sur le worker intégré ET sur l'agent distant : le passage sur agent ne doit pas modifier le contrat de l'étape Script Python.

Utilisé par `test_python_parity.py` (exécuteur sans isolation, rapide) (l'essai avec l'exécuteur Docker dans un démon distinct reste à faire).
Chaque cas : source, entrées, délai, et ce qu'un utilisateur peut observer — statut, résultat, code d'erreur, présence de fragments dans les journaux.
"""

SECRET = "sk-live-" + "very-secret-123456"  # valeur fictive (même fabrication que tests/test_connections.py)
ANY = {"type": "object"}
BIG_LOG = "import sys\ndef run(i):\n    sys.stderr.write('x' * 200000 + 'FIN')\n    return {}\n"

# nom -> (source, entrées, délai en secondes, secrets requis)
CASES: dict[str, tuple[str, dict, int, bool]] = {
    "stdlib": (
        "import json, statistics\ndef run(i):\n    return {'mean': statistics.mean(i['values']), 'size': len(json.dumps(i))}\n",
        {"values": [1, 2, 3, 4]},
        20,
        False,
    ),
    "inputs-dict-list-null": (
        "def run(i):\n    return {'keys': sorted(i), 'first': i['rows'][0], 'none': i['none'] is None, 'n': len(i['rows'])}\n",
        {"rows": [{"a": 1}, [2, 3], None], "none": None, "nested": {"x": [None]}},
        20,
        False,
    ),
    "structured-output": (
        "def run(i):\n    return {'a': [1, {'b': None}], 'texte': 'été ✓', 'f': 1.5, 'ok': True}\n",
        {},
        20,
        False,
    ),
    "stdout-stderr": (
        "import sys\ndef run(i):\n    print('vers-stdout')\n    sys.stderr.write('vers-stderr\\n')\n    return {'n': 1}\n",
        {},
        20,
        False,
    ),
    "exception": ("def run(i):\n    raise ValueError('panne-volontaire')\n", {}, 20, False),
    "timeout": ("import time\ndef run(i):\n    time.sleep(60)\n    return {}\n", {}, 2, False),
    "missing-dependency": ("import numpy\ndef run(i):\n    return {}\n", {}, 20, False),
    "result-too-large": ("def run(i):\n    return {'x': 'a' * 2_000_000}\n", {}, 20, False),
    "logs-too-large": (BIG_LOG, {}, 20, False),
    "secret": (
        "import os\ndef run(i):\n    print('jeton', os.environ['API_TOKEN'])\n    return {'n': len(os.environ['API_TOKEN']), 'echo': os.environ['API_TOKEN']}\n",
        {},
        20,
        True,
    ),
}
