# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Restrict a user to one kind of compound (Rent / Employee Family /
Bachelor / Sale).

* ``res.users.property_access_type`` — empty means no restriction.
* Global record rules (security/property_access_type_rules.xml) filter
  Compounds, Buildings and Properties on that value, for every user it is
  set on (officers and managers alike).
* Users restricted to employee accommodation see the property status
  "On Rent" labelled "Occupied".
"""
from odoo import _, api, fields, models

PROPERTY_ACCESS_TYPES = [
    ("rent", "Rent"),
    ("employee_family", "Employee Family Accommodation"),
    ("bachelor", "Bachelor Accommodation"),
    ("sale", "Sale"),
]

# Users of these types see "Occupied" instead of "On Rent".
OCCUPIED_LABEL_TYPES = ("employee_family", "bachelor")


class ResUsersPropertyAccessType(models.Model):
    _inherit = "res.users"

    property_access_type = fields.Selection(
        PROPERTY_ACCESS_TYPES,
        string="Property Type Access",
        help="Limit this user to compounds, buildings and properties of one type. "
        "Leave empty to keep the normal access.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if any(vals.get("property_access_type") for vals in vals_list):
            # Record-rule domains are cached per user.
            self.env.registry.clear_cache()
        return users

    def write(self, vals):
        res = super().write(vals)
        if "property_access_type" in vals:
            self.env.registry.clear_cache()
        return res


class PropertyDetailsStageLabel(models.Model):
    _inherit = "property.details"

    # Same values as before; only the label of 'on_lease' depends on the user.
    stage = fields.Selection(selection="_selection_stage")

    @api.model
    def _selection_stage(self):
        occupied = self.env.user.property_access_type in OCCUPIED_LABEL_TYPES
        return [
            ("draft", _("Draft")),
            ("available", _("Available")),
            ("booked", _("In Booking")),
            ("on_lease", _("Occupied") if occupied else _("On Rent")),
            ("sale", _("In Sale")),
            ("sold", _("Sold")),
        ]