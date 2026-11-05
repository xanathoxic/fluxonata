"""Export / import of packages. Import validates everything (no code is ever executed) and can run as a dry run."""

import hashlib
import json

from django.db import transaction
from rest_framework import serializers

from audit.models import record
from catalog.library import SECRET_LIKE, VersionInput
from catalog.models import Action, ActionVersion
from core.tags import clean_tags
from formbuilder import service as formservice
from formbuilder.models import Form, FormVersion
from runs.artifacts import get_store
from triggers import formengine
from workflows.models import Workflow, WorkflowVersion
from workflows.validation import validate_graph

from . import archive
from .models import Package, PackageVersion, TrustedKey


class ImportRefused(Exception):
    def __init__(self, report):
        self.report = report


def _action_payload(av: ActionVersion) -> dict:
    a = av.action
    return {
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "domain": a.domain,
        "tags": a.tags,
        "version": av.version,
        "runtime": av.runtime,
        "entrypoint": av.entrypoint,
        "input_schema": av.input_schema,
        "output_schema": av.output_schema,
        "license": av.license,
        "side_effects": av.side_effects,
        "idempotent": av.idempotent,
        "network_policy": av.network_policy,
        "limits": av.limits,
        "examples": av.examples,
        "secrets_required": av.secrets_required,
        "required_tags": av.required_tags,
        "changelog": av.changelog,
        "source": av.source,
    }


def _workflow_payload(wv: WorkflowVersion) -> dict:
    w = wv.workflow
    return {
        "slug": w.slug,
        "name": w.name,
        "description": w.description,
        "version": wv.version,
        "graph": wv.graph,
        "input_schema": wv.input_schema,
        "changelog": wv.changelog,
    }


def _form_payload(fv: FormVersion) -> dict:
    """What travels: one published version of a library form. Never an address, a limit or an owner."""
    f = fv.form
    return {
        "slug": f.slug,
        "version": fv.version,
        "name": f.name,
        "description": f.description,
        "tags": f.tags,
        "changelog": fv.changelog,
        "definition": fv.definition,
    }


@transaction.atomic
def export_package(
    workspace,
    user,
    *,
    meta: dict,
    actions: list[dict],
    workflows: list[dict],
    sign: bool = True,
    forms: list[dict] | None = None,
) -> PackageVersion:
    action_payloads, workflow_payloads, form_payloads = [], [], []
    for ref in forms or []:
        fv = (
            FormVersion.objects.filter(form__workspace=workspace, form__slug=ref["slug"], version=ref["version"])
            .select_related("form")
            .first()
        )
        if fv is None:
            raise serializers.ValidationError({"forms": f"Formulaire introuvable : {ref['slug']}@{ref['version']}"})
        form_payloads.append(_form_payload(fv))
    for ref in actions:
        av = (
            ActionVersion.objects.filter(action__workspace=workspace, action__slug=ref["slug"], version=ref["version"])
            .select_related("action")
            .first()
        )
        if av is None:
            raise serializers.ValidationError({"actions": f"Action introuvable : {ref['slug']}@{ref['version']}"})
        action_payloads.append(_action_payload(av))
    for ref in workflows:
        wv = (
            WorkflowVersion.objects.filter(
                workflow__workspace=workspace, workflow__slug=ref["slug"], version=ref["version"]
            )
            .select_related("workflow")
            .first()
        )
        if wv is None:
            raise serializers.ValidationError({"workflows": f"Workflow introuvable : {ref['slug']}@{ref['version']}"})
        workflow_payloads.append(_workflow_payload(wv))
    if not action_payloads and not workflow_payloads and not form_payloads:
        raise serializers.ValidationError(
            {"actions": "Sélectionnez au moins une action, un workflow ou un formulaire."}
        )
    if (
        Package.objects.filter(workspace=workspace, slug=meta["slug"]).exists()
        and PackageVersion.objects.filter(
            package__workspace=workspace, package__slug=meta["slug"], version=meta["version"]
        ).exists()
    ):
        raise serializers.ValidationError({"version": "Cette version du paquet existe déjà."})
    data, manifest = archive.build(
        meta,
        action_payloads,
        workflow_payloads,
        readme=f"# {meta['name']}\n\n{meta['description']}\n",
        sign=sign,
        forms=form_payloads,
    )
    # defence in depth: never ship something that looks like a credential
    for name, content in archive.read(data)[1].items():
        if SECRET_LIKE.search(content.decode(errors="ignore")):
            raise serializers.ValidationError({"actions": f"Un secret semble présent dans {name} : export refusé."})
    return _store(workspace, user, meta, manifest, data, origin="export")


def _store(workspace, user, meta, manifest, data, origin) -> PackageVersion:
    pkg, _ = Package.objects.get_or_create(
        workspace=workspace, slug=meta["slug"], defaults={"name": meta["name"], "description": meta["description"]}
    )
    key = f"{workspace.id}/packages/{meta['slug']}/{meta['version']}-{hashlib.sha256(data).hexdigest()[:12]}.zip"
    get_store().put(key, data)
    pv = PackageVersion.objects.create(
        package=pkg,
        version=meta["version"],
        manifest=manifest,
        checksum=archive.sha(data),
        archive_key=key,
        origin=origin,
        created_by=user,
    )
    record(
        f"package.{origin}",
        actor=user,
        workspace=workspace,
        target=pv,
        package=meta["slug"],
        version=meta["version"],
        checksum=pv.checksum,
    )
    return pv


def _item(kind, slug, version, status, *messages):
    return {"kind": kind, "slug": slug, "version": version, "status": status, "messages": list(messages)}


def import_package(workspace, user, data: bytes, *, apply: bool, strategy: str = "abort") -> dict:
    """Validate (and optionally apply) an archive. Always returns a report; raises ImportRefused only inside
    the transaction machinery to roll back."""
    report = {"package": None, "items": [], "errors": [], "applied": False}
    try:
        manifest, files = archive.read(data)
    except archive.PackageError as exc:
        report["errors"].append(str(exc))
        report["can_apply"] = False
        return report
    report["package"] = {k: manifest[k] for k in ("name", "slug", "version", "license", "description")}
    trusted = {k.public_key: k.name for k in TrustedKey.objects.filter(workspace=workspace)}
    report["signature"] = archive.signature_status(manifest, trusted)
    from tenancy.governance import current_policy

    if current_policy(workspace)[1].get("require_signed_packages") and report["signature"]["status"] != "trusted":
        report["errors"].append(
            "La politique de l'espace exige un paquet signé par un éditeur de confiance "
            f"(signature : {report['signature']['status']})."
        )
    for name, content in files.items():
        if SECRET_LIKE.search(content.decode(errors="ignore")):
            report["errors"].append(f"Secret probable détecté dans {name} : import refusé.")
    if report["errors"]:
        report["can_apply"] = False
        return report

    try:
        with transaction.atomic():
            _process(workspace, user, manifest, files, report, strategy)
            blocking = bool(report["errors"]) or (
                strategy != "skip" and any(i["status"] in ("conflict", "invalid") for i in report["items"])
            )
            report["can_apply"] = not blocking
            if apply and not blocking:
                if PackageVersion.objects.filter(
                    package__workspace=workspace, package__slug=manifest["slug"], version=manifest["version"]
                ).exists():
                    report["errors"].append("Cette version du paquet a déjà été importée.")
                    report["can_apply"] = False
                    raise ImportRefused(report)
                _store(workspace, user, manifest, manifest, data, origin="import")
                report["applied"] = True
            else:
                transaction.set_rollback(True)  # preview, or refused: nothing persists
    except ImportRefused:
        pass
    return report


def _process(workspace, user, manifest, files, report, strategy):  # noqa: C901
    contents = manifest["contents"]
    created_actions: dict[tuple, bool] = {}
    for ref in contents.get("actions", []):
        slug, version = ref["slug"], ref["version"]
        try:
            meta = json.loads(files[ref["path"]])
            source = files[ref["source"]].decode()
        except (KeyError, ValueError, UnicodeDecodeError):
            report["items"].append(_item("action", slug, version, "invalid", "Fichier d'action illisible."))
            continue
        if meta.get("slug") != slug or meta.get("version") != version:
            report["items"].append(
                _item("action", slug, version, "invalid", "Le manifeste et l'action ne concordent pas.")
            )
            continue
        ser = VersionInput(
            data={**{k: meta[k] for k in meta if k in VersionInput().fields}, "source": source, "version": version},
            partial=False,
        )
        if not ser.is_valid():
            report["items"].append(
                _item("action", slug, version, "invalid", json.dumps(ser.errors, ensure_ascii=False)[:300])
            )
            continue
        d = ser.validated_data
        existing = ActionVersion.objects.filter(action__workspace=workspace, action__slug=slug, version=version).first()
        digest = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
        if existing:
            same = (
                existing.source_digest == digest
                and existing.input_schema == d["input_schema"]
                and existing.output_schema == d["output_schema"]
            )
            report["items"].append(
                _item(
                    "action",
                    slug,
                    version,
                    "identical" if same else "conflict",
                    *(
                        []
                        if same
                        else [
                            "Une version publiée différente existe déjà : une version publiée est immuable, incrémentez le numéro de version."
                        ]
                    ),
                )
            )
            continue
        action = Action.objects.filter(workspace=workspace, slug=slug).first() or Action.objects.create(
            workspace=workspace,
            slug=slug,
            name=str(meta.get("name", slug))[:160],
            description=str(meta.get("description", "")),
            domain=str(meta.get("domain", ""))[:80],
            tags=list(meta.get("tags", []))[:10],
            owner=user,
        )
        fields = {
            k: d[k]
            for k in (
                "runtime",
                "entrypoint",
                "source",
                "input_schema",
                "output_schema",
                "license",
                "side_effects",
                "idempotent",
                "network_policy",
                "limits",
                "examples",
                "secrets_required",
                "required_tags",  # validated, defaults to [] for packages exported before runners existed
                "changelog",
            )
        }
        ActionVersion.objects.create(action=action, version=version, created_by=user, **fields)
        created_actions[(slug, version)] = True
        report["items"].append(_item("action", slug, version, "new"))

    for ref in contents.get("workflows", []):
        slug, version = ref["slug"], ref["version"]
        try:
            w = json.loads(files[ref["path"]])
            graph = w["graph"]
        except (KeyError, ValueError):
            report["items"].append(_item("workflow", slug, version, "invalid", "Fichier de workflow illisible."))
            continue
        if w.get("slug") != slug or w.get("version") != version:
            report["items"].append(
                _item("workflow", slug, version, "invalid", "Le manifeste et le workflow ne concordent pas.")
            )
            continue
        digest = "sha256:" + hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
        existing = WorkflowVersion.objects.filter(
            workflow__workspace=workspace, workflow__slug=slug, version=version
        ).first()
        if existing:
            same = existing.digest == digest
            report["items"].append(
                _item(
                    "workflow",
                    slug,
                    version,
                    "identical" if same else "conflict",
                    *([] if same else ["Une version publiée différente existe déjà."]),
                )
            )
            continue
        unpinned = [
            n["id"]
            for n in graph.get("nodes", [])
            if n.get("type") in ("action", "subworkflow", "foreach") and not n.get("version")
        ]
        if unpinned:
            report["items"].append(
                _item("workflow", slug, version, "invalid", f"Étapes sans version exacte : {unpinned[:3]}")
            )
            continue
        # sub-workflows must exist already or come earlier in this package
        issues = [i for i in validate_graph(graph, workspace, slug) if i["level"] == "error"]
        if issues:
            msgs = [i["message"] for i in issues[:3]]
            missing = [i for i in issues if i["code"] in ("unknown_action", "version_unavailable", "unknown_workflow")]
            report["items"].append(
                _item(
                    "workflow",
                    slug,
                    version,
                    "invalid",
                    *(["Dépendance manquante dans l'espace : "] if missing else []),
                    *msgs,
                )
            )
            continue
        wf = Workflow.objects.filter(workspace=workspace, slug=slug).first() or Workflow.objects.create(
            workspace=workspace,
            slug=slug,
            name=str(w.get("name", slug))[:160],
            description=str(w.get("description", "")),
            owner=user,
            draft_graph=graph,
        )
        WorkflowVersion.objects.create(
            workflow=wf,
            version=version,
            graph=graph,
            input_schema=w.get("input_schema") or graph.get("inputs", {}),
            changelog=str(w.get("changelog", ""))[:300],
            created_by=user,
        )
        report["items"].append(_item("workflow", slug, version, "new"))

    for ref in contents.get("forms", []):
        _process_form(workspace, user, ref, files, report)


def _process_form(workspace, user, ref, files, report):
    slug, version = ref["slug"], ref["version"]
    try:
        f = json.loads(files[ref["path"]])
        definition, name = f["definition"], str(f["name"])[:160]
    except (KeyError, ValueError):
        report["items"].append(_item("form", slug, version, "invalid", "Fichier de formulaire illisible."))
        return
    if f.get("slug") != slug or f.get("version") != version or not name:
        report["items"].append(
            _item("form", slug, version, "invalid", "Le manifeste et le formulaire ne concordent pas.")
        )
        return
    problems = formengine.check_definition(definition)
    if problems:
        shown = (f"{k} : {v}" for k, v in list(problems.items())[:3])
        report["items"].append(_item("form", slug, version, "invalid", *shown))
        return
    existing = FormVersion.objects.filter(form__workspace=workspace, form__slug=slug, version=version).first()
    if existing:
        same = existing.digest == formservice.digest(definition)
        report["items"].append(
            _item(
                "form",
                slug,
                version,
                "identical" if same else "conflict",
                *([] if same else ["Une version publiée différente existe déjà : une version publiée est immuable."]),
            )
        )
        return
    form = Form.objects.filter(workspace=workspace, slug=slug).first() or Form.objects.create(
        workspace=workspace,
        slug=slug,
        name=name,
        description=str(f.get("description", ""))[:2000],
        tags=clean_tags(f.get("tags") or []),
        owner=user,
        draft_definition=definition,
    )
    FormVersion.objects.create(
        form=form,
        version=version,
        definition=definition,
        digest=formservice.digest(definition),
        changelog=str(f.get("changelog", ""))[:300],
        created_by=user,
    )
    notes = sorted(
        {
            f"Connexion « {fl['source']['connection']} » à créer ou vérifier dans cet espace."
            for fl in formengine.iter_fields(definition)
            if fl.get("source")
        }
    )
    report["items"].append(_item("form", slug, version, "new", *notes))
