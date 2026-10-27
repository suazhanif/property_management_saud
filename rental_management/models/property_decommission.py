# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Decommission (archive) Compounds, Buildings and Properties.

* Archiving a Compound archives all its Buildings and Units.
* Archiving a Building archives all its Units.
* Restoring a parent restores only the children that were archived together
  with it (not units that had been decommissioned on their own before).
* Not allowed while a unit is booked / rented / occupied or has an open
  rent contract or occupancy.
* Only Rental Managers can archive / restore.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

BLOCKING_TENANCY_STATES = ("pending_approval", "approved", "running_contract")
BLOCKING_OCCUPY_STATES = ("draft", "running")
BLOCKING_UNIT_STAGES = ("booked", "on_lease")


class PropertyDecommissionMixin(models.AbstractModel):
    _name = "property.decommission.mixin"
    _description = "Compound / Building / Property Decommissioning"

    active = fields.Boolean(default=True, tracking=True)
    decommission_date = fields.Date(string="Decommissioned On", copy=False, readonly=True)
    decommission_reason = fields.Text(string="Decommission Reason", copy=False, readonly=True)
    decommissioned_by_id = fields.Many2one(
        "res.users", string="Decommissioned By", copy=False, readonly=True)
    archived_with_parent = fields.Boolean(
        copy=False, readonly=True,
        help="Archived automatically together with its compound / building; "
        "restored automatically when the parent is restored.")

    # ------------------------------------------------------------------ #
    #  Hierarchy (overridden per model)                                  #
    # ------------------------------------------------------------------ #
    def _get_decommission_buildings(self):
        return self.env["property.sub.project"]

    def _get_decommission_units(self):
        return self.env["property.details"]

    def _get_decommission_children(self):
        """Records archived / restored together with self."""
        return [self._get_decommission_buildings(), self._get_decommission_units()]

    def _get_restore_blockers(self):
        return []

    # ------------------------------------------------------------------ #
    #  Checks                                                            #
    # ------------------------------------------------------------------ #
    def _get_decommission_blockers(self):
        units = self._get_decommission_units()
        if not units:
            return []
        msgs = []
        stage_labels = dict(units._fields["stage"]._description_selection(self.env))
        for unit in units.filtered(lambda u: u.stage in BLOCKING_UNIT_STAGES):
            msgs.append(_("%(unit)s is %(stage)s",
                          unit=unit.display_name, stage=stage_labels.get(unit.stage)))
        Tenancy = self.env["tenancy.details"].sudo()
        type_labels = dict(Tenancy._fields["contract_type"]._description_selection(self.env))
        for contract in Tenancy.search([
            ("property_id", "in", units.ids),
            ("contract_type", "in", BLOCKING_TENANCY_STATES),
        ]):
            msgs.append(_("%(unit)s: rent contract %(ref)s is %(state)s",
                          unit=contract.property_id.display_name, ref=contract.tenancy_seq,
                          state=type_labels.get(contract.contract_type)))
        Occupy = self.env["occupy.details"].sudo()
        for occ in Occupy.search([
            ("property_id", "in", units.ids),
            ("state", "in", BLOCKING_OCCUPY_STATES),
        ]):
            msgs.append(_("%(unit)s: occupancy %(ref)s (%(who)s) is still open",
                          unit=occ.property_id.display_name, ref=occ.occupy_seq,
                          who=occ.occupant_id.name or "-"))
        return list(dict.fromkeys(msgs))

    @staticmethod
    def _format_blockers(title, msgs, limit=15):
        lines = msgs[:limit]
        if len(msgs) > limit:
            lines.append(_("... and %s more", len(msgs) - limit))
        return title + "\n\n• " + "\n• ".join(lines)

    def _check_decommission_rights(self):
        if not self.env.su and not self.env.user.has_group(
            "rental_management.property_rental_manager"
        ):
            raise AccessError(_("Only Rental Managers can decommission or restore %s.",
                                self._description))

    # ------------------------------------------------------------------ #
    #  Archive / restore with cascade                                    #
    # ------------------------------------------------------------------ #
    def write(self, vals):
        if "active" not in vals or self.env.context.get("decommission_cascade"):
            return super().write(vals)
        self._check_decommission_rights()
        if vals["active"]:
            return self._restore_write(vals)
        return self._archive_write(vals)

    def _archive_write(self, vals):
        targets = self.filtered("active")
        blockers = targets._get_decommission_blockers()
        if blockers:
            raise UserError(self._format_blockers(
                _("Cannot decommission while units are still in use. "
                  "Close / vacate them first:"), blockers))
        ctx = self.env.context
        info = {
            "decommission_date": ctx.get("decommission_date") or fields.Date.context_today(self),
            "decommission_reason": ctx.get("decommission_reason") or False,
            "decommissioned_by_id": self.env.uid,
        }
        res = super().write(dict(vals, **info, archived_with_parent=False))
        for children in targets._get_decommission_children():
            children = children.filtered("active")
            if children:
                children.with_context(decommission_cascade=True).write(
                    dict(info, active=False, archived_with_parent=True))
        for rec in targets:
            rec.message_post(
                body=_("Decommissioned. Reason: %s", info["decommission_reason"] or "-"),
                subtype_xmlid="mail.mt_note")
        return res

    def _restore_write(self, vals):
        targets = self.filtered(lambda r: not r.active)
        blockers = targets._get_restore_blockers()
        if blockers:
            raise UserError(self._format_blockers(_("Cannot restore:"), blockers))
        clear = {
            "decommission_date": False,
            "decommission_reason": False,
            "decommissioned_by_id": False,
            "archived_with_parent": False,
        }
        res = super().write(dict(vals, **clear))
        for children in targets._get_decommission_children():
            children = children.filtered(lambda c: not c.active and c.archived_with_parent)
            if children:
                children.with_context(decommission_cascade=True).write(dict(clear, active=True))
        for rec in targets:
            rec.message_post(body=_("Restored (re-activated)."), subtype_xmlid="mail.mt_note")
        return res

    # ------------------------------------------------------------------ #
    #  Buttons                                                           #
    # ------------------------------------------------------------------ #
    def action_open_decommission_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Decommission %s", self.display_name),
            "res_model": "property.decommission.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_res_model": self._name, "default_res_id": self.id},
        }

    def action_restore_decommissioned(self):
        self.write({"active": True})
        return True


class PropertyProjectDecommission(models.Model):
    _name = "property.project"
    _inherit = ["property.project", "property.decommission.mixin"]

    def _get_decommission_buildings(self):
        return self.env["property.sub.project"].with_context(active_test=False).search(
            [("property_project_id", "in", self.ids)])

    def _get_decommission_units(self):
        Unit = self.env["property.details"].with_context(active_test=False)
        buildings = self._get_decommission_buildings()
        return Unit.search(["|", ("property_project_id", "in", self.ids),
                            ("subproject_id", "in", buildings.ids)])


class PropertySubProjectDecommission(models.Model):
    _name = "property.sub.project"
    _inherit = ["property.sub.project", "property.decommission.mixin"]

    def _get_decommission_units(self):
        return self.env["property.details"].with_context(active_test=False).search(
            [("subproject_id", "in", self.ids)])

    def _get_decommission_children(self):
        return [self._get_decommission_units()]

    def _get_restore_blockers(self):
        return [
            _("Compound %s of building %s is decommissioned. Restore the compound first.",
              rec.property_project_id.display_name, rec.display_name)
            for rec in self
            if rec.property_project_id and not rec.property_project_id.active
        ]


class PropertyDetailsDecommission(models.Model):
    _name = "property.details"
    _inherit = ["property.details", "property.decommission.mixin"]

    def _get_decommission_units(self):
        return self

    def _get_decommission_children(self):
        return []

    def _get_restore_blockers(self):
        msgs = []
        for rec in self:
            if rec.subproject_id and not rec.subproject_id.active:
                msgs.append(_("Building %s of %s is decommissioned. Restore the building first.",
                              rec.subproject_id.display_name, rec.display_name))
            elif rec.property_project_id and not rec.property_project_id.active:
                msgs.append(_("Compound %s of %s is decommissioned. Restore the compound first.",
                              rec.property_project_id.display_name, rec.display_name))
        return msgs