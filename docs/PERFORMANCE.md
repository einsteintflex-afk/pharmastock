# Performance Benchmark

Measured with `scripts/benchmark.py` on 24 September 2026. The numbers describe this
machine and this data shape only. They are **not** a capacity guarantee.

## Method

- A throw-away database is built from the project schema and all migrations. The API
  runs as the **restricted application role**, so row level security is active on every
  query, exactly as in production.
- For each organization count, organizations are added through the normal creation
  service (settings, first location, owner). Each organization has:
  - 100 medicines;
  - 200 batches, some expired or expiring;
  - one opening movement per batch;
  - 500 sales movements spread over 90 days.
- The organization counts are cumulative: 10, then 100, then 1,000.
- Five random organizations are measured, with 25 requests per endpoint.
- Requests run in-process (FastAPI TestClient, one worker, 10 pooled connections), one at
  a time. The figures therefore cover application and database time, without network or
  concurrent load.
- Environment: 4 vCPU, 15 GB RAM, PostgreSQL 16.13 with default configuration, on the same
  host.

## Results

| Orgs | Database | Movements | Batches | Endpoint | p50 ms | p95 ms | max ms |
|---:|---:|---:|---:|---|---:|---:|---:|
| 10 | 14 MB | 7,000 | 2,000 | POST /auth/login (scrypt) | 50 | 61 | 70 |
| 10 | | | | GET /medicines | 10 | 15 | 75 |
| 10 | | | | GET /inventory (page of 50) | 12 | 15 | 24 |
| 10 | | | | GET /dashboard | 33 | 40 | 81 |
| 10 | | | | GET /attention | 20 | 26 | 29 |
| 10 | | | | GET /search | 9 | 11 | 11 |
| 10 | | | | GET /analytics/reorder | 15 | 16 | 19 |
| 10 | | | | GET /reports/valuation | 12 | 15 | 17 |
| 10 | | | | POST /dispensations | 16 | 19 | 24 |
| 100 | 45 MB | 70,025 | 20,000 | POST /auth/login (scrypt) | 50 | 61 | 67 |
| 100 | | | | GET /medicines | 13 | 17 | 22 |
| 100 | | | | GET /inventory (page of 50) | 15 | 19 | 25 |
| 100 | | | | GET /dashboard | 42 | 54 | 67 |
| 100 | | | | GET /attention | 26 | 31 | 37 |
| 100 | | | | GET /search | 15 | 19 | 20 |
| 100 | | | | GET /analytics/reorder | 16 | 18 | 20 |
| 100 | | | | GET /reports/valuation | 16 | 19 | 19 |
| 100 | | | | POST /dispensations | 24 | 33 | 36 |
| 1,000 | 343 MB | 700,050 | 200,000 | POST /auth/login (scrypt) | 45 | 45 | 63 |
| 1,000 | | | | GET /medicines | 12 | 15 | 22 |
| 1,000 | | | | GET /inventory (page of 50) | 14 | 17 | 21 |
| 1,000 | | | | GET /dashboard | 37 | 42 | 43 |
| 1,000 | | | | GET /attention | 24 | 39 | 55 |
| 1,000 | | | | GET /search | 13 | 18 | 20 |
| 1,000 | | | | GET /analytics/reorder | 16 | 18 | 20 |
| 1,000 | | | | GET /reports/valuation | 15 | 20 | 32 |
| 1,000 | | | | POST /dispensations | 19 | 23 | 26 |

Seeding 1,000 organizations took 78 seconds.

## What this shows

- Per-request time did not grow noticeably from 10 to 1,000 organizations. Each query is
  scoped to one organization, and the `organization_id` indexes keep the other tenants'
  rows out of the plan.
- Sign-in is deliberately slow (about 50 ms) because of scrypt password hashing.
- The dashboard is the heaviest page: it runs several analyses in one request.

## What this does not show

- **Concurrency.** Requests ran one at a time. With many simultaneous users, throughput
  depends on the number of workers, the pool size (`DB_POOL_MAX`) and PostgreSQL resources.
  Measure with a load tool (for example k6 or Locust) on the target server before promising
  a user count.
- **Larger tenants.** A chain with 10,000 medicines and years of history per organization
  has not been measured. Medicines and inventory lists load all rows of one organization.
- **Background jobs.** The notification refresh and the delivery worker visit every active
  organization in turn. At 1,000 organizations one refresh cycle takes proportionally longer
  (default interval: 15 minutes). This cycle was not timed here.
- **Network and TLS**, which the reverse proxy adds.

## Reproduce

```
TEST_DATABASE_URL=postgresql://owner:pw@localhost:5432/pharmastock_bench \
python scripts/benchmark.py --orgs 10 100 1000
```

The owner role needs CREATEDB and CREATEROLE. The script only touches the database named
`pharmastock_bench`.
