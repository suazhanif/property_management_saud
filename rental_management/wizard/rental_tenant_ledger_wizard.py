# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Tenant / Unit Ledger wizard: on-screen view or Excel statement."""
import base64
import io
from collections import OrderedDict

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RentalTenantLedgerWizard(models.TransientModel):
    _name = "rental.tenant.ledger.wizard"
    _description = "Tenant / Unit Ledger Wizard"

    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To", default=fields.Date.context_today)
    partner_ids = fields.Many2many("res.partner", string="Tenants")
    property_project_id = fields.Many2one("property.project", string="Compound")
    subproject_id = fields.Many2one(
        "property.sub.project", string="Building",
        domain="[('property_project_id', '=?', property_project_id)]")
    property_ids = fields.Many2many("property.details", string="Units")
    group_by = fields.Selection(
        [("tenant", "Tenant > Unit"), ("unit", "Unit > Tenant")],
        default="tenant", required=True, string="Group By")
    open_only = fields.Boolean(
        string="Outstanding (open items only)",
        help="Only invoices / payments that are not fully matched yet.")
    include_unallocated = fields.Boolean(
        string="Include unallocated payments", default=True,
        help="Tenant payments / credits not yet matched to any contract invoice.")
    file_name = fields.Char(readonly=True)
    report_file = fields.Binary(readonly=True)

    @api.onchange("property_project_id")
    def _onchange_property_project_id(self):
        if self.subproject_id and self.subproject_id.property_project_id != self.property_project_id:
            self.subproject_id = False

    # ------------------------------------------------------------------ #
    def _base_domain(self):
        """Filters without the date range."""
        self.ensure_one()
        domain = []
        if self.partner_ids:
            domain.append(("partner_id", "child_of", self.partner_ids.ids))
        unit_filter = []
        if self.property_project_id:
            unit_filter.append(("property_project_id", "=", self.property_project_id.id))
        if self.subproject_id:
            unit_filter.append(("subproject_id", "=", self.subproject_id.id))
        if self.property_ids:
            unit_filter.append(("property_id", "in", self.property_ids.ids))
        if unit_filter:
            # Unit filters drop the unallocated rows (they have no unit).
            domain += unit_filter
        elif not self.include_unallocated:
            domain.append(("kind", "!=", "unallocated"))
        if self.open_only:
            domain.append(("is_open", "=", True))
        return domain

    def _get_domain(self):
        domain = self._base_domain()
        if self.date_to:
            domain.append(("date", "<=", self.date_to))
        if self.date_from and not self.open_only:
            domain.append(("date", ">=", self.date_from))
        return domain

    def _group_fields(self):
        if self.group_by == "unit":
            return ["property_id", "partner_id"]
        return ["partner_id", "property_id"]

    # ------------------------------------------------------------------ #
    def action_view(self):
        self.ensure_one()
        ref = self.env.ref
        return {
            "type": "ir.actions.act_window",
            "name": _("Tenant / Unit Ledger"),
            "res_model": "rental.tenant.ledger",
            "view_mode": "list,pivot",
            "views": [
                (ref("rental_management.rental_tenant_ledger_view_list").id, "list"),
                (ref("rental_management.rental_tenant_ledger_view_pivot").id, "pivot"),
            ],
            "search_view_id": ref("rental_management.rental_tenant_ledger_view_search").id,
            "domain": self._get_domain(),
            "context": {
                "group_by": self._group_fields(),
                "pivot_row_groupby": self._group_fields(),
                "pivot_measures": ["debit", "credit", "balance"],
            },
            "target": "current",
        }

    # ------------------------------------------------------------------ #
    def action_excel(self):
        self.ensure_one()
        Ledger = self.env["rental.tenant.ledger"]
        lines = Ledger.search(self._get_domain(), order="date, id")
        if not lines:
            raise UserError(_("Nothing to report for the selected filters."))

        # Opening balances per (tenant, unit) before date_from.
        opening = {}
        if self.date_from and not self.open_only:
            for group in Ledger._read_group(
                self._base_domain() + [("date", "<", self.date_from)],
                ["partner_id", "property_id"], ["balance:sum"],
            ):
                opening[(group[0].id, group[1].id)] = group[2]

        def key_of(line):
            return (line.partner_id.id, line.property_id.id)

        sections = OrderedDict()
        keys = set(opening) | {key_of(l) for l in lines}
        Partner = self.env["res.partner"]
        Property = self.env["property.details"]

        def sort_key(k):
            p, u = Partner.browse(k[0]), Property.browse(k[1])
            unit = (u.property_project_id.name or "", u.subproject_id.name or "",
                    u.building_no or u.name or "") if u else ("~", "", "")
            return (p.name or "", unit) if self.group_by == "tenant" else (unit, p.name or "")

        for k in sorted(keys, key=sort_key):
            sections[k] = lines.filtered(lambda l, k=k: key_of(l) == k)

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        F = {
            "title": wb.add_format({"bold": True, "font_size": 14, "font_color": "#714B67"}),
            "sub": wb.add_format({"italic": True, "font_color": "#666666"}),
            "head": wb.add_format({"bold": True, "bg_color": "#714B67", "font_color": "#FFFFFF",
                                   "border": 1, "align": "center", "valign": "vcenter",
                                   "text_wrap": True}),
            "cell": wb.add_format({"border": 1, "valign": "vcenter"}),
            "center": wb.add_format({"border": 1, "align": "center", "bold": True}),
            "date": wb.add_format({"border": 1, "num_format": "dd-mm-yyyy"}),
            "money": wb.add_format({"border": 1, "num_format": "#,##0.000"}),
            "group": wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF"}),
            "group_money": wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF",
                                          "num_format": "#,##0.000"}),
            "total": wb.add_format({"border": 1, "bold": True, "bg_color": "#714B67",
                                    "font_color": "#FFFFFF"}),
            "total_money": wb.add_format({"border": 1, "bold": True, "bg_color": "#714B67",
                                          "font_color": "#FFFFFF", "num_format": "#,##0.000"}),
        }
        kind_labels = dict(Ledger._fields["kind"]._description_selection(self.env))
        today = fields.Date.context_today(self)
        if self.open_only:
            period = _("Open items as of %s", (self.date_to or today).strftime("%d-%m-%Y"))
        else:
            period = _("%(f)s to %(t)s",
                       f=self.date_from.strftime("%d-%m-%Y") if self.date_from else _("Start"),
                       t=(self.date_to or today).strftime("%d-%m-%Y"))

        def unit_name(prop):
            if not prop:
                return _("Unallocated (no unit)")
            return " / ".join(n for n in [prop.property_project_id.name, prop.subproject_id.name,
                                          _("Unit %s", prop.building_no or prop.name)] if n)

        def section_title(k):
            p, u = Partner.browse(k[0]), Property.browse(k[1])
            if self.group_by == "tenant":
                return "%s  |  %s" % (p.name or "-", unit_name(u))
            return "%s  |  %s" % (unit_name(u), p.name or "-")

        def start(name, title, headers, widths):
            sh = wb.add_worksheet(name)
            sh.write(0, 0, title, F["title"])
            sh.write(1, 0, period, F["sub"])
            for c, (h, w) in enumerate(zip(headers, widths)):
                sh.write(3, c, h, F["head"])
                sh.set_column(c, c, w)
            sh.set_row(3, 30)
            sh.freeze_panes(4, 0)
            return sh

        def wdate(sh, r, c, v):
            if v:
                sh.write_datetime(r, c, v, F["date"])
            else:
                sh.write(r, c, "", F["cell"])

        # ---------------- Summary ----------------
        heads = ["Tenant", "Compound", "Building", "Unit No.", "Contract(s)", "Opening",
                 "Debit", "Credit", "Closing", "Open Amount"]
        sh = start("Summary", _("Tenant / Unit Ledger - Summary"), heads,
                   [28, 20, 18, 12, 18, 14, 14, 14, 14, 14])
        r = 4
        tot = [0.0] * 5
        for k, recs in sections.items():
            p, u = Partner.browse(k[0]), Property.browse(k[1])
            op = opening.get(k, 0.0)
            dr, cr = sum(recs.mapped("debit")), sum(recs.mapped("credit"))
            open_amt = sum(recs.filtered("is_open").mapped("amount_residual"))
            vals = [op, dr, cr, op + dr - cr, open_amt]
            sh.write(r, 0, p.name or "", F["cell"])
            sh.write(r, 1, u.property_project_id.name or "", F["cell"])
            sh.write(r, 2, u.subproject_id.name or "", F["cell"])
            sh.write(r, 3, (u.building_no or u.name) if u else _("Unallocated"), F["cell"])
            sh.write(r, 4, ", ".join(recs.tenancy_id.mapped("tenancy_seq")), F["cell"])
            for i, v in enumerate(vals):
                sh.write_number(r, 5 + i, v, F["money"])
                tot[i] += v
            r += 1
        sh.merge_range(r, 0, r, 4, _("Total"), F["total"])
        for i, v in enumerate(tot):
            sh.write_number(r, 5 + i, v, F["total_money"])

        # ---------------- Ledger ----------------
        if self.open_only:
            heads = ["Date", "Type", "Reference", "Applied To", "Contract", "Description",
                     "Due Date", "Days Overdue", "Debit", "Credit", "Open Amount", "Match"]
            widths = [11, 14, 16, 16, 15, 34, 11, 9, 13, 13, 14, 7]
        else:
            heads = ["Date", "Type", "Reference", "Applied To", "Contract", "Description",
                     "Due Date", "Debit", "Credit", "Balance", "Match"]
            widths = [11, 14, 16, 16, 15, 34, 11, 13, 13, 14, 7]
        last = len(heads) - 1
        sh = start("Ledger", _("Tenant / Unit Ledger"), heads, widths)
        r = 4
        g_dr = g_cr = g_bal = 0.0
        for k, recs in sections.items():
            sh.merge_range(r, 0, r, last, section_title(k), F["group"])
            r += 1
            bal = opening.get(k, 0.0)
            if not self.open_only:
                sh.merge_range(r, 0, r, 6, _("Opening Balance"), F["cell"])
                sh.write(r, 7, "", F["cell"])
                sh.write(r, 8, "", F["cell"])
                sh.write_number(r, 9, bal, F["money"])
                sh.write(r, 10, "", F["cell"])
                r += 1
            for l in recs:
                bal += l.balance
                wdate(sh, r, 0, l.date)
                sh.write(r, 1, kind_labels.get(l.kind, ""), F["cell"])
                sh.write(r, 2, l.ref or "", F["cell"])
                sh.write(r, 3, l.applied_move_id.name or "", F["cell"])
                sh.write(r, 4, l.tenancy_id.tenancy_seq or "", F["cell"])
                sh.write(r, 5, l.label or "", F["cell"])
                wdate(sh, r, 6, l.date_maturity)
                if self.open_only:
                    overdue = (today - l.date_maturity).days if l.date_maturity and l.date_maturity < today else 0
                    sh.write_number(r, 7, overdue, F["cell"])
                    sh.write_number(r, 8, l.debit, F["money"])
                    sh.write_number(r, 9, l.credit, F["money"])
                    sh.write_number(r, 10, l.amount_residual, F["money"])
                    sh.write(r, 11, l.matching or "", F["center"])
                else:
                    sh.write_number(r, 7, l.debit, F["money"])
                    sh.write_number(r, 8, l.credit, F["money"])
                    sh.write_number(r, 9, bal, F["money"])
                    sh.write(r, 10, l.matching or "", F["center"])
                r += 1
            dr, cr = sum(recs.mapped("debit")), sum(recs.mapped("credit"))
            g_dr += dr
            g_cr += cr
            if self.open_only:
                amt = sum(recs.mapped("amount_residual"))
                g_bal += amt
                sh.merge_range(r, 0, r, 7, _("Open Total"), F["group"])
                sh.write_number(r, 8, dr, F["group_money"])
                sh.write_number(r, 9, cr, F["group_money"])
                sh.write_number(r, 10, amt, F["group_money"])
                sh.write(r, 11, "", F["group"])
            else:
                g_bal += bal
                sh.merge_range(r, 0, r, 6, _("Closing Balance"), F["group"])
                sh.write_number(r, 7, dr, F["group_money"])
                sh.write_number(r, 8, cr, F["group_money"])
                sh.write_number(r, 9, bal, F["group_money"])
                sh.write(r, 10, "", F["group"])
            r += 2
        money_col = 8 if self.open_only else 7
        sh.merge_range(r, 0, r, money_col - 1, _("Grand Total"), F["total"])
        sh.write_number(r, money_col, g_dr, F["total_money"])
        sh.write_number(r, money_col + 1, g_cr, F["total_money"])
        sh.write_number(r, money_col + 2, g_bal, F["total_money"])
        sh.write(r, money_col + 3, "", F["total"])
        sh.write(r + 2, 0, _("Match: M = fully matched, P = partially matched, blank = open."), F["sub"])

        wb.close()
        output.seek(0)
        self.write({
            "report_file": base64.b64encode(output.read()),
            "file_name": "Tenant_Unit_Ledger_%s.xlsx" % today.strftime("%d%m%Y"),
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/?model=rental.tenant.ledger.wizard&id={self.id}"
                   "&field=report_file&filename_field=file_name&download=true",
            "target": "self",
        }