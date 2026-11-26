"""Menu screen: items, availability, default price, new item, CSV import. The Telegram
bot reads this menu directly."""

import streamlit as st


def menu_page() -> None:
    api = st.session_state.api
    st.title("المنيو")
    tab_items, tab_import = st.tabs(["الأصناف", "استيراد CSV"])
    with tab_items:
        _items_tab(api)
    with tab_import:
        _import_tab(api)


def _items_tab(api) -> None:
    items = api.menu_items()
    if not items:
        st.info("مفيش أصناف لسه. ضيف أول صنف أو استورد ملف CSV.")
    for item in items:
        head = f"{item['category']} - {item['name']}"
        if not item["is_available"]:
            head += " (مش متاح)"
        with st.expander(head):
            st.caption(f"البيع: {item['sold_by']}" +
                       (f" بال{item['unit_label']}" if item.get("unit_label") else "") +
                       (f" - نوع: {item['kind']}" if item.get("kind") != "food" else ""))
            for v in item["variants"]:
                price = f"{v['price']} جنيه" if v["price"] is not None \
                    else "السعر عند الكاشير"
                default = " (الأساسي)" if v["is_default"] else ""
                st.write(f"- {v['name']}: {price}{default}")
            if item.get("aliases"):
                st.caption("أسماء تانية: " + "، ".join(a["alias"]
                                                       for a in item["aliases"]))
            col1, col2 = st.columns(2)
            if col1.button("متاح" if not item["is_available"] else "نفد",
                           key=f"avail-{item['id']}"):
                api.set_availability(item["id"], not item["is_available"])
                st.rerun()
            new_price = col2.text_input("سعر جديد للأساسي", key=f"price-{item['id']}")
            if col2.button("حفظ السعر", key=f"save-{item['id']}") and new_price:
                default = next(v for v in item["variants"] if v["is_default"])
                api.update_variant(item["id"], default["id"], price=new_price)
                st.rerun()
    with st.form("add_item"):
        st.subheader("صنف جديد")
        category = st.text_input("الفئة")
        name = st.text_input("الاسم")
        variant = st.text_input("اسم السعر/الحجم", value="سعر 1")
        price = st.text_input("السعر (فاضي = عند الكاشير)")
        sold_by = st.selectbox("البيع", ["unit", "weight"],
                               format_func=lambda x: "بالقطعة" if x == "unit" else "بالوزن")
        unit_label = st.text_input("وحدة الوزن (مثال: كيلو)")
        if st.form_submit_button("إضافة"):
            api.create_menu_item({
                "category": category, "name": name, "sold_by": sold_by,
                "unit_label": unit_label or None,
                "variants": [{"name": variant, "price": price or None}],
            })
            st.rerun()


def _import_tab(api) -> None:
    st.caption("ملف CSV بالأعمدة: category, item, variant, price, sold_by "
               "(واختياري: unit_label, kind, min_quantity, aliases, sort_order, "
               "description). التجربة أولاً مش بتكتب أي حاجة.")
    uploaded = st.file_uploader("ملف المنيو", type=["csv"])
    if uploaded is None:
        return
    text = uploaded.getvalue().decode("utf-8-sig")
    if st.button("تجربة الاستيراد (بدون حفظ)"):
        result = api.import_menu(text, dry_run=True)
        _show_import_result(result, "هيتحفظ")
        st.session_state["import_csv"] = text
    if "import_csv" in st.session_state and st.button("حفظ فعلي"):
        result = api.import_menu(st.session_state.pop("import_csv"), dry_run=False)
        _show_import_result(result, "اتحفظ")


def _show_import_result(result, verb) -> None:
    if result["errors"]:
        st.error(f"{len(result['errors'])} خطأ - لم يُحفظ شيء:")
        for e in result["errors"]:
            st.write(f"- سطر {e['row']}, عمود {e['column']}: {e['reason']}")
    else:
        st.success(f"{verb}: {result['created']} جديد، {result['updated']} متحدث، "
                   f"{result['unchanged']} بدون تغيير")
