# Deploy to Google Cloud Run

Follow Google's [continuous-deployment guide](https://docs.cloud.google.com/run/docs/quickstarts/deploy-continuously), selecting **Dockerfile** builds for this repository.

1. Push this project to your GitHub repo. If private, invite the assignment's grader accounts: `codeboi07`, `bhuvighosh3`, `nniishhh`, `x`.
2. Enable Cloud Run, Cloud Build, Artifact Registry, Vertex AI and Secret Manager APIs in project `ieor-4570-f26-yt2979`. Routes and Places API (New) must already be enabled for the Maps key's project.
3. Create a dedicated runtime service account (`meetfair-runtime`), grant it `roles/aiplatform.user`, store the Maps key as a Secret Manager secret (`meetfair-maps-key`), and grant that account `roles/secretmanager.secretAccessor` on **that secret**.
4. In Cloud Run, create a service and choose **continuously deploy from a repository**. Connect GitHub, select your repo and the branch containing this code, build using the root `Dockerfile`, and select a region such as `us-central1`.
5. Under service settings: runtime service account = `meetfair-runtime`; container port = **8080**; public/unauthenticated access for the grader; **maximum instances 1**, **concurrency 8**, **request timeout 600 seconds**. Use **one worker** (already set by the Dockerfile). Minimum instances 0 is sufficient; instances/restarts can reset chats as disclosed above.
6. Set environment `GOOGLE_CLOUD_PROJECT=ieor-4570-f26-yt2979`, `GEMINI_MODEL=vertex_ai/gemini-3.5-flash-lite`. Add secret environment variable `GOOGLE_MAPS_API_KEY` from `meetfair-maps-key`. Do not put the key in the repository or build arguments.
7. Deploy and run all three sample queries on the **deployed URL**. Push a small visible change and check that GitHub continuous deployment produces a new healthy revision. Keep the service reachable until grades are released.
8. Generate the required real root manifest:

   ```bash
   uv run python scripts/write_submission.py https://YOUR-ACTUAL-SERVICE.run.app
   ```

   This verifies HTTPS, the app homepage and configured Maps runtime before writing `submission.json` with `authors: ["yt2979"]`. Commit/push that file and submit the **GitHub repo URL** on Courseworks.

`submission.example.json` is only a template and is **not a valid submission**. Final `submission.json` is intentionally generated after a real deployment exists; do not submit the example. Root `app.py`, `pyproject.toml`, `uv.lock` and `README.md` are included.
