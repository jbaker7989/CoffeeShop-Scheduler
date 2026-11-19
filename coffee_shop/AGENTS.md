# coffee-scheduler

Repo-wide conventions.

## Stack
- n8n (workflow orchestration / exported workflows in `n8n/`)
- LangGraph (agent orchestration graphs)
- A2A (agent-to-agent protocol; agent cards in `agents/*/agent_card.json`)

## Layer split
- **n8n** — triggers, external I/O, scheduled runs, human-in-the-loop glue.
- **LangGraph** — per-agent reasoning graphs and tool calls.
- **A2A** — contracts between agents (cards, auth, versioning).

## Data rules
- Source data lives in `data/` (roster, labor_rules, labor_standards, weather, school calendar).
- Agents never hardcode legality; see `agents/labor_compliance`.

## Test commands
- `npm test`
- `npm run lint`
- `npx tsc --noEmit`

## Manager UI session and test rules
- The backend remains the authority for token validity, membership, and logout revocation. Cookie presence in middleware is only a navigation hint; never treat it as authentication.
- Keep auth requests same-origin and cookie credentials explicit. Logout must call the server endpoint before redirecting; do not rely on deleting a browser cookie alone to invalidate a session.
- Never place access or refresh tokens in URLs, local storage, or client-visible response data for browser-cookie flows. Preserve fixed redirect targets; do not accept arbitrary `returnTo` URLs without validation.
- Run `npm test`, `npm run lint`, and `npx tsc --noEmit` for UI changes. Report failures and skipped checks explicitly.
