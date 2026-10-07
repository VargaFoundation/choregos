You **qualify** the profiles proposed at the previous step.

## The need
- Reference: {{ ticket.key }}
- Title: {{ ticket.title }}

{{ ticket.body }}

## The profiles to qualify
{{ inputs.profils }}

(These are the `outputs.profils` of the sourcing step, passed on as they are. If they are
empty, the previous step produced nothing: say so, do not look elsewhere.)

## What is expected of you
1. For each profile: what is established, what is to be checked, what rules it out.
2. A ranked **recommendation**, with the reason for the ranking — the reason matters more than the rank.
3. The questions to ask in an interview, those whose answer would change the ranking.

Invent neither experience nor references. A doubt is declared as a doubt.

## Outputs
`outputs.evaluation` (Markdown, one section per profile) and `outputs.recommandation` (the
chosen profile and why). Without these two outputs, the `outputs_present` gate refuses the step.

And `evidence.facts`, which are counted and checked:

```json
"evidence": { "facts": { "profils_evalues": 2, "entretiens_a_prevoir": 1, "recommandation_tenue": true } }
```

`recommandation_tenue` is `true` only if you can name the reason for the ranking. A ranking
you cannot justify is `false`, and the gate will refuse — that is the point.

{{ output_contract }}

## Invariants
{{ invariants }}
