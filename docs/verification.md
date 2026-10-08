# Verification record

Date: 2026-10-08. Developer machine, class project ieor-4570-f26-yt2979.

## Earlier verification baseline

- Deterministic Python tests: minimax vs sum, hard/infeasible constraints, missing/negative observations, no invented route matrix, previous-day departure, DST, early scheduled transit departure.
- Model harness tests: no-tool explanations, malformed/non-finite argument JSON, trace preservation after model failure, bounded tool loop.
- API boundary tests: request headers/field masks, transit arrivalTime, timeout/auth failures without key disclosure, actual destination coordinates, unknown venue metadata.
- Session tests: same conversation remembered, separate conversation isolated, expiry/new-chat reset, concurrent same-chat conflict, input bounds.
- Frontend Node tests: safe HTTP(S) links, Markdown tokenization, unsafe-link text, per-tab restore validation.
- Final regression suite: 31 Python tests, 7 Node tests; lockfile consistency and Python/JS syntax checks passed.
- Limit persistence: refresh to a specific venue, change meeting time, add/remove members, or evaluate an older snapshot without dropping current limits; explicit null removal is validated and session-scoped.
- HTTP recovery: safe backend detail, full-chat New chat action, pending-request wait, session reset notice.
- Real Vertex Gemini: Chinese conceptual explanation returned without tool call.
- Real Vertex Gemini with synthetic Maps fixtures: initial get_group_routes → evaluate_meeting_fairness; follow-up evaluated fair and total_time without route requery; third turn queried future routes → evaluated → searched restaurant/activity → rechecked exact venue → evaluated → plan_group_departures. No tool errors. These fixtures do not prove real Maps results.
- Local browser: renders chat, example1 sends query, actual Gemini tool trace expands to arguments/result, missing Maps configuration is explained without fabricated travel times.
- Local browser: New chat did not recall Alice/Bob/Carol's previous origins; active-chat follow-up buttons remained reachable. Desktop and 375px responsive checks found no document horizontal overflow. A UI screenshot is saved locally as /tmp/meet-fair-nyc-preview.jpg.
- Final tool schema: real Gemini with synthetic 30-minute routes preserved Alice's 20-minute maximum and returned no feasible recommendation. No tool argument errors occurred.

## Follow-up verification: 2026-10-08

- Current regression suite: 38 Python tests and 12 Node tests passed. Python compile checks and the frontend JavaScript syntax check passed.
- Added regressions for current-request fairness checks, explicit removal of a person's limit, invalidating older checks when limits change, and blocking places searches or departure plans at infeasible destinations.
- Empty model replies retry once without saving an empty assistant message or repeating completed tool calls. Repeated empty replies stop with an actionable error and preserve the trace.
- Real Gemini replay with controlled Maps fixtures passed: remove Bob's limit through `evaluate_meeting_fairness` with an explicit null, then search near the newly selected Times Square; fetch routes for tomorrow at 7 PM in New York, check fairness, and plan departures. Assertions checked the saved limit, selected destination, date, time and route-derived travel duration. This replay does not validate live Maps durations or restaurant listings.
- Feature buttons now open short English guides locally. They do not send sample people, limits or dates to Gemini. The user's own reply starts the request; frontend tests cover clicks, submission, refresh and input bounds.
- User-provided integrated conversation shows live Routes and Places tool results. `/health` also reported a configured Maps key. The conversation exposed skipped fairness checks after limit removal and before departure planning; the changes above address those gaps. Updated Python code still requires a backend restart and a fresh live conversation.

## Required before submission

- Latest user trace: all six leaving-now route requests returned HTTP 400, followed by eight repeated model calls. The old generic message did not preserve Google's specific reason, so the invalid field is not yet identified. Added per-turn duplicate-route suppression and bounded repeated-failure handling, plus redacted Google HTTP diagnostics. 41 Python tests and compile checks passed, including reproductions of repeated failed and successful queries. Restart and inspect the next live result to diagnose the remaining HTTP 400; no claim that live route access is fixed.

- Restart the local backend and repeat integrated Maps+Gemini tests with the updated code, then repeat them on the deployed service. A configured runtime key alone does not prove that every query works.
- Container build: Docker CLI exists but the local Docker/OrbStack daemon is not running, so no successful container build is claimed.
- GitHub repository push and continuous Cloud Run deployment. GitHub CLI is currently unauthenticated and this repo has no remote. Do not infer a deployed URL.
- Run README examples on public Cloud Run site, confirm GitHub push creates a revision, generate final submission.json with actual deploy_url and solo author yt2979, keep site running until grades.

## Independent review

Initial review requested three fixes: preserve hard limits across new route sets, retain usable follow-up controls, and show actionable server HTTP detail. All three were fixed with regressions; independent re-review approved with no remaining Important findings. The browser confirms active-chat follow-up controls remain visible.
