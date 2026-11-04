# Results - the development project on the dev tenant

## 2026-10-07 - prepared, not yet played

**Ready:**
- the sandbox repository `DiametralGroup/choregos-sandbox-dev` (private): `invoices` (a `compute_total`
  that ignores credits - the bug of the first work item), three tests, `python -m invoices.smoke`, an ADR
  0000 in MADR 4 format, GitHub Actions - **green** on `main` at the first push;
- `scenario_dev.py` (prepare, requirements, open, follow, verify).

**Not played**, because the dev tenant does not yet carry what it takes:
- the S21 stack (#264 -> #291, S21-01 to S21-25) is not merged, no release carries it, and
  `choregos-deploy` does not vendor it;
- Choregos' GitHub App is not installed on the sandbox;
- the groups `maintainers`, `product-owners`, `release-captains`, `architects` are not checked
  in the identity provider.

Nothing is therefore measured on the tenant: no states, no costs, no merged PR. This page will be
completed at the first run.
