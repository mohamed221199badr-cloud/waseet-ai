"""Kitchen (المطبخ): today's confirmed orders, items only. No prices, no phone."""

import streamlit as st

from ui.common import METHOD_AR, client, qty_text


def kitchen_page() -> None:
    st.title("المطبخ")
    st.button("🔄 تحديث")
    orders = client().board_orders("today", "confirmed")
    st.caption(f"{len(orders)} طلب النهارده")
    if not orders:
        st.info("مفيش طلبات للتجهيز دلوقتي.")
    for o in orders:
        with st.container(border=True):
            st.markdown(f"**#{o['short_id']}** | {o['created_at'][11:]} | "
                        f"{METHOD_AR.get(o['method'], '')}")
            for ln in o["lines"]:
                name = f"{ln['item']} ({ln['variant']})"
                st.write(f"• {name} × {qty_text(ln)}")
