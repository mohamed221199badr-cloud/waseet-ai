"""Roles and permissions (proposal v3, F1). This module is the ONLY place that maps a
role to what it may do. Routes and services ask `role_has()` / `require()`; nobody
compares role names directly.

`system` is an internal actor (draft expiry, later outbox/housekeeping), not one of the
six staff roles, and it can never log in (no password, is_active=false).
"""

from enum import Enum


class Permission(str, Enum):
    ORDER_VIEW_ALL = "order.view_all"
    ORDER_VIEW_ASSIGNED = "order.view_assigned"  # own assigned deliveries only
    ORDER_CREATE_MANUAL = "order.create_manual"
    ORDER_EDIT_LINES = "order.edit_lines"  # qty, remove, notes, resolve/dismiss exceptions
    ORDER_SET_LINE_PRICE = "order.set_line_price"  # only when price_source -> cashier_manual
    ORDER_CONFIRM = "order.confirm"
    ORDER_REJECT = "order.reject"
    ORDER_CANCEL_AFTER_CONFIRM = "order.cancel_after_confirm"  # reverses stock
    ORDER_ASSIGN_DRIVER = "order.assign_driver"
    ORDER_ADVANCE_PREPARATION = "order.advance_preparation"  # confirmed->preparing->ready
    ORDER_ADJUST_WEIGHT = "order.adjust_weight"  # confirmed..preparing only
    ORDER_RECONFIRM_TOTAL = "order.reconfirm_total"
    ORDER_ADVANCE_DELIVERY = "order.advance_delivery"  # out_for_delivery/delivered/failed
    PAYMENT_COLLECT = "payment.collect"
    PAYMENT_RECONCILE = "payment.reconcile"
    PAYMENT_REFUND = "payment.refund"  # up to the refund limit
    PAYMENT_REFUND_LARGE = "payment.refund_large"  # above the limit
    MENU_MANAGE = "menu.manage"
    MENU_TOGGLE_AVAILABILITY = "menu.toggle_availability"
    PRICE_MANAGE = "price.manage"
    INVENTORY_VIEW = "inventory.view"
    LEDGER_VIEW = "ledger.view"
    INVENTORY_MANAGE = "inventory.manage"
    CRM_VIEW = "crm.view"
    CRM_MANAGE = "crm.manage"
    REPORT_VIEW = "report.view"
    STAFF_MANAGE = "staff.manage"  # full: any role, including owner
    STAFF_MANAGE_LIMITED = "staff.manage_limited"  # non-privileged staff only (F7)
    SETTINGS_MANAGE = "settings.manage"
    NOTIFICATION_MANAGE = "notification.manage"
    AUDIT_VIEW = "audit.view"


P = Permission

# The six staff roles the owner talked about, plus the internal system actor.
STAFF_ROLES = ("owner", "manager", "cashier", "kitchen", "storekeeper", "delivery")
SYSTEM_ROLE = "system"
ALL_ROLES = STAFF_ROLES + (SYSTEM_ROLE,)

# Roles a STAFF_MANAGE_LIMITED holder (the manager, F7) may create/edit/deactivate.
LIMITED_STAFF_ROLES = ("cashier", "kitchen", "storekeeper", "delivery")

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "owner": frozenset(Permission),
    "manager": frozenset({
        P.ORDER_VIEW_ALL, P.ORDER_CREATE_MANUAL, P.ORDER_EDIT_LINES, P.ORDER_SET_LINE_PRICE,
        P.ORDER_CONFIRM, P.ORDER_REJECT, P.ORDER_CANCEL_AFTER_CONFIRM, P.ORDER_ASSIGN_DRIVER,
        P.ORDER_RECONFIRM_TOTAL, P.PAYMENT_COLLECT, P.PAYMENT_RECONCILE, P.PAYMENT_REFUND,
        P.PAYMENT_REFUND_LARGE, P.MENU_MANAGE, P.MENU_TOGGLE_AVAILABILITY, P.PRICE_MANAGE,
        P.INVENTORY_VIEW, P.LEDGER_VIEW, P.INVENTORY_MANAGE, P.CRM_VIEW, P.CRM_MANAGE,
        P.REPORT_VIEW, P.NOTIFICATION_MANAGE, P.AUDIT_VIEW, P.STAFF_MANAGE_LIMITED,
    }),
    "cashier": frozenset({
        P.ORDER_VIEW_ALL, P.ORDER_CREATE_MANUAL, P.ORDER_EDIT_LINES, P.ORDER_SET_LINE_PRICE,
        P.ORDER_CONFIRM, P.ORDER_REJECT, P.ORDER_ASSIGN_DRIVER, P.ORDER_RECONFIRM_TOTAL,
        P.PAYMENT_COLLECT, P.PAYMENT_RECONCILE, P.PAYMENT_REFUND, P.CRM_VIEW,
    }),
    "kitchen": frozenset({
        P.ORDER_VIEW_ALL, P.ORDER_ADVANCE_PREPARATION, P.ORDER_ADJUST_WEIGHT,
        P.MENU_TOGGLE_AVAILABILITY,
    }),
    "storekeeper": frozenset({P.INVENTORY_VIEW, P.LEDGER_VIEW, P.INVENTORY_MANAGE}),
    "delivery": frozenset({P.ORDER_VIEW_ASSIGNED, P.ORDER_ADVANCE_DELIVERY, P.PAYMENT_COLLECT}),
    SYSTEM_ROLE: frozenset(),
}


def permissions_for_role(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def role_has(role: str, permission: Permission) -> bool:
    return permission in permissions_for_role(role)
