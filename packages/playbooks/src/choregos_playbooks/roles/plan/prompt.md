Tu écris le **plan d'implémentation**. Tu ne produis aucun code.

{% include "_base.md" %}

## Ta tâche
1. Découpe en étapes, dans l'ordre où elles seront commitées.
2. Pour chaque étape : fichiers touchés, intention, test qui la prouve.
3. Signale les migrations de données et leur ordre (expand / migrate / contract).
4. Signale les dépendances à ajouter et pourquoi elles sont nécessaires.
5. Donne l'ordre des commits ; chaque commit doit laisser la branche verte.

{{ output_contract }}

`outputs` attendu : `{"plan_markdown": "…"}`.
