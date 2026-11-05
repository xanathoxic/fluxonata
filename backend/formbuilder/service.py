"""Library forms: versioning, references (a form *version* is what gets attached) and usage."""

import copy
import hashlib
import json

from django.db import transaction
from rest_framework import serializers

from audit.models import record

from .models import Form, FormVersion


class FormRefError(ValueError):
    """A reference to a form (or a version of it) that does not exist or cannot be used."""


def digest(definition: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _parse(v: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in v.split("."))
    return a, b, c


def next_bump(current: str | None, bump: str = "minor") -> str:
    """The version after `current` (None: the first one)."""
    if current is None:
        return "1.0.0"
    major, minor, patch = _parse(current)
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[
        bump
    ]


def next_version(form: Form, bump: str = "minor") -> str:
    latest = form.latest_version()
    return next_bump(latest.version if latest else None, bump)


@transaction.atomic
def publish(form: Form, user, *, changelog: str = "", bump: str = "minor") -> FormVersion:
    from triggers import formengine

    form = Form.objects.select_for_update().get(pk=form.pk)
    problems = formengine.check_definition(form.draft_definition)
    if problems:
        raise serializers.ValidationError({"definition": problems})
    fields = list(formengine.iter_fields(form.draft_definition))
    if not fields:
        raise serializers.ValidationError({"definition": {"_": "Ajoutez au moins un champ avant de publier."}})
    version = FormVersion.objects.create(
        form=form,
        version=next_version(form, bump),
        definition=copy.deepcopy(form.draft_definition),
        digest=digest(form.draft_definition),
        changelog=changelog[:300],
        created_by=user,
    )
    record("form.publish", actor=user, workspace=form.workspace, target=form, version=version.version)
    return version


def resolve(workspace, ref) -> tuple[Form, FormVersion]:
    """The exact version a reference names (the latest published one when no version is given)."""
    if not isinstance(ref, dict) or not isinstance(ref.get("form"), str):
        raise FormRefError("Référence de formulaire invalide.")
    form = Form.objects.filter(workspace=workspace, slug=ref["form"]).first()
    if form is None:
        raise FormRefError(f"Formulaire « {ref['form']} » introuvable.")
    if form.is_template:
        raise FormRefError(f"« {form.name} » est un modèle : copiez-le en formulaire pour le rattacher.")
    wanted = ref.get("version")
    version = form.versions.filter(version=wanted).first() if wanted else form.latest_version()
    if version is None:
        raise FormRefError(
            f"Le formulaire « {form.name} » n'a pas de version publiée."
            if not wanted
            else f"Version {wanted} du formulaire « {form.name} » introuvable."
        )
    return form, version


def pin_input_form(workspace, input_form: dict | None) -> dict | None:
    """The `input_form` of a graph being published. A reference becomes the exact version AND a copy of its definition,
    so everything that reads a published workflow keeps reading plain definitions; an inline definition stays as it is."""
    if not input_form or "ref" not in input_form:
        return input_form
    form, version = resolve(workspace, input_form["ref"])
    mapping = input_form.get("map")
    return {
        **copy.deepcopy(version.definition),
        "ref": {"form": form.slug, "version": version.version},
        **({"map": copy.deepcopy(mapping)} if mapping else {}),
    }


def draft_definition_of(workspace, input_form: dict | None) -> dict | None:
    """The definition a draft graph stands for (a bare reference is resolved), None when there is none or it is broken."""
    if not input_form:
        return None
    if "ref" in input_form and "sections" not in input_form:
        try:
            return resolve(workspace, input_form["ref"])[1].definition
        except FormRefError:
            return None
    return input_form


def usage(form: Form) -> dict:
    """Where the form is attached: workflows (draft or published versions), public forms."""
    from triggers.models import PublicForm
    from workflows.models import Workflow, WorkflowVersion

    ws = form.workspace
    published: dict[str, set[str]] = {}
    for slug, version in WorkflowVersion.objects.filter(
        workflow__workspace=ws, graph__input_form__ref__form=form.slug
    ).values_list("workflow__slug", "version"):
        published.setdefault(slug, set()).add(version)
    drafts = set(
        Workflow.objects.filter(workspace=ws, draft_graph__input_form__ref__form=form.slug).values_list(
            "slug", flat=True
        )
    )
    workflows = [
        {"slug": s, "versions": sorted(published.get(s, ()), key=_parse), "in_draft": s in drafts}
        for s in sorted(set(published) | drafts)
    ]
    public = [
        {
            "id": p.pk,
            "name": p.name,
            "workflow": p.workflow.slug,
            "version": p.library_version.version if p.library_version else None,
        }
        for p in PublicForm.objects.filter(workspace=ws, library_version__form=form).select_related(
            "workflow", "library_version"
        )
    ]
    return {"workflows": workflows, "public_forms": public}
