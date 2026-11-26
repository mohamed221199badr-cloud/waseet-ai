"""Accounts (الحسابات): confirmed-order totals for a day. Read-only."""

import streamlit as st

from ui.common import DAY_AR, client, day_picker, money


def accounts_page() -> None:
    st.title("الحسابات")
    day = day_picker("acc-day")
    s = client().board_summary(day)
    st.subheader(f"ملخص {DAY_AR[day]} (الطلبات المتأكدة بس)")
    a, b, c = st.columns(3)
    a.metric("عدد الطلبات", s["orders"])
    b.metric("إجمالي المبيعات (ج)", money(s["revenue"]))
    c.metric("متوسط الطلب (ج)", money(s["average"]))
    if s["pending"]:
        st.warning(f"في {s['pending']} طلب مستني الكاشير ومش محسوب.")
    st.subheader("استلام ودليفري")
    st.table([{"النوع": "استلام من المحل", "الطلبات": s["by_method"]["pickup"]["orders"],
               "الإجمالي (ج)": money(s["by_method"]["pickup"]["total"])},
              {"النوع": "دليفري", "الطلبات": s["by_method"]["delivery"]["orders"],
               "الإجمالي (ج)": money(s["by_method"]["delivery"]["total"])}])
    st.subheader("أكتر الأصناف مبيعاً")
    if s["top_items"]:
        st.table([{"الصنف": r["item"], "الكمية": r["qty"], "الإجمالي (ج)": money(r["total"])}
                  for r in s["top_items"]])
    else:
        st.info("مفيش مبيعات في الفترة دي.")
