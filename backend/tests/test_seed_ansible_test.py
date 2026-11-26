"""Le workflow de test Ansible installé par `seed_ansible_test` : valide, idempotent, retirable, et réellement jouable dans le bac à sable (connexion locale)."""

import pytest
from django.core.management import call_command

from catalog.management.commands import seed_ansible_test as seed
from catalog.models import Action, ActionVersion, Inventory
from tests.test_ansible_runtime import needs_ansible_image
from workflows.models import Workflow
from workflows.validation import validate


@pytest.fixture
def installed(world):
    call_command("seed_ansible_test", workspace="team-a", user="dana")
    return world


def test_it_installs_an_inventory_an_action_and_a_valid_workflow(installed):
    ws = installed["ws_a"]
    inv = Inventory.objects.get(workspace=ws, slug=seed.INVENTORY)
    assert inv.hosts.count() == 2 and inv.hosts.filter(enabled=False).count() == 1  # une machine éteinte : jamais jouée
    v = ActionVersion.objects.get(action__workspace=ws, action__slug=seed.ACTION)
    assert v.runtime == "ansible" and v.required_tags == ["ansible"] and v.network_policy == "none"
    wf = Workflow.objects.get(workspace=ws, slug=seed.WORKFLOW)
    assert wf.versions.count() == 1
    assert [i for i in validate(seed.graph(), ws, seed.WORKFLOW) if i["level"] == "error"] == []


def test_the_published_action_passes_the_catalog_rules(installed):
    from catalog.library import VersionInput

    v = ActionVersion.objects.get(action__slug=seed.ACTION)
    ser = VersionInput(
        data={
            "runtime": v.runtime,
            "source": v.source,
            "input_schema": v.input_schema,
            "output_schema": v.output_schema,
            "required_tags": v.required_tags,
            "network_policy": v.network_policy,
            "side_effects": v.side_effects,
        }
    )
    assert ser.is_valid(), ser.errors


def test_running_it_twice_changes_nothing_and_purge_removes_it(installed):
    call_command("seed_ansible_test", workspace="team-a", user="dana")
    ws = installed["ws_a"]
    assert Action.objects.filter(workspace=ws, slug=seed.ACTION).count() == 1
    assert Workflow.objects.get(workspace=ws, slug=seed.WORKFLOW).versions.count() == 1
    call_command("seed_ansible_test", workspace="team-a", purge=True)
    assert not Workflow.objects.filter(slug=seed.WORKFLOW).exists()
    assert (
        not Inventory.objects.filter(slug=seed.INVENTORY).exists()
        and not Action.objects.filter(slug=seed.ACTION).exists()
    )


def test_unknown_workspace_or_user_is_refused(world):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("seed_ansible_test", workspace="nulle-part")
    with pytest.raises(CommandError):
        call_command("seed_ansible_test", workspace="team-a", user="inconnu")


@needs_ansible_image
def test_the_action_really_runs_on_the_local_inventory(installed, settings):
    """L'essai à blanc annonce un changement sans rien écrire ; l'application écrit le fichier et publie le compte."""
    from runs import services
    from runs.runners import DockerRunner
    from runs.worker import process_one
    from tenancy.models import Environment

    settings.ORCH_WORKER_TAGS = ["ansible"]
    ws = installed["ws_a"]
    v = ActionVersion.objects.get(action__workspace=ws, action__slug=seed.ACTION)

    def play(check):
        ex, _ = services.create_execution(
            workspace=ws,
            environment=Environment.objects.get(workspace=ws, kind="test"),
            action_version=v,
            user=installed["designer"],
            inputs={"inventory_ref": seed.INVENTORY, "check": check, "extra_vars": {"message": "bonjour"}},
        )
        process_one("w", DockerRunner())
        ex.refresh_from_db()
        return ex

    dry = play(True)
    assert dry.status == "succeeded", dry.error
    assert dry.result["hosts"] == 1 and dry.result["check"] is True  # la machine éteinte n'est pas jouée
    assert any(t["task"] == "Poser le fichier témoin" and t["changed"] for t in dry.result["tasks"])
    real = play(False)
    assert real.status == "succeeded", real.error
    assert real.result["data"] == {"fichier": "/tmp/fluxonata-test-ansible.txt", "compte": "nobody"}
    assert [t["msg"] for t in real.result["tasks"] if t["task"] == "Annoncer le message"] == [
        "bonjour (compte nobody, machine serveur-test)"
    ]
