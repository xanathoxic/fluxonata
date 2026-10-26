# Politique de sécurité

## Signaler une vulnérabilité
N'ouvrez pas de ticket public. Utilisez le signalement privé de GitHub (onglet *Security* du dépôt → *Report a vulnerability*) avec :
version/commit, étapes de reproduction, impact estimé. Accusé de réception visé sous 5 jours ouvrés ; correctif ou contournement documenté sous 90 jours
selon la gravité ; divulgation coordonnée après correctif.

## Périmètre et garanties (honnêteté)
- Les actions s'exécutent dans des conteneurs **non privilégiés, sans réseau, système de fichiers en lecture seule** : adapté à du code *de confiance* ou *peu fiable*,
  **pas** à du code hostile en multi-tenant (noyau partagé). Voir « Durcissement et limites » dans [docs/INSTALL.md](docs/INSTALL.md) pour le profil renforcé et ses limites.
- Les secrets sont chiffrés au repos ; les clés sont hors base et hors dépôt. Le masquage des journaux est une défense complémentaire.
- L'audit est append-only au niveau applicatif, pas contre un administrateur de base de données.
- Livraison des tâches **au moins une fois** ; aucune promesse « exactement une fois » pour les effets externes.

## Pratiques
Dépendances auditées en CI (`pip-audit`, `npm audit`), SBOM CycloneDX générés (`artifacts/`), analyse de secrets (`scripts/scan-secrets.py`),
tests d'isolation inter-équipes automatiques, tests SSRF, import de paquets durci (traversée de chemins, bombes de décompression, symlinks, sommes de contrôle, signatures).
