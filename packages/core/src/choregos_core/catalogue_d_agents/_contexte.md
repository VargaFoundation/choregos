## Work item
**{{ ticket.key }}** — {{ ticket.title }}

{{ ticket.body }}
{%- if spec %}

## Approved specification
{{ spec }}
{%- endif %}
{%- if plan_markdown %}

## Plan
{{ plan_markdown }}
{%- endif %}
{%- if allowed_paths %}

## Allowed paths
{%- for chemin in allowed_paths %}
- `{{ chemin }}`
{%- endfor %}
{%- endif %}
{%- for nom, valeur in inputs.items() %}{% if valeur %}

## Input: {{ nom }}
{{ valeur }}
{%- endif %}{% endfor %}

---

