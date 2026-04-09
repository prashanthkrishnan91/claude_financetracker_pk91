"""
Portfolio War Room v13.1 — Streamlit Cloud Entry Point

All v1 source code lives in v1/. This shim ensures Streamlit Cloud
compatibility without any changes to the deployment configuration.
The main file path in Streamlit Cloud remains "App.py".
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.join(_HERE, "v1")

# Ensure v1/ modules are importable (data_engine, price_service, etc.)
if _V1 not in sys.path:
    sys.path.insert(0, _V1)

# Execute the actual v1 application in this module's namespace
# so that Streamlit sees all st.* calls at the top level.
with open(os.path.join(_V1, "App.py"), encoding="utf-8") as _f:
    exec(compile(_f.read(), os.path.join(_V1, "App.py"), "exec"))
