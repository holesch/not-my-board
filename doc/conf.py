# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import pathlib
import subprocess

project_dir = pathlib.Path(__file__).parents[1]

project = "not-my-board"
copyright = "2023-present, Simon Holesch"
author = "Simon Holesch"
release = subprocess.run(
    "scripts/get_version", cwd=project_dir, capture_output=True, text=True, check=True
).stdout.strip()

extensions = [
    "myst_parser",
    "sphinx_copybutton",
    "sphinxext.opengraph",
]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = f"{project} Documentation"

myst_enable_extensions = [
    "deflist",
]

# exclude prompts and output from copies
copybutton_exclude = ".linenos, .gp, .go"

# add search console verification
html_extra_path = ["googlef248cffc234a230d.html"]
