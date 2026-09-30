"""Run with CLIPROXYAPI_API_KEY and CLIPROXYAPI_MANAGEMENT_KEY in the environment."""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("CLIPROXYAPI_BASE_URL", "http://cliproxyapi.tail94c55.ts.net/v1").rstrip("/")
CLIENT = os.environ["CLIPROXYAPI_API_KEY"]
ADMIN = os.environ["CLIPROXYAPI_MANAGEMENT_KEY"]
MODEL = "codex-lb/gpt-6-luna"


def request(url, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=180)
        return response.status, response
    except urllib.error.HTTPError as error:
        return error.code, error


assert request(BASE.removesuffix("/v1") + "/healthz")[0] == 200
assert request(BASE + "/models")[0] == 401
assert request(BASE + "/models", "invalid-key")[0] == 401
status, response = request(BASE + "/models", CLIENT)
assert status == 200, status
assert MODEL in [m["id"] for m in json.load(response)["data"]]
management = BASE.removesuffix("/v1") + "/v0/management/auth-files"
assert request(management, CLIENT)[0] in (401, 403)
assert request(management, ADMIN)[0] == 200
print("PASS: tailnet health, model listing and separated inference/management auth", flush=True)

payload = {
    "model": MODEL,
    "input": "Call record_result with value OK. Do not do anything else.",
    "reasoning": {"effort": "max"},
    "tools": [{"type": "function", "name": "record_result", "description": "Record the test value",
               "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                              "required": ["value"], "additionalProperties": False}, "strict": True}],
    "tool_choice": {"type": "function", "name": "record_result"},
    "stream": True,
}
status, response = request(BASE + "/responses", CLIENT, payload)
assert status == 200, status
completed = None
events = 0
for line in response:
    if not line.startswith(b"data:"):
        continue
    data = line[5:].strip()
    if data == b"[DONE]":
        continue
    event = json.loads(data)
    events += 1
    assert event.get("type") not in ("error", "response.failed"), event.get("type")
    if event.get("type") == "response.completed":
        completed = event["response"]
assert completed is not None, "No completed response"
assert completed.get("reasoning", {}).get("effort") == "max", "max reasoning not confirmed in response"
calls = [item for item in completed["output"] if item.get("type") == "function_call"]
assert calls and calls[0]["name"] == "record_result"
assert json.loads(calls[0]["arguments"]) == {"value": "OK"}
print(f"PASS: Responses SSE ({events} events), max reasoning and schema-valid function call", flush=True)

# Codex upstream strips previous_response_id: replay history explicitly.
followup = {
    "model": MODEL,
    "input": [{"role": "user", "content": payload["input"]}] + completed["output"] + [
        {"type": "function_call_output", "call_id": calls[0]["call_id"], "output": "OK"},
        {"role": "user", "content": "The test is finished. Reply exactly DONE."},
    ],
    "reasoning": {"effort": "max"},
    "stream": False,
}
status, response = request(BASE + "/responses", CLIENT, followup)
assert status == 200, status
result = json.load(response)
assert result["status"] == "completed"
assert result.get("reasoning", {}).get("effort") == "max"
text = "".join(part.get("text", "") for item in result["output"] if item.get("type") == "message"
               for part in item.get("content", []))
assert text.strip() == "DONE", "Unexpected tool-result continuation"
print("PASS: nonstreaming Responses with replayed history and function result", flush=True)
