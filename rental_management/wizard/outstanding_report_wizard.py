# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Outstanding Report: open tenant invoices, on screen (View) or in Excel,
grouped tenant-wise or unit-wise."""
import base64
import io

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError

AGING_ORDER = ["not_due", "0_30", "31_60", "61_90", "90_plus"]
AGING_LABELS = {
    "not_due": "Not Due",
    "0_30": "1-30 Days",
    "31_60": "31-60 Days",
    "61_90": "61-90 Days",
    "90_plus": "90+ Days",
}


class OutstandingReportWizard(models.TransientModel):
    _name = "outstanding.report.wizard"
    _description = "Outstanding Report Wizard"

    property_project_id = fields.Many2one("property.project", string="Compound")
    subproject_id = fields.Many2one(
        "property.sub.project", string="Building",
        domain="[('property_project_id', '=?', property_project_id)]",
    )
    floor = fields.Integer(string="Floor", help="Leave 0 to include all floors.")
    property_type = fields.Selection(
        [("land", "Land"), ("residential", "Residential"),
         ("commercial", "Commercial"), ("industrial", "Industrial")],
        string="Property Type",
    )
    partner_ids = fields.Many2many("res.partner", string="Tenants")
    property_ids = fields.Many2many("property.details", string="Units")
    invoice_scope = fields.Selection(
        [("rent", "Rent invoices only"), ("all", "All tenant invoices")],
        string="Invoices", default="rent", required=True,
        help="All = rent, deposit, maintenance, services, penalties and other "
        "invoices linked to a contract.",
    )
    group_by = fields.Selection(
        [("tenant", "Tenant-wise (Tenant > Unit)"), ("unit", "Unit-wise (Compound > Building > Unit)")],
        string="Group By", default="tenant", required=True,
    )
    only_overdue = fields.Boolean(
        string="Only Overdue", default=False,
        help="Only invoices whose due date has already passed.",
    )
    file_name = fields.Char(string="File Name", readonly=True)
    report_file = fields.Binary(string="Report", readonly=True)

    @api.onchange("property_project_id")
    def _onchange_property_project_id(self):
        if self.subproject_id and self.subproject_id.property_project_id != self.property_project_id:
            self.subproject_id = False

    # ------------------------------------------------------------------ #
    def _get_domain(self):
        self.ensure_one()
        domain = []
        if self.invoice_scope == "rent":
            domain.append(("rent_type", "in", ("rent", "full_rent")))
        if self.property_project_id:
            domain.append(("property_project_id", "=", self.property_project_id.id))
        if self.subproject_id:
            domain.append(("subproject_id", "=", self.subproject_id.id))
        if self.floor:
            domain.append(("floor", "=", self.floor))
        if self.property_type:
            domain.append(("property_id.type", "=", self.property_type))
        if self.partner_ids:
            domain.append(("partner_id", "child_of", self.partner_ids.ids))
        if self.property_ids:
            domain.append(("property_id", "in", self.property_ids.ids))
        if self.only_overdue:
            domain.append(("is_overdue", "=", True))
        return domain

    def _group_by_fields(self):
        if self.group_by == "unit":
            return ["property_project_id", "subproject_id", "property_id"]
        return ["partner_id", "property_id"]

    def _get_lines(self):
        lines = self.env["rental.outstanding.report"].search(
            self._get_domain(),
            order="partner_id, property_id, invoice_date_due, id"
            if self.group_by == "tenant"
            else "property_project_id, subproject_id, property_id, invoice_date_due, id",
        )
        if not lines:
            raise UserError(_("No outstanding invoices for the selected filters."))
        return lines

    @staticmethod
    def _unit_label(prop):
        return prop.building_no or prop.name or ""

    # ------------------------------------------------------------------ #
    #  View                                                              #
    # ------------------------------------------------------------------ #
    def action_view_report(self):
        self.ensure_one()
        title = _("Outstanding - Tenant-wise") if self.group_by == "tenant" \
            else _("Outstanding - Unit-wise")
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": "rental.outstanding.report",
            "view_mode": "list,pivot",
            "views": [
                (self.env.ref("rental_management.rental_outstanding_report_view_list").id, "list"),
                (self.env.ref("rental_management.rental_outstanding_report_view_pivot").id, "pivot"),
            ],
            "search_view_id": self.env.ref("rental_management.rental_outstanding_report_view_search").id,
            "domain": self._get_domain(),
            "context": {
                "group_by": self._group_by_fields(),
                "pivot_row_groupby": self._group_by_fields(),
                "pivot_column_groupby": ["aging"],
                "pivot_measures": ["amount_residual"],
            },
            "target": "current",
        }

    # ------------------------------------------------------------------ #
    #  Excel                                                             #
    # ------------------------------------------------------------------ #
    def action_generate_report(self):
        self.ensure_one()
        lines = self._get_lines()
        rent_labels = dict(lines._fields["rent_type"]._description_selection(self.env))

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        title_f = wb.add_format({"bold": True, "font_size": 14, "font_color": "#714B67"})
        sub_f = wb.add_format({"italic": True, "font_color": "#666666"})
        header_f = wb.add_format({
            "bold": True, "bg_color": "#714B67", "font_color": "#FFFFFF",
            "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        cell_f = wb.add_format({"border": 1, "valign": "vcenter"})
        int_f = wb.add_format({"border": 1, "valign": "vcenter", "align": "center"})
        money_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.000"})
        red_money_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.000",
                                     "font_color": "#C0392B", "bold": True})
        date_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "dd-mm-yyyy"})
        group_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF"})
        group_money_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF",
                                       "num_format": "#,##0.000"})
        total_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#714B67", "font_color": "#FFFFFF"})
        total_money_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#714B67",
                                       "font_color": "#FFFFFF", "num_format": "#,##0.000"})

        today = fields.Date.context_today(self)
        filters = [
            _("Compound: %s", self.property_project_id.name or _("All")),
            _("Building: %s", self.subproject_id.name or _("All")),
            dict(self._fields["invoice_scope"].selection)[self.invoice_scope],
        ]
        if self.only_overdue:
            filters.append(_("Overdue only"))
        subtitle = "  |  ".join(filters) + "  |  " + _("As of %s", today.strftime("%d-%m-%Y"))

        def start(name, title, headers, widths):
            sheet = wb.add_worksheet(name)
            sheet.write(0, 0, title, title_f)
            sheet.write(1, 0, subtitle, sub_f)
            for col, (h, w) in enumerate(zip(headers, widths)):
                sheet.write(3, col, h, header_f)
                sheet.set_column(col, col, w)
            sheet.set_row(3, 30)
            sheet.freeze_panes(4, 0)
            return sheet

        def write_date(sheet, row, col, value):
            if value:
                sheet.write_datetime(row, col, value, date_f)
            else:
                sheet.write(row, col, "", cell_f)

        def aging_totals(recs):
            res = dict.fromkeys(AGING_ORDER, 0.0)
            for r in recs:
                res[r.aging] += r.amount_residual
            return res

        grand = sum(lines.mapped("amount_residual"))

        # ---------------- Sheet 1: tenant-wise summary ----------------
        headers = ["Tenant", "Phone", "Units", "Invoices", "Outstanding"] + \
            [AGING_LABELS[a] for a in AGING_ORDER]
        widths = [30, 15, 30, 9, 15] + [13] * len(AGING_ORDER)
        sheet = start("Tenant-wise", _("Outstanding - Tenant-wise"), headers, widths)
        row = 4
        for partner in lines.partner_id.sorted("name"):
            recs = lines.filtered(lambda l: l.partner_id == partner)
            ag = aging_totals(recs)
            sheet.write(row, 0, partner.name or "", cell_f)
            sheet.write(row, 1, partner.phone or "", cell_f)
            sheet.write(row, 2, ", ".join(self._unit_label(p) for p in recs.property_id), cell_f)
            sheet.write_number(row, 3, len(recs), int_f)
            sheet.write_number(row, 4, sum(recs.mapped("amount_residual")), red_money_f)
            for i, key in enumerate(AGING_ORDER):
                sheet.write_number(row, 5 + i, ag[key], money_f)
            row += 1
        ag = aging_totals(lines)
        sheet.merge_range(row, 0, row, 3, _("Total"), total_f)
        sheet.write_number(row, 4, grand, total_money_f)
        for i, key in enumerate(AGING_ORDER):
            sheet.write_number(row, 5 + i, ag[key], total_money_f)

        # ---------------- Sheet 2: unit-wise summary ----------------
        headers = ["Compound", "Building", "Unit No.", "Floor", "Tenant(s)", "Contract(s)",
                   "Invoices", "Outstanding"] + [AGING_LABELS[a] for a in AGING_ORDER]
        widths = [22, 20, 12, 7, 28, 16, 9, 15] + [13] * len(AGING_ORDER)
        sheet = start("Unit-wise", _("Outstanding - Unit-wise"), headers, widths)
        row = 4
        units = lines.property_id.sorted(
            lambda p: (p.property_project_id.name or "", p.subproject_id.name or "", self._unit_label(p)))
        for prop in units:
            recs = lines.filtered(lambda l: l.property_id == prop)
            ag = aging_totals(recs)
            sheet.write(row, 0, prop.property_project_id.name or "", cell_f)
            sheet.write(row, 1, prop.subproject_id.name or "", cell_f)
            sheet.write(row, 2, self._unit_label(prop), cell_f)
            sheet.write(row, 3, prop.floor or "", int_f)
            sheet.write(row, 4, ", ".join(recs.partner_id.mapped("name")), cell_f)
            sheet.write(row, 5, ", ".join(recs.tenancy_id.mapped("tenancy_seq")), cell_f)
            sheet.write_number(row, 6, len(recs), int_f)
            sheet.write_number(row, 7, sum(recs.mapped("amount_residual")), red_money_f)
            for i, key in enumerate(AGING_ORDER):
                sheet.write_number(row, 8 + i, ag[key], money_f)
            row += 1
        ag = aging_totals(lines)
        sheet.merge_range(row, 0, row, 6, _("Total"), total_f)
        sheet.write_number(row, 7, grand, total_money_f)
        for i, key in enumerate(AGING_ORDER):
            sheet.write_number(row, 8 + i, ag[key], total_money_f)

        # ---------------- Sheet 3: invoice detail, grouped ----------------
        headers = ["Unit No.", "Building", "Tenant Name", "Contract", "Rent / Month", "Area (SQM)",
                   "Unit Type", "Invoice", "For", "Description", "Invoice Date", "Due Date",
                   "Days Overdue", "Invoice Amount", "Paid", "Outstanding"]
        widths = [12, 18, 26, 14, 12, 10, 14, 16, 12, 28, 12, 12, 9, 14, 13, 14]
        sheet = start("Invoice Detail", _("Outstanding Invoices"), headers, widths)
        row = 4
        if self.group_by == "tenant":
            groups = [(p.name or "-", lines.filtered(lambda l, p=p: l.partner_id == p))
                      for p in lines.partner_id.sorted("name")]
        else:
            groups = [("%s / %s / %s" % (p.property_project_id.name or "-", p.subproject_id.name or "-",
                                         self._unit_label(p)),
                       lines.filtered(lambda l, p=p: l.property_id == p)) for p in units]
        for label, recs in groups:
            sheet.merge_range(row, 0, row, len(headers) - 2, label, group_f)
            sheet.write_number(row, len(headers) - 1, sum(recs.mapped("amount_residual")), group_money_f)
            row += 1
            for line in recs:
                prop = line.property_id
                tenancy = line.tenancy_id
                sheet.write(row, 0, self._unit_label(prop), cell_f)
                sheet.write(row, 1, prop.subproject_id.name or "", cell_f)
                sheet.write(row, 2, line.partner_id.name or "", cell_f)
                sheet.write(row, 3, tenancy.tenancy_seq or "", cell_f)
                sheet.write_number(row, 4, tenancy.total_rent or 0.0, money_f)
                sheet.write_number(row, 5, prop.total_area or 0.0, money_f)
                sheet.write(row, 6, prop.property_subtype_id.name or "", cell_f)
                sheet.write(row, 7, line.move_id.name or "", cell_f)
                sheet.write(row, 8, rent_labels.get(line.rent_type, ""), cell_f)
                sheet.write(row, 9, line.description or "", cell_f)
                write_date(sheet, row, 10, line.invoice_date)
                write_date(sheet, row, 11, line.invoice_date_due)
                sheet.write_number(row, 12, line.days_overdue, int_f)
                sheet.write_number(row, 13, line.amount_total, money_f)
                sheet.write_number(row, 14, line.amount_paid, money_f)
                sheet.write_number(row, 15, line.amount_residual,
                                   red_money_f if line.is_overdue else money_f)
                row += 1
        sheet.merge_range(row, 0, row, len(headers) - 2, _("Grand Total"), total_f)
        sheet.write_number(row, len(headers) - 1, grand, total_money_f)

        wb.close()
        output.seek(0)
        self.write({
            "report_file": base64.b64encode(output.read()),
            "file_name": "Outstanding_Report_%s.xlsx" % today.strftime("%d%m%Y"),
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/?model=outstanding.report.wizard&id={self.id}"
                   "&field=report_file&filename_field=file_name&download=true",
            "target": "self",
        }