"""Exceptions screen (Streamlit). Run from the project root:
    streamlit run ui/app.py
The screen holds no business logic: prices, stock and approval rules live in the API.
The LLM is not involved anywhere here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from ui import api_client, menu_page, staff_page  # noqa: E402

REASONS = {
    "not_found": "المنتج مش موجود في الكتالوج",
    "ambiguous": "أكتر من منتج محتمل",
    "missing_quantity": "الكمية ناقصة",
    "unclear_quantity": "الكمية مش واضحة",
    "duplicate_product": "نفس المنتج مذكور مرتين",
}
STATUS_AR = {"draft": "مسودة", "pending": "في انتظار الاعتماد",
             "confirmed": "معتمد", "rejected": "مرفوض"}
CHANNEL_AR = {"whatsapp": "واتساب", "telegram": "تليجرام", "manual": "يدوي"}

st.set_page_config(page_title="وسيط AI", page_icon="📦", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>[data-testid='stMainBlockContainer'], [data-testid='stSidebarContent'],"
            " [data-testid='stSidebarHeader'] {direction: rtl;}"
            "[data-testid='stAppDeployButton'] {display: none;}"
            "</style>", unsafe_allow_html=True)


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


def login_form() -> None:
    st.title("وسيط AI: تسجيل الدخول")
    with st.form("login"):
        slug = st.text_input("معرّف الشركة (slug)")
        email = st.text_input("الإيميل")
        password = st.text_input("كلمة السر", type="password")
        if st.form_submit_button("دخول"):
            try:
                client().login(slug.strip(), email.strip(), password)
                st.session_state.me = client().me()
                st.rerun()
            except api_client.ApiError as exc:
                st.error("بيانات الدخول غير صحيحة" if exc.status == 401 else exc.detail)


def lookups():
    c = client()
    customers = {x["id"]: x["name"] for x in c.customers()}
    products = {x["id"]: x for x in c.products()}
    return customers, products


def money(v) -> str:
    return f"{float(v):,.2f}"


def order_header(o, customers) -> str:
    channel = CHANNEL_AR.get(o['channel'], o['channel'])
    return (f"{customers.get(o['customer_id'], '؟')} | {STATUS_AR.get(o['status'], o['status'])}"
            f" | {channel} | {o['created_at'][:16].replace('T', ' ')}")


def items_table(o, products) -> None:
    if not o["items"]:
        st.caption("لا توجد بنود محسومة بعد")
        return
    rows = [{"المنتج": products.get(i["product_id"], {}).get("name", "؟"),
             "الكمية": i["quantity"], "سعر الوحدة": money(i["unit_price"]),
             "الإجمالي": money(i["line_total"])} for i in o["items"]]
    st.table(rows)
    st.write(f"**إجمالي الطلب: {money(o['total'])}**")


def exception_box(o, e, products) -> None:
    c = client()
    with st.container(border=True):
        st.markdown(f"**السطر:** {e['raw_text']}")
        st.caption(f"السبب: {REASONS.get(e['reason'], e['reason'])}")
        options = {x["product_id"]: f"{x['name']} ({x['sku']}) | تطابق {x['score']:.0f}"
                   for x in e["candidates"] if "name" in x}
        if e["candidates"] and not options:  # candidate saved by id only
            options = {x["product_id"]: products.get(x["product_id"], {}).get("name", "؟")
                       for x in e["candidates"]}
        if not options:
            st.info("مفيش مرشحين. اختار منتج من القائمة الكاملة.")
        use_all = st.checkbox("اختار من كل المنتجات", key=f"all-{e['id']}",
                              value=not options)
        if use_all:
            options = {pid: f"{p['name']} ({p['sku']})" for pid, p in products.items()
                       if p.get("is_active", True)}
        by_label = {label: pid for pid, label in options.items()}
        picked = st.selectbox("المنتج", list(by_label), key=f"p-{e['id']}", index=None,
                              placeholder="اختار منتج")
        choice = by_label.get(picked)
        qty = st.number_input("الكمية", min_value=1, step=1, value=e["quantity"] or 1,
                              key=f"q-{e['id']}", disabled=e["quantity"] is not None)
        a, b = st.columns(2)
        if a.button("حسم بالمنتج ده", key=f"r-{e['id']}", disabled=choice is None):
            try:
                c.resolve(o["id"], e["id"], choice, None if e["quantity"] else int(qty))
                st.rerun()
            except api_client.ApiError as exc:
                show_error(exc)
        note = st.text_input("سبب التجاهل", key=f"dn-{e['id']}",
                             placeholder="مثال: مش سطر أوردر")
        if b.button("تجاهل السطر", key=f"d-{e['id']}", disabled=not note.strip()):
            try:
                c.dismiss(o["id"], e["id"], note.strip())
                st.rerun()
            except api_client.ApiError as exc:
                show_error(exc)


def drafts_page(customers, products) -> None:
    c = client()
    orders = c.orders("draft")
    st.subheader(f"المسودات ({len(orders)})")
    if not orders:
        st.info("مفيش مسودات.")
    for o in orders:
        open_exc = [e for e in o["exceptions"] if e["status"] == "open"]
        label = order_header(o, customers) + (f" | استثناءات مفتوحة: {len(open_exc)}"
                                              if open_exc else "")
        with st.expander(label, expanded=bool(open_exc)):
            if o["source_text"]:
                st.text_area("النص الأصلي", o["source_text"], disabled=True, key=f"src-{o['id']}")
            items_table(o, products)
            for e in open_exc:
                exception_box(o, e, products)
            closed = [e for e in o["exceptions"] if e["status"] != "open"]
            if closed:
                st.caption("محسومة: " + "، ".join(
                    f"{e['raw_text']} ({'اتحسم' if e['status'] == 'resolved' else 'اتجاهل'})"
                    for e in closed))
            if st.button("تقديم للاعتماد", key=f"s-{o['id']}",
                         disabled=bool(open_exc) or not o["items"],
                         help="لازم كل الاستثناءات تتحسم ويكون فيه بند واحد على الأقل"):
                try:
                    c.submit(o["id"])
                    st.rerun()
                except api_client.ApiError as exc:
                    show_error(exc)


def pending_page(customers, products) -> None:
    c = client()
    orders = c.orders("pending")
    st.subheader(f"في انتظار الاعتماد ({len(orders)})")
    if not orders:
        st.info("مفيش طلبات في الانتظار.")
    for o in orders:
        with st.expander(order_header(o, customers), expanded=True):
            items_table(o, products)
            st.warning("الاعتماد بيخصم المخزون فورًا ومينفعش يتراجع منه.")
            a, b = st.columns(2)
            if a.button("اعتماد", key=f"c-{o['id']}", type="primary"):
                try:
                    c.confirm(o["id"])
                    st.success("تم الاعتماد وخصم المخزون")
                    st.rerun()
                except api_client.ApiError as exc:
                    show_error(exc)  # e.g. 409 insufficient stock: nothing was deducted
            reason = b.text_input("سبب الرفض (اختياري)", key=f"rr-{o['id']}")
            if b.button("رفض", key=f"x-{o['id']}"):
                try:
                    c.reject(o["id"], reason)
                    st.rerun()
                except api_client.ApiError as exc:
                    show_error(exc)


def history_page(customers, products) -> None:
    c = client()
    for status in ("confirmed", "rejected"):
        orders = c.orders(status)
        st.subheader(f"{STATUS_AR[status]} ({len(orders)})")
        for o in orders[:30]:
            with st.expander(order_header(o, customers)):
                items_table(o, products)
                if o["rejection_reason"]:
                    st.caption(f"سبب الرفض: {o['rejection_reason']}")


def telegram_page(customers, products) -> None:
    c = client()
    st.subheader("ربط عملاء تيليجرام")
    st.caption("اختار العميل واعمل كود. العميل يبعته للبوت بالشكل: /start الكود. "
               "الكود يشتغل مرة واحدة وينتهي بعد 24 ساعة.")
    by_name = {f"{name} ({cid[:6]})": cid for cid, name in customers.items()}
    picked = st.selectbox("العميل", list(by_name), index=None, placeholder="اختار عميل")
    cid = by_name.get(picked)
    if st.button("إنشاء كود", disabled=cid is None):
        try:
            st.session_state.last_code = c.new_link_code(cid)
        except api_client.ApiError as exc:
            show_error(exc)
    if "last_code" in st.session_state:
        lc = st.session_state.last_code
        st.success("الكود اتعمل. ابعته للعميل، يشتغل مرة واحدة وينتهي بعد 24 ساعة.")
        st.code(f"/start {lc['code']}", language=None)
    st.divider()
    for link in c.links():
        a, b = st.columns([4, 1])
        a.write(f"{link['customer_name']} | اتربط {link['created_at'][:10]}")
        if b.button("فصل", key=f"u-{link['id']}"):
            try:
                c.revoke_link(link["id"])
                st.rerun()
            except api_client.ApiError as exc:
                show_error(exc)


def chat_page(customers, products) -> None:
    c = client()
    st.subheader("جرّب وكيل المحادثة (بدون تيليجرام)")
    st.caption("بتتكلم كأنك العميل. نفس منطق بوت تيليجرام بالظبط. "
               "الطلب بيتسجل مسودة تظهر في «المسودات».")
    by_name = {f"{name} ({cid[:6]})": cid for cid, name in customers.items()}
    picked = st.selectbox("تتكلم كأنك العميل", list(by_name), index=0)
    cid = by_name[picked]
    key = f"chat-{cid}"
    if key not in st.session_state:
        st.session_state[key] = [("bot", "أهلاً! ابعت طلبك، مثال: ٣ سكر وكرتونتين زيت")]
    if st.button("محادثة جديدة"):
        c.chat_reset(cid)
        st.session_state.pop(key, None)
        st.rerun()
    for who, text in st.session_state[key]:
        with st.chat_message("user" if who == "me" else "assistant"):
            st.text(text)
    msg = st.chat_input("اكتب رسالتك للبوت")
    if msg:
        st.session_state[key].append(("me", msg))
        st.session_state[key].append(("bot", c.chat(cid, msg)["reply"]))
        st.rerun()


def main() -> None:
    if "me" not in st.session_state:
        login_form()
        return
    me = st.session_state.me
    st.sidebar.write(f"**{me['business_name']}**\n\n{me['email']}")
    page = st.sidebar.radio(
        "الصفحة", ["المسودات", "في انتظار الاعتماد", "السجل", "تيليجرام", "جرّب البوت",
                   "المنيو", "الموظفون"])
    if st.sidebar.button("خروج"):
        st.session_state.clear()
        st.rerun()
    try:
        if page == "المنيو":
            menu_page.menu_page()
            return
        if page == "الموظفون":
            staff_page.staff_page()
            return
        customers, products = lookups()
        {"المسودات": drafts_page, "في انتظار الاعتماد": pending_page,
         "السجل": history_page, "تيليجرام": telegram_page,
         "جرّب البوت": chat_page}[page](customers, products)
    except api_client.ApiError as exc:
        show_error(exc)
        if exc.status == 401:
            st.rerun()


main()
