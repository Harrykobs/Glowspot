# Test report

## Current PostgreSQL migration verification status

**Not yet verified complete.** The historical results below were produced before the PostgreSQL migration and must not be presented as PostgreSQL test evidence.

A GitHub Actions workflow now runs the system suite against a disposable PostgreSQL 16 service. It uses `TEST_DATABASE_URL`, resets the test schema data between tests, and never uses the production Supabase database. The latest run is linked from the migration pull request. Its result must be checked before this report can be marked passed.

## Historical pre-migration test results

The previous SQLite-based run reported 40 tests passed. This is historical evidence for the pre-migration application only; it does not validate PostgreSQL compatibility.

The historical suite covered:
- Phone formats, duplicate customers, self/unknown/circular referrals.
- Skin-analysis rewards and qualifying referral rules.
- Package commissions, instalments, reversals, sale voiding and adjustments.
- Approvals, payouts, settings history, staff/manager permissions and login lockout.
- Reports, Excel/PDF exports, CSRF and selected security checks.

## Required PostgreSQL verification

The following must pass against PostgreSQL before release:
- Schema installation on a clean database and repeat/idempotent application.
- Initial manager bootstrap and first-login password change.
- Manager and staff authentication using the existing application-managed password hashes and sessions (no Supabase Auth).
- Customer registration, referral assignment and referral changes.
- Sales, partial/full payments, commission calculations, reversals, approvals and payouts.
- All date-filtered reports, Excel/PDF exports and search behavior.
- Application restart with records retained.
- PostgreSQL backup archive creation, archive validation and restore into a disposable recovery database.
- Full automated test suite and PostgreSQL integration workflow.

## Environment and deployment limitations

A passing GitHub Actions test against its PostgreSQL 16 service is necessary but does not prove connectivity to the user's Supabase project or Render deployment. Supabase credentials and a dedicated Supabase test database have not been provided to this run. No production deployment or merge is claimed.

Before production use, verify the actual Supabase connection mode, SSL configuration, backup retention/recovery options, and all workflows against a separate Supabase test project/database. Never run destructive integration tests against production.
