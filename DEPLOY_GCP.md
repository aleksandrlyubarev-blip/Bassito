# Deploying Filmitto on GCP free credits

Goal: stand up the whole Filmitto stack (bassito LongLive service +
romeo_phd Filmitto control plane + UI) on a GCP free trial without
burning the **$300 / 90 days** of credits. NVFP4 inference is
intentionally **NOT** part of this stack — it requires a Blackwell GPU,
and even one hour of A4 costs ~$88. Instead, the bassito service runs
in **mock mode** so the full UX (storyboard agent, SSE chunk streaming,
HITL accept/reject/reroll) is exercisable end-to-end on CPU.

## Free-tier cost budget

| Component | Service | Pricing | Stays free? |
| --- | --- | --- | --- |
| `bassito_longlive_service` (mock mode) | Cloud Run, 1 vCPU / 512 MB, 0–10 instances | 2 M req/mo + 360 k GB-s free | yes |
| `@workspace/api-server` (Filmitto routes) | Cloud Run, 1 vCPU / 512 MB | same | yes |
| `@workspace/romeo-phd` (UI) | Cloud Run static (or Firebase Hosting) | same | yes |
| Postgres | **Neon free tier** (3 GB) | external, $0 | yes |
| Storage for placeholder mp4s | Cloud Storage Standard | 5 GB/mo free in us-* | yes |
| Anthropic (storyboard agent) | external API | ~$0.01–0.03 per film, cached system prompt | not GCP |

Expected GCP spend at ~100 storyboards/day: **$0 — entirely inside the
Always Free tier**. The $300 trial stays untouched, available later for
real-GPU experiments.

## Prerequisites

- A GCP project with billing linked (required even for free tier).
- `gcloud` CLI installed and authenticated.
- An Anthropic API key (for the Filmitto storyboard agent).
- Region: `us-central1` is the safest free-tier region.

## 1. Stand up Postgres (Neon, 5 min)

GCP's smallest Cloud SQL instance (~$10/mo) does not fit the free tier.
Neon's free tier (3 GB, autosuspend) is the cleanest path:

1. https://console.neon.tech → New project → Postgres 16.
2. Copy the connection string (`postgres://...`).
3. Apply the Filmitto schema:
   ```bash
   cd romeo_phd
   export DATABASE_URL="postgres://..."
   pnpm install
   pnpm --filter @workspace/db run push
   ```
   This creates the `pipelines`, `pipeline_nodes`, `consultations`,
   `telemetry_events`, **`filmitto_projects`**, and **`filmitto_shots`**
   tables.

## 2. Deploy the bassito service in mock mode (Cloud Run)

From the root of the bassito repo:

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud config set run/region us-central1

gcloud run deploy bassito-longlive \
  --source . \
  --command "uvicorn" \
  --args "bassito_longlive_service:app,--host,0.0.0.0,--port,8080" \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  --set-env-vars "BASSITO_LONGLIVE_MOCK=1,BASSITO_LONGLIVE_MOCK_CHUNK_MS=400,BASSITO_LONGLIVE_MOCK_CHUNKS_PER_SHOT=5"
```

Cloud Run will use Python buildpacks to install `requirements.txt`. The
Blackwell-only deps (`torch`, `transformer-engine`, `nvidia-modelopt`,
`diffusers`) are commented out in `requirements.txt` so the buildpack
stays small — only `fastapi`, `uvicorn`, `sse-starlette`, `httpx`, and
friends are installed.

Verify:

```bash
SERVICE_URL=$(gcloud run services describe bassito-longlive --format "value(status.url)")
curl "$SERVICE_URL/v1/health"
# { "service": "bassito-longlive", "engine_loaded": true, "mock_mode": true, ... }
```

## 3. Deploy the romeo_phd api-server (Cloud Run)

From the root of the romeo_phd repo:

```bash
gcloud run deploy filmitto-api \
  --source . \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  --set-env-vars "DATABASE_URL=postgres://...,BASSITO_ENGINE_URL=$SERVICE_URL,AI_INTEGRATIONS_ANTHROPIC_API_KEY=sk-ant-...,AI_INTEGRATIONS_ANTHROPIC_BASE_URL=https://api.anthropic.com"
```

The buildpack picks up the `dev:api` script if you add a `start` script,
or you can deploy with an explicit Procfile (`web: pnpm --filter
@workspace/api-server run dev`). For production, build a Docker image —
the buildpack is fine for free-tier testing.

## 4. Deploy the UI

Either Firebase Hosting (free 10 GB) or Cloud Run static:

```bash
cd artifacts/romeo-phd
pnpm build  # produces dist/
# Then either:
#  - firebase deploy --only hosting
#  - or `gcloud run deploy filmitto-ui --source . ...` if you have an nginx setup
```

Point the UI's API base URL at `filmitto-api`'s Cloud Run URL.

## 5. Smoke-test the full flow

```bash
API_URL=$(gcloud run services describe filmitto-api --format "value(status.url)")

# Create a project
PROJECT=$(curl -s -X POST "$API_URL/api/filmitto/projects" \
  -H "Content-Type: application/json" \
  -d '{"title":"Demo","prompt":"a moonlit desert chase, three shots"}')
echo "$PROJECT"
PROJECT_ID=$(echo "$PROJECT" | jq -r .id)

# Storyboard via the Anthropic agent
curl -s -X POST "$API_URL/api/filmitto/projects/$PROJECT_ID/storyboard" -d '{}'

# Generate — streams SSE chunks from the bassito mock engine
curl -N -X POST "$API_URL/api/filmitto/projects/$PROJECT_ID/generate"
# data: {"event":"shot_started", ...}
# data: {"event":"shot_chunk", "chunk":{"shot_id":"shot_01", "chunk_index":0, ...}}
# ... (one chunk every 400ms)
# data: {"event":"shot_done", ...}
# data: {"event":"project_done", ...}
# data: {"event":"stream_end"}

# HITL: accept the first shot
curl -s -X POST "$API_URL/api/filmitto/shots/1/decision" \
  -H "Content-Type: application/json" -d '{"decision":"accept"}'
```

## When you want real video (later, on the $300 credit)

Three options, in order of recommendation:

1. **Use the credit on a non-GCP GPU host.** $300 lasts ~50 hours on a
   CoreWeave 2×B200 spot, easily enough for hundreds of test films.
   Point `BASSITO_ENGINE_URL` at the new host; nothing in the control
   plane changes.
2. **GCP A2 (A100 40GB)**, on-demand $3.70/hr or spot ~$1.10/hr. $300
   = ~275 spot hours. Swap the mock backend for Wan2.1 / HunyuanVideo /
   CogVideoX-5B (each fits in 40 GB).
3. **GCP A4 (8×B200)** when LongLive-2.0 weights ship. ~3 hours total
   on $300 — only worth it for a final demo render.

In all three cases, set `BASSITO_LONGLIVE_MOCK=0` (or unset it) and
provide `BASSITO_LONGLIVE_WEIGHTS=/path/to/checkpoint` plus the real
GPU deps in `requirements.txt`. The rest of the stack is untouched.
