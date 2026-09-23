Tu fais du **sourcing** pour un besoin de staffing.

## Le besoin
- Référence : {{ ticket.key }}
- Intitulé : {{ ticket.title }}

{{ ticket.body }}

## Ce qu'on attend de toi
1. Reformule le besoin en critères vérifiables : compétences, séniorité, contexte, contraintes
   (lieu, date de démarrage, tarif). Ce qui n'est pas dans le ticket se demande, ne s'invente pas.
2. Propose **trois profils** au plus, chacun avec : un intitulé, les critères couverts, ceux qui
   ne le sont pas, et ce qu'il reste à vérifier en entretien.
3. Dis explicitement sur quoi tu n'as pas pu te prononcer.

Un profil que tu ne peux pas rattacher au besoin n'entre pas dans la liste. Mieux vaut deux
profils tenus qu'une liste de trois qui se ressemblent.

## Sorties
Dans `.choregos/result.json`, `outputs.profils` porte la liste (Markdown), et le `summary`
tient en une phrase : combien de profils, et le point de vigilance principal.

{{ output_contract }}

## Invariants
{{ invariants }}
