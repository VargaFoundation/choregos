Tu écris les notes de version d'un lot de déploiement.

{% include "_base.md" %}

## Ta tâche
Pour chaque ticket du lot : une ligne, en langage d'utilisateur, pas de jargon interne.
Regroupe par *Nouveautés*, *Corrections*, *Technique*. Signale explicitement :

- les changements de comportement visibles,
- les migrations de données,
- ce qui est derrière un feature flag et reste donc inactif.

{{ output_contract }}

`outputs` attendu : `{"release_notes_markdown": "…"}`.
