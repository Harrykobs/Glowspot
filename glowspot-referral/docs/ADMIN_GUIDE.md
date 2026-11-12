# Administrator (manager) guide

**First login:** use the manager account created at setup; you must choose a new password.
**Staff:** Manage > Staff accounts. Create accounts with a temporary password; reset or disable them there. Disabling takes effect immediately. Keep at least one active manager.
**Prices and rates:** Manage > Prices and rates. Every change needs a reason and is logged. It applies to future transactions only.
**Packages:** Manage > Packages. Add, rename, change price, or hide (hidden packages cannot be sold; past sales keep their price).
**Approving commission:** Commissions > filter Pending > tick > Approve selected.
**Paying a referrer:** open the referrer's account > Record payout, tick the approved items, choose method and date. The system records the payout; it does not send money.
**Mistakes:** open the sale. Reverse a single payment, or void the whole sale, with a reason. Related commission is reversed automatically; if it was already paid out, a negative adjustment appears and is netted in the next payout.
**Referral links:** customer account > Referral link. Needs a reason; history is kept. Old commission stays with the old referrer.
**Reports:** Reports > choose dates > view, or export Excel/PDF. **Audit log:** every sensitive action, with who and why. **Backup:** Manage > Backup, weekly at least.
**Restore:** stop the app, run `python scripts/restore.py BACKUPFILE`, start the app.
