# Railway database note

Production backend must use the new Postgres service:

- Service: `Postgres-bML6`
- Service ID: `8e91438a-1045-4ced-aaa3-221af0836b3d`
- Expected private host: `postgres-bml6.railway.internal`
- Backend service: `moneymoney-tw-stock`

The old `Postgres` service is intentionally retained for possible manual data recovery only. Do not point production `DATABASE_URL` back to the old service unless a recovery plan explicitly requires it.

The backend health endpoint `/api/v1/health` reports the active database host and whether it matches `EXPECTED_DATABASE_HOST`.
