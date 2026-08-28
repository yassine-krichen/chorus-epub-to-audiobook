# Deploying book2audio

This is a checklist, not a tutorial — it assumes you'll click through each
provider's own signup/console UI. Everything here is derived directly from
the code (env var names are exact, grepped from the source, not guessed).

Nothing in this guide has been executed against real infrastructure — no
live credentials existed while this was written. Treat the first full
end-to-end run as the actual test.

## Services you need

| Service | What it's for | Free tier used |
|---|---|---|
| [Neon](https://neon.tech) | Postgres (users, jobs, refresh tokens) | Serverless Postgres free plan |
| [Cloudflare R2](https://developers.cloudflare.com/r2/) | Object storage (uploaded epubs, rendered audio, thumbnails) | 10GB free |
| [Render](https://render.com) | Hosts the FastAPI control-plane API | Free Web Service |
| [Google Cloud Run](https://cloud.google.com/run) | Runs the actual TTS render worker | Always-free vCPU-second allowance |
| [Vercel](https://vercel.com) | Hosts the Next.js frontend | Free (Hobby) plan |
| freellmapi (or any OpenAI-compatible image-gen endpoint) | Chapter thumbnails — **optional**, only needed if you turn on `thumbnails` | Self-hosted, see note below |

Order matters below — Cloud Run and Neon need to exist before Render's API
can be configured, and the API needs to be live before Vercel's
`BACKEND_API_URL` can point at it.

---

## 1. Neon (Postgres)

1. Create a project, grab the pooled connection string.
2. Set it as `DATABASE_URL` everywhere below (Render's API service **and**
   the Cloud Run worker both need it — the worker writes progress directly
   to Postgres, see `src/book2audio/worker/progress.py`).
3. Run migrations once, from your machine, pointed at the real DB:
   ```bash
   DATABASE_URL="<neon connection string>" uv run alembic upgrade head
   ```
   This creates `users`, `refresh_tokens`, `jobs` (see
   `src/book2audio/db/migrations/versions/`) and, as of migration `0003`,
   adds `jobs.artifacts_swept_at` (used by the cleanup job in step 3.7).
   Re-run this same command after pulling any new migration — it's
   idempotent, already-applied revisions are skipped.

## 2. Cloudflare R2

1. Create a bucket.
2. Create an R2 API token (Account → R2 → Manage API Tokens) with
   read/write access to that bucket. You get an Access Key ID, a Secret
   Access Key, and your Account ID.
3. These four values become `R2_BUCKET`, `R2_ACCOUNT_ID`,
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` — needed on **both** Render
   (API presigns upload/download URLs) and Cloud Run (worker uploads
   rendered audio + thumbnails).

## 3. Google Cloud Run (the render worker)

The worker is a **Job**, not a Service — provisioned once, then triggered
per-render by the API via the Cloud Run Admin API.

1. Create a GCP project, enable the Cloud Run API.
2. Build and push the worker image:
   ```bash
   gcloud builds submit --tag gcr.io/<project-id>/book2audio-worker -f docker/Dockerfile.worker .
   ```
3. Create the job **once** (reused for every render execution after this):
   ```bash
   gcloud run jobs create book2audio-render-worker \
     --image=gcr.io/<project-id>/book2audio-worker \
     --region=us-central1 \
     --memory=2Gi --cpu=2 \
     --task-timeout=6h \
     --max-retries=0
   ```
   The full resource name (`projects/<project-id>/locations/us-central1/jobs/book2audio-render-worker`)
   is what Render's API needs as `CLOUD_RUN_JOB_NAME`.
4. Set the worker's own env vars on the job (`gcloud run jobs update ... --set-env-vars`):
   `DATABASE_URL`, `R2_BUCKET`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
   (`JOB_ID`, `EPUB_OBJECT_KEY`, `JOB_CONFIG` are **not** set here — the API
   injects those per-execution as overrides, see
   `src/book2audio/api/jobs/service.py::_trigger_render`.)
5. **Thumbnails are opt-in and need their own credentials** — only set
   these if you actually have a working freellmapi (or other
   OpenAI-compatible image-gen) deployment; leave them unset and just don't
   enable `thumbnails` on jobs otherwise:
   `FREELLMAPI_BASE_URL`, `FREELLMAPI_API_KEY`, `FREELLMAPI_IMAGE_MODEL`.
   See "About freellmapi" below — this is the one piece that could not be
   verified against a live deployment.
6. Create a service account with the `roles/run.developer` role (lets it
   trigger/cancel/inspect executions of this one job) and download a JSON
   key — Render's API process needs this to authenticate to the Cloud Run
   Admin API (see `src/book2audio/api/jobs/cloud_run.py`, uses
   `google.auth.default()`).
7. **R2 cleanup job** (`src/book2audio/worker/cleanup.py`) — sweeps R2
   artifacts for jobs that ended `canceled`/`failed` more than a grace
   period ago. Separate, thin image (no ffmpeg, no baked TTS model):
   ```bash
   gcloud builds submit --tag gcr.io/<project-id>/book2audio-cleanup -f docker/Dockerfile.cleanup .

   gcloud run jobs create book2audio-cleanup \
     --image=gcr.io/<project-id>/book2audio-cleanup \
     --region=us-central1 \
     --memory=512Mi --cpu=1 \
     --task-timeout=15m --max-retries=0 \
     --set-env-vars="DATABASE_URL=<neon connection string>,R2_BUCKET=<bucket>,R2_ACCOUNT_ID=<account>,R2_ACCESS_KEY_ID=<key>,R2_SECRET_ACCESS_KEY=<secret>,CLEANUP_GRACE_PERIOD_DAYS=7"
   ```
   Use the same `roles/run.developer` service account (or a dedicated one)
   for the scheduler to invoke it, then wire up a Cloud Scheduler job to
   trigger it on a cadence (e.g. daily), targeting the Cloud Run Admin
   API's `:run` endpoint with OAuth:
   ```bash
   gcloud scheduler jobs create http book2audio-cleanup-trigger \
     --schedule="0 4 * * *" \
     --uri="https://run.googleapis.com/v2/projects/<project-id>/locations/us-central1/jobs/book2audio-cleanup:run" \
     --http-method=POST \
     --oauth-service-account-email=<service-account-email>
   ```

## 4. Render (the API)

1. New Web Service, connect this repo, Dockerfile: `docker/Dockerfile.api`.
2. Free instance type.
3. Environment variables:
   - `DATABASE_URL` — from Neon.
   - `JWT_SECRET` — generate a real random secret, **at least 32 characters**
     (the app rejects shorter ones at startup — `api/auth/security.py`).
   - `CORS_ORIGINS` — leave **empty**. The frontend never calls this API
     directly from the browser (it proxies through Vercel's own
     server-side routes), so CORS isn't in the request path at all. Setting
     it to `*` is actively rejected at startup if credentials are enabled.
   - `CLOUD_RUN_JOB_NAME` — the full resource name from step 3.3.
   - `R2_BUCKET`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
   - `GOOGLE_APPLICATION_CREDENTIALS` — set to `/etc/secrets/gcp-key.json`,
     and upload the actual JSON key from step 3.6 as a **Secret File** at
     that path (Render Dashboard → your service → Environment → Secret
     Files — available on the free tier). Never paste the key contents
     into a plain env var.
   - Auth rate limiting (all **optional** — sensible defaults apply if
     unset, see `src/book2audio/api/auth/rate_limit.py`):
     `AUTH_LOGIN_RATE_LIMIT_PER_EMAIL` (default 5),
     `AUTH_LOGIN_RATE_LIMIT_PER_IP` (default 20),
     `AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` (default 60),
     `AUTH_REFRESH_RATE_LIMIT_PER_IP` (default 30),
     `AUTH_REFRESH_RATE_LIMIT_WINDOW_SECONDS` (default 60).
4. Deploy. First request will be slow (free tier cold start, ~30-60s) —
   that's expected, not a bug.

## 5. Vercel (the frontend)

1. Import the repo, set the project root to `frontend/`.
2. Environment variable: `BACKEND_API_URL` = your Render service's URL
   (e.g. `https://book2audio-api.onrender.com`). **Do not** prefix this
   `NEXT_PUBLIC_` — it's read server-side only
   (`frontend/lib/server/backend-client.ts`); prefixing it would ship your
   backend URL into the client bundle for no benefit and slightly widens
   the surface unnecessarily.
3. Deploy. No other config needed — the BFF architecture means Vercel
   talks to Render server-to-server, the browser never sees the Render URL
   or any token directly.

---

## About freellmapi (read before enabling thumbnails)

If you're seeing this for the first time: **freellmapi is not a hosted
free image-gen API you can just call.** It's a self-hosted, open-source
gateway ([tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi))
that proxies *your own* upstream provider API keys behind one
OpenAI-compatible endpoint. You still need:

1. To deploy freellmapi itself somewhere (it's a Node.js app).
2. Real API keys from an actual image-generation provider, configured
   inside freellmapi's own dashboard.

Until that exists, **leave `thumbnails` off** on every job — everything
else in the app works completely independently of it (it's the one
feature gated behind its own env vars, checked fail-fast only when a job
opts in — `worker/entrypoint.py`).

The client code in `src/book2audio/images/freellmapi.py` is written
against the documented OpenAI Images API contract
(`POST {base}/v1/images/generations`) and has never been exercised against
a real freellmapi deployment. Test it against a real instance before
trusting it in production.

---

## Smoke-testing after deploy

1. Register a user via the frontend.
2. Upload a small epub (a 2-3 chapter test file, not a 100-chapter book —
   `--limit`-style previews aren't exposed in the UI yet beyond
   `chapter_limit`, but a genuinely short book is the fastest real check).
3. Watch the job go `queued` → `running` → `succeeded` on the dashboard.
4. Confirm a part actually has a working download link — this depends on
   `filename_template` being set, which the new-job form defaults to a
   working value (`{title} - Part {part:02d}`); if you ever construct a
   job with `filename_template: null` some other way, downloads will
   silently have no URL (this is a real backend quirk, not a bug — see
   `src/book2audio/api/jobs/service.py::_to_part_progress`).

## Known gaps at time of writing

- CLI (`book2audio generate ...`) now has `--male-voice`/`--female-voice`/
  `--music-preset`/`--thumbnails` flags for config parity with the API, but
  `--thumbnails` is config-validation-only locally — it checks freellmapi
  credentials are set but doesn't produce local thumbnail files; that
  machinery only exists via the hosted worker's R2-backed job pipeline
  (`worker/progress.py`).
