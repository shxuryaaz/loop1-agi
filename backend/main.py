"""Agilow Loop 1 — Sprint Goals generator.

FastAPI backend exposing POST /api/process. It accepts a meeting transcript
(.txt) plus a sprint label and optional team members, then runs a two-phase
OpenAI pipeline:

  Phase 1 (extraction): transcript -> structured JSON of per-person goals.
  Phase 2 (formatting):  structured JSON -> a formatted Markdown document.

The endpoint returns the markdown, a short meeting summary, and the number of
people with at least one commitment.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# --------------------------------------------------------------------------- #
# Configuration & startup
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agilow.loop1")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    # Fail fast on startup with a clear, actionable error.
    sys.stderr.write(
        "\nFATAL: OPENAI_API_KEY environment variable is not set.\n"
        "Set it before starting the server, e.g.:\n"
        '  PowerShell:  $env:OPENAI_API_KEY = "sk-..."\n'
        "  bash:        export OPENAI_API_KEY=sk-...\n\n"
    )
    raise RuntimeError("OPENAI_API_KEY environment variable is required but not set.")

MODEL = "gpt-4o"
TEMPERATURE = 0.2

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Agilow Loop 1 — Sprint Goals", version="1.0.0")

# Allow the local Vite dev server by default. In production, set ALLOWED_ORIGINS
# to a comma-separated list of frontend origins (e.g. your Vercel URL).
_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_env_origins = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
allowed_origins = _default_origins + _env_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# OpenAI call helpers (with retry)
# --------------------------------------------------------------------------- #

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(RETRYABLE_ERRORS),
    reraise=True,
)
def _chat_completion(messages: list[dict], *, phase: str):
    """Call the OpenAI Chat Completions API, retrying on transient errors.

    Returns the OpenAI response object. Token usage is logged by the caller.
    """
    response = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        messages=messages,
    )
    return response


def _log_usage(response, *, phase: str) -> None:
    """Log prompt/completion token usage for a response, server-side only."""
    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "%s token usage: prompt=%s completion=%s total=%s",
            phase,
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
        )
    else:
        logger.info("%s token usage: unavailable", phase)


def _strip_code_fences(text: str) -> str:
    """Remove a wrapping ``` or ```json fence from a model response, if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop the opening fence line (``` or ```json).
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop the closing fence line.
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


# --------------------------------------------------------------------------- #
# Phase 1 — Extraction
# --------------------------------------------------------------------------- #

PHASE1_SYSTEM_PROMPT = """\
You are a project management assistant for Agilow, a project management \
consulting team. You are analyzing the raw transcript of a sprint planning \
meeting and extracting the concrete sprint goals each person committed to.

How to read the transcript:
- Transcripts are messy. Expect filler words, false starts, crosstalk, and \
interruptions.
- Speaker labels like "Me:" and "Them:" do NOT reliably map to names. Infer \
the real names of speakers from context (who they address, what they own, how \
others refer to them).
- The transcript may mix English with other languages (for example Hindi). \
Extract meaning regardless of language. NEVER skip or ignore non-English \
content — translate the intent into English in your output.

Whose goals to extract:
- If a list of team members is provided, prioritize those names, but also \
include any other person who is clearly discussed as owning a commitment.
- If no team members are provided, infer the participants' names from context.

What counts as a goal:
- Extract ONLY commitments that have a clear verb of commitment: "I will", \
"I'm going to", "my goal is", or someone explicitly assigning a task to a \
named person.
- Do NOT invent goals, subtasks, success criteria, dependencies, or risks that \
are not grounded in the transcript.
- If a named person has no concrete commitment, OMIT them entirely.

Points:
- If a point value is explicitly stated for a goal, use it and set \
"points_is_estimated" to false.
- Otherwise estimate the effort (small = 0.5-1, medium = 1.5-3, large = 4+) \
and set "points_is_estimated" to true.

Output format:
- Output ONLY a single JSON object exactly matching the schema below.
- No markdown code fences, no commentary, no explanatory text before or after.
- "subtasks", "dependencies", and "risks" are ALWAYS arrays — use [] when there \
are none, never null.
- "kaizen" and "success_criteria" are null when not discussed — never fabricate \
them.

JSON schema:
{
  "meeting_summary": "2-3 sentence summary",
  "people": [
    {
      "name": "string",
      "kaizen": "string or null — ONE sentence, only if explicitly discussed as a process-improvement focus",
      "goals": [
        {
          "title": "string, under 15 words, imperative phrasing",
          "points": 0.0,
          "points_is_estimated": false,
          "subtasks": ["string"],
          "success_criteria": "string or null, one sentence, form 'X is/has been Y'",
          "dependencies": [{"description": "string", "owner": "string or null"}],
          "risks": [{"description": "string", "mitigation": "string or null"}]
        }
      ]
    }
  ]
}\
"""

PHASE1_JSON_RETRY_MESSAGE = (
    "Your previous response was not valid JSON. Return ONLY the JSON object "
    "described in the system prompt, with no markdown formatting, no code "
    "fences, and no explanatory text."
)


def _validate_extraction(data: object) -> None:
    """Validate the top-level shape of the extraction result."""
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Phase 1 extraction failed: expected a JSON object, got {type(data).__name__}."
        )
    if not isinstance(data.get("meeting_summary"), str):
        raise RuntimeError(
            "Phase 1 extraction failed: 'meeting_summary' missing or not a string."
        )
    if not isinstance(data.get("people"), list):
        raise RuntimeError(
            "Phase 1 extraction failed: 'people' missing or not a list."
        )


def extract_goals(transcript: str, team_members: list[str] | None) -> dict:
    """Phase 1: extract structured sprint goals from a raw transcript."""
    system_prompt = PHASE1_SYSTEM_PROMPT
    if team_members:
        system_prompt += (
            "\n\nThe following team members were provided — prioritize them: "
            + ", ".join(team_members)
            + "."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "TRANSCRIPT:\n\n" + transcript},
    ]

    response = _chat_completion(messages, phase="Phase 1")
    _log_usage(response, phase="Phase 1")
    raw_output = response.choices[0].message.content or ""

    parsed: dict | None = None
    try:
        parsed = json.loads(_strip_code_fences(raw_output))
    except json.JSONDecodeError:
        logger.warning("Phase 1 returned invalid JSON; retrying once with correction.")
        retry_messages = messages + [
            {"role": "assistant", "content": raw_output},
            {"role": "user", "content": PHASE1_JSON_RETRY_MESSAGE},
        ]
        retry_response = _chat_completion(retry_messages, phase="Phase 1 (retry)")
        _log_usage(retry_response, phase="Phase 1 (retry)")
        raw_output = retry_response.choices[0].message.content or ""
        try:
            parsed = json.loads(_strip_code_fences(raw_output))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Phase 1 extraction failed: invalid JSON. Raw output: " + raw_output
            ) from exc

    _validate_extraction(parsed)
    return parsed


# --------------------------------------------------------------------------- #
# Phase 2 — Formatting
# --------------------------------------------------------------------------- #

PHASE2_SYSTEM_PROMPT = """\
You are a project management assistant for Agilow. Convert the extracted sprint \
goal JSON into a clean Markdown document. Output ONLY the markdown — no code \
fences wrapping the whole document, no commentary before or after.

Follow this EXACT structure (worked example):

# Sprint Goals (June 6th - June 13th)

## Shiv

**Kaizen:** Create timeline on whiteboard of program deliverables and EPICs.

### 1. Create visibility of work by creating PM documentation of cross-program needs (2 points)

- Document CONOPS, RHA in Pre-Pilot Timeline
- List vendors to be managed
- List EPICs correlated to workstreams

**Success criteria:** Operations schedule, vendors, and SW workstream & EPICs is visible, estimated, and prioritized.

**Dependencies:**
- Support in-office facilitation on Monday and Tuesday (Owner: Antonio Bojorges)
- Support decision making on tooling and operational support (Owner: Antonio Bojorges)

**Risks:**
- Keith is available only for 2 days, making decisions after Tuesday difficult
  - *Mitigation:* Cover all aspects on the whiteboard Monday and Tuesday, focus on software migration starting Tuesday evening

### 2. North Star document shared with Keith and Reeg (2 points)

- Document high-level initiatives from the original contract
- Set goals and success criteria for each initiative with timelines

**Success criteria:** North Star document reviewed and shared with Keith and Reeg by end of week.

---

**Shiv total: 4 / 4**

---

## Antonio

### 1. Deliver Program Schedule for Pilot Phase 1 to JR (1 point)

- Deliver Program Schedule for Pilot Phase 1 to Keith and Reeg in MS Planner and PDF format

**Success criteria:** Program Schedule is sent and acknowledged by Keith and Reeg.

---

**Antonio total: 1 / 1**

---

Formatting rules (follow every one exactly):

1. The document title is `# Sprint Goals ({sprint_label})`, using the sprint \
label provided in the user message.
2. Each person gets a `## {name}` heading.
3. Include `**Kaizen:** {text}` ONLY if that person's "kaizen" is not null. If \
it is null, skip the line entirely — never print "Kaizen: null".
4. Each goal heading is `### {n}. {title} ({points} point{s})`:
   - Numbering restarts at 1 for each person.
   - Use "point" (singular) when points == 1, otherwise "points".
   - Format points as an integer when whole (2, not 2.0); otherwise as a \
decimal (1.5).
   - If "points_is_estimated" is true, append " (estimated)" after the points, \
e.g. `(3 points (estimated))`.
5. List subtasks as `- ` bullets, ONLY when the subtasks array is non-empty.
6. Include `**Success criteria:** {text}` ONLY when "success_criteria" is not \
null.
7. Include a `**Dependencies:**` heading followed by `- {description} (Owner: \
{owner or "unassigned"})` bullets, ONLY when the dependencies array is \
non-empty. When an owner is null, write "unassigned".
8. Include a `**Risks:**` heading followed by `- {description}` bullets, ONLY \
when the risks array is non-empty. Under each risk, on the next line, add a \
nested `  - *Mitigation:* {mitigation}` bullet. When mitigation is null, write \
"Not yet defined".
9. After all of a person's goals, print `**{name} total: {sum} / {sum}**`, \
where {sum} is the total of that person's goal points, formatted the same way \
(integer when whole, else decimal). Both numbers are equal for now.
10. Place a `---` horizontal rule between each person's section (as shown in \
the example, around the totals).
11. If a person has zero goals, still print their `## {name}` heading, their \
Kaizen line if present, and `**{name} total: 0 / 0**`.
12. Output ONLY the markdown — no code fences wrapping the whole thing, no \
commentary.\
"""


def format_sprint_doc(extraction: dict, sprint_label: str) -> str:
    """Phase 2: render the extraction JSON into a formatted Markdown document."""
    messages = [
        {"role": "system", "content": PHASE2_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "SPRINT LABEL: "
                + sprint_label
                + "\n\nEXTRACTED DATA (JSON):\n"
                + json.dumps(extraction, indent=2)
            ),
        },
    ]

    response = _chat_completion(messages, phase="Phase 2")
    _log_usage(response, phase="Phase 2")
    raw_output = response.choices[0].message.content or ""
    return _strip_code_fences(raw_output).strip()


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    sprint_label: str = Form(...),
    team_members: str | None = Form(None),
):
    """Process a transcript upload into a formatted sprint goals document."""
    # --- Validation --------------------------------------------------------
    filename = file.filename or ""
    if not filename.lower().endswith(".txt"):
        return JSONResponse(
            status_code=400,
            content={"error": "File must be a .txt transcript."},
        )

    raw_bytes = await file.read()
    try:
        transcript = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        transcript = raw_bytes.decode("utf-8", errors="replace")

    if not transcript.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Transcript file is empty."},
        )

    if not sprint_label.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Sprint label is required."},
        )

    members: list[str] | None = None
    if team_members and team_members.strip():
        members = [name.strip() for name in team_members.split(",") if name.strip()]
        if not members:
            members = None

    # --- Pipeline ----------------------------------------------------------
    try:
        extraction = extract_goals(transcript, members)
        markdown = format_sprint_doc(extraction, sprint_label.strip())
    except Exception:  # noqa: BLE001 — convert any failure into a clean 500.
        logger.exception("Pipeline failed while processing transcript.")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Failed to process the transcript. Please try again."
            },
        )

    people = extraction.get("people", [])
    people_count = len(people) if isinstance(people, list) else 0

    return {
        "markdown": markdown,
        "meeting_summary": extraction.get("meeting_summary", ""),
        "people_count": people_count,
    }


@app.get("/api/health")
async def health():
    """Simple liveness probe."""
    return {"status": "ok"}
