# B.0 — the end-to-end suite every kit must pass before it is installed

A.25 through A.29 all shipped without a single click-through check. This suite is the gate that stops that.

It walks every page in the application **twice** — once as an administrator, once as an ordinary user — and fails on:

- any uncaught console error or page crash
- any request that answers 4xx or 5xx (401 on a page a role genuinely cannot see is expected and allowed)
- any page slower than 3 seconds
- **any forbidden string in any response a non-admin receives** — the vendor names, the model names, the weights
  and points, the factor keys, the `model_version` strings. This is the A.29 / B.1 rule enforced from the outside,
  on real traffic, rather than from a unit test's fixture
- the word `AttributeError`, or any Python traceback fragment, appearing in a page or a response body

It also screenshots every page at desktop, tablet and phone widths, so Part 3 of the audit can be done by looking
at them side by side rather than guessing from stylesheets.

## It never stores a credential

Credentials come from the environment and are never written to disk, never logged, and never put in a screenshot.
The suite redacts any value it was given from everything it prints.

```bash
# PowerShell, on your own machine
$env:E2E_BASE_URL   = 'https://tradgo.us'
$env:E2E_ADMIN_USER = '...'
$env:E2E_ADMIN_PASS = '...'
$env:E2E_USER_USER  = '...'
$env:E2E_USER_PASS  = '...'
npx playwright install chromium     # first time only
npx playwright test                 # from the e2e folder
```

Leave the user credentials unset and it runs the admin pass only, and says so rather than silently skipping half
the check.

## Against what

`E2E_BASE_URL` decides. Point it at a staging copy if you have one. Pointed at production it is **read-only by
design**: it never submits a form that places an order, changes a setting, acknowledges an alert or writes to the
paper book. The destructive-action selectors are listed in `guards.ts` and the suite fails if a test tries one.

## Output

- `e2e/report/` — the HTML report, one row per page per role
- `e2e/screenshots/` — `<page>.<role>.<width>.png`
- `e2e/leaks.json` — every forbidden string seen, with the route that sent it

## What it does not do

It does not log in with a one-time code or an OTP flow. If the account needs WhatsApp OTP, run the suite against an
account that uses a password, or supply a storage-state file (`E2E_STORAGE_STATE`) captured by signing in yourself.
