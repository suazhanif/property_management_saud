# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Utility consumption per employee / occupant / property.

Readings on a property are cumulative meter values.  Consumption of one
interval = reading - previous reading of the same meter.  Each interval is
allocated to the occupancy (occupy.details) that covered the whole interval:

    occupancy.start_date <= previous reading date
    occupancy.end        >= reading date   (open-ended while running)

* Interval between a vacate reading and the next move-in → no occupant
  ("Vacant").
* Several occupancies covering the same interval (bachelor beds) → the
  consumption is split equally between them ("Shared").
"""
from odoo import api, fields, models, tools
from odoo.tools import SQL

OCCUPANCY_STATUS = [
    ("occupied", "Occupied"),
    ("shared", "Shared"),
    ("vacant", "Vacant / Unassigned"),
]


class PropertyUtilityConsumptionReport(models.Model):
    _name = "property.utility.consumption.report"
    _description = "Utility Consumption Analysis"
    _auto = False
    _order = "reading_date desc"
    _rec_name = "property_id"

    utility_type = fields.Selection(
        [("water", "Water"), ("electricity", "Electricity")], string="Utility", readonly=True)
    reading_date = fields.Datetime(string="Reading Date", readonly=True)
    previous_date = fields.Datetime(string="Previous Reading Date", readonly=True)
    reference = fields.Char(string="Ref.", readonly=True)
    opening_reading = fields.Float(string="Opening Reading", readonly=True, aggregator="min")
    closing_reading = fields.Float(string="Closing Reading", readonly=True, aggregator="max")
    consumption = fields.Float(string="Consumption", readonly=True, aggregator="sum")
    water_qty = fields.Float(string="Water (m³)", readonly=True, aggregator="sum")
    electricity_qty = fields.Float(string="Electricity (kWh)", readonly=True, aggregator="sum")
    share_count = fields.Integer(string="Shared Between", readonly=True, aggregator="max")
    occupancy_status = fields.Selection(OCCUPANCY_STATUS, string="Occupancy", readonly=True)
    meter_reset = fields.Boolean(
        string="Meter Reset", readonly=True,
        help="Reading lower than the previous one (meter replaced / wrong entry); counted as 0.")

    property_id = fields.Many2one("property.details", string="Property", readonly=True)
    subproject_id = fields.Many2one("property.sub.project", string="Building", readonly=True)
    property_project_id = fields.Many2one("property.project", string="Compound", readonly=True)
    sale_lease = fields.Selection(
        [("for_sale", "Sale"), ("employee_family", "Employee Family Accommodation"),
         ("bachelor", "Bachelor Accommodation"), ("for_tenancy", "Rent")],
        string="Property For", readonly=True)
    company_id = fields.Many2one("res.company", string="Company", readonly=True)

    occupy_id = fields.Many2one("occupy.details", string="Occupancy", readonly=True)
    employee_id = fields.Many2one("hr.employee", string="Employee", readonly=True)
    occupant_id = fields.Many2one("res.partner", string="Occupant", readonly=True)
    department_id = fields.Many2one("hr.department", string="Department", readonly=True)

    def _department_join_sql(self):
        """Odoo 19 moved department_id from hr.employee to hr.version."""
        cr = self.env.cr

        def has_column(table, column):
            cr.execute(
                """SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s AND column_name = %s""",
                (table, column),
            )
            return bool(cr.fetchone())

        if has_column("hr_employee", "department_id"):
            return SQL("e.department_id"), SQL("")
        if has_column("hr_employee", "current_version_id") and has_column("hr_version", "department_id"):
            return (
                SQL("v.department_id"),
                SQL("LEFT JOIN hr_version v ON v.id = e.current_version_id"),
            )
        return SQL("NULL::integer"), SQL("")

    def _report_timezone(self):
        """Admin user's timezone, read with SQL.

        init() runs during module upgrades, before every new column exists;
        reading the user through the ORM would load all res.users fields and
        fail on a column that is not created yet.
        """
        admin_id = self.env["ir.model.data"]._xmlid_to_res_id(
            "base.user_admin", raise_if_not_found=False
        )
        if not admin_id:
            return "UTC"
        self.env.cr.execute(
            """SELECT p.tz FROM res_users u
                 JOIN res_partner p ON p.id = u.partner_id
                WHERE u.id = %s""",
            (admin_id,),
        )
        row = self.env.cr.fetchone()
        return (row and row[0]) or "UTC"


    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        tz = self._report_timezone()
        department_col, department_join = self._department_join_sql()
        self.env.cr.execute(SQL(
            """
            CREATE OR REPLACE VIEW %(table)s AS (
            WITH readings AS (
                SELECT 'water'::varchar AS utility_type, r.id AS reading_id,
                       r.property_details_id AS property_id, r.reading_date,
                       r.name AS reading, r.reference
                  FROM property_water_readings r
                 WHERE r.reading_date IS NOT NULL
                UNION ALL
                SELECT 'electricity'::varchar, r.id, r.property_details_id,
                       r.reading_date, r.name, r.reference
                  FROM property_electricity_readings r
                 WHERE r.reading_date IS NOT NULL
            ),
            intervals AS (
                SELECT rd.*,
                       LAG(rd.reading_date) OVER w AS previous_date,
                       LAG(rd.reading) OVER w AS previous_reading
                  FROM readings rd
                WINDOW w AS (PARTITION BY rd.utility_type, rd.property_id
                             ORDER BY rd.reading_date, rd.reading_id)
            ),
            alloc AS (
                SELECT i.*,
                       o.id AS occupy_id, o.employee_id, o.occupant_id,
                       COUNT(o.id) OVER (PARTITION BY i.utility_type, i.reading_id) AS share_count
                  FROM intervals i
             LEFT JOIN occupy_details o
                    ON o.property_id = i.property_id
                   AND o.state IN ('running', 'close', 'expire')
                   AND o.start_date <= (i.previous_date AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date
                   AND (
                        (o.state = 'running' AND o.terminate_date IS NULL)
                        OR COALESCE(o.terminate_date, o.expected_end_date)
                           >= (i.reading_date AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date
                   )
                 WHERE i.previous_date IS NOT NULL
            )
            SELECT row_number() OVER (ORDER BY a.utility_type, a.reading_id, a.occupy_id NULLS FIRST) AS id,
                   a.utility_type,
                   a.reading_date,
                   a.previous_date,
                   a.reference,
                   a.previous_reading AS opening_reading,
                   a.reading AS closing_reading,
                   GREATEST(a.reading - a.previous_reading, 0) / GREATEST(a.share_count, 1) AS consumption,
                   CASE WHEN a.utility_type = 'water'
                        THEN GREATEST(a.reading - a.previous_reading, 0) / GREATEST(a.share_count, 1)
                        ELSE 0 END AS water_qty,
                   CASE WHEN a.utility_type = 'electricity'
                        THEN GREATEST(a.reading - a.previous_reading, 0) / GREATEST(a.share_count, 1)
                        ELSE 0 END AS electricity_qty,
                   GREATEST(a.share_count, 1)::int AS share_count,
                   CASE WHEN a.occupy_id IS NULL THEN 'vacant'
                        WHEN a.share_count > 1 THEN 'shared'
                        ELSE 'occupied' END AS occupancy_status,
                   (a.reading < a.previous_reading) AS meter_reset,
                   a.property_id,
                   p.subproject_id,
                   p.property_project_id,
                   p.sale_lease,
                   p.company_id,
                   a.occupy_id,
                   a.employee_id,
                   a.occupant_id,
                   %(department_col)s AS department_id
              FROM alloc a
              JOIN property_details p ON p.id = a.property_id
         LEFT JOIN hr_employee e ON e.id = a.employee_id
                   %(department_join)s
            )
            """,
            table=SQL.identifier(self._table),
            tz=tz,
            department_col=department_col,
            department_join=department_join,
        ))


# ---------------------------------------------------------------------- #
#  Consumption column on the property's reading lists                    #
# ---------------------------------------------------------------------- #
def _compute_reading_consumption(records):
    """previous_reading / consumption for cumulative meter readings."""
    origin_ids = {r._origin.id for r in records if r._origin.id}
    values = {}
    if origin_ids:
        props = records.mapped("property_details_id")._origin
        all_readings = records.env[records._name].search(
            [("property_details_id", "in", props.ids)],
            order="property_details_id, reading_date, id",
        )
        last = {}
        for r in all_readings:
            prev = last.get(r.property_details_id.id)
            if prev is not None:
                values[r.id] = (prev, max(r.name - prev, 0.0))
            last[r.property_details_id.id] = r.name
    for rec in records:
        prev, cons = values.get(rec._origin.id, (0.0, 0.0))
        rec.previous_reading = prev
        rec.consumption = cons


class PropertyWaterReadingsConsumption(models.Model):
    _inherit = "property.water.readings"

    previous_reading = fields.Float(string="Previous", compute="_compute_consumption")
    consumption = fields.Float(string="Consumption (m³)", compute="_compute_consumption")

    @api.depends("name", "reading_date", "property_details_id")
    def _compute_consumption(self):
        _compute_reading_consumption(self)


class PropertyElectricityReadingsConsumption(models.Model):
    _inherit = "property.electricity.readings"

    previous_reading = fields.Float(string="Previous", compute="_compute_consumption")
    consumption = fields.Float(string="Consumption (kWh)", compute="_compute_consumption")

    @api.depends("name", "reading_date", "property_details_id")
    def _compute_consumption(self):
        _compute_reading_consumption(self)


# ---------------------------------------------------------------------- #
#  Totals + drill-down from Property / Occupancy / Employee               #
# ---------------------------------------------------------------------- #
class UtilityConsumptionMixin(models.AbstractModel):
    _name = "utility.consumption.mixin"
    _description = "Utility Consumption Totals"

    utility_water_total = fields.Float(
        string="Water Consumed (m³)", compute="_compute_utility_totals")
    utility_electricity_total = fields.Float(
        string="Electricity Consumed (kWh)", compute="_compute_utility_totals")

    def _utility_report_field(self):
        """Field of the consumption report pointing to this model."""
        raise NotImplementedError

    def _compute_utility_totals(self):
        fname = self._utility_report_field()
        ids = [i for i in self._origin.ids if i]
        totals = {}
        if ids:
            groups = self.env["property.utility.consumption.report"].sudo()._read_group(
                [(fname, "in", ids)], [fname], ["water_qty:sum", "electricity_qty:sum"])
            totals = {rec.id: (water, elec) for rec, water, elec in groups}
        for rec in self:
            rec.utility_water_total, rec.utility_electricity_total = totals.get(
                rec._origin.id, (0.0, 0.0))

    def action_view_utility_consumption(self):
        self.ensure_one()
        fname = self._utility_report_field()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rental_management.property_utility_consumption_report_action")
        action.update({
            "name": f"Utility Consumption - {self.display_name}",
            "domain": [(fname, "=", self.id)],
            "context": {
                "pivot_row_groupby": ["property_id" if fname != "property_id" else "employee_id"],
                "pivot_column_groupby": ["reading_date:month"],
            },
        })
        return action


class OccupyDetailsUtility(models.Model):
    _name = "occupy.details"
    _inherit = ["occupy.details", "utility.consumption.mixin"]

    def _utility_report_field(self):
        return "occupy_id"


class PropertyDetailsUtility(models.Model):
    _name = "property.details"
    _inherit = ["property.details", "utility.consumption.mixin"]

    def _utility_report_field(self):
        return "property_id"


class HrEmployeeUtility(models.Model):
    _name = "hr.employee"
    _inherit = ["hr.employee", "utility.consumption.mixin"]

    def _utility_report_field(self):
        return "employee_id"