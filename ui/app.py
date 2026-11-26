"""Waseet dashboard (Streamlit). Run from the project root:
    streamlit run ui/app.py
The screen holds no business logic: prices, roles and approval rules live in the API.
The LLM is not involved anywhere here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from ui import (  # noqa: E402
    accounts_page,
    api_client,
    bot_page,
    customers_page,
    kitchen_page,
    menu_page,
    orders_page,
    staff_page,
)
from ui.common import client, show_error  # noqa: E402

st.set_page_config(page_title="وسيط AI", page_icon="📦", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown("<style>[data-testid='stMainBlockContainer'], [data-testid='stSidebarContent'],"
            " [data-testid='stSidebarHeader'] {direction: rtl;}"
            "[data-testid='stAppDeployButton'] {display: none;}"
            "</style>", unsafe_allow_html=True)

PAGES = {
    "الطلبات": orders_page.orders_page,
    "المطبخ": kitchen_page.kitchen_page,
    "الحسابات": accounts_page.accounts_page,
    "المنيو": menu_page.menu_page,
    "العملاء": customers_page.customers_page,
    "البوت": bot_page.bot_page,
    "الموظفون": staff_page.staff_page,
}
# which pages each role sees (the API enforces the same limits again)
ROLE_PAGES = {
    "owner": list(PAGES),
    "manager": list(PAGES),
    "cashier": ["الطلبات", "العملاء"],
    "kitchen": ["المطبخ"],
}


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


def main() -> None:
    if "me" not in st.session_state:
        login_form()
        return
    me = st.session_state.me
    st.sidebar.write(f"**{me['business_name']}**\n\n{me['email']}")
    allowed = ROLE_PAGES.get(me["role"], [])
    if not allowed:
        st.info("مفيش صفحات متاحة لدورك في اللوحة دي.")
        page = None
    else:
        page = st.sidebar.radio("الصفحة", allowed)
    if st.sidebar.button("خروج"):
        st.session_state.clear()
        st.rerun()
    if page is None:
        return
    try:
        PAGES[page]()
    except api_client.ApiError as exc:
        show_error(exc)
        if exc.status == 401:
            st.rerun()


main()
