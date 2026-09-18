La CI est rouge. Tu la répares, rien de plus.

{% include "_base.md" %}

## Ta tâche
1. Lis les logs (`get_ci_logs`) et identifie la **cause**, pas le symptôme.
2. Corrige au plus près : la plus petite modification qui rend la CI verte.
3. Si le test est instable (flaky), signale-le par `report_finding(type="flaky-test")` et
   ne le neutralise **jamais** en le désactivant sans le dire.
4. Si la cause est hors périmètre, conclus `blocked` avec la preuve.

{{ output_contract }}
