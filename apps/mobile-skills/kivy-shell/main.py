"""Kivy shell scaffold: run a pure-Python Lightwork skill on a phone.

Lists the bundled pure skills (today: one — ``disagreement.answer_entropy``,
the proposer-disagreement signal from ``packages/maverick-core/maverick/
disagreement.py``, chosen because it imports nothing but stdlib) and runs it
on sample fan-outs.

The module is loaded by file path with importlib, no package context needed:
it has zero intra-package imports. From a repo checkout the path resolves
into packages/; in a buildozer-built APK it must be copied next to main.py
first (see buildozer.spec and the README — packaging is a maintainer act).

Honest fallback: if Kivy is not installed (e.g. in this repo's test
environment), running ``python main.py`` executes the skill in the terminal
instead of crashing, so the scaffold stays exercisable everywhere.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BUNDLED_MODULE = _HERE / "disagreement.py"                      # APK layout
_REPO_MODULE = (                                                  # checkout layout
    _HERE.parents[2] / "packages" / "maverick-core" / "maverick" / "disagreement.py"
)

SAMPLES = {
    "all identical": ["paris", "paris", "paris"],
    "one dissenter": ["paris", "paris", "lyon"],
    "all unique": ["paris", "lyon", "nice"],
}


def load_skill():
    """Load the pure skill module by file path (no maverick install needed)."""
    for candidate in (_BUNDLED_MODULE, _REPO_MODULE):
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("disagreement", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(
        "disagreement.py not found. From a checkout, run from the repo; for "
        "an APK, copy packages/maverick-core/maverick/disagreement.py next "
        "to main.py before `buildozer android debug` (see README.md)."
    )


def _verdict(score: float) -> str:
    if score <= 0.0005:
        return "they all agree"
    if score >= 0.9995:
        return "every answer is different"
    return "they partly disagree"


def plain_results() -> str:
    """The same comparison, worded for people: answers, a percentage, a verdict."""
    skill = load_skill()
    lines = []
    for answers in SAMPLES.values():
        score = max(0.0, skill.answer_entropy(answers))
        shown = ", ".join(answer.title() for answer in answers)
        lines.append(f"{shown}\n{score * 100:.0f}% different: {_verdict(score)}")
    lines.append("0% means all answers agree. 100% means every answer is different.")
    return "\n\n".join(lines)


def run_skill() -> str:
    skill = load_skill()
    lines = [
        f"{name:>14}: entropy={skill.answer_entropy(answers):.3f}"
        for name, answers in SAMPLES.items()
    ]
    lines.append("")
    lines.append("0 = proposers agree, 1 = every answer unique.")
    return "\n".join(lines)


def main() -> None:
    try:
        from kivy.app import App
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
    except ImportError:
        print("kivy is not installed; running the skill in the terminal instead.\n")
        print(run_skill())
        return

    class MaverickSkillsApp(App):
        title = "Lightwork Skills"

        def build(self):
            root = BoxLayout(orientation="vertical", padding=24, spacing=12)
            root.add_widget(Label(
                text="Compare sample answers\nA preview of how Lightwork measures disagreement.",
                size_hint_y=None, height=80,
            ))
            output = Label(
                text="Tap Compare sample answers to see how much three sets of answers differ.",
                halign="center", valign="middle",
            )
            output.bind(size=lambda label, size: setattr(label, "text_size", size))
            diagnostic = ""

            def on_run(_button):
                nonlocal diagnostic
                try:
                    output.text = plain_results()
                    details_button.disabled = True
                except FileNotFoundError as exc:
                    diagnostic = str(exc)
                    output.text = (
                        "This demo is not set up on this phone yet. Ask your administrator to finish "
                        "setting it up. They can tap Setup details for what is missing."
                    )
                    details_button.disabled = False

            run_button = Button(
                text="Compare sample answers", size_hint_y=None, height=56,
            )
            run_button.bind(on_press=on_run)
            root.add_widget(run_button)
            root.add_widget(output)
            # Only enabled after a setup problem; the text is for an administrator.
            details_button = Button(text="Setup details", size_hint_y=None, height=48, disabled=True)
            details_button.bind(on_press=lambda _button: setattr(output, "text", diagnostic))
            root.add_widget(details_button)
            return root

    MaverickSkillsApp().run()


if __name__ == "__main__":
    main()
