# Architecture decision records

One file per decision that changed a boundary, a default, or a guarantee. Each
says what the situation was, what was chosen, what follows from it, and — the
part that is usually missing — **what it costs**.

A decision recorded before the code exists is still a decision. These were made
while refining the design, and they are why the design looks the way it does.
What is *intended* next lives in [docs/proposals](../proposals/0001-the-design.md)
instead; an ADR records a decision already taken, and a plan is neither.

An ADR is never edited to match the present. When a decision stops holding, a
later ADR supersedes it and says so.

| # | Decision |
|---|---|
| [0001](0001-the-domain-depends-on-nothing.md) | The domain layer imports only the standard library |
| [0002](0002-the-context-package-is-a-document.md) | The ContextPackage is a document, not a type |
| [0003](0003-a-package-is-reproducible.md) | A package is reproducible, and its id is its inputs |
| [0004](0004-the-model-quotes.md) | The model quotes; tsumugi resolves the offsets |
| [0005](0005-selection-is-a-report.md) | Selection is a report, not a promise |
| [0006](0006-the-budget-is-an-estimate.md) | The budget is an estimate whose error is measured |
| [0007](0007-index-japanese-by-bigram.md) | Index Japanese by bigram, and confirm against the text |
| [0008](0008-redundancy-is-proposed.md) | Redundancy is proposed, never removed |
| [0009](0009-restore-before-you-verify.md) | Restore before you verify |
| [0010](0010-the-index-stores-the-text.md) | The index stores the text it anchored |
| [0011](0011-record-what-was-sent-and-what-was-used.md) | Record what was sent, and what was used |
| [0012](0012-an-agent-facing-surface.md) | An agent-facing surface, on the standard library |
| [0013](0013-label-the-evidence-not-the-ideal-answer.md) | Label the evidence, not the ideal answer |
| [0014](0014-the-index-does-not-live-beside-the-corpus.md) | The index does not live beside the corpus |
| [0015](0015-redundancy-does-not-decide-which-is-right.md) | Redundancy does not decide which duplicate is right |
| [0016](0016-the-network-lives-in-one-place.md) | The network lives in one place |
| [0017](0017-the-instruction-set-is-a-parameter.md) | The instruction set is a parameter, and the prompt is the package |
| [0018](0018-confirm-a-paraphrase-by-coverage.md) | A paraphrase confirms by coverage, not by phrase |
| [0019](0019-confirmation-is-relative.md) | Confirmation is relative, and it has to say where |
| [0020](0020-a-protection-is-irreversible-until-it-says-otherwise.md) | A protection is irreversible until it says otherwise |
| [0021](0021-the-ledger-records-that-a-package-was-protected-not-how.md) | The ledger records *that* a package was protected, not how |
| [0022](0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md) | An unconfirmed candidate is an omission, not an item |
| [0023](0023-the-public-surface-changes-on-notice.md) | The public surface changes on notice, and the notice is a diff |

Several are borrowed, with thanks, from the sibling projects `mamori` and
`kiseki`. Where that is the case the ADR says so and names the original: a
decision someone else already paid for is worth taking, and worth attributing.
