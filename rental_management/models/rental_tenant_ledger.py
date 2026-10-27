# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Tenant / Unit Ledger.

One row per receivable movement of a tenant, tagged with the contract and
unit it belongs to, so one tenant with several units gets a ledger per unit:

* Document rows   - posted invoices / credit notes / entries of a contract.
* Allocation rows - each payment (or other un-linked document) matched
                    against a contract invoice is split by the invoice it
                    pays, so the payment lands on the right unit.
* Unallocated     - the part of a tenant payment / document not matched to
                    any contract invoice yet (no unit).

Match column: "M" = fully matched (reconciled), "P" = partially matched,
blank = open.  "Open only" = rows still carrying an open balance.
"""
from odoo import fields, models, tools
from odoo.tools import SQL

KINDS = [
    ("invoice", "Invoice"),
    ("credit_note", "Credit Note"),
    ("entry", "Journal Entry"),
    ("allocation", "Payment / Matched"),
    ("unallocated", "Unallocated"),
]


class RentalTenantLedger(models.Model):
    _name = "rental.tenant.ledger"
    _description = "Tenant / Unit Ledger"
    _auto = False
    _order = "partner_id, property_id, date, id"
    _rec_name = "ref"

    kind = fields.Selection(KINDS, string="Type", readonly=True)
    date = fields.Date(readonly=True)
    date_maturity = fields.Date(string="Due Date", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Tenant", readonly=True)
    tenancy_id = fields.Many2one("tenancy.details", string="Contract", readonly=True)
    property_id = fields.Many2one("property.details", string="Unit", readonly=True)
    unit_no = fields.Char(string="Unit No.", readonly=True)
    subproject_id = fields.Many2one("property.sub.project", string="Building", readonly=True)
    property_project_id = fields.Many2one("property.project", string="Compound", readonly=True)
    move_id = fields.Many2one("account.move", string="Document", readonly=True)
    applied_move_id = fields.Many2one("account.move", string="Applied To", readonly=True)
    ref = fields.Char(string="Reference", readonly=True)
    label = fields.Char(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    debit = fields.Monetary(readonly=True, aggregator="sum")
    credit = fields.Monetary(readonly=True, aggregator="sum")
    balance = fields.Monetary(readonly=True, aggregator="sum")
    amount_residual = fields.Monetary(string="Open Amount", readonly=True, aggregator="sum")
    matching = fields.Char(string="Match", readonly=True)
    is_open = fields.Boolean(string="Open", readonly=True)
    unit_balance = fields.Monetary(string="Unit Balance", readonly=True, aggregator=None,
                                   help="Running balance of this tenant on this unit.")
    tenant_balance = fields.Monetary(string="Tenant Balance", readonly=True, aggregator=None,
                                     help="Running balance of this tenant over all units.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(SQL(
            """
            CREATE OR REPLACE VIEW %(table)s AS (
            WITH rl AS (
                SELECT aml.id, aml.move_id, aml.date, aml.date_maturity, aml.partner_id,
                       aml.company_id, aml.debit, aml.credit, aml.balance,
                       aml.amount_residual, aml.full_reconcile_id, aml.name,
                       am.tenancy_id, am.name AS move_name, am.move_type
                  FROM account_move_line aml
                  JOIN account_move am ON am.id = aml.move_id
                  JOIN account_account aa ON aa.id = aml.account_id
                 WHERE aml.parent_state = 'posted'
                   AND aa.account_type = 'asset_receivable'
            ),
            rows AS (
                -- A. contract documents
                SELECT rl.id * 4 AS id,
                       CASE rl.move_type WHEN 'out_invoice' THEN 'invoice'
                                         WHEN 'out_refund' THEN 'credit_note'
                                         ELSE 'entry' END AS kind,
                       rl.date, rl.date_maturity, rl.partner_id, rl.tenancy_id,
                       rl.move_id, NULL::integer AS applied_move_id,
                       rl.move_name AS ref, rl.name AS label, rl.company_id,
                       rl.debit, rl.credit, rl.amount_residual,
                       CASE WHEN rl.full_reconcile_id IS NOT NULL THEN 'M'
                            WHEN rl.amount_residual <> rl.balance THEN 'P'
                            ELSE '' END AS matching,
                       (rl.amount_residual <> 0) AS is_open
                  FROM rl
                 WHERE rl.tenancy_id IS NOT NULL

                UNION ALL
                -- B1. payment / other document paying a contract invoice
                SELECT apr.id * 4 + 1, 'allocation',
                       o.date, o.date_maturity, t.partner_id, t.tenancy_id,
                       o.move_id, t.move_id,
                       o.move_name, o.name, t.company_id,
                       0.0, apr.amount, 0.0,
                       CASE WHEN o.full_reconcile_id IS NOT NULL THEN 'M' ELSE 'P' END,
                       (t.amount_residual <> 0)
                  FROM account_partial_reconcile apr
                  JOIN rl t ON t.id = apr.debit_move_id AND t.tenancy_id IS NOT NULL
                  JOIN rl o ON o.id = apr.credit_move_id AND o.tenancy_id IS NULL

                UNION ALL
                -- B2. refund paid out against a contract credit note
                SELECT apr.id * 4 + 2, 'allocation',
                       o.date, o.date_maturity, t.partner_id, t.tenancy_id,
                       o.move_id, t.move_id,
                       o.move_name, o.name, t.company_id,
                       apr.amount, 0.0, 0.0,
                       CASE WHEN o.full_reconcile_id IS NOT NULL THEN 'M' ELSE 'P' END,
                       (t.amount_residual <> 0)
                  FROM account_partial_reconcile apr
                  JOIN rl t ON t.id = apr.credit_move_id AND t.tenancy_id IS NOT NULL
                  JOIN rl o ON o.id = apr.debit_move_id AND o.tenancy_id IS NULL

                UNION ALL
                -- C. tenant money / documents not matched to a contract yet
                SELECT rl.id * 4 + 3, 'unallocated',
                       rl.date, rl.date_maturity, rl.partner_id, NULL::integer,
                       rl.move_id, NULL::integer,
                       rl.move_name, rl.name, rl.company_id,
                       GREATEST(rl.amount_residual, 0), GREATEST(-rl.amount_residual, 0),
                       rl.amount_residual,
                       CASE WHEN rl.amount_residual <> rl.balance THEN 'P' ELSE '' END,
                       TRUE
                  FROM rl
                 WHERE rl.tenancy_id IS NULL
                   AND rl.amount_residual <> 0
                   AND rl.partner_id IN (SELECT DISTINCT td.tenancy_id FROM tenancy_details td
                                          WHERE td.tenancy_id IS NOT NULL)
            )
            SELECT r.id, r.kind, r.date, r.date_maturity, r.partner_id, r.tenancy_id,
                   td.property_id, p.building_no AS unit_no, p.subproject_id,
                   p.property_project_id, r.move_id, r.applied_move_id, r.ref, r.label,
                   r.company_id, r.debit, r.credit, r.debit - r.credit AS balance,
                   r.amount_residual, r.matching, r.is_open,
                   SUM(r.debit - r.credit) OVER (
                       PARTITION BY r.company_id, r.partner_id, td.property_id
                       ORDER BY r.date, r.id) AS unit_balance,
                   SUM(r.debit - r.credit) OVER (
                       PARTITION BY r.company_id, r.partner_id
                       ORDER BY r.date, r.id) AS tenant_balance
              FROM rows r
              LEFT JOIN tenancy_details td ON td.id = r.tenancy_id
              LEFT JOIN property_details p ON p.id = td.property_id
            )
            """,
            table=SQL.identifier(self._table),
        ))

    def action_open_document(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }