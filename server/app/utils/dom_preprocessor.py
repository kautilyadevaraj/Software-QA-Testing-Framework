"""DOM / HTML preprocessing utilities.

Strips comments, script/style blocks, and collapses whitespace before HTML
content is passed to LLM agents. This significantly reduces token consumption
without losing meaningful DOM structure.
"""
from __future__ import annotations

import re


def minify_html(html: str) -> str:
    """Return a token-efficient version of *html*.

    Transforms applied (in order):
    1. Strip HTML comments  <!-- ... -->
    2. Remove <script> ... </script> blocks (contents not needed for selectors)
    3. Remove <style> ... </style> blocks
    4. Collapse any run of whitespace (spaces, tabs, newlines) to a single space
    5. Strip leading / trailing whitespace from the result
    """
    # 1. HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # 2. <script> blocks
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 3. <style> blocks
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 4. Collapse whitespace
    html = re.sub(r"\s+", " ", html)

    return html.strip()
