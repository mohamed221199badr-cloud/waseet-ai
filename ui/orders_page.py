"""Orders (الطلبات): every order the Telegram bot created, newest first. Pending ones
(a line with no price) can be confirmed or rejected here."""

import streamlit as st

from ui import api_client
from ui.common import METHOD_AR, STATUS_AR, client, day_picker, money, qty_text, show_error


def order_header(o) -> str:
    t = o["created_at"][11:]
    total = f" | {money(o['total'])} ج" if o.get("total") is not None else ""
    return (f"{STATUS_AR.get(o['status'], o['status'])} | #{o['short_id']} | "
            f"{o['customer_name']} | {METHOD_AR.get(o['method'], '')}{total} | {t}")


def lines_table(o) -> None:
    rows = []
    for ln in o["lines"]:
        row = {"الصنف": ln["item"], "الحجم": ln["variant"], "الكمية": qty_text(ln)}
        if ln.get("unit_price") is not None:
            row["السعر"] = money(ln["unit_price"])
            row["الإجمالي"] = money(ln["line_total"])
        rows.append(row)
    st.table(rows)


def orders_page() -> None:
    api = client()
    st.title("الطلبات")
    c1, c2 = st.columns([3, 2])
    with c1:
        day = day_picker("orders-day")
    with c2:
        status = st.selectbox("الحالة", ["الكل", "في انتظار الكاشير", "متأكد", "مرفوض"])
    code = {"في انتظار الكاشير": "pending", "متأكد": "confirmed", "مرفوض": "rejected"}.get(status)
    st.button("🔄 تحديث")
    orders = api.board_orders(day, code)
    pending = [o for o in orders if o["status"] == "pending"]
    if pending:
        st.warning(f"في {len(pending)} طلب مستني الكاشير (فيه صنف سعره عند الكاشير).")
    st.caption(f"{len(orders)} طلب")
    if not orders:
        st.info("مفيش طلبات في الفترة دي.")
    for o in orders:
        with st.expander(order_header(o), expanded=o["status"] == "pending"):
            if o.get("customer_phone"):
                st.write(f"📞 \u200e{o['customer_phone']}")
            if o["method"] == "delivery" and o["address"]:
                st.write(f"📍 {o['address']}")
            lines_table(o)
            if o.get("total") is not None:
                st.write(f"**إجمالي الطلب: {money(o['total'])} ج**")
            if o.get("rejection_reason"):
                st.caption(f"سبب الرفض: {o['rejection_reason']}")
            if o["status"] == "pending":
                st.info("التأكيد من هنا بيغيّر حالة الطلب بس، العميل مش بيتبلّغ تلقائي على تيليجرام.")
                a, b = st.columns(2)
                if a.button("✅ تأكيد", key=f"ok-{o['id']}", type="primary"):
                    _act(api.board_confirm, o["id"])
                reason = b.text_input("سبب الرفض (اختياري)", key=f"rr-{o['id']}")
                if b.button("❌ رفض", key=f"no-{o['id']}"):
                    _act(api.board_reject, o["id"], reason)


def _act(fn, *args) -> None:
    try:
        fn(*args)
        st.rerun()
    except api_client.ApiError as exc:
        show_error(exc)
