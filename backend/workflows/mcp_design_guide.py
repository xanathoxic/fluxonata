"""Guide de conception du MCP (ADR 0025) : le FORMAT exact d'un graphe de workflow, d'un formulaire, d'une action et d'un skill.

Constaté avec Claude Code réel : sans ce guide, l'assistant devine ces formats d'après les messages d'erreur du serveur. Les exemples ci-dessous
sont VALIDÉS par les vrais contrôles dans `tests/test_mcp_design_guide.py` (ils ne peuvent pas dériver) ; les listes de types viennent du code."""

from triggers import formengine

from .validation import NODE_TYPES

WORKFLOW_EXAMPLE = {
    "inputs": {"type": "object", "required": ["texte"], "properties": {"texte": {"type": "string", "title": "Texte"}}},
    "nodes": [
        {
            "id": "compter",
            "type": "action",
            "name": "Compter les voyelles",
            "action": "<slug d'une action existante>",
            "version": "1.0.0",
            "inputs": {"texte": "{{ inputs.texte }}"},
        },
        {
            "id": "resumer",
            "type": "action",
            "name": "Mettre en forme",
            "action": "<slug d'une autre action>",
            "version": "1.0.0",
            "inputs": {"value": "{{ steps.compter.output.n }}"},
        },
    ],
    "edges": [{"from": "compter", "to": "resumer"}],
    "outputs": {"resultat": "{{ steps.resumer.output.value }}"},
}
FORM_EXAMPLE = {
    "version": 1,
    "sections": [
        {
            "id": "s1",
            "title": "Informations",
            "fields": [
                {"key": "texte", "type": "text", "label": "Texte à analyser", "required": True},
            ],
        }
    ],
}
ACTION_EXAMPLE = {
    "slug": "compter-voyelles",
    "name": "Compter les voyelles",
    "description": "Compte le nombre de voyelles d'un texte fourni en entrée.",
    "version": {
        "runtime": "python",
        "source": "def run(inputs):\n    return {'n': sum(c in 'aeiouy' for c in inputs['texte'].lower())}",
        "input_schema": {
            "type": "object",
            "required": ["texte"],
            "properties": {"texte": {"type": "string", "title": "Texte", "description": "Le texte à analyser."}},
        },
        "output_schema": {
            "type": "object",
            "required": ["n"],
            "properties": {"n": {"type": "integer", "title": "Nombre", "description": "Nombre de voyelles trouvées."}},
        },
        "side_effects": "none",
        "network_policy": "none",
    },
}
SKILL_EXAMPLE = {
    "slug": "resumer-texte",
    "name": "Résumer un texte",
    "description": "Résume un texte en trois phrases.",
    "version": {
        "instructions": "Résume en trois phrases : {{ inputs.texte }}",
        "input_schema": {"type": "object", "required": ["texte"], "properties": {"texte": {"type": "string"}}},
        "output_schema": {"type": "object", "required": ["resume"], "properties": {"resume": {"type": "string"}}},
    },
}


# Étapes autres que « action » (formats vérifiés par les contrôles du serveur : voir tests/test_mcp_design_guide.py)
BRANCHING_EXAMPLE = {
    "inputs": {
        "type": "object",
        "required": ["nom", "montant", "service"],
        "properties": {"nom": {"type": "string"}, "montant": {"type": "number"}, "service": {"type": "string"}},
    },
    "nodes": [
        {
            "id": "nettoyer",
            "type": "script",
            "name": "Normaliser le nom",
            "language": "python",
            "script": "propre = nom.strip().title()\n",
            "bindings": {"nom": "{{ inputs.nom }}"},
            "outputs": [{"name": "propre", "type": "string"}],
        },
        {"id": "gros", "type": "condition", "name": "Montant supérieur à 1000 ?", "expr": "inputs.montant > 1000"},
        {
            "id": "valider",
            "type": "approval",
            "name": "Validation",
            "title": "Valider la dépense de {{ steps.nettoyer.output.propre }}",
        },
        {"id": "reprise", "type": "join", "name": "Reprise"},
        {
            "id": "tri",
            "type": "switch",
            "name": "Selon le service",
            "cases": [{"id": "rh", "expr": "inputs.service == 'rh'"}],
        },
        {
            "id": "note_rh",
            "type": "script",
            "name": "Note RH",
            "language": "python",
            "script": "note = 'RH'\n",
            "bindings": {},
            "outputs": [{"name": "note", "type": "string"}],
        },
        {
            "id": "note_autre",
            "type": "script",
            "name": "Autre",
            "language": "python",
            "script": "note = 'autre'\n",
            "bindings": {},
            "outputs": [{"name": "note", "type": "string"}],
        },
        {"id": "fin", "type": "join", "name": "Fin"},
    ],
    "edges": [
        {"from": "nettoyer", "to": "gros"},
        {"from": "gros", "to": "valider", "label": "true"},
        {"from": "gros", "to": "reprise", "label": "false"},
        {"from": "valider", "to": "reprise"},
        {"from": "reprise", "to": "tri"},
        {"from": "tri", "to": "note_rh", "label": "rh"},
        {"from": "tri", "to": "note_autre", "label": "default"},
        {"from": "note_rh", "to": "fin"},
        {"from": "note_autre", "to": "fin"},
    ],
    "outputs": {"nom": "{{ steps.nettoyer.output.propre }}"},
}
OTHER_NODES = {
    "transform": {
        "id": "gros_montants",
        "type": "transform",
        "name": "Garder les gros montants",
        "op": "filter",
        "items": "inputs.lignes",
        "where": "item.montant >= 30",
    },
    "ai (skill actif)": {
        "id": "qualifier",
        "type": "ai",
        "name": "Qualifier",
        "skill": "<slug d'un skill actif>",
        "provider": "<fournisseur d'IA de l'espace>",
        "inputs": {"motif": "{{ inputs.motif }}"},
    },
}
FORM_RULES_EXAMPLE = {
    "version": 1,
    "sections": [
        {
            "id": "s1",
            "title": "Demande",
            "fields": [
                {"key": "montant", "type": "number", "label": "Montant (€)", "required": True, "min": 0},
                {"key": "urgent", "type": "checkbox", "label": "Urgent"},
                {
                    "key": "service",
                    "type": "select",
                    "label": "Service",
                    "options": [{"value": "rh", "label": "RH"}, {"value": "finance", "label": "Finance"}],
                },
                {
                    "key": "motif",
                    "type": "textarea",
                    "label": "Motif de l'urgence",
                    "rules": [
                        {
                            "when": {"field": "urgent", "op": "is_true"},
                            "then": [{"do": "show"}, {"do": "require"}],
                            "else": [{"do": "hide"}],
                            "name": "Urgence",
                        }
                    ],
                },
                {
                    "key": "mail",
                    "type": "email",
                    "label": "E-mail du validateur",
                    "rules": [
                        {
                            "when": {
                                "op": "all",
                                "items": [
                                    {"field": "service", "op": "equals", "value": "finance"},
                                    {"field": "montant", "op": "gte", "value": 500},
                                ],
                            },
                            "then": [{"do": "show"}, {"do": "require"}],
                            "else": [{"do": "hide"}],
                        }
                    ],
                },
            ],
        }
    ],
}


def _types(items) -> str:
    return ", ".join(sorted(items))


def _workspace_facts(workspace) -> dict:
    """Ce que l'espace propose et que l'assistant ne peut pas deviner : noms des fournisseurs d'IA configurés (jamais d'adresse, de clé ni de modèle)."""
    if workspace is None:
        return {}
    from ai.models import AiProvider

    return {"ai_providers": sorted(AiProvider.objects.filter(workspace=workspace).values_list("name", flat=True))}


def guide(topic: str, workspace=None) -> dict:
    sections = {
        "method": (
            "MÉTHODE. 1) Cherchez ce qui existe (search_actions, list_forms, list_skills, list_workflows) avant de créer. 2) Créez ce qui manque : "
            "propose_action / propose_skill (propositions), create_form (brouillon). 3) create_workflow, puis corrigez les `issues` renvoyées "
            "(validate_workflow les redonne). 4) attach_form pour le formulaire d'entrée. 4 bis) ESSAYEZ : start_action_test sur le code de chaque action proposée, puis start_dry_run + get_dry_run sur le workflow, corrigez jusqu'à zéro problème (rubrique `essai`). 5) Arrêtez-vous : une PERSONNE relit, active les propositions et publie. "
            "Vous ne pouvez ni publier, ni lancer, ni activer. ESSAYEZ avant de rendre la main : start_dry_run exécute les scripts et les décisions du brouillon dans un bac à sable isolé (actions, IA et approbations simulées d'après leur contrat), puis get_dry_run donne les problèmes et les valeurs réelles ; corrigez jusqu'à zéro problème. Dites ensuite ce que l'essai ne couvre pas (code des actions, IA, effets externes). Lisez la `revision` d'un brouillon (get_*) avant de le modifier."
        ),
        "essai": (
            "ESSAI À BLANC. start_dry_run {slug, samples?} met un essai en file (le worker l'exécute) et renvoie un job_id ; rappelez get_dry_run {job_id} jusqu'à status = succeeded. "
            "SCRIPTS et DÉCISIONS sont exécutés pour de vrai dans un bac à sable (ni réseau, ni fichiers, ni secret) ; ACTIONS, IA, sous-workflows et approbations sont SIMULÉS d'après leur contrat. "
            "Donnez 1 à 3 jeux d'essai réalistes, un par cas métier important : {label, inputs, expected?, mocks?}. `inputs` respecte le schéma d'entrées et utilise les MÊMES codes que le formulaire (valeurs de `select` en minuscules si le formulaire les définit ainsi). "
            "`expected` : [{step, field, value}] pour vérifier la sortie d'une étape ; `mocks` : {étape: {champ: valeur}} pour imposer la sortie d'une étape SIMULÉE (par exemple le total renvoyé par une action) et tester les scripts qui la lisent. "
            "Sans jeu fourni, un cas typique et un cas limite sont générés. Le résultat donne : `problems` (script qui plante, sortie du mauvais type, décision qui lit un champ absent, attente non satisfaite), `coverage` (étapes jamais exécutées) et, par scénario, les étapes exécutées avec leurs valeurs. "
            "Les approbations sont essayées accordées puis refusées. TESTEZ AUSSI LE CODE de chaque action que vous proposez : start_action_test {slug, cases:[{label, inputs, expected?}]} (1 à 5 cas ; `expected` = sous-ensemble de la sortie attendue) l'exécute dans le même bac à sable (réseau coupé, aucun secret ni connexion, limites plafonnées) et contrôle la sortie contre le contrat ; get_dry_run donne le résultat. Prévoyez un cas nominal, un cas vide ou limite et un cas d'erreur. Ce test ne rend PAS l'action utilisable : elle reste « proposée » jusqu'à l'activation par une personne. Rien ne prouve que les règles métier sont les bonnes."
        ),
        "decision": (
            "ACTION OU SCRIPT ? Réutilisez avant de créer : search_actions donne pour chaque action `used_by` (workflows et étapes qui l'utilisent). Une action existante dont le contrat convient "
            "se réutilise, même imparfaite ; ne dupliquez pas. CRÉEZ une action si : la logique sert (ou servira) à plusieurs étapes ou workflows ; elle a un effet externe, un accès réseau ou un secret ; "
            "elle demande un autre runtime ; son contrat d'entrées/sorties mérite d'être versionné et testé ; ou le même script revient 3 fois (`hints` : duplicate_script). "
            "Restez en SCRIPT si : c'est de la colle locale propre à ce workflow (renommer, filtrer, calculer un champ), courte, sans effet externe, utilisée une seule fois. "
            "Une proposition d'action coûte une relecture humaine : n'en créez pas pour une logique triviale. Dites dans votre réponse, pour chaque action créée, pourquoi, et pour chaque script, pourquoi pas une action. "
            "Les réponses de create_workflow / update_workflow / validate_workflow contiennent `hints` (scripts dupliqués ou longs, très grand workflow) : ce ne sont pas des erreurs, suivez-les ou justifiez de les écarter."
        ),
        "workflow": (
            "WORKFLOW. Graphe = {inputs, nodes, edges, outputs}. `inputs` : JSON Schema `type: object` (propriétés, `required`). `nodes` : liste d'étapes "
            "{id, type, name, …} ; `id` en minuscules/chiffres/_ (32 max, commence par une lettre) ; `name` obligatoire ; types : "
            f'{_types(NODE_TYPES)}. Étape `action` : {{id, type:"action", name, action:<slug>, version:"x.y.z", inputs:{{<paramètre>: <expression>}}}} — la version '
            "doit exister ET l'action doit être ACTIVE (une proposition fait remonter l'erreur `proposed_action` jusqu'à son activation par une personne). "
            "`edges` : [{from, to, label?}] ; les conditions comparent les `value` des options d'un champ `select` (pas leurs libellés) : gardez les mêmes codes partout (formulaire, `switch`, scripts) ; l'étape sans entrante démarre le workflow ; une seule étape de départ ; plusieurs flux vers une étape exigent une étape `join` ; "
            "une `condition` n'a que des sorties `true` / `false` ; un échec se route avec le label `error`. `outputs` : {nom: expression}. "
            "EXPRESSIONS : `{{ inputs.<entrée> }}`, `{{ steps.<id-étape>.output.<champ> }}` (le champ vient de l'`output_schema` de l'action), `{{ vars.<nom> }}`. "
            "Un workflow est un DAG : pas de cycle. Autres étapes (formats dans `examples.branching` et `examples.other_nodes`) : `script` {language:python|javascript, script, bindings:{variable: expression}, "
            "outputs:[{name,type}]} — le script lit les variables liées et DÉFINIT ses sorties comme variables ; `condition` {expr} avec deux sorties (labels `true` / `false`) ; `switch` {cases:[{id, expr}]} "
            "avec une sortie par `id` plus `default` ; `approval` {title} (attend une personne ; un REFUS fait ÉCHOUER l'étape : sans arête `error` de l'approbation vers une étape de traitement, le workflow échoue ; en cas d'accord le flux continue et `approved` vaut toujours true) ; `join` (réunit des branches) ; `transform` {op, items, where…} ; `ai` {skill, provider, inputs} (le `provider` doit être l'un de `workspace.ai_providers`, sinon l'étape est refusée : `unknown_provider`). "
            "`foreach`, `while` et `subworkflow` appellent un workflow PUBLIÉ : vous ne pouvez pas en publier, n'en utilisez que si list_workflows en montre un déjà publié (version non nulle)."
        ),
        "form": (
            "FORMULAIRE. Définition = {version:1, sections:[{id, title, fields:[{key, type, label, required?, help?, placeholder?, default?, options?, visible_if?}]}]}. "
            f"Types de champ : {_types(formengine.FIELD_TYPES)} ; blocs de mise en page : {_types(formengine.BLOCK_TYPES)}. `key` : lettre ou _, puis lettres/chiffres/_ (64 max). "
            "Les champs `select`, `radio`, `combobox`, `checkbox_list` exigent `options`. Pas de clé `id` sur un champ. "
            "Pour qu'un formulaire alimente un workflow, chaque entrée obligatoire du workflow doit être demandée par un champ de même nom (sinon `map` : "
            "{entrée du workflow: clé du champ}) et de type compatible (attach_form le vérifie). "
            "CONDITIONS : sur un champ, `rules:[{when: <condition>, then:[{do:show|hide|require|…}], else:[…], name?}]` ; une condition est {field, op, value} avec op parmi equals, not_equals, "
            "contains, starts_with, ends_with, gt, gte, lt, lte, is_empty, not_empty, is_true, is_false, in_list{values}, ou {op:all|any, items:[…]} pour combiner (voir `examples.form_rules`). "
            "Un champ masqué par une règle ne doit pas correspondre à une entrée OBLIGATOIRE du workflow : déclarez-la facultative."
        ),
        "action": (
            "ACTION (proposition). {slug, name, description (30 caractères au moins : ce que fait l'action), version:{runtime, source, input_schema, output_schema, side_effects, "
            "idempotent, network_policy, secrets_required?}}. Runtimes : python (`def run(inputs): … return {…}`), node, typescript, shell, powershell. Entrée = `inputs` "
            "(objet validé par `input_schema`) ; sortie = objet validé par `output_schema`. CONTRAT COMPLET OBLIGATOIRE : chaque propriété des deux schémas a `type`, `title` "
            "et `description` (12 caractères au moins) ; sans paramètre, dites-le dans la `description` du schéma. `network_policy`: none (défaut, pas de réseau). "
            "`side_effects`: none ou external (alors déclarez `idempotent` honnêtement). Aucun secret dans le code : déclarez un emplacement `secrets_required:[{slot,env}]`."
        ),
        "skill": (
            "SKILL IA (proposition). {slug, name, description, version:{instructions, input_schema, output_schema, default_provider?}}. `instructions` référence les entrées "
            "par `{{ inputs.<nom> }}` ; `output_schema` décrit la réponse attendue (objet)."
        ),
    }
    chosen = sections if topic == "all" else {topic: sections[topic]} if topic in sections else sections
    return {
        "topic": topic if topic in sections else "all",
        "guide": "\n\n".join(chosen.values()),
        "examples": {
            "workflow": WORKFLOW_EXAMPLE,
            "branching": BRANCHING_EXAMPLE,
            "other_nodes": OTHER_NODES,
            "form": FORM_EXAMPLE,
            "form_rules": FORM_RULES_EXAMPLE,
            "action": ACTION_EXAMPLE,
            "skill": SKILL_EXAMPLE,
        },
        "workspace": _workspace_facts(workspace),
        "note": "Les exemples sont validés par les contrôles du serveur ; remplacez les <slug> par des actions réelles (search_actions).",
    }
