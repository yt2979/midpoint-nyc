# THE MIDPOINT NYC

By **yt2979** for IEOR 4570. Built from the class `gemini-web-tool-calling` example.

## 1. Who is it for?

This chat agent helps 2–4 friends find a fair place to meet in New York City. Give each person's name and starting address or station. It finds meeting options based on where the group starts, compares travel times, checks personal time limits, finds nearby places, and plans when to leave. You can also name places you want to compare.

## 2. How does it remember the chat?

Each chat has its own session ID. The agent remembers the group, routes, and time limits within that session. Different sessions stay separate. **New chat** clears the current chat.

Sessions stay in server memory for up to one hour without use. A server restart clears them.

## 3. What tools does it use?

| Tool | Main inputs | What it does |
| --- | --- | --- |
| `get_group_routes` | People, optional meeting points and meeting time | Finds up to 3 meeting options with **Google Places API**, then gets each person's travel time from **Google Routes API**. Supports public transit and driving. |
| `evaluate_meeting_fairness` | Route set ID, goal, personal time limits | Checks limits and ranks the meeting points. This is the project's **original tool**. |
| `search_nearby_places` | Route set ID, meeting point ID, type, optional search word | Gets restaurants, cafes, or activities from **Google Places API** within 1 km. |
| `plan_group_departures` | Route set ID, meeting point ID, buffer | Uses the measured route and available train or bus times to calculate when each person should leave. |

The original fairness tool first removes points that break anyone's time limit. It then picks the point with the shortest longest trip. Ties use the lowest total travel time. It can also compare a goal of lowest total travel time.

If no meeting points are given, Places finds the starting locations and nearby stations. For a group that only drives, it looks for parks and cafes. The group locations and time limits guide the search. It picks up to 3 different places to check. Real route times decide the winner; distance alone does not. There is no fixed list of meeting points.

Tool names, descriptions, and input rules are defined in `tools.py`. Tools use routes saved by the server. Errors give a reason and a next step. Failed routes cannot support a recommendation.

## 4. How are tool calls shown?

Open **How this answer was checked** below a reply to see each tool's inputs and result.

`POST /chat` keeps the starter's response format:

```json
{
  "response": "The agent's answer",
  "session_id": "The chat ID",
  "tool_calls": [
    {"name": "Tool name", "args": {}, "result": {}}
  ]
}
```

## 5. What changed in the frontend?

The site has a new home page, a New York photo, four task buttons, and a chat view. The buttons guide users to enter their own plans. Users can keep asking questions in the same chat.

The main idea is to make a group decision: find a meeting point that reduces the longest trip while keeping each person's time limit.

## 6. Three sample queries

Run these in order in one chat. Times and results may change.

1. **Find a meeting point**

   > Alice: Columbia University, Broadway and W 116th St, Manhattan. Bob: Atlantic Terminal, Brooklyn. Find a fair place for us to meet by public transit.

2. **Add a time limit**

   > Bob can travel at most 20 minutes. Does any spot work?

3. **Remove the limit and plan the meetup**

   > Remove Bob's limit. Find two restaurants near the best meeting point. We will meet at that meeting point tomorrow at 7 PM, New York time. When should each person leave?

## 7. How to run it locally

Install Python 3.11+, uv, and gcloud. Enable Vertex AI, Routes API, and Places API (New) in a billed Google Cloud project.

```bash
git clone https://github.com/yt2979/midpoint-nyc.git
cd midpoint-nyc
uv sync --frozen
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=ieor-4570-f26-yt2979
uv run app.py
```

The app asks for the Google Maps API key in the terminal. Input is hidden. Open **http://localhost:8001/**.

For a server, set `GOOGLE_MAPS_API_KEY` in its environment. Keep keys out of the repo and chat.

## 8. Limits

The agent compares a small set of places found near the group, or the places you name. It does not check every place in NYC. Automatic search makes up to 7 Places requests, followed by up to 12 Routes requests. Travel times are estimates. Changing only a time limit reuses saved routes. Future plans use routes for the requested meeting time at the selected meeting point.

Nearby places are within a straight-line radius of 1 km. A final restaurant needs its own route check. Check hours and availability before going. The app does not book rides, tables, or tickets.
