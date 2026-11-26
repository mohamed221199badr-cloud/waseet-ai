"""Customers (العملاء): who ordered, how often, last order."""

import streamlit as st

from ui.common import client, money


def customers_page() -> None:
    st.title("العملاء")
    rows = client().board_customers()
    st.caption(f"{len(rows)} عميل")
    if not rows:
        st.info("مفيش عملاء لسه.")
        return
    st.dataframe([{
        "الاسم": r["name"], "التليفون": r["phone"] or "-", "الطلبات": r["orders"],
        "إجمالي المشتريات (ج)": money(r["spent"]), "آخر طلب": r["last_order"] or "-",
        "العنوان": r["address"] or "-"} for r in rows], use_container_width=True,
        hide_index=True)
