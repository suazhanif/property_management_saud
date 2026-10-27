# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Backdated contracts: tenant invoices are dated today, not in the past.

When a contract (or one of its installments) is generated with a date that
is already in the past, the invoice used to carry that past date.  Now the
invoice gets today's date and the original schedule date is kept on the
invoice as "Period Date" (the rent.invoice installment line keeps it too).

Only applied automatically when the invoice is created / linked to a
contract while still in draft.  An accountant can still change the date by
hand before posting.  Disable with the system parameter
``rental_management.invoice_date_today`` = False.
"""
from odoo import api, fields, models

TENANT_MOVE_TYPES = ("out_invoice", "out_refund")


class AccountMoveInvoiceDateToday(models.Model):
    _inherit = "account.move"

    rent_period_date = fields.Date(
        string="Period Date",
        readonly=True,
        copy=False,
        help="Contract schedule date of this invoice. The invoice itself is "
             "dated the day it was created when this date was in the past "
             "(backdated contract).",
    )

    @api.model
    def _rental_invoice_date_today_enabled(self):
        if self.env.context.get("rental_keep_invoice_date"):
            return False
        param = self.env["ir.config_parameter"].sudo().get_param(
            "rental_management.invoice_date_today", "True")
        return str(param).lower() not in ("false", "0", "")

    @api.model
    def _rental_fix_invoice_date_vals(self, vals):
        """Move a past invoice_date to today, remembering the schedule date."""
        invoice_date = vals.get("invoice_date")
        if not invoice_date or vals.get("move_type") not in TENANT_MOVE_TYPES:
            return vals
        invoice_date = fields.Date.to_date(invoice_date)
        today = fields.Date.context_today(self)
        if invoice_date < today:
            vals = dict(vals)
            vals.setdefault("rent_period_date", invoice_date)
            vals["invoice_date"] = today
            vals.pop("invoice_date_due", None)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        if self._rental_invoice_date_today_enabled():
            vals_list = [
                self._rental_fix_invoice_date_vals(vals) if vals.get("tenancy_id") else vals
                for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        # Several contract flows create the invoice first and set
        # ``tenancy_id`` afterwards, then post it.
        res = super().write(vals)
        if vals.get("tenancy_id") and "invoice_date" not in vals \
                and self._rental_invoice_date_today_enabled():
            today = fields.Date.context_today(self)
            for move in self.filtered(
                lambda m: m.state == "draft"
                and m.move_type in TENANT_MOVE_TYPES
                and m.invoice_date
                and m.invoice_date < today
            ):
                super(AccountMoveInvoiceDateToday, move).write({
                    "rent_period_date": move.rent_period_date or move.invoice_date,
                    "invoice_date": today,
                })
        return res