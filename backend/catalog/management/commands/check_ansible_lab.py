"""Lance le workflow du laboratoire Ansible (seed_ansible_lab), valide l'accord demandé, attend la fin, puis dresse la liste de ce qui est validé :
d'une part les points d'Ansible jugés par le workflow lui-même (« Bilan »), d'autre part les points de la plate-forme qu'un résultat ne peut pas montrer
(le runner dédié a joué les playbooks, les rôles sont figés, la clé n'a fuité nulle part, l'inventaire n'est pas recopié dans l'exécution).

Il faut la pile de développement (orchestrateur, API) et le runner du laboratoire (scripts/ansible-lab.sh agent-up). Code de sortie 1 si un point échoue."""

import base64
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.management.commands import seed_ansible_lab as lab
from catalog.models import ActionVersion
from runs import engine
from runs.models import Approval, Attempt, Execution, WorkflowRun
from tenancy.models import Environment, Membership, Workspace
from workflows.models import WorkflowVersion

FINAL = ("succeeded", "failed", "cancelled")


def platform_points(run: WorkflowRun, key_path: Path) -> list[tuple[str, bool, str]]:
    """Les points que seule la plate-forme peut juger, sur une exécution terminée."""
    executions = list(
        Execution.objects.filter(step_run__run=run, action_version__action__slug__in=lab.ACTIONS).select_related(
            "runner", "action_version"
        )
    )
    private = key_path.read_text()
    secrets = {private.strip(), base64.b64encode(key_path.read_bytes()).decode()}
    secrets |= {line.strip() for line in private.splitlines() if len(line.strip()) >= 20}
    everywhere = (
        "".join(a.logs or "" for a in Attempt.objects.filter(execution__in=executions))
        + str([e.result for e in executions])
        + str(run.outputs)
    )
    leaked = sorted(s[:12] for s in secrets if s and s in everywhere)
    deployer = ActionVersion.objects.filter(action__slug="lab-deployer").order_by("-created_at").first()
    pinned = list(deployer.ansible_roles) if deployer else []
    approval = Approval.objects.filter(step_run__run=run).first()
    return [
        (
            "Runner dédié : tous les playbooks ont été joués par « lab-ansible »",
            bool(executions) and all(e.runner is not None and e.runner.name == lab.RUNNER for e in executions),
            f"{len(executions)} exécution(s), runners : {sorted({e.runner.name if e.runner else 'worker intégré' for e in executions})}",
        ),
        (
            "Rôles figés dans l'action (versions exactes, dépendances incluses)",
            {r["role"] for r in pinned} == {"common", "web", "db"} and all(r["version"] == "1.0.0" for r in pinned),
            f"lab-deployer : {pinned or '—'}",
        ),
        (
            "La clé SSH n'apparaît ni dans les journaux, ni dans les résultats, ni dans les sorties",
            not leaked,
            "aucune trace" if not leaked else f"fuite : {leaked}",
        ),
        (
            "L'inventaire n'est pas recopié dans l'exécution enregistrée (seule la référence l'est)",
            bool(executions)
            and all("inventory" not in (e.inputs or {}) and (e.inputs or {}).get("inventory_ref") for e in executions),
            "entrées enregistrées : inventory_ref seulement",
        ),
        (
            "Accord demandé puis donné par une autre personne",
            approval is not None and approval.status == "approved" and approval.decided_by is not None,
            f"{approval.status if approval else '—'} par {approval.decided_by if approval else '—'}",
        ),
    ]


class Command(BaseCommand):
    help = "Lance le workflow du laboratoire Ansible et vérifie chaque point."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", default="demo")
        parser.add_argument("--user", default="alice", help="la personne qui lance")
        parser.add_argument("--approver", default="claire", help="la personne qui valide l'accord")
        parser.add_argument("--message", default="Bonjour depuis le laboratoire")
        parser.add_argument("--timeout", type=int, default=900)
        parser.add_argument("--lab-dir", default="")

    def handle(self, *args, workspace, user, approver, message, timeout, lab_dir, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        if ws is None:
            raise CommandError(f"Espace inconnu : {workspace}")
        users = {m.user.username: m.user for m in Membership.objects.filter(workspace=ws).select_related("user")}
        for who in (user, approver):
            if who not in users:
                raise CommandError(f"« {who} » n'est pas membre de l'espace {workspace}.")
        version = (
            WorkflowVersion.objects.filter(workflow__workspace=ws, workflow__slug=lab.WORKFLOW)
            .order_by("-created_at")
            .first()
        )
        if version is None:
            raise CommandError("Workflow absent : lancez seed_ansible_lab.")
        directory = Path(lab_dir) if lab_dir else Path(__file__).resolve().parents[4] / "var" / "ansible-lab"
        run, _ = engine.start_run(
            workspace=ws,
            environment=Environment.objects.get(workspace=ws, kind="test"),
            workflow_version=version,
            user=users[user],
            inputs={"message": message},
        )
        self.stdout.write(f"Exécution {run.id} lancée ({lab.WORKFLOW} {version.version}).")
        run = self._wait(run, users[approver], timeout)
        if run.status != "succeeded":
            failed = [f"{s.node_id} : {s.status}" for s in run.steps.all() if s.status not in ("succeeded", "skipped")]
            raise CommandError(f"Le workflow n'a pas réussi (statut {run.status}) : {failed or run.error}")
        self._report(run, directory)

    def _wait(self, run, approver, timeout) -> WorkflowRun:
        """Attend la fin de l'exécution en affichant les changements d'état, et valide l'accord demandé."""
        deadline = time.monotonic() + timeout
        seen: dict[str, str] = {}
        while time.monotonic() < deadline:
            run = WorkflowRun.objects.get(pk=run.pk)
            for sr in run.steps.all():
                if seen.get(sr.node_id) != sr.status:
                    seen[sr.node_id] = sr.status
                    self.stdout.write(f"  {sr.node_id:14} {sr.status}")
            for approval in Approval.objects.filter(step_run__run=run, status="pending"):
                engine.decide_approval(approval.id, approver, True, "Validé par la commande de contrôle du laboratoire")
                self.stdout.write(f"  accord « {approval.title} » validé par {approver.username}")
            if run.status in FINAL:
                break
            time.sleep(2)
        return WorkflowRun.objects.get(pk=run.pk)

    def _report(self, run, directory: Path) -> None:
        points = [(p["nom"], p["ok"], p["detail"]) for p in (run.outputs or {}).get("points", [])]
        points += platform_points(run, directory / "id_lab")
        width = max(len(n) for n, _, _ in points)
        self.stdout.write("")
        for name, ok, detail in points:
            self.stdout.write(f"  {'✓' if ok else '✗'} {name:{width}}  {detail}")
        good = sum(1 for _, ok, _ in points if ok)
        self.stdout.write(f"\n{good} point(s) validé(s) sur {len(points)}.")
        if good != len(points):
            raise CommandError("Des points ne sont pas validés.")
