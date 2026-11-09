"""Masquage des jetons « capacité » portés par l'adresse dans les journaux (formulaires publics, reprises).

Les adresses ne changent pas (l'URL publique reste `/api/v1/forms/<jeton>/`, `/f/<jeton>`…) : seul ce qui est ÉCRIT dans un journal est
réécrit, la route restant reconnaissable (`/api/v1/forms/[jeton]/`). Routes couvertes : `/api/v1/forms/`, `/api/v1/resume-forms/`,
`/api/v1/hooks/resume/` et les pages `/f/` et `/r/` de l'interface, y compris dans un en-tête Referer (adresse complète).
Le jeton s'arrête à `/ ? # < > & \\`, une espace ou un guillemet (le texte peut être du HTML ou du JSON échappé). Non couvert : `/api/v1/hooks/<uuid>/` (identifiant public, la requête est authentifiée par signature HMAC) et toute autre forme d'adresse.
Les expressions sont dupliquées dans `infra/nginx.conf` et `infra/nginx.roles.conf` (nginx n'exécute pas Python) : `nginx_patterns()` les produit et `tests/test_url_token_logs.py`
vérifie que les fichiers les contiennent à l'identique.
"""

import re

ROUTES = ("api/v1/forms", "api/v1/resume-forms", "api/v1/hooks/resume")
PAGES = ("f", "r")
PLACEHOLDER = "[jeton]"

# Une route peut arriver écrite autrement que dans sa forme littérale : un client (ou un scanner) peut encoder une lettre en pourcent
# (`/api/v1/%66orms/<jeton>/`), la barre (`%2f`) ou la doubler (`//`) ; nginx et Django les ramènent à la même route, mais `$request_uri` et
# le journal de gunicorn gardent l'adresse brute (constaté sur un vrai nginx le 2026-11-09). Les motifs ci-dessous reconnaissent donc la route
# sous ces formes, sans tenir compte de la casse ; le jeton, lui, est tout ce qui suit jusqu'au séparateur.
SLASH = r"(?:/|%2[fF])+"
# Le motif ne démarre qu'au DÉBUT d'une suite de barres : sans cela, `re.sub` l'essaierait à chaque position d'une longue suite de `/` ou de `%2f`
# et chaque essai la consommerait en entier (temps quadratique : 1,5 s pour 8 000 barres, mesuré le 2026-11-09 sur un chemin ou un Referer).
RUN_START = r"(?<!/)(?<!%2[fF])"


def _letters(word: str) -> str:
    """`word` où chaque caractère peut aussi être écrit `%HH` (majuscule ou minuscule), la casse étant indifférente."""
    parts = []
    for ch in word:
        forms = {re.escape(ch)} | {f"%{ord(c):02X}" for c in {ch.lower(), ch.upper()}}
        parts.append("(?:" + "|".join(sorted(forms)) + ")")
    return "".join(parts)


def _route(route: str) -> str:
    return SLASH.join(_letters(word) for word in route.split("/"))


API_ROUTES = "(?:" + "|".join(_route(r) for r in ROUTES) + ")"
PAGE_ROUTES = "(?:" + "|".join(_letters(p) for p in PAGES) + ")"
_API = r"(?P<api>" + RUN_START + r"(?i:" + SLASH + API_ROUTES + SLASH + r"))"
_PAGE = (
    r"(?P<page>(?:^|[\s\"'(>;=,\[{]|:(?!//)|(?i:https?)://[^/\s\"']+)"
    + RUN_START
    + r"(?i:"
    + SLASH
    + PAGE_ROUTES
    + SLASH
    + r"))"
)
_PATTERN = re.compile(rf"(?:{_API}|{_PAGE})[^/?#\s\"'<>&\\]+")


def nginx_patterns() -> dict[str, str]:
    """Les quatre expressions de `infra/nginx.conf` et `infra/nginx.roles.conf` (nginx n'exécute pas Python) : le test les compare au texte des fichiers."""
    api, page = SLASH + API_ROUTES + SLASH, SLASH + PAGE_ROUTES + SLASH
    return {
        "uri_api": rf"~*^(?<fx_api>{api})[^/?]+(?<fx_api_rest>.*)$",
        "uri_page": rf"~*^(?<fx_page>{page})[^/?]+(?<fx_page_rest>.*)$",
        "referer_api": rf"~*^(?<fx_rapi>https?://[^/]+{api})[^/?#]+(?<fx_rapi_rest>.*)$",
        "referer_page": rf"~*^(?<fx_rpage>https?://[^/]+{page})[^/?#]+(?<fx_rpage_rest>.*)$",
    }


def redact_url_tokens(text: str) -> str:
    """`text` où le segment jeton des routes ci-dessus est remplacé par `[jeton]`."""
    return _PATTERN.sub(lambda m: (m.group("api") or m.group("page")) + PLACEHOLDER, text)
