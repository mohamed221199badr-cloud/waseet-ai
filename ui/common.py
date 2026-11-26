"""Small helpers shared by the dashboard pages."""

import importlib

import streamlit as st

from ui import api_client

# Streamlit Cloud can keep a pre-deploy copy of this module in memory.
if not hasattr(api_client.ApiClient, "board_orders"):  # pragma: no cover
    importlib.reload(api_client)

STATUS_AR = {"pending": "⏳ في انتظار الكاشير", "confirmed": "✅ متأكد", "rejected": "❌ مرفوض"}
METHOD_AR = {"delivery": "دليفري 🛵", "pickup": "استلام من المحل 🏪"}
DAY_AR = {"today": "النهارده", "yesterday": "امبارح", "all": "الكل"}


def client() -> api_client.ApiClient:
    if "api" not in st.session_state:
        st.session_state.api = api_client.ApiClient()
    return st.session_state.api


def show_error(exc: api_client.ApiError) -> None:
    if exc.status == 401:
        st.session_state.pop("me", None)
        client().token = None
        st.error("انتهت الجلسة. سجّل دخول تاني.")
    else:
        st.error(exc.detail)


def day_picker(key: str, default: str = "today") -> str:
    labels = list(DAY_AR.values())
    picked = st.radio("اليوم", labels, index=list(DAY_AR).index(default), horizontal=True,
                      key=key)
    return {v: k for k, v in DAY_AR.items()}[picked]


def money(v) -> str:
    return f"{float(v):,.2f}".rstrip("0").rstrip(".") if v is not None else "-"


def qty_text(line) -> str:
    return f"{line['quantity']}" + (f" {line['unit']}" if line.get("unit") else "")
