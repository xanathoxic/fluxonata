"""Guide de conception du MCP (ADR 0025) : le FORMAT exact d'un graphe de workflow, d'un formulaire, d'une action et d'un skill.

Constaté avec Claude Code réel : sans ce guide, l'assistant devine ces formats d'après les messages d'erreur du serveur. Les exemples ci-dessous
sont VALIDÉS par les vrais contrôles dans `tests/test_mcp_design_guide.py` (ils ne peuvent pas dériver) ; les listes de types viennent du code."""

from triggers import formengine

from .expressions import FUNCS, SPECIAL
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
    "http (appel d'un service web par une connexion)": {
        "id": "creer_ticket",
        "type": "http",
        "name": "Créer le ticket",
        "connection": "<nom d'une connexion « Service HTTP » de list_connections>",
        "method": "POST",
        "path": "/tickets",
        "body_format": "json",
        "body": {"titre": "{{ inputs.motif }}"},
        "mode": "test",
    },
    "sql (lecture seule)": {
        "id": "lire_clients",
        "type": "sql",
        "name": "Lire les clients",
        "connection": "<nom d'une connexion PostgreSQL / MySQL>",
        "statement": "SELECT id, nom FROM clients WHERE actif = %(actif)s",
        "params": {"actif": True},
        "mode": "test",
    },
    "smtp (envoi d'e-mail)": {
        "id": "prevenir",
        "type": "smtp",
        "name": "Prévenir par e-mail",
        "connection": "<nom d'une connexion « Serveur SMTP »>",
        "to": "{{ inputs.mail }}",
        "subject": "Demande reçue",
        "body": "Bonjour, votre demande est prise en compte.",
        "mode": "test",
    },
    "http via une intégration (Slack, Jira, GitHub…)": {
        "id": "prevenir_canal",
        "type": "http",
        "name": "Prévenir le canal",
        "integration": "slack",
        "operation": "post_message",
        "params": {"channel": "#ops", "text": "Demande : {{ inputs.motif }}"},
        "connection": "<connexion du type demandé par l'intégration (list_integrations)>",
        "mode": "test",
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
ERROR_HANDLING_EXAMPLE = {
    "inputs": {"type": "object", "required": ["motif"], "properties": {"motif": {"type": "string", "title": "Motif"}}},
    "nodes": [
        {
            "id": "creer_ticket",
            "type": "http",
            "name": "Créer le ticket",
            "connection": "<connexion « Service HTTP » de list_connections>",
            "method": "POST",
            "path": "/tickets",
            "body_format": "json",
            "body": {"titre": "{{ inputs.motif }}"},
            "mode": "test",
        },
        {
            "id": "alerter",
            "type": "script",
            "name": "Préparer l'alerte",
            "language": "python",
            "script": "message = 'Création du ticket impossible pour : ' + motif\n",
            "bindings": {"motif": "{{ inputs.motif }}"},
            "outputs": [{"name": "message", "type": "string"}],
        },
        {
            "id": "echec",
            "type": "throw",
            "name": "Échec signalé",
            "message": "{{ steps.alerter.output.message }}",
        },
    ],
    "edges": [
        {"from": "creer_ticket", "to": "alerter", "label": "error"},
        {"from": "alerter", "to": "echec"},
    ],
    "outputs": {},
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


def _expression_help() -> str:
    """Rubrique « expressions » : les fonctions viennent de la table réelle du moteur (elle ne peut pas dériver)."""
    funcs = ", ".join(sorted(set(FUNCS) | SPECIAL))
    return (
        "EXPRESSIONS. Elles s'écrivent entre `{{ … }}` dans les champs de texte des étapes ; une valeur qui est UNE seule expression garde son type (nombre, liste, objet). "
        "Chemins : `inputs.<entrée>`, `steps.<id>.output.<champ>`, `vars.<nom>`, `run.id`, `run.started_at`, `run.environment` ; "
        "dans `foreach` : `item` (et `iteration`) ; dans `while` : `last` (sortie de la dernière itération) et `iteration` ; dans `transform` : `item`. Indices : `inputs.lignes[0]`. "
        f"Fonctions (appel `nom(arguments)`, il n'y a PAS de filtre `|`) : {funcs}. `default(valeur, repli)` donne le repli quand la valeur est absente ou nulle. "
        "Opérateurs : `+ - * / %` (nombres ; `+` aussi pour deux textes), `== != < <= > >=`, `and or not`, `in` (`x in liste`), `contains` (`texte contains 'a'`). "
        "Littéraux : nombres, textes entre 'apostrophes' ou \"guillemets\", `true`, `false`, `null`. Une expression fait 500 caractères au plus. "
        "Pas de méthodes Python (`texte.upper()` est refusé : écrivez `upper(inputs.texte)`), pas de ternaire, pas de compréhension : pour une logique plus riche, utilisez `condition`, `transform` ou un script. "
        "Une référence à une étape qui n'est pas en amont, à un champ qui n'existe pas dans la sortie de l'étape ou à un chemin inconnu est signalée par `validate_workflow` : corrigez-la."
    )


def _types(items) -> str:
    return ", ".join(sorted(items))


def _workspace_facts(workspace) -> dict:
    """Ce que l'espace propose et que l'assistant ne peut pas deviner : noms des fournisseurs d'IA configurés (jamais d'adresse, de clé ni de modèle)."""
    if workspace is None:
        return {}
    from ai.models import AiProvider
    from vault.models import Connection

    conns: dict[str, list[str]] = {}
    for name, kind in Connection.objects.filter(workspace=workspace).order_by("name").values_list("name", "kind"):
        conns.setdefault(kind, []).append(name)  # noms et types seulement, jamais de paramètre ni de secret
    return {
        "ai_providers": sorted(AiProvider.objects.filter(workspace=workspace).values_list("name", flat=True)),
        "connections_by_kind": conns,
    }


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
            "ÉLÉMENT NATIF D'ABORD. Un appel à un service externe est une étape `http` avec une connexion (list_connections) : API REST, OAuth2, webhook sortant, Slack, Jira, Microsoft 365… Même règle pour `sql` (base PostgreSQL / MySQL en lecture), `ldap`, `sftp` (fichiers), `smtp` (e-mail) et `ai`. Ces étapes sont exécutées par la plateforme, qui ajoute le jeton, renouvelle OAuth2 et contrôle l'adresse : le jeton n'apparaît jamais dans le workflow. Une ACTION ou un SCRIPT n'a AUCUN réseau (ni Internet, ni réseau interne) : ne proposez JAMAIS une action ou un script pour appeler une API, interroger une base ou envoyer un e-mail, elle serait inutilisable. Utilisez l'action pour traiter les données (avant ou après l'étape `http`, en lisant `steps.<id>.output.body`), pas pour les chercher. Si aucune connexion adaptée n'existe, ne l'inventez pas : mettez le nom attendu, dites à la personne de la créer (menu Connexions) et signalez l'erreur `unknown_connection` comme à faire. ACTION OU SCRIPT ? Réutilisez avant de créer : search_actions donne pour chaque action `used_by` (workflows et étapes qui l'utilisent). Une action existante dont le contrat convient "
            "se réutilise, même imparfaite ; ne dupliquez pas. CRÉEZ une action si : la logique sert (ou servira) à plusieurs étapes ou workflows ; elle a un effet externe SANS réseau (ex. écrire un fichier d'artefact) ou doit porter un secret relié par une connexion ; "
            "elle demande un autre runtime ; son contrat d'entrées/sorties mérite d'être versionné et testé ; ou le même script revient 3 fois (`hints` : duplicate_script). "
            "Restez en SCRIPT si : c'est de la colle locale propre à ce workflow (renommer, filtrer, calculer un champ), courte, sans effet externe, utilisée une seule fois. "
            "Une proposition d'action coûte une relecture humaine : n'en créez pas pour une logique triviale. Dites dans votre réponse, pour chaque action créée, pourquoi, et pour chaque script, pourquoi pas une action. "
            "Les réponses de create_workflow / update_workflow / validate_workflow contiennent `hints` (scripts dupliqués ou longs, très grand workflow, `network_in_script` / `network_in_action` : code qui tente le réseau) : ce ne sont pas des erreurs, suivez-les ou justifiez de les écarter."
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
            "`http` {connection, method (GET|POST|PUT|PATCH|DELETE), path (commence par « / » : l'adresse vient de la connexion), query?, headers?, body?, body_format?: json|form|multipart|file, timeout_seconds? (1-60), pagination?, mode: test|reel} ; sorties `status`, `ok`, `body`, `headers`, `file` ; une réponse non 2xx fait ÉCHOUER l'étape (routez le label `error`). `sql` {connection, statement (un seul SELECT, valeurs par `%(nom)s` + `params`), mode} sorties `rows`, `count`, `columns` ; `ldap` {connection, filter, scope, params, mode} ; `sftp` {connection, operation: list|read|write|delete, path, content?, mode} ; `smtp` {connection, to, subject, body, mode} ; `wait` {seconds, resume?: timer|webhook|form} ; `throw` {message} ; `race` ; `agent` {goal}. MODE : ces étapes sont en `test` par défaut (simulées, rien n'est envoyé) : laissez `test` et dites à la personne de passer en `reel` après relecture ; le validateur renvoie un avertissement `*_test_mode`, c'est normal. CONNEXIONS : jamais de secret, de jeton ni d'adresse dans le graphe : `connection` est un NOM pris dans list_connections (kind http_bearer/api_key/basic/oauth2_client/oauth2_code/aws_sigv4 pour `http`, postgres/mysql pour `sql`, ldap, sftp, smtp). Pour une action qui déclare `secrets_required`, reliez la connexion sur l'étape : `connections: {<slot>: <nom>}`. Un workflow ne peut pas créer de connexion : si elle manque, listez-la dans votre récapitulatif. `foreach`, `while` et `subworkflow` appellent un workflow PUBLIÉ : vous ne pouvez pas en publier, n'en utilisez que si list_workflows en montre un déjà publié (version non nulle)."
        ),
        "expressions": _expression_help(),
        "integrations": (
            "INTÉGRATIONS. Pour Slack, Jira, GitHub, GitLab, ServiceNow, Microsoft 365, PagerDuty, Datadog, etc., NE décrivez PAS la requête à la main : appelez list_integrations "
            '(sans argument : les services ; avec {slug} : opérations et paramètres), puis écrivez une étape `http` {integration:<slug>, operation:<id>, params:{<nom>: <valeur ou expression>}, connection:<nom>, mode:"test"} '
            "(voir `examples.other_nodes`) : pas de `method` ni de `path`, ils viennent de l'opération. Les paramètres `required` sont obligatoires (`missing_param` sinon) ; une opération inconnue donne `bad_http`. "
            "La connexion doit être d'un des types que l'intégration demande (champ `connection.kind` de list_integrations) ; prenez son nom dans list_connections ou `workspace.connections_by_kind`. "
            "Si l'espace n'en a pas, dites à la personne de la créer par Connexions → « Partir d'un modèle d'intégration » : ne l'inventez pas. "
            "Les intégrations sont décrites d'après la documentation publique des services et vérifiées contre un faux serveur seulement : signalez-le dans votre récapitulatif. "
            "Pour un service absent de la liste, utilisez l'étape `http` générique avec une connexion « Service HTTP »."
        ),
        "erreurs": (
            "GESTION DES ERREURS. Une étape `action`, `script`, `http`, `sql`, `ldap`, `sftp`, `smtp`, `ai`, `agent`, `approval`, `foreach`, `while` ou `subworkflow` qui échoue fait échouer le workflow, sauf si une arête de label `error` part d'elle : "
            "le flux continue alors sur cette branche (voir `examples.error_handling`). Pour les appels réseau, prévoyez la branche `error` : notification (`smtp` ou une intégration Slack), "
            "ou `throw` {message} pour échouer avec un message clair. Règles du validateur : la branche `error` ne peut PAS lire la sortie de l'étape qui a échoué (elle n'en a pas ; `reads_failed_output` signale l'erreur) : "
            "lisez `inputs`, `vars` ou les étapes précédentes. Une réponse non 2xx est un échec de l'étape `http` ; pour tolérer un 404, mettez `fail_on_status: false` sur l'étape `http` : "
            "lisez alors `steps.<id>.output.ok` / `.status` dans une `condition`. Un REFUS d'approbation fait aussi échouer l'étape (arête `error` de l'approbation). "
            "Les appels réseau ne sont pas rejoués automatiquement quand ils écrivent (SMTP, SFTP en écriture) : ne bouclez pas dessus. Dites dans votre récapitulatif quelles étapes ont une branche `error` et lesquelles n'en ont pas, et pourquoi."
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
            "et `description` (12 caractères au moins) ; sans paramètre, dites-le dans la `description` du schéma. `network_policy`: none (défaut, pas de réseau ; ne demandez jamais autre chose : pour joindre un service, utilisez l'étape `http` d'un workflow). "
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
            "error_handling": ERROR_HANDLING_EXAMPLE,
            "form": FORM_EXAMPLE,
            "form_rules": FORM_RULES_EXAMPLE,
            "action": ACTION_EXAMPLE,
            "skill": SKILL_EXAMPLE,
        },
        "workspace": _workspace_facts(workspace),
        "note": "Les exemples sont validés par les contrôles du serveur ; remplacez les <slug> par des actions réelles (search_actions).",
    }
