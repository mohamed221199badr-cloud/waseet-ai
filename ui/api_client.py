"""Thin client for the Waseet API. The Streamlit screen talks to the backend ONLY through
this class (JWT bearer), so every rule (tenancy, roles, prices) stays on the server."""

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


BOARD_API = True  # marker: common.py reloads a stale pre-deploy copy without it


class ApiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 http: httpx.Client | None = None):
        self.base_url = (base_url or _default_url()).rstrip("/")
        self.token = token
        self.refresh_token: str | None = None
        self._http = http or httpx.Client(timeout=15)

    def _req(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self._http.request(method, self.base_url + path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"تعذر الاتصال بالسيرفر ({type(exc).__name__})") from None
        if r.status_code == 401 and self.refresh_token and path != "/auth/refresh":
            # access tokens are short-lived (Phase 1): renew once and retry
            try:
                data = self._http.request("POST", self.base_url + "/auth/refresh",
                                          json={"refresh_token": self.refresh_token})
                if data.status_code == 200:
                    body = data.json()
                    self.token = body["access_token"]
                    self.refresh_token = body["refresh_token"]
                    headers["Authorization"] = f"Bearer {self.token}"
                    r = self._http.request(method, self.base_url + path,
                                           headers=headers, **kw)
            except httpx.HTTPError:
                pass
        if r.status_code >= 400:
            raise ApiError(r.status_code, _detail(r))
        return None if r.status_code == 204 else r.json()

    def login(self, business_slug: str, email: str, password: str) -> None:
        data = self._req("POST", "/auth/login", json={
            "business_slug": business_slug, "email": email, "password": password})
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token")

    def me(self): return self._req("GET", "/auth/me")
    # dashboard v2: the orders the Telegram bot creates
    def board_orders(self, day: str = "today", status: str | None = None):
        params = {"day": day, **({"status": status} if status else {})}
        return self._req("GET", "/board/orders", params=params)

    def board_summary(self, day: str = "today"):
        return self._req("GET", "/board/summary", params={"day": day})

    def board_customers(self): return self._req("GET", "/board/customers")
    def board_confirm(self, oid): return self._req("POST", f"/board/orders/{oid}/confirm")

    def board_reject(self, oid, reason: str | None = None):
        return self._req("POST", f"/board/orders/{oid}/reject", json={"reason": reason or None})

    def links(self): return self._req("GET", "/telegram/links")
    def revoke_link(self, link_id): return self._req("DELETE", f"/telegram/links/{link_id}")
    def mark_owner(self, link_id): return self._req("POST", f"/telegram/links/{link_id}/owner")

    # Phase 1: menu + staff
    def menu_items(self): return self._req("GET", "/menu/items")

    def create_menu_item(self, body: dict): return self._req("POST", "/menu/items", json=body)

    def set_availability(self, item_id, is_available: bool):
        return self._req("POST", f"/menu/items/{item_id}/availability",
                         json={"is_available": is_available})

    def update_variant(self, item_id, variant_id, **changes):
        return self._req("PATCH", f"/menu/items/{item_id}/variants/{variant_id}",
                         json=changes)

    def import_menu(self, csv_text: str, dry_run: bool = True):
        return self._req("POST", "/menu/import",
                         json={"csv_text": csv_text, "dry_run": dry_run})

    def staff(self): return self._req("GET", "/staff")
    def create_staff(self, body: dict): return self._req("POST", "/staff", json=body)
    def deactivate_staff(self, staff_id): return self._req("POST", f"/staff/{staff_id}/deactivate")
