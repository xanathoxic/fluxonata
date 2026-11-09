"""Jetons « capacité » dans l'adresse : absents des journaux réellement produits (core.urlredact, filtre Django, classe gunicorn, nginx).

Jetons synthétiques. Les adresses publiques et le comportement des routes ne changent pas : seul le texte écrit dans les journaux est réécrit.
nginx n'est pas démarré ici : seule la cohérence de ses fichiers est vérifiée (les expressions des `map` sont émulées en Python) ; un nginx réel jetable les valide (docs/ADMIN.md)."""

import io
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from django.test import Client

from core import urlredact
from core.logging import RedactUrlTokensFilter
from core.urlredact import redact_url_tokens

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TOK = "SYNTH-TOK-Zq8_x-91"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"GET /api/v1/forms/{TOK}/ HTTP/1.1", "GET /api/v1/forms/[jeton]/ HTTP/1.1"),
        (f"/api/v1/forms/{TOK}/lookup/champ/?q=1", "/api/v1/forms/[jeton]/lookup/champ/?q=1"),
        (f"/api/v1/resume-forms/{TOK}/", "/api/v1/resume-forms/[jeton]/"),
        (
            f"Method Not Allowed (GET): /api/v1/hooks/resume/{TOK}/",
            "Method Not Allowed (GET): /api/v1/hooks/resume/[jeton]/",
        ),
        (f"GET /f/{TOK}", "GET /f/[jeton]"),
        (f"GET /r/{TOK}?x=1 HTTP/1.1", "GET /r/[jeton]?x=1 HTTP/1.1"),
        (f'"https://hote.example/f/{TOK}"', '"https://hote.example/f/[jeton]"'),
        (f'"http://localhost:5173/r/{TOK}#a"', '"http://localhost:5173/r/[jeton]#a"'),
        (f"a /f/{TOK} b /api/v1/forms/{TOK}/", "a /f/[jeton] b /api/v1/forms/[jeton]/"),
    ],
)
def test_token_segments_are_replaced_and_the_route_stays_readable(raw, expected):
    assert redact_url_tokens(raw) == expected


@pytest.mark.parametrize(
    "unchanged",
    [
        "GET /api/v1/hooks/11111111-2222-3333-4444-555555555555/ HTTP/1.1",  # public identifier, authenticated by HMAC
        "GET /api/v1/workspaces/team-a/workflows/?page=2",
        "GET /api/v1/workspaces/team-a/f/abc",  # `/f/` inside another route is not a public page
        "GET /library/forms/mon-formulaire/",
        "message sans adresse, ni /f ni /r/ seuls",
        "",
    ],
)
def test_other_addresses_and_text_are_left_alone(unchanged):
    assert redact_url_tokens(unchanged) == unchanged


@pytest.fixture
def console(monkeypatch):
    """Sortie réelle des gestionnaires configurés par fluxonata.settings (racine et enregistreur « django »), redirigée vers un tampon."""
    buf = io.StringIO()
    seen = set()
    for name in ("", "django", "django.request", "django.server"):
        for handler in logging.getLogger(name).handlers:
            if isinstance(handler, logging.StreamHandler) and id(handler) not in seen:
                seen.add(id(handler))
                monkeypatch.setattr(handler, "stream", buf)
    return buf


@pytest.mark.parametrize(
    "debug", [False, True]
)  # True : le gestionnaire texte de Django (développement) est actif en plus
def test_django_request_messages_on_error_paths_never_carry_the_token(console, settings, debug):
    settings.DEBUG = debug
    c = Client(raise_request_exception=False)
    assert c.get(f"/api/v1/hooks/resume/{TOK}/").status_code == 405  # warning, no database needed
    assert (
        c.get(f"/api/v1/resume-forms/{TOK}/", HTTP_REFERER=f"http://h.example/r/{TOK}").status_code == 500
    )  # error + traceback
    out = console.getvalue()
    assert (
        "Method Not Allowed" in out and "/api/v1/hooks/resume/[jeton]/" in out and "Internal Server Error" in out
    )  # useful part kept
    assert TOK not in out


def test_the_json_formatter_receives_the_masked_message_whatever_the_arguments(console):
    logging.getLogger("django.request").warning("Not Found: %s", f"/api/v1/forms/{TOK}/")
    logging.getLogger("django.server").info('"GET /f/%s HTTP/1.1" 200', TOK)
    out = console.getvalue()
    assert TOK not in out and "/api/v1/forms/[jeton]/" in out and "/f/[jeton]" in out


def test_a_message_whose_arguments_do_not_match_is_left_to_logging_without_crashing():
    from core.logging import RedactUrlTokensFilter

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s %s", ("un seul",), None)
    assert RedactUrlTokensFilter().filter(record) is True and record.args == ("un seul",)


# --- gunicorn réel ------------------------------------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_gunicorn(args: list[str], paths: list[str], cwd: Path, env_extra: dict) -> tuple[str, list[int]]:
    port = _free_port()
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "PYTHONPATH": str(BACKEND), **env_extra}
    proc = subprocess.Popen(  # noqa: S603 -- commande fixée par ce test : interpréteur courant, arguments littéraux
        [sys.executable, "-m", "gunicorn", *args, "-b", f"127.0.0.1:{port}", "-w", "1", "--access-logfile", "-"],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    statuses = []
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        for path in paths:
            url = f"http://127.0.0.1:{port}{path}"  # adresse locale construite par ce test
            req = urllib.request.Request(url, headers={"Referer": f"http://h.example/f/{TOK}"})  # noqa: S310 -- schéma http local fixe
            try:
                statuses.append(urllib.request.urlopen(req, timeout=10).status)  # noqa: S310 -- idem
            except urllib.error.HTTPError as exc:
                statuses.append(exc.code)
        time.sleep(0.3)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=15)
    return out, statuses


WSGI_OK = (
    "def app(environ, start_response):\n    start_response('200 OK', [('Content-Length', '2')])\n    return [b'ok']\n"
)
PATHS = [f"/api/v1/forms/{TOK}/", f"/api/v1/resume-forms/{TOK}/?x=1", f"/api/v1/hooks/resume/{TOK}/", f"/f/{TOK}"]
LOGGER = ["--logger-class", "core.gunicorn_logging.RedactingLogger"]


def test_gunicorn_success_path_without_the_class_shows_the_token_and_with_it_does_not(tmp_path):
    (tmp_path / "app.py").write_text(WSGI_OK)
    plain, st1 = _run_gunicorn(["app:app"], PATHS, tmp_path, {})
    assert st1 == [200] * 4 and TOK in plain  # the check is meaningful: the default format does leak
    masked, st2 = _run_gunicorn(["app:app", *LOGGER], PATHS, tmp_path, {})
    assert st2 == [200] * 4  # same behaviour for the request
    assert TOK not in masked
    assert (
        "GET /api/v1/forms/[jeton]/ HTTP/1.1" in masked and " 200 " in masked and "/f/[jeton]" in masked
    )  # route, method, status, referer kept


@pytest.mark.parametrize("debug", ["0", "1"])
def test_gunicorn_with_the_real_application_error_paths_never_log_the_token(tmp_path, debug):
    env = {
        "ORCH_SECRET_KEY": "synthetique-sans-valeur-reelle",
        "ORCH_DEBUG": debug,
        "ORCH_DB_HOST": "127.0.0.1",
        "ORCH_DB_PORT": "1",  # nothing listens: the form routes fail with a database error (500)
        "ORCH_DB_USER": "x",
        "ORCH_DB_PASSWORD": "x",
        "ORCH_DB_NAME": "x",
        "ORCH_ALLOWED_HOSTS": "127.0.0.1",
    }
    out, statuses = _run_gunicorn(["fluxonata.wsgi:application", *LOGGER], PATHS[:3], tmp_path, env)
    assert 405 in statuses and 500 in statuses, statuses
    assert TOK not in out
    assert re.search(r'"GET /api/v1/hooks/resume/\[jeton\]/ HTTP/1\.1" 405', out)


# --- cohérence des fichiers livrés ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
def test_nginx_configs_contain_the_patterns_generated_from_the_python_side_and_write_the_masked_log(conf):
    text = (ROOT / "infra" / conf).read_text()
    for name, pattern in urlredact.nginx_patterns().items():
        assert f"    {pattern} " in text, f"{conf}: motif {name} absent ou différent de nginx_patterns()"
    assert "$fluxonata_log_uri" in text and "$fluxonata_log_referer" in text
    assert "access_log /var/log/nginx/access.log fluxonata;" in text
    assert "$request " not in text.split("log_format fluxonata")[1].split(";")[0]  # never the raw request line


def _nginx_map(text: str, header: str, value: str) -> str:
    """Ce que le `map` nginx `header` de `text` écrirait pour `value` (expressions `~*` émulées avec `re`, première qui correspond)."""
    block = text.split(header + " {")[1].split("\n}")[0]
    for pattern, replacement in re.findall(r'^\s+~\*(\^.*\$) "(\$\{.*\})";$', block, flags=re.M):
        m = re.search(pattern.replace("(?<", "(?P<"), value, flags=re.IGNORECASE)
        if m:
            return re.sub(
                r"\$\{(\w+)\}",
                lambda g: m.group(g.group(1)) if g.group(1) in m.groupdict() else "[jeton]",
                replacement.strip('"'),
            )
    return value


def _nginx_log_uri(text: str, uri: str) -> str:
    return _nginx_map(text, "map $request_uri $fluxonata_log_uri", uri)


def _nginx_log_referer(text: str, referer: str) -> str:
    return _nginx_map(text, "map $http_referer $fluxonata_log_referer", referer)


ENCODED_URIS = [
    "/api/v1/forms/TOK123/",
    "/api/v1/forms/TOK123/lookup/champ/?x=1",
    "/api/v1/resume-forms/TOK123/",
    "/api/v1/hooks/resume/TOK123/",
    "/api/v1/%66orms/TOK123/",
    "/api/v1%2fforms/TOK123/",
    "//api/v1/forms/TOK123/",
    "/api/v1/%46ORMS/TOK123/",
    "/api/v1/hooks/%72esume/TOK123/",
    "/api/v1/%72esume%2Dforms/TOK123/",
    "/f/TOK123",
    "/%66/TOK123",
    "/%52/TOK123",
    "/api/v1/workspaces/w1/workflows/?page=2",
    "/api/v1/hooks/3f2c9a1e-0000-4000-8000-000000000000/",
    "/",
]


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
@pytest.mark.parametrize("uri", ENCODED_URIS)
def test_nginx_log_uri_never_keeps_the_token_and_agrees_with_the_python_masking(conf, uri):
    # Constaté sur un vrai nginx (2026-11-09) : `/api/v1/%66orms/<jeton>/` est acheminé comme la route littérale mais `$request_uri` reste brut.
    written = _nginx_log_uri((ROOT / "infra" / conf).read_text(), uri)
    assert written == redact_url_tokens(uri)
    assert "TOK123" not in written  # aucune adresse de cette liste ne doit laisser le jeton dans le journal d'accès


REFERERS = [
    "http://h.example/f/TOK123",
    "https://h.example/r/TOK123?x=1",
    "https://h/api/v1/forms/TOK123/",
    "http://h/api/v1/%66orms/TOK123/",
    "HTTPS://h/%66/TOK123",
    "http://h/api%2fv1%2fresume-forms%2fTOK123",
    "http://h/API/V1/Hooks/Resume/TOK123/",
    "http://h.example/",
    "http://h/api/v1/hooks/3f2c9a1e-0000-4000-8000-000000000000/",
    "",
]


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
@pytest.mark.parametrize("referer", REFERERS)
def test_nginx_log_referer_never_keeps_the_token_and_agrees_with_the_python_masking(conf, referer):
    written = _nginx_log_referer((ROOT / "infra" / conf).read_text(), referer)
    assert written == redact_url_tokens(referer)
    assert "TOK123" not in written


@pytest.mark.parametrize("unit", ["/", "%2f", "/a", "/f/", "%2f%66", "/%66"])
def test_masking_stays_linear_on_long_runs_of_slashes(unit):
    # Régression constatée le 2026-11-09 : 8 000 barres coûtaient 1,5 s (temps quadratique), exploitable par un chemin ou un Referer sans authentification.
    started = time.perf_counter()
    redact_url_tokens(unit * 50_000)
    redact_url_tokens("http://" + "h" * 50_000 + "/" * 10_000)
    assert time.perf_counter() - started < 2.0


def test_the_json_formatter_masks_the_token_inside_a_stack_text():
    from core.logging import JsonFormatter

    record = logging.LogRecord("t", logging.WARNING, __file__, 1, "pile", None, None)
    record.stack_info = f"Stack (most recent call last):\n  File x, in handler\n    url = '/api/v1/forms/{TOK}/'"
    out = JsonFormatter().format(record)
    assert TOK not in out and "/api/v1/forms/[jeton]/" in out


@pytest.mark.parametrize(
    "text, expected",
    [
        ("GET /api/v1/%66orms/TOK1/ HTTP/1.1", "GET /api/v1/%66orms/[jeton]/ HTTP/1.1"),
        ("GET /api/v1%2Fforms/TOK1/", "GET /api/v1%2Fforms/[jeton]/"),
        ("GET //api/v1/forms/TOK1/", "GET //api/v1/forms/[jeton]/"),
        ("GET /API/V1/Resume-Forms/TOK1/", "GET /API/V1/Resume-Forms/[jeton]/"),
        ("GET /%46/TOK1 ", "GET /%46/[jeton] "),
        ("path=/f/TOK1", "path=/f/[jeton]"),
        ("HTTPS://h/f/TOK1", "HTTPS://h/f/[jeton]"),
        ("http://f/x", "http://f/x"),
        ("GET ///////api%2fv1%2fforms/TOK1", "GET ///////api%2fv1%2fforms/[jeton]"),
        ("ref:/r/TOK1", "ref:/r/[jeton]"),
        ("a,/f/TOK1", "a,/f/[jeton]"),
        ("{'url':/f/TOK1}", "{'url':/f/[jeton]"),
        (
            "GET /api/v1/hooks/3f2c9a1e-0000-4000-8000-000000000000/",
            "GET /api/v1/hooks/3f2c9a1e-0000-4000-8000-000000000000/",
        ),
        ("/files/f/readme", "/files/f/readme"),
    ],
)
def test_routes_written_with_percent_encoding_doubled_slashes_or_another_case_are_masked(text, expected):
    assert redact_url_tokens(text) == expected


def test_the_json_formatter_masks_the_token_inside_an_exception_trace():
    from core.logging import JsonFormatter

    logger = logging.getLogger("test_url_token_logs.exc")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise RuntimeError(f"échec sur /api/v1/forms/{TOK}/")
        except RuntimeError:
            logger.exception("erreur")
        logger.warning("pile", stack_info=True, extra={})
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert TOK not in out and "/api/v1/forms/[jeton]/" in out


def _nginx_location_for(text: str, path: str) -> str:
    """Corps de l'emplacement nginx qui sert `path` (expressions `~` d'abord, sinon le plus long préfixe ; aucun `^~` n'est utilisé)."""
    blocks = re.findall(r"^    location (~ )?(\S+) \{\n(.*?)^    \}", text, flags=re.S | re.M)
    assert "^~" not in text
    for regex, pattern, body in blocks:
        if regex and re.search(pattern, path):
            return body
    prefixes = [(pattern, body) for regex, pattern, body in blocks if not regex and path.startswith(pattern)]
    return max(prefixes, key=lambda pb: len(pb[0]))[1]


TOKEN_PATHS = [f"/{r}/SYNTH-TOK-123/" for r in urlredact.ROUTES] + [f"/{p}/SYNTH-TOK-123" for p in urlredact.PAGES]


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
@pytest.mark.parametrize("path", TOKEN_PATHS)
def test_nginx_limits_the_error_log_to_crit_on_every_token_route(conf, path):
    # Le journal d'erreurs de nginx écrit la ligne de requête brute et n'a pas de format : seul le niveau permet d'éviter le jeton.
    body = _nginx_location_for((ROOT / "infra" / conf).read_text(), path)
    assert re.search(r"^\s*error_log /var/log/nginx/error\.log crit;", body, flags=re.M), f"{conf}: {path}"


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workspaces/w1/workflows/",
        "/api/v1/runner/jobs/",
        "/api/v1/hooks/3f2c9a1e-0000-4000-8000-000000000000/",
        "/",
        "/assets/app.js",
    ],
)
def test_nginx_keeps_the_default_error_log_level_elsewhere(conf, path):
    body = _nginx_location_for((ROOT / "infra" / conf).read_text(), path)
    assert "error_log" not in body, f"{conf}: {path}"


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx.roles.conf"])
def test_nginx_masked_access_log_keeps_the_upstream_diagnostics(conf):
    fmt = (ROOT / "infra" / conf).read_text().split("log_format fluxonata")[1].split(";\n")[0]
    for field in ("$request_time", "$upstream_addr", "$upstream_status", "$upstream_response_time"):
        assert field in fmt
    assert "$request " not in fmt and "$request_uri" not in fmt  # le jeton ne revient pas par un autre champ


def test_every_gunicorn_command_shipped_with_an_access_log_uses_the_masking_class():
    seen = 0
    for path in [
        *(ROOT / "infra").rglob("*.yml"),
        *(ROOT / "infra").rglob("*.yaml"),
        ROOT / "infra" / "Dockerfile.backend",
    ]:
        for line in path.read_text().splitlines():
            if "gunicorn" in line and "--access-logfile" in line:
                seen += 1
                assert "core.gunicorn_logging.RedactingLogger" in line, f"{path}: {line[:80]}"
    assert seen >= 5


def test_the_development_server_logger_keeps_its_own_handler_so_its_messages_are_not_lost():
    server = logging.getLogger("django.server")
    assert server.handlers and server.propagate is False  # same shape as Django's default configuration
    assert any(isinstance(f, RedactUrlTokensFilter) for h in server.handlers for f in h.filters)


# --- rapports d'erreur envoyés à ADMINS (backend de messagerie en mémoire : aucun envoi réel) -----------------------------
def _admin_mail_report(settings, monkeypatch, *, include_html=False, **request):
    from django.core import mail

    settings.DEBUG = False
    settings.ADMINS = ["ops@example.invalid"]
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
    for handler in logging.getLogger("django").handlers:
        if hasattr(handler, "include_html"):
            monkeypatch.setattr(handler, "include_html", include_html)
    method = request.pop("method", "get")
    getattr(Client(raise_request_exception=False), method)(f"/api/v1/resume-forms/{TOK}/?x=1", **request)
    assert len(mail.outbox) == 1
    return mail.outbox[0]


def _all_parts(message) -> str:
    return "\n".join([message.subject, message.body, *(content for content, _ in getattr(message, "alternatives", []))])


@pytest.mark.parametrize("include_html", [False, True])
def test_the_error_report_for_admins_never_carries_the_token_anywhere_in_the_message(
    settings, monkeypatch, include_html
):
    message = _admin_mail_report(
        settings,
        monkeypatch,
        include_html=include_html,
        HTTP_REFERER=f"http://h.example/r/{TOK}",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
    )
    everything = _all_parts(message)
    assert TOK not in everything  # subject, text body and HTML body, "Request URL", PATH_INFO, HTTP_REFERER, titles
    assert include_html == bool(message.alternatives)
    # what helps diagnosing stays: route, query, exception type, method
    assert "/api/v1/resume-forms/[jeton]/" in message.subject
    assert "Request URL: http://testserver/api/v1/resume-forms/[jeton]/?x=1" in message.body
    assert "RuntimeError" in message.body and "Request Method: GET" in message.body
    assert "HTTP_REFERER = 'http://h.example/r/[jeton]'" in message.body


def test_the_error_report_keeps_post_field_names_but_not_their_values(settings, monkeypatch):
    message = _admin_mail_report(
        settings,
        monkeypatch,
        method="post",
        data={"champ": "SYNTH-FORM-VALUE-42", "password": "SYNTH-PW-1"},
        HTTP_AUTHORIZATION="Bearer orch_SYNTH-BEARER-77",
        HTTP_COOKIE="sessionid=SYNTH-SESSION-9",
    )
    body = _all_parts(message)
    for secret in ("SYNTH-FORM-VALUE-42", "SYNTH-PW-1", "SYNTH-BEARER-77", "SYNTH-SESSION-9", TOK):
        assert secret not in body, secret
    assert "champ" in body and "password" in body  # the names remain, for diagnosis


def test_a_json_body_never_reaches_the_error_report(settings, monkeypatch):
    message = _admin_mail_report(
        settings, monkeypatch, method="post", data='{"champ": "SYNTH-JSON-VALUE-43"}', content_type="application/json"
    )
    assert "SYNTH-JSON-VALUE-43" not in _all_parts(message)


@pytest.mark.parametrize(
    "html", ["<td>/f/{t}</td>", "href='/api/v1/forms/{t}/x'", "&#x27;/r/{t}&#x27;", 'a "/f/{t}" b']
)
def test_the_token_stops_at_html_and_quote_characters(html):
    out = redact_url_tokens(html.format(t=TOK))
    assert TOK not in out and "[jeton]" in out
    assert out.count("<") == html.count("<") and out.count(">") == html.count(">")  # the markup is intact
