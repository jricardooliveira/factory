"""The board's look: the design handoff's tokens as a Textual theme, and the shared TCSS.

Colour roles, never raw hex in the views: markup uses `$accent`, `$success`, `$error`,
`$warning`, `$dim`, `$bright`; panels, chips and buttons are styled here.
"""

from __future__ import annotations

from textual.theme import Theme

FACTORY_THEME = Theme(
    name="factory",
    primary="#6cc3e8",
    secondary="#4a5462",
    accent="#6cc3e8",
    warning="#e6c86e",
    error="#f08a7e",
    success="#7ccf8f",
    foreground="#c9d1d9",
    background="#0d1117",
    surface="#0d1117",
    panel="#151a21",
    dark=True,
    variables={
        "dim": "#6e7681",
        "bright": "#f0f3f6",
        "header-bg": "#1a2433",
        "footer-bg": "#151a21",
        "selected-bg": "#245a85",
        "chip-bg": "#262d36",
        "chip-off-bg": "#30363d",
        "chip-off-fg": "#e6edf3",
        "button-secondary-bg": "#4a5462",
        "ink": "#0d1117",
        "footer-key-foreground": "#6cc3e8",
        "block-cursor-background": "#245a85",
        "block-cursor-foreground": "#ffffff",
    },
)

BOARD_CSS = """
Screen { background: $background; }
#header, #lifecycle { height: 1; background: $header-bg; padding: 0 1; }
#tabs { height: 2; padding: 0 1; }
#feedback { height: 1; padding: 0 1; }
#keys { height: 1; background: $footer-bg; padding: 0 1; }
#views { height: 1fr; }

.panel { border: round $dim; border-title-color: $chip-off-fg; border-title-background: $chip-off-bg;
         border-title-style: bold; border-subtitle-color: $dim; padding: 0 1; }
.panel:focus-within { border: round $accent; border-title-color: $ink;
                      border-title-background: $accent; }
.panel .top { height: auto; }
.panel .body { height: 1fr; }
.panel .more { height: 1; color: $dim; text-align: right; }
.panel .actions { height: auto; border-top: solid $dim; padding-top: 0; }
.panel .actions Button { margin-right: 2; }
.actions .hint { width: 1fr; text-align: right; color: $dim; }

Button { min-width: 6; height: 1; border: none; background: $button-secondary-bg;
         color: #ffffff; text-style: bold; }
Button.-primary { background: $accent; color: $ink; }
Button:focus { text-style: bold reverse; }

OptionList { background: $background; border: none; padding: 0; }
OptionList:focus { border: none; }
OptionList > .option-list--option-highlighted { background: $selected-bg; color: #ffffff; }
OptionList:focus > .option-list--option-highlighted { background: $selected-bg; color: #ffffff; }
OptionList > .option-list--separator { color: $dim; }

TextArea { height: 4; border: round $accent; background: $background; }
Input { border: none; background: $chip-bg; height: 1; padding: 0 1; }
"""
