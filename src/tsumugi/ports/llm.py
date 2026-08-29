"""Somewhere to send a package, and get an answer back.

Optional, and the only part of tsumugi that talks to anything. Everything else
— reading, indexing, selecting, budgeting, verifying — happens with no model
and no network, and that is not going to change
([ADR 0016](../../docs/adr/0016-the-network-lives-in-one-place.md)).

The interface is deliberately the smallest thing that does the job. A provider
is asked for text and never for a decision, which is what keeps a model outside
every judgement tsumugi makes: it does not rank, it does not choose what to
send, and it does not resolve a citation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["Endpoint", "LLMProvider"]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Where a provider sends, and whether that is inside the boundary.

    A local-first library that quietly posted a person's notes to a host on the
    internet would be worse than one that never had a provider at all, so the
    check is on the *boundary* rather than on a spelling of "localhost".
    """

    url: str
    #: ``True`` when the host is the machine this is running on.
    is_local: bool

    def describe(self) -> str:
        return f"{self.url} ({'local' if self.is_local else 'REMOTE'})"


@runtime_checkable
class LLMProvider(Protocol):
    """Turns a prompt into text. Nothing more."""

    @property
    def name(self) -> str:
        """Versioned identifier, recorded with an answer so a reader knows
        which model produced the claims that were checked."""
        ...

    @property
    def endpoint(self) -> Endpoint:
        """Where this sends. Inspectable before anything is sent."""
        ...

    def generate(self, prompt: str) -> str:
        """Ask, and return what came back.

        **Raises on failure.** Returning an empty string would be
        indistinguishable from a model that answered with nothing, and a
        verification run over an empty answer reports zero claims — which reads
        as success.
        """
        ...
