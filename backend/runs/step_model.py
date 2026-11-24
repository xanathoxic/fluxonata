"""Nœud « Modèle de document » (ADR 0036) : un texte Markdown (avec images et `{{ expressions }}`) rendu une fois par exécution.

S'exécute dans l'orchestrateur (calcul borné, ni réseau ni code utilisateur). Il ne produit pas de fichier ni de message : il prépare
le contenu que d'autres étapes réutilisent (« Créer un fichier », « Envoyer un e-mail ») par `{{ steps.<id>.output }}`."""

from workflows import model_md, model_spec
from workflows.expressions import ExpressionError, resolve

from . import model_docs


class ModelStepError(Exception):
    def __init__(self, code, message, next_step="Corrigez le modèle puis relancez."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def apply(run, node, ctx) -> dict:
    try:
        source = model_md.resolve_markdown(str(node.get("markdown") or ""), ctx)
        title = str(resolve(node.get("title") or "", ctx) or "")[: model_spec.MAX_TITLE]
        if len(source) > model_md.MAX_MARKDOWN:
            raise model_docs.ModelError(
                "MODEL_CONTENT", f"Le modèle dépasse {model_md.MAX_MARKDOWN // 1000} 000 caractères une fois rempli."
            )
        model_docs.load_images(
            run.workspace_id, source
        )  # les images doivent exister maintenant : pas de surprise à l'envoi
    except ExpressionError as exc:
        raise ModelStepError("MODEL_CONTENT", str(exc)) from None
    except model_docs.ModelError as exc:
        raise ModelStepError(exc.code, exc.message, exc.next_step) from None
    return {
        "title": title,
        "markdown": source,
        "html": model_md.to_html(source, model_md.placeholder_url),
        "text": model_md.to_text(source),
        "images": model_md.image_ids(source),
    }
