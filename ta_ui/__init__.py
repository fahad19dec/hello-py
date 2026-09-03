"""Streamlit UI for the TauricResearch/TradingAgents framework.

The package holds everything the Streamlit pages need: the provider/model
catalog, persisted user settings, the background run engine, and the report
layout. Pages stay thin so the logic here can be tested without Streamlit.
"""

__all__ = ["catalog", "settings", "engine", "sections"]
