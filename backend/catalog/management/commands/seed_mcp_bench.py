"""Bibliothèque d'essai pour juger la DÉCISION d'un assistant (MCP de conception, ADR 0025) : réutiliser une action existante ou en créer une.

Installe, dans un espace, cinq petites actions actives et trois workflows publiés qui les utilisent à des degrés divers :
  normaliser-texte (3 workflows) · valider-email (2) · calculer-tva (1) · valider-iban (0, existe mais jamais utilisée) · generer-reference (0).
Tout est étiqueté `banc-mcp` ; `--purge` retire tout ce que la commande a installé. Ne touche à rien d'autre. Idempotent."""

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Action
from catalog.seed_helpers import layout, publish_workflow
from tenancy.models import Membership, Role, Workspace
from workflows.models import Workflow

TAG = "banc-mcp"
FOLDER = "Banc MCP"


def prop(type_, title, description):
    return {"type": type_, "title": title, "description": description}


def schema(required, **props):
    return {"type": "object", "required": required, "properties": props}


ACTIONS = [
    {
        "slug": "normaliser-texte",
        "name": "Normaliser un texte",
        "description": "Retire les espaces superflus d'un texte et met une majuscule à chaque mot.",
        "source": "def run(inputs):\n    return {'texte': ' '.join(inputs['texte'].split()).title()}",
        "in": schema(["texte"], texte=prop("string", "Texte", "Le texte à normaliser.")),
        "out": schema(
            ["texte"], texte=prop("string", "Texte normalisé", "Le texte sans espaces superflus, un mot par majuscule.")
        ),
    },
    {
        "slug": "valider-email",
        "name": "Valider une adresse e-mail",
        "description": "Vérifie qu'une adresse e-mail a une forme valide et renvoie son domaine.",
        "source": "def run(inputs):\n    e = inputs['email'].strip()\n    ok = e.count('@') == 1 and '.' in e.split('@')[1] and ' ' not in e\n    return {'valide': ok, 'domaine': e.split('@')[1] if ok else ''}",
        "in": schema(["email"], email=prop("string", "Adresse e-mail", "L'adresse e-mail à contrôler.")),
        "out": schema(
            ["valide", "domaine"],
            valide=prop("boolean", "Valide", "Vrai si l'adresse a une forme valide."),
            domaine=prop("string", "Domaine", "Le domaine de l'adresse, vide si elle est invalide."),
        ),
    },
    {
        "slug": "calculer-tva",
        "name": "Calculer la TVA",
        "description": "Calcule le montant de TVA et le total toutes taxes comprises à partir d'un montant hors taxe.",
        "source": "def run(inputs):\n    ht = float(inputs['montant'])\n    taux = float(inputs.get('taux', 0.2))\n    return {'tva': round(ht * taux, 2), 'total': round(ht * (1 + taux), 2)}",
        "in": schema(
            ["montant"],
            montant=prop("number", "Montant HT", "Le montant hors taxe, en euros."),
            taux=prop("number", "Taux", "Le taux de TVA, par exemple 0.2 pour 20 %."),
        ),
        "out": schema(
            ["tva", "total"],
            tva=prop("number", "TVA", "Le montant de la TVA, arrondi au centime."),
            total=prop("number", "Total TTC", "Le total toutes taxes comprises."),
        ),
    },
    {
        "slug": "valider-iban",
        "name": "Valider un IBAN",
        "description": "Contrôle la clé d'un IBAN (modulo 97) sans consulter aucun service externe.",
        "source": "def run(inputs):\n    s = inputs['iban'].replace(' ', '').upper()\n    if len(s) < 15 or not s.isalnum():\n        return {'valide': False}\n    n = int(''.join(str(int(c, 36)) for c in s[4:] + s[:4]))\n    return {'valide': n % 97 == 1}",
        "in": schema(["iban"], iban=prop("string", "IBAN", "L'IBAN à contrôler, avec ou sans espaces.")),
        "out": schema(["valide"], valide=prop("boolean", "Valide", "Vrai si la clé de l'IBAN est correcte.")),
    },
    {
        "slug": "generer-reference",
        "name": "Générer une référence",
        "description": "Fabrique une référence lisible à partir d'un préfixe et d'un numéro, par exemple NDF-000042.",
        "source": "def run(inputs):\n    return {'reference': f\"{inputs['prefixe'].upper()}-{int(inputs['numero']):06d}\"}",
        "in": schema(
            ["prefixe", "numero"],
            prefixe=prop("string", "Préfixe", "Le préfixe de la référence."),
            numero=prop("integer", "Numéro", "Le numéro de séquence."),
        ),
        "out": schema(
            ["reference"], reference=prop("string", "Référence", "La référence formatée, par exemple NDF-000042.")
        ),
    },
]


def _wf(uses):
    """Un petit workflow en chaîne qui appelle `uses` (liste de slugs d'actions) ; les entrées sont les premières clés de chaque contrat."""
    spec = {a["slug"]: a for a in ACTIONS}
    props, nodes, edges, prev = {}, [], [], None
    for i, slug in enumerate(uses):
        a = spec[slug]
        inputs = {}
        for key, p in a["in"]["properties"].items():
            name = f"{slug.replace('-', '_')}_{key}"
            props[name] = {"type": p["type"]}
            inputs[key] = "{{ inputs." + name + " }}"
        nodes.append(
            {"id": f"e{i}", "type": "action", "name": a["name"], "action": slug, "version": "1.0.0", "inputs": inputs}
        )
        if prev:
            edges.append({"from": prev, "to": f"e{i}"})
        prev = f"e{i}"
    layout(nodes, edges)
    return {
        "inputs": {"type": "object", "properties": props, "required": []},
        "nodes": nodes,
        "edges": edges,
        "outputs": {},
    }


WORKFLOWS = [
    ("banc-onboarding", "Banc — accueil d'un collaborateur", ["valider-email", "normaliser-texte"]),
    ("banc-note-de-frais", "Banc — note de frais", ["normaliser-texte", "calculer-tva"]),
    ("banc-contact-client", "Banc — fiche contact client", ["valider-email", "normaliser-texte"]),
]


class Command(BaseCommand):
    help = "Installe (ou retire avec --purge) une petite bibliothèque d'actions et de workflows pour juger la décision d'un assistant MCP."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True, help="identifiant (slug) de l'espace")
        parser.add_argument("--purge", action="store_true", help="retire ce que cette commande a installé")

    def handle(self, *args, workspace, purge, **options):
        ws = Workspace.objects.filter(slug=workspace).first()
        if ws is None:
            raise CommandError(f"Espace « {workspace} » introuvable (c'est l'identifiant, pas le nom affiché).")
        if purge:
            n_wf = Workflow.objects.filter(
                workspace=ws, slug__in=[s for s, _, _ in WORKFLOWS], tags__contains=[TAG]
            ).delete()[0]
            n_ac = Action.objects.filter(
                workspace=ws, slug__in=[a["slug"] for a in ACTIONS], tags__contains=[TAG]
            ).delete()[0]  # requête groupée : les versions publiées suivent
            self.stdout.write(f"retiré : {n_wf} objet(s) de workflow, {n_ac} objet(s) d'action")
            return
        owner = (
            Membership.objects.filter(workspace=ws, role__in=[Role.ADMIN, Role.SPACE_MANAGER, Role.DESIGNER])
            .select_related("user")
            .first()
        )
        if owner is None:
            raise CommandError("L'espace n'a aucun membre pouvant posséder ces objets.")
        from catalog.models import ActionVersion

        for a in ACTIONS:
            action, _ = Action.objects.get_or_create(
                workspace=ws,
                slug=a["slug"],
                defaults={
                    "name": a["name"],
                    "description": a["description"],
                    "folder": FOLDER,
                    "tags": [TAG],
                    "owner": owner.user,
                },
            )
            ActionVersion.objects.get_or_create(
                action=action,
                version="1.0.0",
                defaults={
                    "source": a["source"],
                    "input_schema": a["in"],
                    "output_schema": a["out"],
                    "limits": {"timeout_seconds": 20, "memory_mb": 64},
                    "created_by": owner.user,
                },
            )
        for slug, name, uses in WORKFLOWS:
            wf = publish_workflow(
                ws, owner.user, slug, name, FOLDER, "Workflow d'essai du banc MCP.", _wf(uses), changelog="Banc MCP"
            )
            if TAG not in wf.tags:
                wf.tags = [*wf.tags, TAG]
                wf.save(update_fields=["tags"])
        self.stdout.write(
            f"installé dans « {ws.slug} » : {len(ACTIONS)} actions, {len(WORKFLOWS)} workflows (étiquette « {TAG} », dossier « {FOLDER} »). Retrait : --purge."
        )
