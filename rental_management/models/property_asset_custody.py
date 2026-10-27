# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Asset custody: who is responsible for each asset of a unit, with history.

A custody line is opened for every asset of a unit when an occupancy
(occupy.details) starts running or a rent contract (tenancy.details) becomes
a running contract, and closed when it ends. So when an employee leaves and
a new one moves in, the new occupant automatically becomes responsible and
the previous holder stays in the history.
"""
from odoo import _, api, fields, models

OCCUPY_ACTIVE = ("running",)
OCCUPY_ENDED = ("close", "expire", "cancel")
TENANCY_ACTIVE = ("running_contract",)
TENANCY_ENDED = ("close_contract", "expire_contract", "cancel_contract")


class PropertyUnitAssetCustody(models.Model):
    _name = "property.unit.asset.custody"
    _description = "Asset Custody (Responsible Person)"
    _order = "date_from desc, id desc"

    asset_id = fields.Many2one("property.unit.asset", required=True, ondelete="cascade", index=True)
    property_id = fields.Many2one(related="asset_id.property_id", store=True, string="Property")
    partner_id = fields.Many2one("res.partner", string="Responsible", required=True)
    employee_id = fields.Many2one("hr.employee", string="Employee")
    occupy_id = fields.Many2one("occupy.details", string="Occupancy", ondelete="set null", index=True)
    tenancy_id = fields.Many2one("tenancy.details", string="Rent Contract", ondelete="set null", index=True)
    date_from = fields.Date(string="Responsible From", required=True, default=fields.Date.context_today)
    date_to = fields.Date(string="Responsible Until")
    condition_in = fields.Char(string="Condition at Handover")
    condition_out = fields.Char(string="Condition at Return")
    is_current = fields.Boolean(compute="_compute_is_current", store=True, string="Current")
    note = fields.Char()

    @api.depends("date_to")
    def _compute_is_current(self):
        for rec in self:
            rec.is_current = not rec.date_to

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id and not self.partner_id:
            self.partner_id = self.employee_id.work_contact_id


class PropertyUnitAssetCustodyFields(models.Model):
    _inherit = "property.unit.asset"

    custody_ids = fields.One2many("property.unit.asset.custody", "asset_id", string="Custody History")
    responsible_ids = fields.Many2many(
        "res.partner", compute="_compute_responsible", string="Responsible")
    responsible_since = fields.Date(compute="_compute_responsible", string="Responsible Since")

    @api.depends("custody_ids.date_to", "custody_ids.partner_id")
    def _compute_responsible(self):
        for asset in self:
            current = asset.custody_ids.filtered("is_current")
            asset.responsible_ids = current.partner_id
            asset.responsible_since = min(current.mapped("date_from")) if current else False

    def _condition_label(self):
        self.ensure_one()
        return dict(self._fields["condition"]._description_selection(self.env)).get(self.condition, "")

    @api.model_create_multi
    def create(self, vals_list):
        assets = super().create(vals_list)
        # An asset added to an occupied unit belongs to the current occupant(s).
        for prop in assets.property_id:
            holders = prop._current_asset_holders()
            if holders:
                assets.filtered(lambda a: a.property_id == prop)._open_custody(holders)
        return assets

    def _open_custody(self, holders, exclusive=False):
        """Open custody lines for ``holders`` (list of dicts) on these assets."""
        Custody = self.env["property.unit.asset.custody"].sudo()
        today = fields.Date.context_today(self)
        vals_list = []
        for asset in self:
            open_lines = asset.sudo().custody_ids.filtered("is_current")
            if exclusive:
                keep = {(h.get("occupy_id"), h.get("tenancy_id")) for h in holders}
                stale = open_lines.filtered(
                    lambda l: (l.occupy_id.id or False, l.tenancy_id.id or False) not in keep)
                stale.write({"date_to": today, "condition_out": asset._condition_label()})
                open_lines -= stale
            for holder in holders:
                if open_lines.filtered(
                    lambda l: (l.occupy_id.id or False) == holder.get("occupy_id")
                    and (l.tenancy_id.id or False) == holder.get("tenancy_id")
                ):
                    continue
                vals_list.append(dict(holder, asset_id=asset.id,
                                      condition_in=asset._condition_label()))
        if vals_list:
            Custody.create(vals_list)


class PropertyDetailsAssetCustody(models.Model):
    _inherit = "property.details"

    def _current_asset_holders(self):
        """Running occupancies / rent contracts of this unit as custody values."""
        self.ensure_one()
        holders = []
        for occ in self.env["occupy.details"].sudo().search(
            [("property_id", "=", self.id), ("state", "in", OCCUPY_ACTIVE)]
        ):
            holders.append(occ._custody_holder_vals())
        for ten in self.env["tenancy.details"].sudo().search(
            [("property_id", "=", self.id), ("contract_type", "in", TENANCY_ACTIVE)]
        ):
            holders.append(ten._custody_holder_vals())
        return [h for h in holders if h.get("partner_id")]

    @api.model
    def _backfill_asset_custody(self):
        """Open custody for assets of units that are occupied today (idempotent)."""
        props = self.env["property.unit.asset"].sudo().search([]).property_id
        for prop in props:
            holders = prop._current_asset_holders()
            if holders:
                prop.asset_ids._open_custody(holders)

    # ---------------- Asset register report data ---------------- #
    def _asset_register_occupants(self):
        """Current occupants / tenants with their contract details (sudo:
        employee private fields such as civil ID are HR-restricted)."""
        self.ensure_one()
        rows = []
        lease_labels = dict(self._fields["sale_lease"]._description_selection(self.env))
        for occ in self.env["occupy.details"].sudo().search(
            [("property_id", "=", self.id), ("state", "in", OCCUPY_ACTIVE)], order="start_date"
        ):
            emp = occ.employee_id
            rows.append({
                "kind": _("Occupancy"),
                "name": occ.occupant_id.name or emp.name or "",
                "employee_code": occ.employee_code or "",
                "department": occ.department_id.name or "",
                "designation": emp.job_title or "",
                "phone": occ.occupant_phone or emp.mobile_phone or emp.work_phone or "",
                "email": occ.occupant_email or emp.work_email or "",
                "civil_id": emp.identification_id or "",
                "bed": occ.bed_id.name or "",
                "contract_ref": occ.occupy_seq or "",
                "contract_type": lease_labels.get(occ.property_id.sale_lease, ""),
                "start": occ.start_date,
                "end": occ.expected_end_date,
                "amount": False,
                "currency": False,
            })
        for ten in self.env["tenancy.details"].sudo().search(
            [("property_id", "=", self.id), ("contract_type", "in", TENANCY_ACTIVE)], order="start_date"
        ):
            partner = ten.tenancy_id
            rows.append({
                "kind": _("Rent Contract"),
                "name": partner.name or "",
                "employee_code": "",
                "department": "",
                "designation": partner.function or "",
                "phone": partner.phone or "",
                "email": partner.email or "",
                "civil_id": "",
                "bed": "",
                "contract_ref": ten.tenancy_seq or "",
                "contract_type": _("Rent"),
                "start": ten.start_date,
                "end": ten.end_date,
                "amount": ten.total_rent,
                "currency": ten.currency_id,
            })
        return rows

    def _asset_register_custody_history(self, limit=30):
        self.ensure_one()
        return self.env["property.unit.asset.custody"].sudo().search(
            [("property_id", "=", self.id)], order="date_from desc, id desc", limit=limit)


class OccupyDetailsAssetCustody(models.Model):
    _inherit = "occupy.details"

    def _custody_holder_vals(self):
        self.ensure_one()
        return {
            "partner_id": (self.occupant_id or self.employee_id.work_contact_id).id,
            "employee_id": self.employee_id.id or False,
            "occupy_id": self.id,
            "tenancy_id": False,
            "date_from": self.start_date or fields.Date.context_today(self),
        }

    def _sync_asset_custody(self):
        today = fields.Date.context_today(self)
        for occ in self:
            assets = occ.property_id.asset_ids
            if not assets:
                continue
            if occ.state in OCCUPY_ACTIVE:
                # Family / rent units have one holder; bachelor units share.
                exclusive = occ.property_id.sale_lease != "bachelor"
                assets._open_custody([occ._custody_holder_vals()], exclusive=exclusive)
            elif occ.state in OCCUPY_ENDED:
                lines = self.env["property.unit.asset.custody"].sudo().search(
                    [("occupy_id", "=", occ.id), ("is_current", "=", True)])
                for line in lines:
                    line.write({
                        "date_to": occ.terminate_date or today,
                        "condition_out": line.asset_id._condition_label(),
                    })

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered(lambda o: o.state in OCCUPY_ACTIVE)._sync_asset_custody()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "state" in vals:
            self._sync_asset_custody()
        return res


class TenancyDetailsAssetCustody(models.Model):
    _inherit = "tenancy.details"

    def _custody_holder_vals(self):
        self.ensure_one()
        return {
            "partner_id": self.tenancy_id.id,
            "employee_id": False,
            "occupy_id": False,
            "tenancy_id": self.id,
            "date_from": self.start_date or fields.Date.context_today(self),
        }

    def _sync_asset_custody(self):
        today = fields.Date.context_today(self)
        for ten in self:
            assets = ten.property_id.asset_ids
            if not assets or not ten.tenancy_id:
                continue
            if ten.contract_type in TENANCY_ACTIVE:
                assets._open_custody([ten._custody_holder_vals()], exclusive=True)
            elif ten.contract_type in TENANCY_ENDED:
                lines = self.env["property.unit.asset.custody"].sudo().search(
                    [("tenancy_id", "=", ten.id), ("is_current", "=", True)])
                for line in lines:
                    line.write({
                        "date_to": today,
                        "condition_out": line.asset_id._condition_label(),
                    })

    def write(self, vals):
        res = super().write(vals)
        if "contract_type" in vals:
            self._sync_asset_custody()
        return res