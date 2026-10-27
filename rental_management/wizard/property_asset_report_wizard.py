# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Asset Report: assets per compound / building / property (PDF + Excel)."""
import base64
import io

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.property_asset import ASSET_CONDITION

PROBLEM_CONDITIONS = ("damaged", "missing")


class PropertyAssetReportWizard(models.TransientModel):
    _name = "property.asset.report.wizard"
    _description = "Property Asset Report Wizard"

    property_project_id = fields.Many2one("property.project", string="Compound")
    subproject_id = fields.Many2one(
        "property.sub.project", string="Building",
        domain="[('property_project_id', '=?', property_project_id)]",
    )
    floor = fields.Integer(string="Floor", help="Leave 0 to include all floors.")
    property_ids = fields.Many2many(
        "property.details", string="Properties",
        help="Leave empty to include every property of the selected compound / building.",
    )
    category_ids = fields.Many2many("property.asset.category", string="Asset Categories")
    condition = fields.Selection(ASSET_CONDITION, string="Condition")
    include_empty = fields.Boolean(
        string="Include Units Without Assets",
        help="Also list units that have no asset registered, to spot gaps.",
    )
    pdf_layout = fields.Selection(
        [("summary", "Summary list"), ("unit", "One page per unit (handover sheet)")],
        string="PDF Layout", default="summary", required=True,
        help="The per-unit handover sheet always prints the full asset list "
        "(category / condition filters are ignored).",
    )
    file_name = fields.Char(string="File Name", readonly=True)
    report_file = fields.Binary(string="Report", readonly=True)

    # ------------------------------------------------------------------ #
    #  Filters                                                           #
    # ------------------------------------------------------------------ #
    def _match_location(self, prop):
        if self.property_project_id and prop.property_project_id != self.property_project_id:
            return False
        if self.subproject_id and prop.subproject_id != self.subproject_id:
            return False
        if self.floor and prop.floor != self.floor:
            return False
        return True

    @api.onchange("property_project_id", "subproject_id", "floor")
    def _onchange_location(self):
        if (
            self.subproject_id
            and self.property_project_id
            and self.subproject_id.property_project_id != self.property_project_id
        ):
            self.subproject_id = False
        self.property_ids = self.property_ids.filtered(self._match_location)

    def _get_property_domain(self):
        if self.property_ids:
            return [("id", "in", self.property_ids.ids)]
        domain = []
        if self.property_project_id:
            domain.append(("property_project_id", "=", self.property_project_id.id))
        if self.subproject_id:
            domain.append(("subproject_id", "=", self.subproject_id.id))
        if self.floor:
            domain.append(("floor", "=", self.floor))
        return domain

    def _get_properties(self):
        return self.env["property.details"].search(
            self._get_property_domain(),
            order="property_project_id, subproject_id, floor, name",
        )

    def _get_assets(self, properties):
        domain = [("property_id", "in", properties.ids)]
        if self.category_ids:
            domain.append(("category_id", "in", self.category_ids.ids))
        if self.condition:
            domain.append(("condition", "=", self.condition))
        return self.env["property.unit.asset"].search(domain, order="sequence, id")

    def _get_filter_label(self):
        parts = []
        if self.property_ids:
            parts.append(_("Properties: %s", ", ".join(self.property_ids.mapped("name"))))
        else:
            parts.append(_("Compound: %s", self.property_project_id.name or _("All")))
            parts.append(_("Building: %s", self.subproject_id.name or _("All")))
            if self.floor:
                parts.append(_("Floor: %s", self.floor))
        if self.category_ids:
            parts.append(_("Categories: %s", ", ".join(self.category_ids.mapped("name"))))
        if self.condition:
            parts.append(_("Condition: %s", dict(ASSET_CONDITION)[self.condition]))
        return "  |  ".join(parts)

    @staticmethod
    def _unit_label(prop):
        return " - ".join(x for x in [prop.property_seq, prop.building_no or prop.name] if x)

    # ------------------------------------------------------------------ #
    #  Data                                                              #
    # ------------------------------------------------------------------ #
    def _get_item_totals(self, assets):
        totals = {}
        for a in assets:
            key = ((a.name or "").strip().lower(), a.category_id.id, a.uom_id.id)
            t = totals.setdefault(key, {
                "name": a.name, "category": a.category_id.name or "",
                "uom": a.uom_id.name or "", "qty": 0.0, "value": 0.0,
                "problem": 0.0, "units": set(),
            })
            t["qty"] += a.quantity
            t["value"] += a.value
            t["units"].add(a.property_id.id)
            if a.condition in PROBLEM_CONDITIONS:
                t["problem"] += a.quantity
        rows = sorted(totals.values(), key=lambda t: (t["category"], (t["name"] or "").lower()))
        for t in rows:
            t["units"] = len(t["units"])
        return rows

    def _get_report_data(self):
        """Assets grouped by Compound / Building → Unit."""
        self.ensure_one()
        Asset = self.env["property.unit.asset"]
        properties = self._get_properties()
        assets = self._get_assets(properties)
        by_prop = assets.grouped("property_id")
        groups, index = [], {}
        for prop in properties:
            prop_assets = by_prop.get(prop, Asset)
            if not prop_assets and not self.include_empty:
                continue
            key = (prop.property_project_id.id, prop.subproject_id.id)
            if key not in index:
                index[key] = {
                    "compound": prop.property_project_id.name or _("No Compound"),
                    "building": prop.subproject_id.name or "-",
                    "units": [], "count": 0, "qty": 0.0, "value": 0.0, "problem": 0,
                }
                groups.append(index[key])
            grp = index[key]
            unit = {
                "property": prop,
                "label": self._unit_label(prop),
                "assets": prop_assets,
                "qty": sum(prop_assets.mapped("quantity")),
                "value": sum(prop_assets.mapped("value")),
                "problem": len(prop_assets.filtered(lambda a: a.condition in PROBLEM_CONDITIONS)),
            }
            grp["units"].append(unit)
            for k in ("qty", "value", "problem"):
                grp[k] += unit[k]
            grp["count"] += len(prop_assets)
        if not groups:
            raise UserError(_("No assets found for the selected filters."))
        return {
            "groups": groups,
            "items": self._get_item_totals(assets),
            "filter_label": self._get_filter_label(),
            "unit_count": sum(len(g["units"]) for g in groups),
            "count": sum(g["count"] for g in groups),
            "qty": sum(g["qty"] for g in groups),
            "value": sum(g["value"] for g in groups),
            "problem": sum(g["problem"] for g in groups),
        }

    # ------------------------------------------------------------------ #
    #  PDF                                                               #
    # ------------------------------------------------------------------ #
    def action_print_pdf(self):
        self.ensure_one()
        if self.pdf_layout == "unit":
            properties = self._get_properties()
            if not self.include_empty:
                properties = properties.filtered("asset_ids")
            if not properties:
                raise UserError(_("No assets found for the selected filters."))
            return self.env.ref(
                "rental_management.action_report_property_assets"
            ).report_action(properties)
        self._get_report_data()  # raise early when empty
        return self.env.ref(
            "rental_management.action_report_property_asset_summary"
        ).report_action(self)

    # ------------------------------------------------------------------ #
    #  Excel                                                             #
    # ------------------------------------------------------------------ #
    def action_export_xlsx(self):
        self.ensure_one()
        data = self._get_report_data()
        conditions = dict(ASSET_CONDITION)

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        title_f = wb.add_format({"bold": True, "font_size": 14, "font_color": "#714B67"})
        sub_f = wb.add_format({"italic": True, "font_color": "#666666"})
        head_f = wb.add_format({
            "bold": True, "bg_color": "#714B67", "font_color": "#FFFFFF",
            "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        cell_f = wb.add_format({"border": 1, "valign": "vcenter"})
        muted_f = wb.add_format({"border": 1, "valign": "vcenter", "italic": True, "font_color": "#888888"})
        bad_f = wb.add_format({"border": 1, "valign": "vcenter", "font_color": "#C0392B", "bold": True})
        num_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.##"})
        money_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "#,##0.000"})
        date_f = wb.add_format({"border": 1, "valign": "vcenter", "num_format": "dd-mm-yyyy"})
        tot_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF"})
        tot_num_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF", "num_format": "#,##0.##"})
        tot_money_f = wb.add_format({"border": 1, "bold": True, "bg_color": "#F0ECEF", "num_format": "#,##0.000"})

        def start_sheet(name, title, headers, widths):
            sheet = wb.add_worksheet(name)
            sheet.write(0, 0, title, title_f)
            sheet.write(1, 0, data["filter_label"], sub_f)
            for col, (h, w) in enumerate(zip(headers, widths)):
                sheet.write(3, col, h, head_f)
                sheet.set_column(col, col, w)
            sheet.freeze_panes(4, 0)
            return sheet

        def write_date(sheet, row, col, value):
            if value:
                sheet.write_datetime(row, col, value, date_f)
            else:
                sheet.write(row, col, "", cell_f)

        # ---- Sheet 1: every asset line ----
        headers = [
            "Compound", "Building", "Unit Code", "Unit No.", "Floor", "Unit Type",
            "Asset", "Category", "Brand", "Model", "Serial / Tag", "Qty", "Unit",
            "Condition", "Value", "Purchase Date", "Warranty Until", "Note",
        ]
        widths = [22, 22, 14, 12, 7, 16, 28, 16, 14, 14, 16, 8, 8, 12, 12, 13, 13, 28]
        sheet = start_sheet("Assets", "Asset Report", headers, widths)
        row = 4
        for grp in data["groups"]:
            for unit in grp["units"]:
                prop = unit["property"]
                loc = [
                    grp["compound"], grp["building"], prop.property_seq or "",
                    prop.building_no or prop.name or "", prop.floor or "",
                    prop.property_subtype_id.name or "",
                ]
                if not unit["assets"]:
                    for col, val in enumerate(loc):
                        sheet.write(row, col, val, cell_f)
                    sheet.write(row, 6, _("No assets registered"), muted_f)
                    for col in range(7, len(headers)):
                        sheet.write(row, col, "", cell_f)
                    row += 1
                    continue
                for a in unit["assets"]:
                    for col, val in enumerate(loc):
                        sheet.write(row, col, val, cell_f)
                    sheet.write(row, 6, a.name or "", cell_f)
                    sheet.write(row, 7, a.category_id.name or "", cell_f)
                    sheet.write(row, 8, a.brand or "", cell_f)
                    sheet.write(row, 9, a.model_no or "", cell_f)
                    sheet.write(row, 10, a.serial_no or "", cell_f)
                    sheet.write_number(row, 11, a.quantity, num_f)
                    sheet.write(row, 12, a.uom_id.name or "", cell_f)
                    sheet.write(row, 13, conditions.get(a.condition, ""),
                                bad_f if a.condition in PROBLEM_CONDITIONS else cell_f)
                    sheet.write_number(row, 14, a.value or 0.0, money_f)
                    write_date(sheet, row, 15, a.purchase_date)
                    write_date(sheet, row, 16, a.warranty_end_date)
                    sheet.write(row, 17, a.note or "", cell_f)
                    row += 1
        sheet.autofilter(3, 0, max(row - 1, 3), len(headers) - 1)
        sheet.write(row, 10, "Total", tot_f)
        sheet.write_number(row, 11, data["qty"], tot_num_f)
        sheet.write_number(row, 14, data["value"], tot_money_f)

        # ---- Sheet 2: one line per unit, subtotal per building ----
        headers = [
            "Compound", "Building", "Unit Code", "Unit No.", "Floor", "Unit Type",
            "Asset Template", "Asset Lines", "Total Qty", "Damaged / Missing", "Value",
        ]
        widths = [22, 22, 14, 12, 7, 16, 20, 11, 11, 12, 13]
        sheet = start_sheet("By Unit", "Assets by Unit", headers, widths)
        row = 4
        for grp in data["groups"]:
            for unit in grp["units"]:
                prop = unit["property"]
                sheet.write(row, 0, grp["compound"], cell_f)
                sheet.write(row, 1, grp["building"], cell_f)
                sheet.write(row, 2, prop.property_seq or "", cell_f)
                sheet.write(row, 3, prop.building_no or prop.name or "", cell_f)
                sheet.write(row, 4, prop.floor or "", cell_f)
                sheet.write(row, 5, prop.property_subtype_id.name or "", cell_f)
                sheet.write(row, 6, prop.asset_template_id.name or "", cell_f)
                sheet.write_number(row, 7, len(unit["assets"]), num_f)
                sheet.write_number(row, 8, unit["qty"], num_f)
                sheet.write_number(row, 9, unit["problem"], bad_f if unit["problem"] else num_f)
                sheet.write_number(row, 10, unit["value"], money_f)
                row += 1
            sheet.merge_range(row, 0, row, 6, _("Total %s (%d units)", grp["building"], len(grp["units"])), tot_f)
            sheet.write_number(row, 7, grp["count"], tot_num_f)
            sheet.write_number(row, 8, grp["qty"], tot_num_f)
            sheet.write_number(row, 9, grp["problem"], tot_num_f)
            sheet.write_number(row, 10, grp["value"], tot_money_f)
            row += 2
        sheet.merge_range(row, 0, row, 6, _("Grand Total (%d units)", data["unit_count"]), tot_f)
        sheet.write_number(row, 7, data["count"], tot_num_f)
        sheet.write_number(row, 8, data["qty"], tot_num_f)
        sheet.write_number(row, 9, data["problem"], tot_num_f)
        sheet.write_number(row, 10, data["value"], tot_money_f)

        # ---- Sheet 3: totals per asset item ----
        headers = ["Category", "Asset", "Unit", "No. of Units", "Total Qty", "Damaged / Missing Qty", "Value"]
        widths = [18, 30, 8, 12, 11, 14, 13]
        sheet = start_sheet("By Item", "Asset Totals by Item", headers, widths)
        row = 4
        for item in data["items"]:
            sheet.write(row, 0, item["category"], cell_f)
            sheet.write(row, 1, item["name"] or "", cell_f)
            sheet.write(row, 2, item["uom"], cell_f)
            sheet.write_number(row, 3, item["units"], num_f)
            sheet.write_number(row, 4, item["qty"], num_f)
            sheet.write_number(row, 5, item["problem"], bad_f if item["problem"] else num_f)
            sheet.write_number(row, 6, item["value"], money_f)
            row += 1
        sheet.autofilter(3, 0, max(row - 1, 3), len(headers) - 1)

        wb.close()
        output.seek(0)
        name = self.subproject_id.name or self.property_project_id.name or "All"
        self.write({
            "report_file": base64.b64encode(output.read()),
            "file_name": "Asset_Report_%s.xlsx" % name.replace(" ", "_").replace("/", "-"),
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/?model=property.asset.report.wizard&id={self.id}"
                   "&field=report_file&filename_field=file_name&download=true",
            "target": "self",
        }