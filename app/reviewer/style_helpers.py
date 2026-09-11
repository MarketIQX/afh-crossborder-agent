"""Escaping, in one place both the console and the views can import.

`esc` lived in `server.py`, which the view modules cannot import without
a cycle. Copying it would have been worse: two escape functions is one
escape function nobody maintains, and the one that drifts is the one
handling text a client typed.
"""

import html


def esc(value):
    return html.escape("" if value is None else str(value))
