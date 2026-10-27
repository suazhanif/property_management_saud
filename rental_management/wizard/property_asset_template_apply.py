# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PropertyAssetTemplateApplyWizard(models.TransientModel):
    _name = "property.asset.template.apply.wizard"
    _description = "Apply Asset Template to Properties"

    template_id = fields.Many2one("property.asset.template", string="Asset Template", required=True)
    property_ids = fields.Many2many("property.details", string="Properties")
    mode = fields.Selection(
        [("add", "Add missing items"), ("replace", "Replace current assets")],
        default="add", required=True,
    )
    set_as_template = fields.Boolean(
        string="Set as property template", default=True,
        help="Also store this template on the selected properties.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "property.details":
            res["property_ids"] = [(6, 0, self.env.context.get("active_ids", []))]
        return res

    def action_apply(self):
        self.ensure_one()
        if not self.property_ids:
            raise UserError(_("Select at least one property."))
        properties = self.property_ids.with_context(skip_asset_autofill=True)
        if self.set_as_template:
            properties.write({"asset_template_id": self.template_id.id})
        properties._apply_asset_template(template=self.template_id, mode=self.mode)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Asset Template Applied"),
                "message": _("%(tmpl)s applied to %(count)d propert(ies).",
                             tmpl=self.template_id.name, count=len(self.property_ids)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }