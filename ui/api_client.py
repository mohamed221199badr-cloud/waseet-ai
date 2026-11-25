"""Thin client for the Waseet API. The Streamlit screen talks to the backend ONLY through
this class (JWT bearer), so every rule (tenancy, exceptions, stock) stays on the server."""

import os

import httpx

DEFAULT_URL = "http://localhost:8000/api/v1"


def _default_url() -> str:
    """Env var first; on Streamlit Community Cloud the value lives in st.secrets."""
    url = os.environ.get("WASEET_API_URL")
    if url:
        return url
    try:
        import streamlit as st

        return str(st.secrets.get("WASEET_API_URL", DEFAULT_URL))
    except Exception:
        return DEFAULT_URL


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(resp: httpx.Response) -> str:
    try:
        d = resp.json().get("detail")
    except Exception:
        d = None
    if isinstance(d, list):  # FastAPI validation errors
        d = "; ".join(str(x.get("msg", x)) for x in d)
    return str(d or f"HTTP {resp.status_code}")


class ApiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 http: httpx.Client | None = None):
        self.base_url = (base_url or _default_url()).rstrip("/")
        self.token = token
        self._http = http or httpx.Client(timeout=15)

    def _req(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self._http.request(method, self.base_url + path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"تعذر الاتصال بالسيرفر ({type(exc).__name__})") from None
        if r.status_code >= 400:
            raise ApiError(r.status_code, _detail(r))
        return None if r.status_code == 204 else r.json()

    def login(self, business_slug: str, email: str, password: str) -> None:
        data = self._req("POST", "/auth/login", json={
            "business_slug": business_slug, "email": email, "password": password})
        self.token = data["access_token"]

    def me(self): return self._req("GET", "/auth/me")
    def customers(self): return self._req("GET", "/customers")
    def products(self): return self._req("GET", "/products")
    def orders(self, status: str): return self._req("GET", "/orders", params={"status": status})
    def submit(self, oid): return self._req("POST", f"/orders/{oid}/submit")
    def confirm(self, oid): return self._req("POST", f"/orders/{oid}/confirm")

    def reject(self, oid, reason: str | None = None):
        return self._req("POST", f"/orders/{oid}/reject", json={"reason": reason or None})

    def resolve(self, oid, eid, product_id, quantity: int | None = None):
        return self._req("POST", f"/orders/{oid}/exceptions/{eid}/resolve",
                         json={"product_id": product_id, "quantity": quantity})

    def dismiss(self, oid, eid): return self._req("POST", f"/orders/{oid}/exceptions/{eid}/dismiss")

    def new_link_code(self, customer_id):
        return self._req("POST", "/telegram/link-codes", json={"customer_id": customer_id})

    def links(self): return self._req("GET", "/telegram/links")
    def revoke_link(self, link_id): return self._req("DELETE", f"/telegram/links/{link_id}")
    def chat(self, customer_id, text):
        return self._req("POST", "/chat/message", json={"customer_id": customer_id, "text": text})

    def chat_reset(self, customer_id):
        return self._req("POST", "/chat/reset", json={"customer_id": customer_id, "text": "x"})
