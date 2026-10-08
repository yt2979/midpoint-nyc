# THE MIDPOINT NYC

A visual redesign of the Meet Fair NYC coursework agent.

The four home buttons open short English guides. They do not send sample people, limits, or dates, and make no model or Maps request. The user supplies their own details before the first chat request. Follow-up guides keep the current group, and ask for new limits, nearby interests, or a meeting time. The three grader queries below remain optional test examples.

**A fairer place to meet.** A tool-calling chat agent for 2–4 friends traveling from different parts of New York City. It compares real travel times to a shortlist, respects each person's hard time limit, explains who bears the longest commute, and helps the group find nearby restaurants or activities.

Built by **yt2979** for IEOR 4570, starting from the instructor's `gemini-web-tool-calling` example. It retains FastAPI, LiteLLM/Vertex Gemini and the assistant → tool → assistant loop, while replacing the weather tool, adding trusted route observations and deterministic group planning, and redesigning the chat.

Answers default to 2–4 short sentences; full tool details remain expandable. Longer explanations appear only when requested.

## Why use this instead of looking up individual routes?

The question is a group decision: which destination works for everyone, which person is excluded by a time limit, and how does minimizing total travel differ from protecting the person with the longest trip? The agent checks multiple person/destination pairs together and explains that tradeoff. It compares supplied candidates only, not every location in NYC. Google Maps remains the source of individual routes and venue data.

## Tools

| Tool | Behavior | Source |
| --- | --- | --- |
| `get_group_routes` | 2–4 people × 1–3 candidate destinations; public transport or driving; future arrival-by transit or leaving-now estimates | Google Routes API; up to 12 HTTP calls |
| `evaluate_meeting_fairness` | **Original tool:** filter hard limits and unavailable routes; rank by longest commute, then total time; compare total-time objective and report blockers when no candidate works | Deterministic Python over server-held route snapshots |
| `search_nearby_places` | Up to 3 restaurants, cafes or activities within 1 km; optional keyword; maps links and observed metadata | Google Places API (New), Nearby/Text Search |
| `plan_group_departures` | NYC leave-by times, actual transit schedule when present, chosen buffer and disclosed driving assumptions | Deterministic Python over scheduled route observations |

The fairness tool is more than a geographic midpoint calculator. If candidate A takes Alice/Bob 10/50 minutes and candidate B takes 35/35, `fair` picks B (longest 35 instead of 50), while `total_time` picks A (60 combined instead of 70). Alice's 20-minute limit plus Bob's 40-minute limit makes **neither** feasible. The tool reports both conflicts and does not relax either limit. Missing routes never become zero-minute journeys.

This group-constraint decision tool is this solo project's original contribution. Minimax is an established optimization objective, not a claimed new mathematical algorithm. Class-wide tool uniqueness remains subject to instructor comparison.

The model passes an opaque `route_set_id` between tools. The server retrieves that session's actual API observations; the model cannot supply a fabricated duration matrix to the fairness/departure tools. A restaurant gets its own exact-address route check before it can be called a fair final venue.

## Three sample queries for the grader

Run these in order in one chat. Query 2 is a memory/constraint follow-up. Query 3 can also follow query 1 directly. If query 2 has no feasible candidate, the agent should explain that conflict and ask for a new candidate or an explicit limit change before promising a final venue.

1. **Initial fair comparison:**

   > Alice leaves from Columbia University at Broadway & W 116th St, Bob from Atlantic Terminal in Brooklyn, and Carol from Jackson Heights–Roosevelt Av station in Queens. All use public transport. Compare Times Square, Union Square and Grand Central for a meetup leaving now. Choose the fairest option and show everyone's travel time.

   Expected: `get_group_routes`, then `evaluate_meeting_fairness`, observed per-person times and a minimax explanation. The winner may change with actual service schedules.

2. **Remember the group and expose a conflict/tradeoff:**

   > Keep the same group and routes. Alice can travel at most 35 minutes and Bob at most 25 minutes. Does any candidate work? Compare fairness with minimizing total travel time, and explain the compromise without changing our limits.

   Expected: reuse the route set, evaluate both objectives with the limits, show violations when applicable. No needless repeated Maps calls. To demonstrate the guaranteed no-solution behavior, follow up with “Both Alice and Bob can travel at most 1 minute; what blocks the plan?”

3. **Nearby venues and exact destination check:**

   > Find three restaurants and three things to do within 1 km of the recommended meeting point. Show Google Maps links. Before calling a restaurant our fair final venue, check everyone's routes to its exact address.

   Expected: Places calls for restaurant/activity; exact restaurant-address route check and fairness evaluation; no invented hours, dollar prices, reservations or ticket availability. If no fair candidate exists under current limits, the agent must resolve the conflict first.

To exercise the fourth tool after a feasible plan, say: “Let's meet there tomorrow at 6 pm New York time. Recheck the routes for that meeting time and tell each of us when to leave with a 10-minute buffer.” The agent must query the **future** arrival-by routes before departure planning, rather than recycling leaving-now times. Start **New chat** and ask “Where am I starting?” to verify it cannot recall the other chat's group.

## Local setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The checked-in lock file selects the dependencies. The app uses the instructor's default Gemini model; `GEMINI_MODEL` can override it when necessary.

```bash
cd "/Users/elainetao/Documents/ChatGPT/GenAI-HW"
uv sync --frozen
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=ieor-4570-f26-yt2979
uv run app.py
```

If ADC already works, no new login is necessary. `app.py` privately prompts for a Google Maps key if it is not set in the environment. Input is hidden and remains only in that process. Open **http://localhost:8001/**. The instructor sample on port 8000 can remain running. The key must permit **Routes API** and **Places API (New)** in a billed project. `.env.example` documents variables; the app does not automatically load a `.env` file. Never paste credentials into chat or commit them.

For a server without interactive input:

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8001 --workers 1
```

Set `GOOGLE_MAPS_API_KEY` securely in its runtime environment first. `/health` reports whether a key is configured; that is not proof of API permissions or connectivity. API calls use explicit field masks, timeouts, bounded counts and sanitized actionable errors. Place rating, price-level and current-hours fields can affect the billing tier; the model doesn't request every field with a wildcard.

## API and session behavior

`POST /chat` with `{ "message": "...", "session_id": "optional existing id" }` returns:

```json
{
  "response": "Human-readable answer",
  "session_id": "server-generated UUID",
  "tool_calls": [{"name": "evaluate_meeting_fairness", "args": {"route_set_id": "observed-id"}, "result": {"objective": "fair"}}]
}
```

The illustrative record above is abbreviated; actual results contain metrics and violations. Every requested call is recorded with `name`, `args`, `result`, including failures. The UI displays expandable arguments and results. The model decides when tools are appropriate; conceptual explanations need no API call. A turn is bounded to 8 model rounds and 12 tool executions.

Places searches and departure plans require a successful fairness check in the current request. Changed limits invalidate earlier checks, and infeasible destinations are rejected. Future departure plans use new routes for the requested meeting time, then check fairness before planning. An empty Gemini reply is retried once without repeating completed tool calls; another empty reply returns an actionable error and preserves the trace.

Identical route requests within one user turn reuse their result when limits have not changed. Repeating a wholly failed request stops with its error instead of querying Maps eight times. HTTP failures include Google's diagnostic message with the runtime key redacted. These messages are untrusted API data, not instructions.

Histories, constraints and route snapshots live in independent server sessions with per-session locks. A group's travel limits carry into new route queries for the same member names, including exact-venue checks; omitting a limit preserves it. Set a new numeric limit in a route query or fairness evaluation, and remove a limit explicitly with `max_minutes: null` in `evaluate_meeting_fairness`. Sessions expire after one hour of inactivity. New chat clears the active session and this tab's stored transcript; tabs use `sessionStorage`, not shared `localStorage`. **Sessions are in memory:** a server/container restart or a new instance resets them, and the UI flags a changed server session so the group and limits can be restated. Deploy one worker on one Cloud Run instance as below. This coursework version does not provide accounts or durable cross-device history.

## Verification

```bash
uv run pytest -q
node --test static/chat.test.js
uv run python -m compileall -q app.py agent.py tools.py maps_client.py fairness.py
```

Tests cover original fairness vs total time, hard limits, incomplete/invalid routes, midnight/DST, early transit arrivals, external request shapes/errors, session-owned snapshots, malformed tool JSON, trace preservation on model failure, conversation memory/session isolation, simultaneous-message protection and safe text/link rendering. Model and network boundaries are replaced with test doubles; these tests do not certify live Maps access. `test_routes.py` and `test_places.py` are the separately runnable real API smoke tests already supplied in this workspace.

## Cloud Run continuous deployment from GitHub

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

## Data sources and limits

- [Google Routes computeRoutes reference](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes): actual route duration, travel mode, transit steps and destination coordinates. DRIVE uses traffic-aware estimates for an explicit assumed departure; it is not an Uber integration.
- [Google Places Nearby Search (New)](https://developers.google.com/maps/documentation/places/web-service/nearby-search) and [Text Search (New)](https://developers.google.com/maps/documentation/places/web-service/text-search): listings, maps links, ratings and optional metadata. Searches use a **straight-line** 1km radius; keyword results are distance-filtered. No bookings/tickets or future opening guarantees.
- [Google Maps attribution policies](https://developers.google.com/maps/documentation/places/web-service/policies): source tool cards display Google Maps and any returned third-party attribution. Public privacy and terms pages are available at `/privacy` and `/terms`.

Routing is estimated, not guaranteed arrival. Transit departure plans honor an arrive-by service's actual earlier start when supplied. Leave-by buffer isn't a guarantee of an earlier train; check the displayed service schedule. No weather tool, Uber fare API, accounts, reservations, ticket purchasing or exhaustive NYC venue optimization is included.

## How to explain the code

`app.py` handles HTTP/session ownership; `agent.py` runs Gemini until it answers or requests a tool; `tools.py` defines argument contracts and dispatches; `maps_client.py` calls external APIs; `fairness.py` performs the original calculation; `index.html` and `static/` render the chat. The model chooses tools and interprets results; Python validates arguments, fetches observations and computes decisions. Understand this distinction before presenting the project.
