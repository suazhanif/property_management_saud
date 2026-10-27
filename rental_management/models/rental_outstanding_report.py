# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Outstanding (open) tenant invoices, one row per invoice, with tenant,
contract, unit, building and compound — used by the on-screen Outstanding
Report (tenant-wise / unit-wise) and its Excel export."""
from odoo import fields, models, tools
from odoo.tools import SQL

AGING_BUCKETS = [
    ("not_due", "Not Due"),
    ("0_30", "1-30 Days"),
    ("31_60", "31-60 Days"),
    ("61_90", "61-90 Days"),
    ("90_plus", "90+ Days"),
]

RENT_TYPES = [
    ("deposit", "Deposit"),
    ("rent", "Rent"),
    ("maintenance", "Maintenance"),
    ("utility", "Utility"),
    ("penalty", "Penalty"),
    ("full_rent", "Full Rent"),
    ("settlement", "Settlement"),
    ("other", "Other"),
]


class RentalOutstandingReport(models.Model):
    _name = "rental.outstanding.report"
    _description = "Rental Outstanding Invoices"
    _auto = False
    _order = "partner_id, property_id, invoice_date_due, id"
    _rec_name = "move_id"

    move_id = fields.Many2one("account.move", string="Invoice", readonly=True)
    move_type = fields.Selection(
        [("out_invoice", "Invoice"), ("out_refund", "Credit Note")], string="Type", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Tenant", readonly=True)
    tenancy_id = fields.Many2one("tenancy.details", string="Contract", readonly=True)
    property_id = fields.Many2one("property.details", string="Unit", readonly=True)
    unit_no = fields.Char(string="Unit No.", readonly=True)
    floor = fields.Integer(readonly=True)
    subproject_id = fields.Many2one("property.sub.project", string="Building", readonly=True)
    property_project_id = fields.Many2one("property.project", string="Compound", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    rent_type = fields.Selection(RENT_TYPES, string="Invoice For", readonly=True)
    description = fields.Char(readonly=True)
    invoice_date = fields.Date(readonly=True)
    invoice_date_due = fields.Date(string="Due Date", readonly=True)
    days_overdue = fields.Integer(string="Days Overdue", readonly=True, aggregator="max")
    aging = fields.Selection(AGING_BUCKETS, readonly=True)
    is_overdue = fields.Boolean(readonly=True)
    amount_total = fields.Monetary(string="Invoice Amount", readonly=True, aggregator="sum")
    amount_paid = fields.Monetary(string="Paid", readonly=True, aggregator="sum")
    amount_residual = fields.Monetary(string="Outstanding", readonly=True, aggregator="sum")
    payment_state = fields.Selection(
        [("not_paid", "Not Paid"), ("partial", "Partially Paid"), ("in_payment", "In Payment"),
         ("paid", "Paid"), ("reversed", "Reversed")], readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(SQL(
            """
            CREATE OR REPLACE VIEW %(table)s AS (
                SELECT am.id AS id,
                       am.id AS move_id,
                       am.move_type,
                       am.partner_id,
                       am.tenancy_id,
                       td.property_id,
                       p.building_no AS unit_no,
                       p.floor,
                       p.subproject_id,
                       p.property_project_id,
                       am.company_id,
                       ri.type AS rent_type,
                       ri.description,
                       am.invoice_date,
                       COALESCE(am.invoice_date_due, am.invoice_date) AS invoice_date_due,
                       GREATEST(CURRENT_DATE - COALESCE(am.invoice_date_due, am.invoice_date), 0) AS days_overdue,
                       CASE
                           WHEN COALESCE(am.invoice_date_due, am.invoice_date) >= CURRENT_DATE THEN 'not_due'
                           WHEN CURRENT_DATE - COALESCE(am.invoice_date_due, am.invoice_date) <= 30 THEN '0_30'
                           WHEN CURRENT_DATE - COALESCE(am.invoice_date_due, am.invoice_date) <= 60 THEN '31_60'
                           WHEN CURRENT_DATE - COALESCE(am.invoice_date_due, am.invoice_date) <= 90 THEN '61_90'
                           ELSE '90_plus'
                       END AS aging,
                       (COALESCE(am.invoice_date_due, am.invoice_date) < CURRENT_DATE) AS is_overdue,
                       am.amount_total_signed AS amount_total,
                       am.amount_total_signed - am.amount_residual_signed AS amount_paid,
                       am.amount_residual_signed AS amount_residual,
                       am.payment_state
                  FROM account_move am
                  JOIN tenancy_details td ON td.id = am.tenancy_id
                  LEFT JOIN property_details p ON p.id = td.property_id
             LEFT JOIN LATERAL (
                       SELECT r.type, r.description
                         FROM rent_invoice r
                        WHERE r.rent_invoice_id = am.id
                        ORDER BY r.id
                        LIMIT 1
                   ) ri ON TRUE
                 WHERE am.state = 'posted'
                   AND am.move_type IN ('out_invoice', 'out_refund')
                   AND am.amount_residual <> 0
            )
            """,
            table=SQL.identifier(self._table),
        ))

    def action_open_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }