# MIGRATION-NOTES.md skeleton

Hand this file's **path** to the Phase B notes agent. It fixes the document's SHAPE so the
agent spends its tokens only on judgement, never on boilerplate or on trimming an overlong
draft back down.

Why it exists: on the reference run the notes agent authored a 12.3 KB draft, then trimmed it
twice to land at 9.7 KB against a 4–7 KB brief — ~17.4 KB emitted for a 9.7 KB file (~1.8×
waste), 199s, 20 tool uses, and it became the long pole of the whole authoring fan-out.
Re-editing a just-written file is the same latency defect the skill already forbids for YAML.

**Rules for the agent:**

1. Copy the structure below verbatim. Seven sections, in this order, with these headings.
   Sections do not get added, dropped, renumbered, or reordered — even when a section is
   nearly empty for the stack at hand (say so in one line instead).
2. Replace every `<FILL: …>` marker. Each marker states what is wanted AND its byte budget.
   Treat the budget as a ceiling, not a target.
3. Text outside the markers is boilerplate — reproduce it, do not rewrite it or "improve" it.
4. **Write the file ONCE.** Decide each section's content before the first `Write`. If you
   find yourself editing it down afterwards, the budgets were ignored.
5. **Budget what you AUTHOR, not the finished file: ≤5 KB of `<FILL:>` content.** The
   skeleton's own boilerplate is ~4.6 KB and is reproduced verbatim, so a correct file lands
   around **9–10 KB total** on a 5-service stack. That total is NOT a defect and is NOT
   something to trim toward — measured, the floor is ~9.4 KB. **Never delete boilerplate to
   hit a size number** (rule 3 forbids it, and an earlier version of this file wrongly stated
   a 4–7 KB total, which is arithmetically unreachable).
   The signal that narrative leaked in from `report.html` is **authored content over ~6 KB**,
   or any section exceeding its own marker budget — not the file total.
   Stacks with more services push the total up legitimately (section 4 gains a row and
   section 3 a clause per service); that is expected.
6. Never restate the Phase 1–4 decision record — that is `decisions.json` → `report.html`'s
   sole job. Cross-reference it with the one-line blockquote in the header.

**Generality.** This skeleton is stack-independent. Section 3 scales with however many ACK
services the migration touches; section 4's table takes one row per service; section 6 grows
or shrinks with what had no ACK equivalent. On a stack where a whole service has no controller
(e.g. no `redshift` or `lakeformation` controller exists), section 6 is the most important
section in the document and the cross-boundary warning in 6c is mandatory — see the note there.

---

## The skeleton

```markdown
# Operator runbook — `<RGD-NAME>` (<Adopt_Path|Create_Path>)

Context `<CTX>` · region `<REGION>` · namespace `<NS>` · account `<ACCT>`
Platform: ACK `<ACK-VER>`, KRO `<KRO-VER>` (<EKS managed capabilities | self-managed>)

> Decision record and resource graph: see `report.html` § Phase decisions.

---

## 1. Apply order

Files under `<mode>/<rgd>/resources/` are **review-only** previews — do not apply them. The
RGD is the source of truth; KRO creates the ACK CRs itself.

```bash
cd migration-output/<mode>/<rgd>
kubectl --context <CTX> apply -f rgd.yaml

# Wait for Active BEFORE applying the instance
kubectl --context <CTX> get resourcegraphdefinition <RGD-NAME> -o jsonpath='{.status.state}'
```

Expect `Active`. `Inactive` means the graph was rejected and **no instance can reconcile** —
check `GraphAccepted` in `status.conditions` and fix the RGD first.

```bash
kubectl --context <CTX> apply -f instance.yaml
kubectl --context <CTX> -n <NS> get <lowercase-kind> <instance-name> -w
```

<FILL: one short paragraph, <=400 B — the adoption-vs-creation caveat for THIS mode. For
adopt: lookup resolves via adoption-fields and a wrong key fails silently as NotFound, so
section 2 is mandatory. For create: resources are created fresh, so watch for partial graphs.>

---

## 2. Post-adoption validation

<FILL: one sentence, <=200 B — the three conditions that must hold per CR. For adopt these are
READY: True, a populated status.ackResourceMetadata.arn (proof the LIVE object was resolved,
not created), and ACK.ResourceSynced: True.>

```bash
K=<comma-separated <plural>.<group> for every adopted Kind — take verbatim from
  decisions.json -> adopted_resources[].{plural,group}; do NOT pluralize by hand>

kubectl --context <CTX> -n <NS> get $K

kubectl --context <CTX> -n <NS> get $K \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,ARN:.status.ackResourceMetadata.arn,SYNCED:.status.conditions[?(@.type=="ACK.ResourceSynced")].status'

kubectl --context <CTX> -n <NS> get <lowercase-kind> <instance-name> -o yaml
```

<FILL: one sentence, <=200 B — what an empty ARN column means and the first thing to recheck.>

Diagnosis command for any CR not `READY: True`:

```bash
kubectl describe <kind> <name> | grep -A3 "ACK.Recoverable"
```

`ACK.Recoverable=True` → transient (IAM propagation, ordering); retry.
`ACK.Terminal=True` → spec invalid or unmatchable; it will never self-heal.

---

## 3. Required ACK controllers

<FILL: one sentence naming every service and its Kinds, <=100 B per service. Derive the
service set from decisions.json -> adopted_resources[].group (the part before
.services.k8s.aws). All must be running before the instance, or the CRDs will not exist and
the RGD will not go Active.>

```bash
kubectl --context <CTX> get crd | grep -E '<pipe-joined plurals>' | grep services.k8s.aws
```

---

## 4. Permissions — ALERT ONLY

Adoption needs **read/describe** to resolve the live object, plus the normal **write/tag**
actions once ACK manages it. No IAM policy JSON is generated here — the ACK capability role is
managed externally<FILL: <=120 B — if EKS managed capabilities, add that it is managed for you>.

| Service | Read (to adopt) | Write/tag (ongoing) | AWS managed policy | Flag |
|---|---|---|---|---|
<FILL: ONE ROW PER SERVICE, <=300 B per row. This is judgement work and the reason this
section is not scriptable: name the specific API actions, then state whether a common AWS
managed policy covers them. Mark **FLAG** only for a service with a real gap — no narrow
managed-policy fit, an action missing from the usual policy, or a permission (e.g.
iam:PassRole) that no managed policy ever grants. Do NOT flag a service that a standard
policy covers cleanly.>

<FILL: 1–2 sentences, <=400 B — name ONLY the flagged services, give the failure signature an
operator would actually see, and say to raise it with the capability-role owner rather than
hand-editing CRs. If nothing is flagged, write one line saying standard managed policies
cover every service on this stack.>

---

## 5. KRO Kubernetes RBAC — informational

Two independent authorization layers, failing differently:

| Layer | Governs | Identity | Failure signature |
|---|---|---|---|
| AWS IAM | calls to the AWS API | ACK controller role (per service) | `AccessDenied` in the CR's `ACK.Recoverable`/`ACK.Terminal` condition |
| Kubernetes RBAC | get/list/watch/create/update on CRDs and CRs *inside* the cluster | KRO controller role | `cannot get resource ... is forbidden` on the RGD; no CRs ever appear |

KRO — not you — creates the ACK CRs while reconciling the RGD, so without RBAC on those API
groups reconciliation stops before a single AWS call is attempted. The groups involved:
<FILL: the distinct API groups, one line, from adopted_resources[].group>.

Grant it either way: an **EKS access entry** for the KRO controller role, or a scoped
**ClusterRole + ClusterRoleBinding** over those groups.

**This is most relevant to SELF-MANAGED ACK/KRO deployments.** <FILL: <=250 B — state what
THIS cluster runs. On EKS Auto Mode managed capabilities, KRO's RBAC and the ACK capability
role are provisioned automatically, so no action is expected and this is a check-only-if-
forbidden item.>

---

## 6. Unsupported TF attributes and resources

### Attributes with no live-CRD equivalent — deliberately omitted

<FILL: <=600 B. Group by reason, do not list one bullet per attribute: (a) absent from the
live CRD — name them and say in one clause why dropping them is safe; (b) present but
immutable — name them and give the consequence of having written them; (c) any attribute a
reader would WRONGLY assume was dropped but which was in fact renamed to its ACK equivalent —
state the mapping. Omit any sub-case that does not apply.>

### Type-mismatch omissions

<FILL: <=500 B — any field that EXISTS on the CRD but could not be populated because its type
differs from the tfstate value (e.g. a SecretKeyReference pointing at a Kubernetes Secret
rather than a plain string). State what is untouched as a result, and what the operator would
have to do to let ACK manage it later. Write "None." if there are none.>

### Retained in Terraform (NOT migrated)

<FILL: <=400 B — the excluded addresses grouped by reason, from decisions.json -> unsupported.
One clause per reason, not per address.>

<FILL: ⚠️ MANDATORY WHENEVER ANYTHING WAS EXCLUDED — a blockquote, <=600 B, naming every
CROSS-BOUNDARY DEPENDENCY: an adopted resource that still depends on something left behind.
This is the highest-value content in the document and exists nowhere in the input data — it
cannot be derived, only reasoned about. Name the adopted resource, the retained object it
needs, what breaks if that object is destroyed or renamed, and the fact that the CR may still
report READY: True while the application is broken. Scales with the stack: a warehouse stack
with no redshift/lakeformation controller will have several of these, and they matter more
than every other section combined. Write "No cross-boundary dependencies." ONLY if every
adopted resource is self-contained.>

---

## 7. Rollback

**Terraform state is never modified by this migration.** `terraform.tfstate` remains a valid
rollback backup, and after adoption Terraform and ACK **co-manage** the same live resources.

<FILL: one sentence, <=300 B — why rollback is safe by construction, naming the two mechanisms
actually used: deletion-policy retain on every CR, and whether ownerReferences was omitted.>

```bash
# 1. Delete the instance (removes the ACK CRs; AWS resources survive)
kubectl --context <CTX> -n <NS> delete <lowercase-kind> <instance-name>

# 2. Confirm no ACK CRs remain
K=<repeat the full plural.group list from section 2 — do NOT write "reuse $K", this block
  must stand alone in a fresh shell>
kubectl --context <CTX> -n <NS> get $K

# 3. Remove the abstraction
kubectl --context <CTX> delete resourcegraphdefinition <RGD-NAME>
```

> ⚠️ **Do not run `terraform apply` or `terraform destroy` against the adopted resources while
> ACK is also managing them.** <FILL: <=350 B — the concrete failure: both controllers believe
> they own the resource, giving drift flapping or, with destroy, deletion of live
> infrastructure that ACK then reports as NotFound. End with the rule: one owner per resource.>
```

---

## Budget summary

| Section | Agent-authored budget |
|---|---|
| Header | ~200 B |
| 1 Apply order | ~400 B |
| 2 Validation | ~400 B + the `$K` list |
| 3 Controllers | ~100 B per service |
| 4 Permissions | ~300 B per service row + ~500 B prose |
| 5 KRO RBAC | ~250 B |
| 6 Unsupported | ~2.1 KB (the judgement-heavy section) |
| 7 Rollback | ~650 B |

Boilerplate reproduced from this skeleton does not count against the agent's thinking time —
it is fixed text. The budgets above apply only to `<FILL:>` content.

**Authored total: ~5 KB. Plus ~4.6 KB of reproduced boilerplate = ~9–10 KB on disk.** Verify
against the per-marker ceilings above, never against the file size. Measured on the reference
stack: 4.8 KB authored, 9.4 KB on disk, every marker inside its ceiling.

**One known duplication is deliberate.** The 7-item `<plural>.<group>` list (~380 B) appears
in both section 2 and section 7. Section 7's rollback block must stand alone in a fresh
shell, so "reuse `$K` from section 2" is forbidden — an operator pasting only the rollback
block would otherwise run `kubectl get` against an empty variable and conclude, wrongly, that
no CRs remain. Pay the 380 B.
