# Derived values ship under their own status, never as gate-verified extractions

Status: accepted (2026-08-23)

Some field values are true but appear in no document. The measured case:
a Bulgarian university that never states its language of instruction
still teaches in Bulgarian, and the product needs that value. The domain
owner ruled it a safe assumption and asked for it in the pipeline
(2026-08-23), after being shown what it costs.

ADR-0002 says every value is gate-checked against verbatim Provenance.
A derived value has no verbatim Provenance to check — that is what makes
it derived. Three ways to ship one were considered.

**Rejected: ship it as PASS.** This is the tempting one and it is the
worst. PASS means "the gate proved this string is in the Artifact". A
derived value carries an invented snippet or an empty one, so either the
gate is bypassed for a status that promises it was not, or the snippet
is a fabrication in the literal sense. The pipeline's one rare guarantee
— the previous attempt shipped 36% unsupported values, this ships none —
would become unauditable, because no consumer could tell which PASS
values were proven and which were assumed.

**Rejected: keep them out and leave honest nulls.** This was the
standing decision and it is still defensible; it was overturned by the
owner, whose product needs the value. Recorded so the reversal is
visible: nulls lose real information the owner can supply.

**Accepted: a fourth status, `DERIVED`.** The value ships. It never
enters `gate()`, because there is nothing for the gate to check, and it
is impossible to confuse with a proven value: the status differs, the
tier is `D`, and instead of `source_snippets` it carries a `derivation`
block naming the rule that produced it and the configured input. The
guarantee is restated, not weakened: **every EXTRACTED value is verbatim
in its Artifact; derived values are labelled as derived and counted
separately.** A consumer that wants only proven data filters on PASS.

**The rule is config data, not code (ADR-0001).** A site declares
`default_language`; where the key is absent no derivation happens. This
is deliberate: AUBG teaches in English and VUM in English and Bulgarian,
so a hardcoded fleet-wide "Bulgarian" would ship a wrong value at the
first university that does not fit. The config diff is the record of who
asserted what, the same discipline as a page-wide anchor attestation.
Derivation fires ONLY when the deterministic cascade and the LLM tail
have both produced nothing — it can never displace a value a document
actually states, including one that states another language.

**The blind key grades derived values in their own category.** A
labeller who wrote "not stated anywhere on the programme's pages" was
answering *what does the page say*; a derived value answers *what is
true*. Scoring one against the other as FABRICATION would be a category
error, and scoring it as OK_VALUE would let assumption inflate the
correctness rate. `GradeCategory.DERIVED` is neither: it is reported on
its own line and excluded from both the correct set and the fabrication
count. The fabrication metric therefore keeps meaning exactly what it
meant — an unsupported value shipped as proven — and stays at 0.

Derived values DO enter the ledger and the published dataset; keeping
them out would make the change do nothing the owner asked for.

Revisit if: a derivation rule is ever proposed for a field whose value
varies within a university (tuition, admission), where a fleet default
is not a safe assumption but a guess; or if a consumer needs derived
values to carry a confidence beyond "a human asserted this rule".
