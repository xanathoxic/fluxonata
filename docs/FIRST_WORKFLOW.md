# Votre premier workflow (≈ 5 minutes)

Prérequis : `scripts/dev-up.sh` lancé ; ouvrez http://127.0.0.1:5173 ; compte de démonstration `alice` / `demo-fluxonata` (données fictives).

1. **Lancer une automatisation** — *Catalogue → Statistiques de texte → Lancer*, saisissez un texte, *Lancer l'automatisation*. Le résultat s'affiche en toutes lettres.
2. **Créer un workflow** — *Workflows → Nom du nouveau workflow → Créer et ouvrir l'éditeur*.
3. **Définir l'entrée** — panneau de droite : *Ajouter une entrée*, identifiant `texte`, libellé « Texte », *Obligatoire*.
4. **Ajouter une action** — palette de gauche : `+ Statistiques de texte`. Dans le paramètre *Texte*, *Utiliser une variable… → Entrée « Texte »*.
5. **Ajouter une condition** — `+ Condition`, puis *Insérer une variable → Statistiques de texte → Mots* et complétez par ` > 3`.
6. **Ajouter l'étape suivante** — `+ Attente simulée` (elle est reliée à la sortie « Si oui » de la condition).
7. **Vérifier** — la zone *Validation* doit afficher « aucun blocage » ; le brouillon s'enregistre automatiquement.
8. **Publier** — *Publier une nouvelle version* (1.0.0), puis *Lancer v1.0.0*.
9. **Suivre** — la page d'exécution résume ce qui a réussi, ce qui a échoué et la suite possible.

La vue **Liste** et le **Canevas** montrent le même workflow ; tout se fait au clavier dans la vue Liste (aucun glisser-déposer requis).
Test automatisé de ce parcours : `frontend/e2e/workflows.spec.ts`.
