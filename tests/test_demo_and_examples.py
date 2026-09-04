"""The demo and the example: they run, and they touch nothing of yours.

The second half is the reason this file exists. A demo that wrote into the
reader's real index would be a bad first impression and a real one -- `kiseki`
records a CLI test that once wrote into a developer's live database, and this
is the same failure with a wider audience.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tsumugi.interfaces.cli.demo import CORPUS, QUESTION, run_demo
from tsumugi.interfaces.cli.main import main

ROOT = Path(__file__).resolve().parent.parent


class TestTheDemo:
    def test_it_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_demo() == 0

    def test_it_walks_through_every_stage(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo()
        out = capsys.readouterr().out
        for stage in (
            "A small corpus",
            "Reading it",
            "ContextPackage",
            "left out",
            "citations checked",
            "Backwards",
            "what was used",
        ):
            assert stage in out, stage

    def test_it_shows_the_refused_credential_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo()
        out = capsys.readouterr().out
        assert "refused" in out
        assert ".env" in out

    def test_it_shows_all_three_verification_outcomes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The distinction is the point, so a demo that only showed "supported"
        # would be showing the least interesting third of it.
        run_demo()
        out = capsys.readouterr().out
        for outcome in ("supported", "unsupported", "uncited"):
            assert outcome in out

    def test_it_says_that_supported_is_not_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo()
        assert "does not mean the claim is true" in capsys.readouterr().out

    def test_it_names_what_was_left_out_and_why(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo()
        out = capsys.readouterr().out
        assert "considered and not sent" in out
        assert "below_threshold" in out or "redundant_candidate" in out

    def test_it_states_that_the_token_count_is_an_estimate(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_demo()
        out = capsys.readouterr().out
        assert "estimated, not counted" in out
        assert "cl100k_base" in out


class TestItTouchesNothing:
    def test_it_does_not_write_to_the_configured_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The demo ignores the configured index deliberately. A demo that wrote
        # into somebody's real corpus would be a bad first impression and a
        # real problem.
        index = tmp_path / "real-index.db"
        monkeypatch.setenv("TSUMUGI_INDEX", str(index))
        assert main(["demo"]) == 0
        assert not index.exists()

    def test_it_cleans_up_after_itself(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo()
        out = capsys.readouterr().out
        assert "kept:" not in out

    def test_keep_says_where_it_left_things(self, capsys: pytest.CaptureFixture[str]) -> None:
        run_demo(keep=True)
        out = capsys.readouterr().out
        assert "kept:" in out
        # And tells the reader what to do with it.
        assert "tsumugi --index" in out


class TestTheCorpusItself:
    def test_it_is_small_enough_to_read(self) -> None:
        # A demo corpus nobody reads is a demo nobody follows.
        assert len(CORPUS) <= 6
        assert all(len(text) < 400 for text in CORPUS.values())

    def test_it_plants_an_answer_a_correction_and_a_copy(self) -> None:
        # Rigged on purpose: without adversaries the demo would show a
        # retriever succeeding at nothing.
        joined = "\n".join(CORPUS.values())
        assert joined.count("テントの重量は2.4kg") == 2, "the verbatim copy"
        assert "テントの重量は3.1kg、二人用。\n" in joined, "the older version"
        assert "キャンプ用タープ" in joined, "the adjacent subject"

    def test_the_question_is_answerable_from_it(self) -> None:
        assert any(QUESTION in text for text in CORPUS.values())


class TestTheExample:
    def test_it_runs_as_a_script(self) -> None:
        # Run in a subprocess, because that is how a reader will run it: from
        # a checkout, without installing anything.
        finished = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "examples" / "library.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"PYTHONUTF8": "1", "PATH": ""},
            cwd=ROOT,
        )
        assert finished.returncode == 0, finished.stderr
        assert "supported" in finished.stdout
        assert "considered and not sent" in finished.stdout

    def test_the_ask_example_runs_without_a_model(self) -> None:
        # The point of the test. Everything except the last step works with no
        # model, and an example that died at the import -- or exited nonzero
        # because ollama was not running -- would suggest otherwise to the
        # first person who tries it.
        finished = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "examples" / "ask.py"), "no-such-model-exists"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"PYTHONUTF8": "1", "PATH": ""},
            cwd=ROOT,
        )
        assert finished.returncode == 0, finished.stderr
        # It says where it would send before it tries, and names the local
        # boundary while doing it.
        assert "(local)" in finished.stdout

    def test_it_is_commented_for_why_rather_than_what(self) -> None:
        # The mechanics are short enough to read unaided; the reasons are the
        # part that is easy to get wrong. If this file stops explaining them it
        # has stopped being worth reading.
        source = (ROOT / "examples" / "library.py").read_text(encoding="utf-8")
        for reason in (
            "is a decision",
            "is offered as current",
            "does not send it",
            "does not mean the claim is true",
            "not nearly there",
        ):
            assert reason in source, reason


@pytest.mark.allows_network  # loopback to a closed port: the "provider unreachable" path
class TestTheOptionalModelStage:
    """``--model`` is opt-in, and the demo is whole without it.

    The stage cannot be tested against a real model in CI, so what is tested
    is the property that makes it safe to ship: an absent model costs the
    reader the last stage and nothing else.
    """

    def test_the_default_says_no_model_and_no_network(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_demo()
        assert "No model. No network." in capsys.readouterr().out

    def test_a_model_that_is_not_running_does_not_fail_the_demo(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Port 1 on loopback: reserved, and nothing sane is bound to it. The
        # whole arrangement is that selection and verification are local and
        # deterministic and the model is one step at the end.
        import tsumugi.interfaces.cli.demo as demo_module

        original = demo_module.OllamaProvider
        # `monkeypatch` rather than assign-and-restore: rebinding a name that
        # is a class is something mypy refuses to express, and the `try` was
        # doing by hand what the fixture does by contract.
        monkeypatch.setattr(
            demo_module,
            "OllamaProvider",
            lambda **kwargs: original(url="http://127.0.0.1:1", timeout=5.0, **kwargs),
        )
        if True:
            assert run_demo(model="nothing-is-listening") == 0

        out = capsys.readouterr().out
        assert "Everything above still ran" in out
        # And every earlier stage is still there.
        assert "citations checked" in out and "left out" in out

    def test_it_says_where_it_would_send_before_it_sends(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tsumugi.interfaces.cli.demo as demo_module

        original = demo_module.OllamaProvider
        # `monkeypatch` rather than assign-and-restore: rebinding a name that
        # is a class is something mypy refuses to express, and the `try` was
        # doing by hand what the fixture does by contract.
        monkeypatch.setattr(
            demo_module,
            "OllamaProvider",
            lambda **kwargs: original(url="http://127.0.0.1:1", timeout=5.0, **kwargs),
        )
        if True:
            run_demo(model="nothing-is-listening")
        assert "sending to http://127.0.0.1:1 (local)" in capsys.readouterr().out
