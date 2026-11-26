"""Bot (البوت): the Telegram chats linked to the shop. The owner chat receives the
cashier, kitchen and accounts notices, so it must be marked as owner."""

import streamlit as st

from ui import api_client
from ui.common import client, show_error


def bot_page() -> None:
    api = client()
    me = st.session_state.me
    st.title("البوت")
    st.caption("المحادثات المربوطة بتيليجرام. محادثة المالك بتستلم إشعارات الكاشير "
               "والمطبخ والحسابات.")
    links = api.links()
    if not links:
        st.info("مفيش محادثات مربوطة لسه.")
    for ln in links:
        a, b, c = st.columns([4, 2, 2])
        a.write(f"{ln['customer_name']}" + ("  👑 مالك" if ln.get("is_owner") else ""))
        if me["role"] == "owner" and not ln.get("is_owner") and b.button(
                "تحديد كمالك", key=f"own-{ln['id']}"):
            _act(api.mark_owner, ln["id"])
        if c.button("فك الربط", key=f"del-{ln['id']}"):
            _act(api.revoke_link, ln["id"])


def _act(fn, *args) -> None:
    try:
        fn(*args)
        st.rerun()
    except api_client.ApiError as exc:
        show_error(exc)
