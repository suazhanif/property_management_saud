# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ShiftOccupy(models.Model):
    """Shift Occupant from one accommodation to another (Persistent for audit)"""

    _name = "shift.occupy"
    _description = "Shift Occupy"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "shift_seq"

    # Sequence
    shift_seq = fields.Char(
        string="Shift Reference",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("New"),
    )

    # State
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "Waiting Approval"),
            ("approved", "Approved"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        default="draft",
        tracking=True,
        required=True,
    )

    # Employee
    employee_search_id = fields.Many2one(
        "hr.employee",
        string="Search by Employee",
        states={"draft": [("readonly", False)]},
    )

    # Current Occupation (source)
    occupy_id = fields.Many2one(
        "occupy.details",
        string="Current Occupation",
        required=True,
        tracking=True,
        domain="[('state', '=', 'running')]",
    )
    occupant_id = fields.Many2one(
        related="occupy_id.occupant_id",
        string="Occupant",
        readonly=True,
        store=True,
    )
    employee_id = fields.Many2one(
        related="occupy_id.employee_id",
        string="Employee",
        readonly=True,
        store=True,
    )

    # Shift From (source property - auto-populated)
    from_property_id = fields.Many2one(
        related="occupy_id.property_id",
        string="Shift From (Property)",
        readonly=True,
        store=True,
    )
    from_sale_lease = fields.Selection(
        related="from_property_id.sale_lease",
        string="From Accommodation Type",
        readonly=True,
        store=True,
    )
    from_bed_id = fields.Many2one(
        related="occupy_id.bed_id",
        string="Shift From (Bed)",
        readonly=True,
        store=True,
    )

    # Shift To (target property)
    to_property_id = fields.Many2one(
        "property.details",
        string="Shift To (Property)",
        required=True,
        readonly=True,
        states={"draft": [("readonly", False)]},
        domain="[('stage', '=', 'available'), ('sale_lease', 'in', ['employee_family', 'bachelor'])]",
        tracking=True,
    )
    to_sale_lease = fields.Selection(
        related="to_property_id.sale_lease",
        string="To Accommodation Type",
        readonly=True,
        store=True,
    )
    to_bed_id = fields.Many2one(
        "property.bed",
        string="Shift To (Bed)",
        readonly=True,
        states={"draft": [("readonly", False)]},
        domain="[('property_id', '=', to_property_id), ('state', '=', 'available')]",
    )

    # Shift Reason
    shift_reason = fields.Text(
        string="Shift Reason",
        required=True,
        readonly=True,
        states={"draft": [("readonly", False)]},
        tracking=True,
    )

    # Dates
    shift_date = fields.Date(
        string="Shift Date",
        default=fields.Date.today,
        readonly=True,
        states={"draft": [("readonly", False)]},
        tracking=True,
    )

    # Resulting new occupy
    new_occupy_id = fields.Many2one(
        "occupy.details",
        string="New Occupy Contract",
        readonly=True,
        tracking=True,
    )

    # Readings tracking
    is_readings_required = fields.Boolean(
        compute="_compute_is_readings_required",
        store=True,
    )

    # Utility Readings (for vacating old property)
    water_reading = fields.Float("Water Reading (m³)")
    water_reading_date = fields.Datetime("Water Reading Date/Time", default=fields.Datetime.now)
    electricity_reading = fields.Float("Electricity Reading (kWh)")
    electricity_reading_date = fields.Datetime("Electricity Reading Date/Time", default=fields.Datetime.now)

    # Company
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    # Opened from an occupancy contract: contract + employee are fixed
    occupy_locked = fields.Boolean(default=lambda self: self._default_occupy_locked())

    # Approval & supporting document
    approval_required = fields.Boolean(
        string="Requires Approval",
        default=lambda self: self._default_approval_required(),
        tracking=True,
    )
    approval_document = fields.Binary(string="Supporting Document", attachment=True, copy=False)
    approval_document_name = fields.Char(copy=False)
    approval_note = fields.Text(string="Approval Remarks", copy=False)
    approved_by_id = fields.Many2one("res.users", string="Approved By", readonly=True, copy=False)
    approved_date = fields.Datetime(string="Approved On", readonly=True, copy=False)

    @api.model
    def _default_occupy_locked(self):
        ctx = self.env.context
        return bool(ctx.get("default_occupy_id") or ctx.get("active_model") == "occupy.details")

    @api.model
    def _default_approval_required(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "rental_management.shift_approval_required") == "True"

    @api.model
    def _is_rental_manager(self):
        return self.env.user.has_group("rental_management.property_rental_manager")

    @api.model
    def default_get(self, fields_list):
        """Take the contract (and its employee) from the occupancy the
        wizard was opened from."""
        res = super().default_get(fields_list)
        ctx = self.env.context
        occupy_id = res.get("occupy_id") or (
            ctx.get("active_id") if ctx.get("active_model") == "occupy.details" else False)
        if occupy_id:
            occupy = self.env["occupy.details"].browse(occupy_id)
            res["occupy_id"] = occupy.id
            if "employee_search_id" in fields_list and occupy.employee_id:
                res["employee_search_id"] = occupy.employee_id.id
        return res

    @api.onchange("employee_search_id")
    def _onchange_employee_search_id(self):
        for rec in self:
            if rec.occupy_locked or not rec.employee_search_id:
                continue
            if rec.occupy_id.employee_id != rec.employee_search_id:
                rec.occupy_id = rec.employee_search_id.occupy_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Set Current Occupation
            if not vals.get("occupy_id") and vals.get("employee_search_id"):
                employee = self.env["hr.employee"].browse(vals["employee_search_id"])
                if employee.occupy_id:
                    vals["occupy_id"] = employee.occupy_id.id
                    # Non-managers cannot skip approval while it is required globally
            if self._default_approval_required() and not self._is_rental_manager():
                vals["approval_required"] = True
            # Set Sequence
            if vals.get("shift_seq", "New") == "New":
                vals["shift_seq"] = (
                        self.env["ir.sequence"].next_by_code("shift.occupy") or "New"
                )
        return super(ShiftOccupy, self).create(vals_list)

    @api.depends("from_property_id", "from_property_id.sale_lease")
    def _compute_is_readings_required(self):
        for rec in self:
            rec.is_readings_required = (
                    rec.from_property_id.sale_lease == "employee_family"
            )

    @api.onchange("to_property_id")
    def _onchange_to_property_id(self):
        self.to_bed_id = False

    @api.onchange("occupy_id")
    def _onchange_occupy_id(self):
        for rec in self:
            if rec.occupy_id and rec.occupy_id.property_id:
                prop = rec.occupy_id.property_id
                rec.water_reading = (
                    prop.water_readings_id.name if prop.water_readings_id else 0.0
                )
                rec.electricity_reading = (
                    prop.electricity_readings_id.name if prop.electricity_readings_id else 0.0
                )
                now = fields.Datetime.now()
                rec.water_reading_date = now
                rec.electricity_reading_date = now
            else:
                rec.water_reading = 0.0
                rec.electricity_reading = 0.0
                rec.water_reading_date = False
                rec.electricity_reading_date = False

    @api.constrains("to_property_id", "to_bed_id")
    def _check_target_property(self):
        for rec in self:
            if rec.to_property_id.sale_lease == "bachelor" and not rec.to_bed_id:
                raise ValidationError(
                    _("Bed must be selected for bachelor accommodation")
                )

    @api.constrains("water_reading", "electricity_reading")
    def _check_readings(self):
        for rec in self:
            if rec.is_readings_required:
                if rec.water_reading <= 0:
                    raise ValidationError(_("Water reading must be greater than zero"))
                if rec.electricity_reading <= 0:
                    raise ValidationError(
                        _("Electricity reading must be greater than zero")
                    )

    def write(self, vals):
        if (
                "approval_required" in vals
                and not vals["approval_required"]
                and self._default_approval_required()
                and not self._is_rental_manager()
        ):
            raise UserError(_("Only Rental Managers can remove the approval requirement."))
        return super().write(vals)

    @api.constrains("occupy_id", "to_property_id", "to_bed_id")
    def _check_same_accommodation_type(self):
        for rec in self:
            if not rec.to_property_id or not rec.from_property_id:
                continue
            if rec.to_property_id.sale_lease != rec.from_property_id.sale_lease:
                raise ValidationError(_(
                    "%(emp)s is in %(src)s and can only be shifted to the same type of accommodation.",
                    emp=rec.occupant_id.name or rec.employee_id.name or "",
                    src=dict(rec._fields["from_sale_lease"]._description_selection(rec.env)).get(
                        rec.from_sale_lease, rec.from_sale_lease),
                ))
            if rec.to_property_id == rec.from_property_id and (
                    rec.from_sale_lease != "bachelor" or rec.to_bed_id == rec.from_bed_id
            ):
                raise ValidationError(_("Select a different property (or a different bed) to shift to."))

    # ---------------- Approval ---------------- #
    def _check_manager(self):
        if not self._is_rental_manager():
            raise UserError(_("Only Rental Managers can approve or reject a shift."))

    def action_submit_approval(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft shifts can be submitted for approval."))
            if not rec.to_property_id:
                raise UserError(_("Select the property to shift to first."))
            rec.write({"state": "to_approve"})
            rec.message_post(body=_("Shift submitted for approval."), subtype_xmlid="mail.mt_note")
        return True

    def action_approve(self):
        self._check_manager()
        for rec in self.filtered(lambda r: r.state == "to_approve"):
            rec.write({
                "state": "approved",
                "approved_by_id": self.env.uid,
                "approved_date": fields.Datetime.now(),
            })
            rec.message_post(
                body=_("Shift approved. %s", rec.approval_note or ""), subtype_xmlid="mail.mt_note")
        return True

    def action_reject(self):
        self._check_manager()
        for rec in self.filtered(lambda r: r.state == "to_approve"):
            rec.write({"state": "draft"})
            rec.message_post(
                body=_("Shift rejected. %s", rec.approval_note or ""), subtype_xmlid="mail.mt_note")
        return True

    def _link_shift_contracts(self, old_occupy, new_occupy):
        """Record the shift on both contracts (fields + chatter)."""
        self.ensure_one()
        old_occupy.write({"shift_out_id": self.id, "shifted_to_occupy_id": new_occupy.id})
        new_occupy.write({"shift_in_id": self.id, "shifted_from_occupy_id": old_occupy.id})
        to_place = self.to_property_id.display_name + (
            " / %s" % self.to_bed_id.name if self.to_bed_id else "")
        from_place = self.from_property_id.display_name + (
            " / %s" % self.from_bed_id.name if self.from_bed_id else "")
        old_occupy.message_post(
            body=_("Shifted to %(place)s on %(date)s (new contract %(new)s, shift %(ref)s). Reason: %(reason)s",
                   place=to_place, date=self.shift_date, new=new_occupy.occupy_seq,
                   ref=self.shift_seq, reason=self.shift_reason or "-"),
            subtype_xmlid="mail.mt_note")
        new_occupy.message_post(
            body=_("Shifted from %(place)s (contract %(old)s, shift %(ref)s). Reason: %(reason)s",
                   place=from_place, old=old_occupy.occupy_seq, ref=self.shift_seq,
                   reason=self.shift_reason or "-"),
            subtype_xmlid="mail.mt_note")

    def action_shift(self):
        """Execute the shift: vacate old property, occupy new property"""
        self.ensure_one()

        if self.state not in ("draft", "approved"):
            raise ValidationError(_("Only draft or approved shift records can be processed"))
        if self.approval_required and self.state != "approved":
            raise ValidationError(_("This shift must be approved before it can be executed."))

        occupy = self.occupy_id
        old_property = self.from_property_id
        new_property = self.to_property_id

        # Validate current state
        if occupy.state != "running":
            raise ValidationError(_("Only running contracts can be shifted"))

        # Validate target availability
        if new_property.stage != "available":
            raise ValidationError(_("Target property is not available"))

        # Validate bed for bachelor
        if new_property.sale_lease == "bachelor":
            if not self.to_bed_id:
                raise ValidationError(
                    _("Bed must be selected for bachelor accommodation")
                )
            if self.to_bed_id.state != "available":
                raise ValidationError(_("Selected bed is not available"))

        # Step 1: Record final readings on old property (if required)
        if self.is_readings_required:
            self._create_vacate_readings(occupy)

        # Step 2: Vacate old property
        if old_property.sale_lease == "bachelor" and occupy.bed_id:
            occupy.bed_id.write({"state": "available"})

        old_property.write({"stage": "available"})

        occupy.write(
            {
                "state": "close",
                "terminate_date": fields.Date.today(),
            }
        )

        # Step 3: Create new occupy record
        new_occupy_vals = {
            "partner_type": occupy.partner_type,
            "occupant_id": occupy.occupant_id.id,
            "property_id": new_property.id,
            "start_date": fields.Date.today(),
            "expected_end_date": occupy.expected_end_date,
            "state": "running",
            "active_contract_state": True,
        }

        if occupy.employee_id:
            new_occupy_vals["employee_id"] = occupy.employee_id.id

        if new_property.sale_lease == "bachelor":
            new_occupy_vals["bed_id"] = self.to_bed_id.id
            self.to_bed_id.write({"state": "occupied"})

        new_property._set_property_not_available()

        new_occupy = self.env["occupy.details"].create(new_occupy_vals)

        # Step 4: Create initial readings for new property if family accommodation
        if new_property.sale_lease == "employee_family":
            self._create_occupy_readings(new_occupy, new_property)

            # Record the shift on both contracts
        self._link_shift_contracts(occupy, new_occupy)

        # Update shift record
        self.write(
            {
                "state": "done",
                "new_occupy_id": new_occupy.id,
            }
        )

        return {
            "type": "ir.actions.act_window",
            "name": "New Occupy Contract",
            "res_model": "occupy.details",
            "res_id": new_occupy.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_cancel(self):
        """Cancel draft shift record"""
        self.write({"state": "cancel"})

    def action_reset_draft(self):
        """Reset to draft (manager only)"""
        if not self.env.user.has_group("rental_management.property_rental_manager"):
            raise ValidationError(_("Only managers can reset to draft"))
        self.write({"state": "draft"})

    def _create_vacate_readings(self, occupy):
        """Create final water/electricity readings for old property"""
        self.ensure_one()
        if not self.from_property_id:
            return

        if self.water_reading_date:
            self.env["property.water.readings"].sudo().create(
                {
                    "name": self.water_reading,
                    "reference": f"{occupy.occupy_seq} (Shift Vacate - {self.shift_seq})",
                    "property_details_id": self.from_property_id.id,
                    "reading_date": self.water_reading_date,
                }
            )

        if self.electricity_reading_date:
            self.env["property.electricity.readings"].sudo().create(
                {
                    "name": self.electricity_reading,
                    "reference": f"{occupy.occupy_seq} (Shift Vacate - {self.shift_seq})",
                    "property_details_id": self.from_property_id.id,
                    "reading_date": self.electricity_reading_date,
                }
            )

    def _create_occupy_readings(self, occupy, property_id):
        """Create initial readings for new property"""
        self.ensure_one()
        if not property_id:
            return

        water_val = (
            property_id.water_readings_id.name if property_id.water_readings_id else 0.0
        )
        elec_val = (
            property_id.electricity_readings_id.name
            if property_id.electricity_readings_id
            else 0.0
        )
        now = fields.Datetime.now()

        self.env["property.water.readings"].sudo().create(
            {
                "name": water_val,
                "reference": f"{occupy.occupy_seq} (Shift Occupy - {self.shift_seq})",
                "property_details_id": property_id.id,
                "reading_date": now,
            }
        )

        self.env["property.electricity.readings"].sudo().create(
            {
                "name": elec_val,
                "reference": f"{occupy.occupy_seq} (Shift Occupy - {self.shift_seq})",
                "property_details_id": property_id.id,
                "reading_date": now,
            }
        )


class ResConfigSettingsShiftApproval(models.TransientModel):
    _inherit = "res.config.settings"

    shift_approval_required = fields.Boolean(
        string="Shift Requires Approval",
        config_parameter="rental_management.shift_approval_required",
    )
