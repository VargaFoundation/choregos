Tu **qualifies** les profils proposés à l'étape précédente.

## Le besoin
- Référence : {{ ticket.key }}
- Intitulé : {{ ticket.title }}

{{ ticket.body }}

## Les profils à qualifier
{{ spec }}

## Ce qu'on attend de toi
1. Pour chaque profil : ce qui est acquis, ce qui est à vérifier, ce qui est rédhibitoire.
2. Une **recommandation** classée, avec la raison du classement — la raison compte plus que le rang.
3. Les questions à poser en entretien, celles dont la réponse changerait le classement.

N'invente ni expérience ni référence. Un doute se déclare comme un doute.

## Sorties
`outputs.evaluation` (Markdown, une section par profil) et `outputs.recommandation` (le
profil retenu et pourquoi). Sans ces deux sorties, la garantie `outputs_present` refuse l'étape.

{{ output_contract }}

## Invariants
{{ invariants }}
