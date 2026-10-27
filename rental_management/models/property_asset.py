# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Property (unit) asset register with reusable asset templates."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ASSET_CONDITION = [
    ("new", "New"),
    ("good", "Good"),
    ("fair", "Fair"),
    ("damaged", "Damaged"),
    ("missing", "Missing"),
]


class PropertyAssetCategory(models.Model):
    _name = "property.asset.category"
    _description = "Property Asset Category"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class PropertyAssetTemplate(models.Model):
    _name = "property.asset.template"
    _description = "Property Asset Template"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Template", required=True, translate=True, tracking=True)
    property_subtype_id = fields.Many2one(
        "property.sub.type", string="Suggested For",
        help="Unit type this template is meant for (e.g. Apartment 2BHK). Informational only.",
    )
    line_ids = fields.One2many("property.asset.template.line", "template_id", string="Assets", copy=True)
    line_count = fields.Integer(compute="_compute_counts", string="Items")
    property_count = fields.Integer(compute="_compute_counts", string="Properties")
    note = fields.Text()
    active = fields.Boolean(default=True)

    def _compute_counts(self):
        Property = self.env["property.details"]
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.property_count = Property.search_count([("asset_template_id", "=", rec.id)])

    def action_view_properties(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Properties"),
            "res_model": "property.details",
            "view_mode": "list,form",
            "domain": [("asset_template_id", "=", self.id)],
            "context": {"create": False},
        }


class PropertyAssetTemplateLine(models.Model):
    _name = "property.asset.template.line"
    _description = "Property Asset Template Line"
    _order = "sequence, id"

    template_id = fields.Many2one("property.asset.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Asset", required=True, translate=True)
    category_id = fields.Many2one("property.asset.category", string="Category")
    quantity = fields.Float(string="Qty", default=1.0, digits=(16, 2))
    uom_id = fields.Many2one("uom.uom", string="Unit")
    brand = fields.Char()
    note = fields.Char()

    @api.constrains("quantity")
    def _check_quantity(self):
        for rec in self:
            if rec.quantity < 0:
                raise ValidationError(_("Quantity must be zero or greater."))


class PropertyUnitAsset(models.Model):
    """An asset physically assigned to one property/unit."""

    _name = "property.unit.asset"
    _description = "Property Asset"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "property_id, sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char(string="Asset", required=True, tracking=True)
    property_id = fields.Many2one(
        "property.details", string="Property", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    property_project_id = fields.Many2one(
        related="property_id.property_project_id", string="Compound", store=True)
    subproject_id = fields.Many2one(
        related="property_id.subproject_id", string="Building", store=True)
    category_id = fields.Many2one("property.asset.category", string="Category", tracking=True)
    quantity = fields.Float(string="Qty", default=1.0, digits=(16, 2), tracking=True)
    uom_id = fields.Many2one("uom.uom", string="Unit")
    brand = fields.Char()
    model_no = fields.Char(string="Model")
    serial_no = fields.Char(string="Serial / Tag No.", tracking=True)
    condition = fields.Selection(ASSET_CONDITION, default="good", required=True, tracking=True)
    purchase_date = fields.Date()
    warranty_end_date = fields.Date(string="Warranty Until")
    company_id = fields.Many2one(related="property_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    value = fields.Monetary(string="Value", currency_field="currency_id")
    template_id = fields.Many2one("property.asset.template", string="From Template", readonly=True)
    note = fields.Char()
    active = fields.Boolean(default=True)

    @api.constrains("quantity")
    def _check_quantity(self):
        for rec in self:
            if rec.quantity < 0:
                raise ValidationError(_("Quantity must be zero or greater."))


class PropertyDetailsAsset(models.Model):
    _inherit = "property.details"

    asset_template_id = fields.Many2one(
        "property.asset.template", string="Asset Template", tracking=True,
        help="Selecting a template on a property without assets fills the asset list automatically.",
    )
    asset_ids = fields.One2many("property.unit.asset", "property_id", string="Assets")
    asset_count = fields.Integer(compute="_compute_asset_count", string="Assets")

    @api.depends("asset_ids")
    def _compute_asset_count(self):
        for rec in self:
            rec.asset_count = len(rec.asset_ids)

    # ------------------------------------------------------------------ #
    def _apply_asset_template(self, template=None, mode="add"):
        """Copy template lines to the property's assets.

        mode='add'     → only add items not already present (same name + category)
        mode='replace' → delete current assets, then copy the template
        """
        Asset = self.env["property.unit.asset"]
        for rec in self:
            tmpl = template or rec.asset_template_id
            if not tmpl:
                raise UserError(_("Select an asset template on %s first.", rec.display_name))
            if mode == "replace":
                rec.asset_ids.unlink()
                existing = set()
            else:
                existing = {
                    ((a.name or "").strip().lower(), a.category_id.id) for a in rec.asset_ids
                }
            vals_list = []
            for line in tmpl.line_ids:
                key = ((line.name or "").strip().lower(), line.category_id.id)
                if key in existing:
                    continue
                existing.add(key)
                vals_list.append({
                    "property_id": rec.id,
                    "sequence": line.sequence,
                    "name": line.name,
                    "category_id": line.category_id.id,
                    "quantity": line.quantity,
                    "uom_id": line.uom_id.id,
                    "brand": line.brand,
                    "note": line.note,
                    "template_id": tmpl.id,
                })
            if vals_list:
                Asset.create(vals_list)
            rec.message_post(
                body=_("Asset template %(tmpl)s applied (%(mode)s): %(count)d item(s) added.",
                       tmpl=tmpl.name, mode=mode, count=len(vals_list)),
                subtype_xmlid="mail.mt_note",
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("skip_asset_autofill"):
            for rec in records.filtered(lambda r: r.asset_template_id and not r.asset_ids):
                rec._apply_asset_template()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("asset_template_id") and not self.env.context.get("skip_asset_autofill"):
            for rec in self.filtered(lambda r: not r.asset_ids):
                rec._apply_asset_template()
        return res

    def action_apply_asset_template(self):
        self._apply_asset_template(mode="add")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Asset Template Applied"),
                "message": _("Missing template items were added to the asset list."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_view_assets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Assets - %s", self.name),
            "res_model": "property.unit.asset",
            "view_mode": "list,form",
            "domain": [("property_id", "=", self.id)],
            "context": {"default_property_id": self.id},
        }