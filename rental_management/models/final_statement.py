# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Final Statement — payment details.

Collects every payment reconciled against the contract's posted customer
invoices (rent, deposit, maintenance, utility, …) so the Final Statement
report can show *how* the tenant paid: Cash, Bank, PDC Cheque, Credit Note.

The classification is done in ``_final_statement_classify_payment`` which
is a hook: ``rental_management_pdc`` overrides it to recognise payments
that came from a cleared PDC cheque, and overrides
``_get_final_statement_pdc_lines`` to print the cheque register.
"""
import json

from odoo import _, models


class TenancyDetailsFinalStatement(models.Model):
    _inherit = "tenancy.details"

    # ------------------------------------------------------------------ #
    #  Data sources                                                      #
    # ------------------------------------------------------------------ #
    def _get_final_statement_invoices(self):
        """Posted customer invoices of this contract, oldest first."""
        self.ensure_one()
        return (
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("tenancy_id", "=", self.id),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                ],
                order="invoice_date asc, id asc",
            )
        )

    def _get_final_statement_invoice_label(self, invoice):
        """Human description of what an invoice was for (Rent, Deposit…)."""
        self.ensure_one()
        rent_line = self.rent_invoice_ids.filtered(
            lambda line: line.rent_invoice_id == invoice
        )[:1]
        if rent_line:
            if rent_line.description:
                return rent_line.description
            return dict(rent_line._fields["type"].selection).get(rent_line.type, "")
        first_line = invoice.invoice_line_ids[:1]
        return (first_line.name or invoice.ref or "").split("\n")[0]

    @staticmethod
    def _get_invoice_payment_entries(invoice):
        """Reconciled payment entries of an invoice (same data as the
        'Paid on …' widget shown on the invoice form)."""
        widget = invoice.invoice_payments_widget
        if not widget:
            return []
        if isinstance(widget, str):
            try:
                widget = json.loads(widget)
            except ValueError:
                return []
        return [
            entry
            for entry in (widget.get("content") or [])
            if not entry.get("is_exchange")
        ]

    # ------------------------------------------------------------------ #
    #  Classification hook                                               #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _final_statement_journal_bank(journal):
        if not journal:
            return ""
        bank_account = (
            journal.bank_account_id if "bank_account_id" in journal._fields else False
        )
        if bank_account and bank_account.bank_id:
            return bank_account.bank_id.name
        return journal.name or ""

    def _final_statement_classify_payment(self, invoice, entry, used):
        """Return the display values for one reconciled payment entry.

        :param invoice: account.move the payment was reconciled with
        :param entry: dict from ``invoice_payments_widget['content']``
        :param used: dict shared across one report run, so overrides can
                     remember what they already matched (e.g. PDC ids)
        :returns: dict with keys method_key, method, detail, bank, reference
        """
        self.ensure_one()
        Payment = self.env["account.payment"].sudo()
        Move = self.env["account.move"].sudo()
        payment = (
            Payment.browse(entry["account_payment_id"]).exists()
            if entry.get("account_payment_id")
            else Payment
        )
        move = Move.browse(entry["move_id"]).exists() if entry.get("move_id") else Move
        journal = payment.journal_id or move.journal_id

        vals = {
            "method_key": "other",
            "method": journal.name or _("Other"),
            "detail": entry.get("payment_method_name") or "",
            "bank": self._final_statement_journal_bank(journal),
            "reference": entry.get("ref") or entry.get("name") or "",
        }

        if payment:
            memo_field = "memo" if "memo" in payment._fields else "ref"
            vals["reference"] = payment[memo_field] or payment.name or vals["reference"]
            vals["detail"] = payment.payment_method_line_id.name or vals["detail"]
            if journal.type == "cash":
                vals.update(method_key="cash", method=_("Cash"), bank="")
            elif journal.type == "bank":
                vals.update(method_key="bank", method=_("Bank"))
        elif move.move_type == "out_refund":
            vals.update(
                method_key="credit_note",
                method=_("Credit Note"),
                detail="",
                bank="",
                reference=move.name,
            )
        elif journal.type == "cash":
            vals.update(method_key="cash", method=_("Cash"), bank="")
        elif journal.type == "bank":
            vals.update(method_key="bank", method=_("Bank"))
        return vals

    # ------------------------------------------------------------------ #
    #  Report API                                                        #
    # ------------------------------------------------------------------ #
    def _get_final_statement_payment_lines(self):
        """One dict per payment received against the contract's invoices."""
        self.ensure_one()
        lines = []
        used = {}
        for invoice in self._get_final_statement_invoices():
            label = self._get_final_statement_invoice_label(invoice)
            for entry in self._get_invoice_payment_entries(invoice):
                vals = self._final_statement_classify_payment(invoice, entry, used)
                vals.update(
                    {
                        "date": entry.get("date") or invoice.invoice_date,
                        "invoice": invoice.name,
                        "description": label,
                        "amount": entry.get("amount") or 0.0,
                    }
                )
                lines.append(vals)
        lines.sort(key=lambda line: (str(line["date"] or ""), line["invoice"] or ""))
        return lines

    def _get_final_statement_payment_summary(self, lines=None):
        """Totals per payment method: list of (label, amount)."""
        self.ensure_one()
        if lines is None:
            lines = self._get_final_statement_payment_lines()
        totals = {}
        for line in lines:
            totals.setdefault(line["method"], 0.0)
            totals[line["method"]] += line["amount"]
        return sorted(totals.items())

    def _get_final_statement_pdc_lines(self):
        """PDC cheque register — empty here, filled by rental_management_pdc."""
        self.ensure_one()
        return []
