"""
Entry point for Streamlit Cloud.
Delegates to the real application in Scripts/Streamlit Cloud/streamlit_app.py.
"""
import os
import sys

# Absolute path to the real application
_real_app = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Scripts", "Streamlit Cloud", "streamlit_app.py",
)
_app_dir = os.path.dirname(_real_app)

# Make imports from the app directory work (dossiers_api, etc.)
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

# Run the real app — pass correct __file__ so os.path.abspath(__file__)
# resolves Logo_NCG.png and URL_SP_demo.txt relative to the right folder
with open(_real_app, encoding="utf-8") as _f:
    exec(compile(_f.read(), _real_app, "exec"),  # noqa: S102
         {"__file__": _real_app, "__name__": "__main__"})
