from sphinx.writers.html5 import HTML5Translator

project = "Memchentic Docs"
copyright = "2026, Memchentic"
author = "Memchentic"

extensions = [
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
myst_heading_anchors = 3
myst_enable_extensions = ["dollarmath"]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_show_sphinx = False
html_title = "Memchentic Docs"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["sidebar-scroll.js"]
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 5,
    "logo_only": True,
    # The theme scrolls the menu by the same amount as the page.
    "sticky_navigation": False,
}

pygments_style = "native"


class ExternalTabHTMLTranslator(HTML5Translator):
    """Open external http(s) links in a new tab."""

    def visit_reference(self, node):
        uri = node.get("refuri") or ""
        if uri.startswith(("http://", "https://")):
            node["target"] = "_blank"
            node["rel"] = "noopener noreferrer"
        super().visit_reference(node)


def setup(app):
    app.set_translator("html", ExternalTabHTMLTranslator, override=True)
