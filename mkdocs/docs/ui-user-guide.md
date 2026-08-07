# UI user guide

## Open FAIR2WISE

After starting the stack, open `http://127.0.0.1:5173`. The initial boot:

1. loads saved browser preferences;
2. synchronizes them to the agent through `PUT /settings`;
3. fetches the active graph; and
4. restores the last selected browser chat session.

If the backend is unavailable, the shell still renders and reports a useful
connection error when an API-dependent action is attempted.

## Ask a question

Enter a domain-specific question in the chat field and press **Enter** or the
send button. During a run the UI displays:

- the latest agent progress message;
- elapsed time;
- graph nodes and edges received through live progress events; and
- a stop button in place of send.

The UI sends the current question, the session ID, and at most the eight most
recent non-empty user/assistant history messages. The agent's persistent
session memory remains authoritative for workflow continuation.

### Stop a run

Select **Stop** while a request or answer animation is active. The browser
aborts the fetch, ignores late events from that request, clears transient
progress state, and preserves any answer text already displayed. Stopping a
browser request does not delete its chat session.

### Read an answer

Assistant cards may contain:

- paragraph text and fenced code blocks;
- KG citations such as `[KG: P3HT]`;
- highlighted PDF filenames and publication references;
- relevant or alternative publication lists;
- confidence/status metadata retained with the message; and
- a **View Knowledge Graph** action when graph nodes were retrieved.

The copy button copies the answer and its publication block. Publication cards
open DOI or arXiv pages when identifiers are available and otherwise offer a
Semantic Scholar search.

## Approval workflow

In agentic Splash mode, insufficient evidence can pause the workflow.

### Choose a paper

When candidate papers are shown, continue in the normal chat input. Identify a
paper by title, number, DOI, or repository, or tell the agent not to download
one. Unavailable candidates are marked and the agent can return alternatives.

### Approve extraction

After a paper downloads, the response presents **Run extraction** and **Skip**.
Run extraction resumes the saved backend workflow; Skip stops it safely. The
action uses the same browser/backend session ID as the originating question.

The active settings determine whether approved extraction is targeted or full.
JSON graph mode does not run download or extraction agents.

## Chat sessions

### Create a session

Select **New chat**. A unique browser and backend session ID is created. The
title starts as `New chat` and becomes the normalized first user prompt,
truncated to 60 characters.

### Find or switch sessions

Select **Search chats**, filter by title, and choose a result. Sessions are
listed by most recently updated. Switching sessions:

- cancels the current browser request;
- restores that session's messages;
- restores the last assistant graph selection when available; and
- sends subsequent turns with the selected session ID.

### Delete a session

Use the trash action in **Search chats**. The UI removes the local session and
requests deletion of its backend memory/workflow state. If the last session is
deleted, a new empty session is created automatically.

The browser keeps at most 80 messages per session. Storage errors, invalid JSON,
or private-browsing restrictions fall back safely to a fresh session.

## Paper search

Select **Paper search**, describe a topic or paste a paragraph, and run the
search. By default it searches publication metadata attached to ranked nodes
in the active KG.

Enable **Include papers beyond the knowledge graph** to merge external OpenAlex
results. Results show:

- title, authors, year, journal, and source pages;
- DOI/arXiv and Semantic Scholar links;
- KG nodes supported by the publication;
- a bookmark action; and
- a copy-citation action.

The external search option requires backend network access. It does not add a
paper to the knowledge graph by itself.

## Bookmarks

Bookmark icons appear in chat publication lists, paper-search results, and
graph node details. Select **Bookmarks** to view all saved publications.

Bookmarks are local to the browser profile. A DOI is used as the stable key
when available; otherwise the source filename and title form the key. Changes
synchronize between open tabs through browser storage events, but they are not
uploaded to the agent.

## Settings

Select **Settings**, change the draft preferences, and choose **Save
Preferences**. Closing without saving restores the last saved values.

| Setting | Values | Effect |
|---|---|---|
| Workflow | Agentic, Deterministic | Choose orchestrated evidence decisions or the fixed retrieve/download/extract loop |
| Extraction | Targeted, Full | Process selected relevant pages or every page after approval |
| Max pages per PDF | 1–100 | Page cap shown only for targeted extraction |
| LLM backend | CBORG, Ollama | Choose hosted or local inference |
| Model | Configured CBORG list or Ollama model text | Select the active model for the chosen backend |
| KG source | `splash_links`, JSON | Choose the editable database or a read-only MatKG JSON file |
| JSON graph file | Files reported from `storage/kg` | Select the retrieval graph when JSON mode is active |

Saving updates backend runtime settings, saves browser preferences, and reloads
the graph. Settings are runtime-wide in the agent process; another browser
connected to the same agent can change them.

### Browser defaults

| Setting | Default |
|---|---|
| Backend | CBORG |
| CBORG model | `lbl/cborg-chat` |
| Ollama model | `deepseek-r1:70b` |
| KG source | `splash_links` |
| Workflow | Agentic |
| Extraction | Targeted |
| Targeted page cap | 6 |

The backend may return different available models, graph files, or server
defaults. Model aliases from older Gemini names are normalized during load.

## Browser persistence

| Local-storage key | Content |
|---|---|
| `fair2wise.chat.sessions.v2` | Sessions, active ID, and up to 80 messages per session |
| `fair2wise.chat.messages.v1` | Legacy single-session history, migrated then removed |
| `fair2wise-agent-settings-v1` | Agent preferences |
| `fair2wise-publication-favorites-v1` | Bookmarked publications and save times |

Clearing site data removes this browser state but does not remove the Splash
database or Docker volumes. Backend named-session data is removed through the
session delete API, not by clearing browser storage alone.

## Common problems

| Symptom | Check |
|---|---|
| Cannot reach the agent backend | Start `./scripts/start_agent_backend.sh`, or start the complete Compose stack |
| Settings endpoint not found | Restart an older backend so the current routes are loaded |
| Agent run failed with CBORG 403 | Authorize the machine's outbound IP and verify `CBORG_IP_FAMILY` |
| Empty graph after an answer | Confirm the selected graph source and inspect the agent/Splash health endpoints |
| Editing controls absent | JSON mode is read-only; switch to `splash_links` |
| No external paper results | Check backend internet access and OpenAlex configuration |
| Browser history disappeared | Check whether site data was cleared or local storage is blocked |

See [Knowledge graph UI](ui-knowledge-graph.md) for graph controls and editing,
and [UI development and testing](ui-development.md) for service diagnostics.
