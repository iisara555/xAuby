"""Small centered modal screens for editing Quick Config values."""

from __future__ import annotations

from typing import List, Optional, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option


class NumberModal(ModalScreen[Optional[float]]):
    """Numeric editor with range validation. Dismisses the value, or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, current, *, minimum=None, maximum=None,
                 is_int: bool = False, unit: str = "") -> None:
        super().__init__()
        self._title = title
        self._current = current
        self._min = minimum
        self._max = maximum
        self._is_int = is_int
        self._unit = unit

    def compose(self) -> ComposeResult:
        with Vertical(id="qc-modal-box"):
            yield Static(Text(self._title, style="bold"), id="qc-modal-title")
            if self._min is not None and self._max is not None:
                rng = f"Range: {self._min}–{self._max}{self._unit}"
            else:
                rng = "Enter a number"
            yield Static(rng, id="qc-modal-hint")
            yield Input(value=str(self._current), id="qc-modal-input")
            with Horizontal(id="qc-modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def _submit(self) -> None:
        raw = self.query_one(Input).value.strip()
        if not raw:
            self.dismiss(None)
            return
        try:
            value = int(raw) if self._is_int else float(raw)
        except ValueError:
            self.notify("Invalid number", severity="error")
            return
        if (self._min is not None and value < self._min) or (
            self._max is not None and value > self._max
        ):
            self.notify(f"Use {self._min}–{self._max}", severity="error")
            return
        self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceModal(ModalScreen[Optional[str]]):
    """Pick one option from a list. Dismisses the chosen id, or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, choices: List[Tuple[str, str]], current=None) -> None:
        super().__init__()
        self._title = title
        self._choices = choices
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="qc-modal-box"):
            yield Static(Text(self._title, style="bold"), id="qc-modal-title")
            options = []
            for cid, label in self._choices:
                mark = "● " if cid == self._current else "  "
                options.append(Option(f"{mark}{label}", id=cid))
            yield OptionList(*options, id="qc-choice-list")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.focus()
        for i, (cid, _) in enumerate(self._choices):
            if cid == self._current:
                ol.highlighted = i
                break

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextModal(ModalScreen[Optional[str]]):
    """Single-line text entry (optionally masked). Dismisses the text, or None.

    An empty submission is treated as "no change" (dismisses None).
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, current_display: str = "", *, secret: bool = False) -> None:
        super().__init__()
        self._title = title
        self._current_display = current_display
        self._secret = secret

    def compose(self) -> ComposeResult:
        with Vertical(id="qc-modal-box"):
            yield Static(Text(self._title, style="bold"), id="qc-modal-title")
            yield Static(f"Current: {self._current_display}", id="qc-modal-hint")
            yield Input(password=self._secret, id="qc-modal-input")
            with Horizontal(id="qc-modal-buttons"):
                yield Button("Save", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def _submit(self) -> None:
        value = self.query_one(Input).value.strip()
        self.dismiss(value or None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses True/False."""

    BINDINGS = [
        Binding("escape,n", "no", "No", show=False),
        Binding("y", "yes", "Yes", show=False),
    ]

    def __init__(self, message: str, *, confirm_label: str = "Yes",
                 cancel_label: str = "No") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="qc-modal-box"):
            yield Static(self._message, id="qc-modal-title")
            with Horizontal(id="qc-modal-buttons"):
                yield Button(self._confirm_label, variant="primary", id="yes")
                yield Button(self._cancel_label, id="no")

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
