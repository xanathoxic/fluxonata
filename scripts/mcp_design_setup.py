"""Prépare (ou nettoie) l'espace jetable « mcp-essai » du banc MCP de conception — lancé par `scripts/mcp-design-smoke.sh` via `manage.py shell`.

Variables : MCP_ACTION=setup|check|cleanup ; setup écrit deux jetons (0600) dans MCP_TOKEN_DIR : `design` (view+edit) et `big` (view+edit+run+publish).
Aucun jeton n'est affiché. Le jeton expire dans 2 heures ; `cleanup` supprime l'espace, ses données et le compte de service."""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from tenancy.auth import new_token
from tenancy.models import ApiToken, Environment, Membership, Organization, Role, ServiceAccount, Workspace

SLUG, USER = "mcp-essai", "mcp-essai-assistant"
action = os.environ["MCP_ACTION"]
U = get_user_model()

if action == "setup":
    out = os.environ["MCP_TOKEN_DIR"]
    org, _ = Organization.objects.get_or_create(slug="org-mcp-essai", defaults={"name": "MCP essai"})
    ws, _ = Workspace.objects.get_or_create(slug=SLUG, defaults={"organization": org, "name": "MCP essai"})
    for kind in ("test", "prod"):
        Environment.objects.get_or_create(workspace=ws, kind=kind, defaults={"name": kind})
    user, _ = U.objects.get_or_create(username=USER)
    user.set_unusable_password()
    user.save()
    Membership.objects.get_or_create(user=user, workspace=ws, defaults={"role": Role.DESIGNER})
    sa, _ = ServiceAccount.objects.get_or_create(user=user, defaults={"workspace": ws, "name": "assistant-essai"})
    for name, scopes in (("design", ["view", "edit"]), ("big", ["view", "edit", "run", "publish"])):
        raw, prefix, digest = new_token()
        ApiToken.objects.create(
            workspace=ws, service_account=sa, name=f"essai-{name}", prefix=prefix, token_hash=digest, scopes=scopes,
            expires_at=timezone.now() + timedelta(hours=2),
        )
        fd = os.open(os.path.join(out, f"token-{name}"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, raw.encode())
        os.close(fd)
    print("espace mcp-essai prêt")

elif action == "check":  # ce que l'assistant ne devait PAS pouvoir faire
    from ai.models import AiSkill
    from catalog.models import Action
    from runs.models import Execution, WorkflowRun
    from workflows.models import WorkflowVersion

    ws = Workspace.objects.get(slug=SLUG)
    problems = []
    if WorkflowVersion.objects.filter(workflow__workspace=ws).exists():
        problems.append("une version de workflow a été publiée")
    if WorkflowRun.objects.filter(workspace=ws).exists() or Execution.objects.filter(workspace=ws).exists():
        problems.append("une exécution a été lancée")
    # les actions internes du moteur (« __script_python »…) naissent de toute exécution de script, y compris l'essai à blanc : ce ne sont pas celles de l'assistant
    if Action.objects.filter(workspace=ws, status="active").exclude(slug__startswith="__").exists() or AiSkill.objects.filter(workspace=ws, status="active").exists():
        problems.append("une action ou un skill est actif")
    if not Action.objects.filter(workspace=ws, status="proposed").exists():
        problems.append("la proposition d'action n'existe pas")
    print("VÉRIFIÉ : rien publié, rien lancé, rien activé" if not problems else "ÉCHEC : " + " ; ".join(problems))
    raise SystemExit(0 if not problems else 1)

elif action == "cleanup":
    ws = Workspace.objects.filter(slug=SLUG).first()
    if ws:
        ws.delete()
    U.objects.filter(username=USER).delete()
    Organization.objects.filter(slug="org-mcp-essai").delete()
    print("espace mcp-essai supprimé")
