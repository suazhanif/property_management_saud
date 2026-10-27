# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Rent invoice period & unit number.

* Every rent installment (rent.invoice of type Rent / Full Rent) gets a
  "From - To" period, worked out from its schedule date, the contract
  payment term (or the number of months billed) and the contract end date.
* Its description, and the rent line of the linked customer invoice, read
  e.g. "Rent - Unit 101, Building A | From 01/01/2026 To 31/03/2026".
* The invoice keeps Period From / Period To, and each rental line shows the
  period ("Jan 2026", "Jan 2026 - Mar 2026") instead of the "Units" UoM on
  the printed invoice.
* Units are shown by their unit number (building_no) instead of the
  property name.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

TERM_MONTHS = {
    "monthly": 1,
    "bi_monthly": 2,
    "quarterly": 3,
    "half_year": 6,
    "year": 12,
}
RENT_TYPES = ("rent", "full_rent")
DATE_FMT = "%d/%m/%Y"


def _period_label(date_from, date_to, rent_unit=None):
    if not date_from or not date_to:
        return False
    if rent_unit == "Day":
        days = (date_to - date_from).days + 1
        return _("%s Day(s)", days)
    if (date_from.year, date_from.month) == (date_to.year, date_to.month):
        return date_from.strftime("%b %Y")
    return "%s - %s" % (date_from.strftime("%b %Y"), date_to.strftime("%b %Y"))


class PropertyDetailsUnitLabel(models.Model):
    _inherit = "property.details"

    unit_label = fields.Char(string="Unit", compute="_compute_unit_label")

    @api.depends("building_no", "name", "subproject_id.name", "property_project_id.name")
    def _compute_unit_label(self):
        for prop in self:
            parts = [_("Unit %s", prop.building_no or prop.name or "")]
            if prop.subproject_id:
                parts.append(prop.subproject_id.name)
            elif prop.property_project_id:
                parts.append(prop.property_project_id.name)
            prop.unit_label = ", ".join(p for p in parts if p)


class TenancyDetailsPeriod(models.Model):
    _inherit = "tenancy.details"

    def _rent_period(self, date_from, rent_amount=0.0, service_days=0):
        """(date_from, date_to) of the installment starting on ``date_from``."""
        self.ensure_one()
        if not date_from:
            return False, False
        date_from = fields.Date.to_date(date_from)
        rent_unit = self.rent_unit or self.final_rent_unit
        end = self.end_date
        if self.payment_term == "full_payment":
            return date_from, end or date_from
        if rent_unit == "Day":
            if service_days:
                date_to = date_from + relativedelta(days=service_days - 1)
            else:
                date_to = end or date_from
        else:
            months = 0
            if rent_amount and self.total_rent:
                ratio = rent_amount / self.total_rent
                if rent_unit == "Year":
                    ratio *= 12
                if ratio >= 0.95:
                    months = round(ratio)
            months = months or TERM_MONTHS.get(self.payment_term, 1)
            date_to = date_from + relativedelta(months=months) - relativedelta(days=1)
        if end and date_to > end:
            date_to = end
        return date_from, date_to

    def _rent_period_description(self, date_from, date_to, full=False):
        self.ensure_one()
        label = _("Full Rent") if full else _("Rent")
        unit = self.property_id.unit_label or ""
        return _(
            "%(label)s - %(unit)s | From %(from)s To %(to)s",
            label=label,
            unit=unit,
            **{"from": date_from.strftime(DATE_FMT), "to": date_to.strftime(DATE_FMT)},
        )


class RentInvoicePeriod(models.Model):
    _inherit = "rent.invoice"

    period_from = fields.Date(string="From")
    period_to = fields.Date(string="To")
    unit_label = fields.Char(related="tenancy_id.property_id.unit_label", string="Unit")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._apply_rent_period()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("rental_period_sync") and (
            {"rent_invoice_id", "invoice_date", "type", "rent_amount"} & set(vals)
        ):
            self._apply_rent_period()
        return res

    def _apply_rent_period(self):
        for line in self.filtered(lambda l: l.type in RENT_TYPES and l.tenancy_id and l.invoice_date):
            tenancy = line.tenancy_id
            move = line.rent_invoice_id.sudo()
            rent_lines = move.invoice_line_ids.filtered(
                lambda ml: ml.display_type == "product"
                and ml.product_id == tenancy.installment_item_id
            ) if move else False
            # An invoice without the installment item (e.g. broker brokerage
            # booked as "rent") is left untouched.
            if move and not rent_lines:
                continue
            date_from, date_to = tenancy._rent_period(
                line.invoice_date, line.rent_amount, line.service_days)
            if not date_to:
                continue
            description = tenancy._rent_period_description(
                date_from, date_to, full=line.type == "full_rent")
            line.with_context(rental_period_sync=True).write({
                "period_from": date_from,
                "period_to": date_to,
                "description": description,
            })
            if move:
                rent_unit = tenancy.rent_unit or tenancy.final_rent_unit
                move.write({
                    "rent_period_from": date_from,
                    "rent_period_to": date_to,
                })
                label = _period_label(date_from, date_to, rent_unit)
                for ml in rent_lines:
                    ml.with_context(check_move_validity=False).write({
                        "name": description,
                        "rent_period_label": label,
                    })
                # Merged service / maintenance lines show the same period.
                other = move.invoice_line_ids.filtered(
                    lambda ml: ml.display_type == "product" and ml not in rent_lines)
                for ml in other:
                    vals = {"rent_period_label": label}
                    suffix = _("From %(from)s To %(to)s",
                               **{"from": date_from.strftime(DATE_FMT),
                                  "to": date_to.strftime(DATE_FMT)})
                    if ml.name and suffix not in ml.name:
                        vals["name"] = "%s\n%s" % (ml.name, suffix)
                    ml.with_context(check_move_validity=False).write(vals)


class AccountMovePeriod(models.Model):
    _inherit = "account.move"

    rent_period_from = fields.Date(string="Period From", readonly=True, copy=False)
    rent_period_to = fields.Date(string="Period To", readonly=True, copy=False)
    tenancy_unit_label = fields.Char(
        related="tenancy_id.property_id.unit_label", string="Unit")


class AccountMoveLinePeriod(models.Model):
    _inherit = "account.move.line"

    rent_period_label = fields.Char(string="Period", copy=False)