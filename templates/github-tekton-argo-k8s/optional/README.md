# Add-ons GitHub optionnels

Ces workflows ne font **pas** partie du cœur de Choregos : ils ajoutent des interactions
directes avec un agent depuis GitHub, en marge de la plateforme. Ils sont **désactivés par
défaut** ; copiez-les dans `.github/workflows/` du projet si vous les voulez.

- `claude-mention.yml` — répondre à `@claude` dans une issue ou une PR.
- `claude-code-review.yml` — revue automatique d'une PR.

Sachez ce que vous perdez en les utilisant hors plateforme : pas de budget par run,
pas de gates, pas de périmètre autorisé, pas de coût dans le ticket.
