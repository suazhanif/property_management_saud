# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Head Lease on Compounds.

Some compounds (and their buildings/units) are not owned but held under a
head lease from an external lessor.  This adds the head-lease details to
``property.project`` and a daily scheduler that emails the compound's
assigned officers a configurable number of days before the lease ends.

Recipients: ``user_ids`` (Assigned Officers, see property_assignment.py).
If no officer is assigned, the Rental Managers are notified instead.
"""
import logging

from dateutil.relativedelta import relativedelta
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PropertyProjectHeadLease(models.Model):
    _inherit = "property.project"

    is_head_lease = fields.Boolean(
        string="Under Head Lease",
        tracking=True,
        help="Tick when this compound is leased from an external lessor "
        "rather than owned.",
    )
    head_lease_lessor_id = fields.Many2one(
        "res.partner", string="Lessor", tracking=True
    )
    head_lease_ref = fields.Char(string="Lease Contract No.", tracking=True)
    head_lease_start_date = fields.Date(string="Lease Start Date", tracking=True)
    head_lease_end_date = fields.Date(string="Lease End Date", tracking=True)
    head_lease_amount = fields.Monetary(
        string="Rent Amount (per period)", currency_field="currency_id", tracking=True
    )
    head_lease_document = fields.Binary(string="Lease Contract", attachment=True)
    head_lease_document_name = fields.Char(string="Lease Contract File Name")
    head_lease_note = fields.Text(string="Lease Notes")

    # --- Contract & parties ---
    head_lease_signing_date = fields.Date(string="Signing Date", tracking=True)
    head_lease_registration_no = fields.Char(
        string="Registration No.", tracking=True,
        help="Lease registration number with the Municipality / authority.",
    )
    head_lease_registration_date = fields.Date(string="Registration Date")
    head_lease_lessor_contact_id = fields.Many2one(
        "res.partner", string="Lessor Contact Person",
        domain="[('parent_id', '=', head_lease_lessor_id)]",
    )
    head_lease_lessor_phone = fields.Char(related="head_lease_lessor_id.phone", string="Lessor Phone")
    head_lease_lessor_email = fields.Char(related="head_lease_lessor_id.email", string="Lessor Email")
    head_lease_attachment_ids = fields.Many2many(
        "ir.attachment", "property_project_head_lease_attachment_rel",
        "project_id", "attachment_id", string="Amendments & Other Documents",
    )

    # --- Term & renewal ---
    head_lease_duration = fields.Char(string="Lease Duration", compute="_compute_head_lease_terms")
    head_lease_notice_days = fields.Integer(
        string="Notice Period (Days)", tracking=True,
        help="Days of notice required before the end date to renew or exit.",
    )
    head_lease_notice_deadline = fields.Date(
        string="Notice Deadline", compute="_compute_head_lease_notice_deadline", store=True,
        help="Last day to give notice to the lessor (end date minus notice period).",
    )
    head_lease_renewal_option = fields.Selection(
        [
            ("none", "No Renewal Option"),
            ("auto", "Automatic Renewal"),
            ("mutual", "Renewal by Mutual Agreement"),
            ("lessee_option", "Lessee's Option to Renew"),
        ],
        string="Renewal Option", tracking=True,
    )
    head_lease_renewal_terms = fields.Text(string="Renewal Terms")

    # --- Financial ---
    head_lease_rent_frequency = fields.Selection(
        [
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("semi_annual", "Semi-Annual"),
            ("annual", "Annual"),
            ("one_time", "One Time (full term)"),
        ],
        string="Rent Frequency", default="annual", tracking=True,
    )
    head_lease_annual_rent = fields.Monetary(
        string="Annual Rent", currency_field="currency_id", compute="_compute_head_lease_terms",
    )
    head_lease_payment_method = fields.Selection(
        [
            ("cheque", "Cheque / PDC"),
            ("bank_transfer", "Bank Transfer"),
            ("cash", "Cash"),
            ("other", "Other"),
        ],
        string="Payment Method", tracking=True,
    )
    head_lease_installments = fields.Integer(string="No. of Cheques / Installments")
    head_lease_deposit = fields.Monetary(
        string="Security Deposit", currency_field="currency_id", tracking=True)
    head_lease_escalation_percent = fields.Float(
        string="Annual Escalation (%)", digits=(5, 2), tracking=True)
    head_lease_escalation_start = fields.Date(string="Escalation Starts On")
    head_lease_rent_free_days = fields.Integer(string="Rent-Free / Grace Period (Days)")

    # --- Usage & responsibilities ---
    head_lease_permitted_use = fields.Char(
        string="Permitted Use", help="e.g. Staff accommodation, Residential, Commercial")
    head_lease_leased_area = fields.Float(string="Leased Area (sqm)")
    head_lease_maintenance_by = fields.Selection(
        [("lessor", "Lessor"), ("lessee", "Lessee"), ("shared", "Shared")],
        string="Major Maintenance By",
    )
    head_lease_utilities_by = fields.Selection(
        [("lessor", "Lessor"), ("lessee", "Lessee"), ("shared", "Shared")],
        string="Utilities Paid By",
    )
    head_lease_sublet_allowed = fields.Boolean(string="Sub-letting Allowed")
    head_lease_insurance_required = fields.Boolean(string="Insurance Required")

    head_lease_reminder_days = fields.Integer(
        string="Remind Before (Days)",
        default=90,
        tracking=True,
        help="Number of days before the lease end date the assigned "
        "officers are emailed. 0 disables the reminder.",
    )
    head_lease_reminder_date = fields.Date(
        string="Reminder Date",
        compute="_compute_head_lease_reminder_date",
        store=True,
    )
    head_lease_reminder_sent = fields.Boolean(
        string="Reminder Sent", default=False, copy=False, readonly=True
    )
    head_lease_reminder_sent_date = fields.Date(
        string="Reminder Sent On", copy=False, readonly=True
    )
    head_lease_days_left = fields.Integer(
        string="Days Left", compute="_compute_head_lease_days_left"
    )
    head_lease_state = fields.Selection(
        [
            ("active", "Active"),
            ("expiring", "Expiring Soon"),
            ("expired", "Expired"),
        ],
        string="Lease Status",
        compute="_compute_head_lease_days_left",
    )

    # ------------------------------------------------------------------ #
    #  Compute / constraints                                             #
    # ------------------------------------------------------------------ #
    @api.depends("head_lease_end_date", "head_lease_reminder_days")
    def _compute_head_lease_reminder_date(self):
        for rec in self:
            if rec.head_lease_end_date and rec.head_lease_reminder_days > 0:
                rec.head_lease_reminder_date = rec.head_lease_end_date - relativedelta(
                    days=rec.head_lease_reminder_days
                )
            else:
                rec.head_lease_reminder_date = False

    @api.depends("is_head_lease", "head_lease_end_date", "head_lease_reminder_date")
    def _compute_head_lease_days_left(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not (rec.is_head_lease and rec.head_lease_end_date):
                rec.head_lease_days_left = 0
                rec.head_lease_state = False
                continue
            days = (rec.head_lease_end_date - today).days
            rec.head_lease_days_left = max(days, 0)
            if days < 0:
                rec.head_lease_state = "expired"
            elif rec.head_lease_reminder_date and today >= rec.head_lease_reminder_date:
                rec.head_lease_state = "expiring"
            else:
                rec.head_lease_state = "active"

    @api.depends("head_lease_end_date", "head_lease_notice_days")
    def _compute_head_lease_notice_deadline(self):
        for rec in self:
            end = rec.head_lease_end_date
            rec.head_lease_notice_deadline = (
                end - relativedelta(days=rec.head_lease_notice_days)
                if end and rec.head_lease_notice_days > 0 else False
            )

    @api.depends(
        "head_lease_start_date", "head_lease_end_date",
        "head_lease_amount", "head_lease_rent_frequency",
    )
    def _compute_head_lease_terms(self):
        periods_per_year = {"monthly": 12, "quarterly": 4, "semi_annual": 2, "annual": 1}
        for rec in self:
            start, end = rec.head_lease_start_date, rec.head_lease_end_date
            duration = False
            if start and end and end >= start:
                delta = relativedelta(end + relativedelta(days=1), start)
                parts = []
                if delta.years:
                    parts.append(_("%s year(s)", delta.years))
                if delta.months:
                    parts.append(_("%s month(s)", delta.months))
                if delta.days or not parts:
                    parts.append(_("%s day(s)", delta.days))
                duration = " ".join(parts)
            rec.head_lease_duration = duration
            freq = rec.head_lease_rent_frequency
            if freq in periods_per_year:
                rec.head_lease_annual_rent = rec.head_lease_amount * periods_per_year[freq]
            elif freq == "one_time" and start and end and end > start:
                years = ((end - start).days + 1) / 365.0
                rec.head_lease_annual_rent = rec.head_lease_amount / years if years else 0.0
            else:
                rec.head_lease_annual_rent = rec.head_lease_amount

    @api.constrains(
        "head_lease_start_date", "head_lease_end_date", "head_lease_reminder_days",
        "head_lease_notice_days", "head_lease_installments", "head_lease_rent_free_days",
        "head_lease_escalation_percent",
    )
    def _check_head_lease(self):
        for rec in self:
            if (
                rec.head_lease_start_date
                and rec.head_lease_end_date
                and rec.head_lease_end_date < rec.head_lease_start_date
            ):
                raise ValidationError(
                    _("Head lease end date must be after the start date.")
                )
            if rec.head_lease_reminder_days < 0:
                raise ValidationError(_("Reminder days must be zero or greater."))
            if rec.head_lease_notice_days < 0 or rec.head_lease_installments < 0 \
                    or rec.head_lease_rent_free_days < 0 or rec.head_lease_escalation_percent < 0:
                raise ValidationError(_("Head lease numbers (notice period, installments, "
                                        "rent-free days, escalation) cannot be negative."))

    def write(self, vals):
        # Lease renewed / reminder period changed → allow a new reminder.
        if {"head_lease_end_date", "head_lease_reminder_days", "is_head_lease"} & set(vals):
            vals = dict(vals, head_lease_reminder_sent=False, head_lease_reminder_sent_date=False)
        return super().write(vals)

    # ------------------------------------------------------------------ #
    #  Reminder                                                          #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _head_lease_user_email(user):
        """Email of a user: partner email, else the login if it is an email.

        Many users are created with their email as *login* only, leaving
        the partner ``email`` empty — those must still be notified.
        """
        email = (user.partner_id.email or "").strip()
        if not email and user.login and "@" in user.login:
            email = user.login.strip()
        return email

    def _get_head_lease_reminder_users(self):
        """Assigned officers of the compound and of its buildings;
        Rental Managers when nobody is assigned."""
        self.ensure_one()
        users = (self.user_ids | self.sub_project_ids.user_ids).sudo()
        if not users:
            manager_group = self.env.ref(
                "rental_management.property_rental_manager", raise_if_not_found=False
            )
            if manager_group:
                field = "all_user_ids" if "all_user_ids" in manager_group._fields else "user_ids"
                users = manager_group.sudo()[field]
        return users.filtered(lambda u: u.active and not u.share)

    def action_send_head_lease_reminder(self):
        """Email the head-lease expiry reminder (also usable as a button)."""
        template = self.env.ref(
            "rental_management.head_lease_expiry_reminder_template",
            raise_if_not_found=False,
        )
        if not template:
            raise UserError(_("The head lease reminder email template is missing."))
        manual = not self.env.context.get("head_lease_cron")
        today = fields.Date.context_today(self)
        for rec in self:
            users = rec._get_head_lease_reminder_users()
            emails = []
            for user in users:
                email = rec._head_lease_user_email(user)
                if email and email not in emails:
                    emails.append(email)
            if not emails:
                msg = _(
                    "No recipient email found for compound %(name)s "
                    "(%(count)d candidate user(s): %(users)s). Set an email on "
                    "the users' contact or use an email as their login.",
                    name=rec.display_name,
                    count=len(users),
                    users=", ".join(users.mapped("name")) or "-",
                )
                if manual:
                    raise UserError(msg)
                _logger.warning(msg)
                continue
            template.send_mail(
                rec.id,
                force_send=True,
                email_values={"email_to": ",".join(emails), "recipient_ids": []},
            )
            rec.message_post(
                body=_(
                    "Head lease expiry reminder sent to %(names)s "
                    "(lease ends %(date)s).",
                    names=", ".join(emails),
                    date=rec.head_lease_end_date,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            # Bypass our write() reset of the flag.
            super(PropertyProjectHeadLease, rec).write(
                {"head_lease_reminder_sent": True, "head_lease_reminder_sent_date": today}
            )
        return True

    @api.model
    def cron_head_lease_expiry_reminder(self):
        """Scheduler: send reminders that are due and not sent yet.

        Uses ``reminder_date <= today`` (not ``==``) so a missed cron run
        still sends the reminder the next day, as long as the lease has not
        already ended.
        """
        today = fields.Date.context_today(self)
        compounds = self.sudo().search(
            [
                ("is_head_lease", "=", True),
                ("head_lease_reminder_sent", "=", False),
                ("head_lease_reminder_date", "!=", False),
                ("head_lease_reminder_date", "<=", today),
                ("head_lease_end_date", ">=", today),
            ]
        )
        for compound in compounds:
            try:
                compound.with_context(head_lease_cron=True).action_send_head_lease_reminder()
            except Exception:  # noqa: BLE001 — one bad record must not stop the cron
                _logger.exception(
                    "Head lease reminder failed for compound %s", compound.display_name
                )