"""Yggdrasil / world-tree splash for empty Conversations."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

# World-tree splash art (ejm) — Yggdrasil nod for Mimir.
TREE_ART = r"""
         # #### ####
       ### \/#|### |/####
      ##\/#/ \||/##/_/##/_#
    ###  \/###|/ \/ # ###
  ##_\_#\_\## | #/###_/_####
 ## #### # \ #| /  #### ##/##
  __#_--###`  |{,###---###-~
            \ }{
             }}{
             }}{
            {{}
       , -=-~{ .-^- _
             `}
              {
""".strip(
    "\n"
)

WELCOME = "Welcome to Mimir"
TIP = "Ask anything  ·  /help  ·  /new"
FLAVOR = "Wisdom drawn from the well beneath the world tree."


class Splash(Vertical):
    """Empty-session welcome with world-tree art."""

    DEFAULT_CSS = """
    Splash {
        height: auto;
        padding: 2 2 1 2;
        align: left middle;
    }
    Splash #splash-row {
        height: auto;
        align: left top;
    }
    Splash #tree {
        width: 48;
        color: #7cb342;
        text-style: bold;
    }
    Splash #splash-copy {
        width: 1fr;
        padding: 2 0 0 2;
    }
    Splash #welcome {
        color: #7cb342;
        text-style: bold;
        margin-bottom: 1;
    }
    Splash #tip {
        color: #9aa0a6;
    }
    Splash #flavor {
        color: #6b7280;
        text-style: italic;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="splash-row"):
            yield Static(TREE_ART, id="tree")
            with Vertical(id="splash-copy"):
                yield Static(WELCOME, id="welcome")
                yield Static(TIP, id="tip")
                yield Static(FLAVOR, id="flavor")
