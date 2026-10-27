# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PropertyDecommissionWizard(models.TransientModel):
    _name = "property.decommission.wizard"
    _description = "Decommission Compound / Building / Property"

    res_model = fields.Selection(
        [("property.project", "Compound"),
         ("property.sub.project", "Building"),
         ("property.details", "Property")],
        string="Type", required=True)
    res_id = fields.Integer(required=True)
    record_name = fields.Char(string="Decommission", compute="_compute_summary")
    decommission_date = fields.Date(
        string="Decommission Date", required=True, default=fields.Date.context_today)
    reason = fields.Text(string="Reason", required=True)
    building_count = fields.Integer(string="Buildings Affected", compute="_compute_summary")
    unit_count = fields.Integer(string="Units Affected", compute="_compute_summary")
    blocker_text = fields.Text(compute="_compute_summary")
    has_blockers = fields.Boolean(compute="_compute_summary")

    def _get_record(self):
        self.ensure_one()
        return self.env[self.res_model].with_context(active_test=False).browse(self.res_id).exists()

    @api.depends("res_model", "res_id")
    def _compute_summary(self):
        for wiz in self:
            rec = wiz._get_record() if wiz.res_model and wiz.res_id else False
            if not rec:
                wiz.update({"record_name": False, "building_count": 0, "unit_count": 0,
                            "blocker_text": False, "has_blockers": False})
                continue
            blockers = rec._get_decommission_blockers()
            wiz.record_name = rec.display_name
            wiz.building_count = len(rec._get_decommission_buildings().filtered("active"))
            wiz.unit_count = len(rec._get_decommission_units().filtered("active"))
            wiz.has_blockers = bool(blockers)
            wiz.blocker_text = "\n".join("• " + b for b in blockers) if blockers else False

    def action_confirm(self):
        self.ensure_one()
        rec = self._get_record()
        if not rec:
            raise UserError(_("The record no longer exists."))
        rec.with_context(
            decommission_date=self.decommission_date,
            decommission_reason=self.reason,
        ).write({"active": False})
        return {"type": "ir.actions.act_window_close"}