"""Every error this library raises.

One base class, so a caller can catch ``TsumugiError`` and mean it, and one
subclass per thing that can go wrong in a way the caller could act on.

Errors carry identifiers and positions, never document text. A traceback is a
place text ends up in logs, and the index holds a person's notes.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CATALOGUE",
    "CONTRACT",
    "ConfigurationError",
    "ErrorKind",
    "IngestionError",
    "StorageError",
    "TsumugiError",
]


class TsumugiError(Exception):
    """Base for everything raised by tsumugi."""


class ConfigurationError(TsumugiError):
    """A setting is missing, unknown or contradictory.

    Unknown keys are an error rather than being ignored: a typo in a budget or
    an ignore rule that silently does nothing is the worst available outcome.
    """


class IngestionError(TsumugiError):
    """A document could not be read or parsed."""


class StorageError(TsumugiError):
    """The store could not answer, or would have to lie to."""


#: Named so a consumer can write it down. `-draft`: not frozen, and only
#: `tsumugi.context-package/1` is.
CONTRACT = "tsumugi.errors/1-draft"


@dataclass(frozen=True, slots=True)
class ErrorKind:
    """One failure this library can print, and what a caller should make of it.

    **No values, and that is the constraint the whole shape obeys.** No paths,
    no message templates with holes in them, no examples a reader could fill
    from a log. `sora` folds repeated failures into one line and can promise
    its incident file holds nothing sensitive *only* because it keeps the name
    and throws the sentence away; a catalogue carrying an example path would
    put one back.
    """

    #: Exactly as it appears before the colon on the first line of stderr.
    kind: str
    #: What the CLI exits with when this reaches the top, or ``None`` when
    #: there is not always one.
    #:
    #: `None` is not a gap. Two kinds reach a caller only through an MCP tool
    #: result, which has no exit code: the CLI either wraps them in a
    #: `ConfigurationError` first, or -- for a bare `ValueError` raised deep in
    #: a call -- lets them traceback, because an internal bug should be loud
    #: rather than tidy.
    #:
    #: Never 0 or 1. Those are outcomes rather than failures: `context` exits 1
    #: for "nothing was confirmed" and `verify` for "not every claim is
    #: supported", and neither is in this catalogue.
    exit_code: int | None
    #: `refused`, `unavailable`, `failed` or `timed_out`, in `sora`'s
    #: vocabulary. An exit code cannot say which.
    outcome: str
    #: Whether making the **same call again, unchanged**, could succeed.
    retryable: bool
    detail: str
    detail_ja: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "exit_code": self.exit_code,
            "outcome": self.outcome,
            "retryable": self.retryable,
            "detail": self.detail,
            "detail_ja": self.detail_ja,
        }


#: Every failure that can appear as the first word on stderr, with what a
#: caller should do about it. Held exhaustive by a test that walks the package
#: for exception classes, so adding one without a line here is a failure rather
#: than a silence.
#:
#: **`retryable` is not the same as `unavailable`, and the difference is the
#: part only this library knows.** A missing or stale index is unavailable and
#: is *not* retryable: the same call will fail identically until somebody runs
#: `ingest`. A model that is not listening is unavailable and *is* retryable:
#: it may be listening in a minute. A caller that retries the first forever
#: learns nothing.
CATALOGUE: tuple[ErrorKind, ...] = (
    ErrorKind(
        kind="ConfigurationError",
        exit_code=2,
        outcome="failed",
        retryable=False,
        detail="A setting is missing, unknown or contradictory. The call has to change.",
        detail_ja="設定が欠けている、未知、または矛盾している。呼び方を直す必要がある。",
    ),
    ErrorKind(
        kind="StorageError",
        exit_code=2,
        outcome="unavailable",
        retryable=False,
        detail=(
            "The index is missing, was built by an older tokenizer or indexing rule, "
            "or was written by a newer tsumugi. Someone has to ingest or rebuild it; "
            "the same call will keep failing until they do."
        ),
        detail_ja=(
            "索引が無い、古い規則で作られている、または新しい版で書かれている。"
            "ingest か rebuild が必要で、それまで同じ呼び出しは同じように失敗する。"
        ),
    ),
    ErrorKind(
        kind="IngestionError",
        exit_code=2,
        outcome="failed",
        retryable=False,
        detail="A document could not be read or parsed.",
        detail_ja="文書を読めなかった、または解析できなかった。",
    ),
    ErrorKind(
        kind="ProviderError",
        exit_code=2,
        outcome="unavailable",
        retryable=True,
        detail=(
            "The local model did not answer: not running, not holding the model asked "
            "for, or slower than the timeout. Timeouts arrive as this kind and are not "
            "distinguished from a refusal to connect."
        ),
        detail_ja=(
            "ローカルモデルが応答しなかった。起動していない、指定のモデルを持っていない、"
            "または timeout。timeout もこの kind で届き、接続拒否と区別されない。"
        ),
    ),
    ErrorKind(
        kind="AnswerFormatError",
        exit_code=2,
        outcome="failed",
        retryable=False,
        detail="An answer was not in a shape that can be read as claims and citations.",
        detail_ja="答えが claim と citation として読める形になっていない。",
    ),
    ErrorKind(
        kind="ProtectedPackageError",
        exit_code=2,
        outcome="refused",
        retryable=False,
        detail=(
            "A protected package was handed to verification with no way to restore it. "
            "Refused rather than reported as unsupported claims, because the quiet "
            "version of this failure is the damaging one."
        ),
        detail_ja=(
            "保護された package が、復元手段なしに検証へ渡された。unsupported な claim "
            "として報告せず拒否する。静かに失敗するほうが有害だから。"
        ),
    ),
    ErrorKind(
        kind="UnsupportedContractError",
        exit_code=None,
        outcome="failed",
        retryable=False,
        detail=(
            "A package names a contract this version does not understand. Reaches a "
            "caller through an MCP tool result; the command line wraps it in a "
            "ConfigurationError first."
        ),
        detail_ja=(
            "package が、この版の知らない契約名を名乗っている。MCP の結果として届く。"
            "コマンドラインでは ConfigurationError に包まれる。"
        ),
    ),
    ErrorKind(
        kind="ValueError",
        exit_code=None,
        outcome="failed",
        retryable=False,
        detail=(
            "Input was malformed in a way with no more specific kind. Reaches a caller "
            "through an MCP tool result. On the command line it is either wrapped in a "
            "ConfigurationError or left to traceback, because one raised deep in a call "
            "is a bug and should be loud rather than tidy."
        ),
        detail_ja=(
            "より具体的な kind の無い入力の不正。MCP の結果として届く。コマンドラインでは "
            "ConfigurationError に包まれるか、内部バグとして traceback になる。"
            "静かに整えるより騒がしいほうがよい。"
        ),
    ),
    ErrorKind(
        kind="DatabaseError",
        exit_code=2,
        outcome="unavailable",
        retryable=True,
        detail=(
            "SQLite could not complete the operation: the file is locked by another "
            "process, or damaged. A lock clears on its own; damage does not."
        ),
        detail_ja=(
            "SQLite が操作を完了できなかった。他プロセスによるロック、または破損。"
            "ロックは自然に解ける。破損は解けない。"
        ),
    ),
)

#: Kinds assembled at runtime from another project's vocabulary. Empty, and
#: expected to stay so: tsumugi names its own failures and an adapter's
#: exception is translated at the boundary rather than passed through.
OPEN_NAMESPACES: tuple[str, ...] = ()


def catalogue(version: str) -> dict[str, object]:
    """The catalogue as plain data, in the shape `tsumugi.errors/1-draft` names.

    ``version`` is passed in rather than read here. This module may import
    nothing but itself -- the architecture suite holds that -- so that an error
    class stays reachable from every layer without dragging anything behind it.
    Stamping the document is the composition root's job anyway: it is the thing
    that knows which build is answering.
    """
    return {
        "contract": CONTRACT,
        "by": f"tsumugi/{version}",
        "errors": [kind.as_dict() for kind in CATALOGUE],
        "open_namespaces": list(OPEN_NAMESPACES),
    }
