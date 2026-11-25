"""Staff screen (Phase 1, F7): the manager enters staff names himself within his
limited scope; the owner sees everything. Rules are enforced server-side."""

import streamlit as st

ROLE_AR = {"owner": "مالك", "manager": "مدير", "cashier": "كاشير",
           "kitchen": "مطبخ", "storekeeper": "أمين مخزن", "delivery": "دليفري"}


def staff_page() -> None:
    api = st.session_state.api
    st.title("الموظفون")
    for u in api.staff():
        line = f"{u['full_name']} - {ROLE_AR.get(u['role'], u['role'])} - {u['email']}"
        if not u["is_active"]:
            line += " (موقوف)"
        col1, col2 = st.columns([4, 1])
        col1.write(line)
        if u["is_active"] and col2.button("إيقاف", key=f"deact-{u['id']}"):
            api.deactivate_staff(u["id"])
            st.rerun()
    with st.form("add_staff"):
        st.subheader("موظف جديد")
        full_name = st.text_input("الاسم")
        email = st.text_input("الإيميل")
        role = st.selectbox("الدور", list(ROLE_AR), format_func=ROLE_AR.get)
        password = st.text_input("باسورد مؤقت (هيغيّره عند أول دخول)", type="password")
        if st.form_submit_button("إضافة"):
            api.create_staff({"full_name": full_name, "email": email, "role": role,
                              "password": password})
            st.rerun()
