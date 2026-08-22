# Plant reference resolution

GardenOps treats external plant links as verified taxonomic references rather
than model-generated suggestions. AI plant lookup supplies plant attributes,
then the RHS resolver independently searches using the complete botanical name.

## Matching rules

- The complete normalized botanical name must match one unique RHS result.
- A cultivar epithet remains part of the identity. A genus-only result never
  matches a named cultivar.
- An RHS genus page is retained only when the GardenOps botanical record is
  itself genus-only.
- A cultivar quoted in the GardenOps common-name field is appended to an
  incomplete botanical query, but the localized common name itself is not sent
  as an additional required RHS search term.
- A synonym is accepted only when the RHS result explicitly identifies its
  accepted parent plant.
- Ambiguous, unaccepted, missing, or malformed results do not produce a link.
- Resolver input is limited to three botanical variants of at most 200
  characters, bounding the number and size of searches generated from AI data.
- RHS request failures do not clear or replace stored links.

Verified and unresolved checks are recorded in `plant_external_references` with
the source ID, entity ID, canonical URL, matched names, decision, reason, and
verification time. This table can support other reference sources without
adding provider-specific columns to `plants`.

## Audit and repair

The repair command is read-only unless an exact reviewed report and its SHA-256
digest are supplied. Audit reports include their generation time, options, and
a deterministic digest covering all proposed decisions.

```bash
.venv/bin/python scripts/repair_rhs_plant_links.py \
  --output /tmp/gardenops-rhs-link-audit.json
```

By default, only plants whose current link is an RHS plant URL are checked. Use
`--scope all` to resolve empty and non-RHS records as well. Existing non-RHS
links remain unchanged unless `--replace-non-rhs` is explicitly selected.

Review the dry-run report and take a database backup before applying that exact
report. The digest is printed in the audit summary and stored in the report:

```bash
.venv/bin/python scripts/repair_rhs_plant_links.py \
  --apply-report /tmp/gardenops-rhs-link-audit.json \
  --confirm-digest <reviewed-report-digest> \
  --output /tmp/gardenops-rhs-link-application-receipt.json
```

Apply never queries RHS again. It rejects altered reports, reports more than 24
hours old, reports containing resolver errors, mismatched summaries or actions,
and records changed since the audit. Any changed or missing plant aborts and
rolls back the entire transaction.

During apply, verified matches replace current RHS links. Deterministic
identity mismatches and `not_found` outcomes clear current RHS links so an
incorrect reference is not presented as authoritative. An exact identity whose
RHS nomenclatural status requires review remains linked and is recorded as
`needs_review`. Resolver errors cannot be applied.

The resolver uses the search service called by the public RHS plant finder. It
is isolated in `gardenops.services.rhs_plant_resolver`, rate-limited by the
repair command, and should not be treated as a stable, documented third-party
API contract. Cache results and coordinate with RHS before materially expanding
automated use.
