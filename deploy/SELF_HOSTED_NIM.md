# Self-Hosted NIMs with Tool Calling

How to bring up the generation + embedding NIMs on a local GPU host so secure agents on VAST run
**fully self-hosted** — no data leaves your environment — with **streaming tool calling**
working end-to-end.

This is a self-contained guide: it explains every sharp edge on the happy path,
including the tool-call parser you must supply (the stock one is broken for streaming).
Follow it top-to-bottom and you won't hit the gaps.

---

## What you end up with

| NIM | Suggested container | Port | Role |
| --- | --- | --- | --- |
| `llama-3.3-nemotron-super-49b-v1.5` | `nim-nemotron-super-49b-tools` | `<CHAT_PORT>` | Generation / OpenClaw — **tool calling ON** |
| `nv-embedqa-e5-v5` | `nv-embedqa-e5-v5` | `<EMBED_PORT>` | Query embeddings (must match the corpus embed model/dim) |

The app then points `model.base_url` → `:<CHAT_PORT>` and `model.embed_base_url` → `:<EMBED_PORT>`
(see [Step 4](#step-4--wire-agents-on-vast-to-the-local-nims)).

> **Golden rule:** sub-agents run OpenClaw with **`stream: true`**. Tool calling must
> work *in streaming mode*. NVIDIA's stock Nemotron tool parser does **not** support
> streaming — that is the #1 gap this guide fixes ([Step 1b](#step-1b--the-tool-call-parser-the-critical-part)).

Throughout, `<NIM_HOST>` is the **IP** of your GPU host (use the IP, not a hostname, so
sandbox pods can reach it over cluster networking).

---

## Prerequisites

- A GPU host with Docker + the NVIDIA container runtime.
- `NGC_API_KEY=nvapi-...` exported (needed to pull the image / warm the weights cache).
- Enough VRAM for the 49B — commonly tensor-parallel across 2 GPUs (see
  [profiles](#gpu-profiles--the-cache)).
- Reference docs:
  [Function calling](https://docs.nvidia.com/nim/large-language-models/latest/function-calling.html),
  [Tool calling & MCP](https://docs.nvidia.com/nim/large-language-models/latest/advanced-use-cases/tool-calling-and-mcp.html),
  [model card](https://huggingface.co/nvidia/Llama-3_3-Nemotron-Super-49B-v1_5).

---

## Step 1a — Launch the chat NIM

Put your tool-call parser plugin (see [Step 1b](#step-1b--the-tool-call-parser-the-critical-part))
in a host dir, e.g. `<PLUGIN_DIR>/parser.py`, then:

```bash
export NGC_API_KEY=nvapi-...

docker run -d --name nim-nemotron-super-49b-tools \
  --gpus all --shm-size=16g \
  -p <CHAT_PORT>:8000 \
  -e NGC_API_KEY="$NGC_API_KEY" \
  -e NIM_ENABLE_AUTO_TOOL_CHOICE=1 \
  -e NIM_TOOL_CALL_PARSER=llama_nemotron_json \
  -e NIM_TOOL_PARSER_PLUGIN=/opt/nim/plugins/parser.py \
  -e NIM_PASSTHROUGH_ARGS="--enable-auto-tool-choice --tool-call-parser llama_nemotron_json --tool-parser-plugin /opt/nim/plugins/parser.py" \
  -v <NIM_CACHE_DIR>:/opt/nim/.cache \
  -v <PLUGIN_DIR>:/opt/nim/plugins:ro \
  nvcr.io/nim/nvidia/llama-3.3-nemotron-super-49b-v1.5:latest
```

Notes:
- **Ports are your choice.** Every NIM serves on `8000` *inside* the container; map it to
  any free host port. `<CHAT_PORT>` / `<EMBED_PORT>` throughout this doc are placeholders —
  pick whatever's free on your host and use the same values in the config ([Step 4](#step-4--wire-agents-on-vast-to-the-local-nims)).
- `NIM_ENABLE_AUTO_TOOL_CHOICE` + `NIM_TOOL_CALL_PARSER` + `NIM_TOOL_PARSER_PLUGIN` are
  the official function-calling env vars. **Recent NIM builds (observed on `2.0.10`) accept
  these vars but never translate them into vLLM's CLI flags**, so tool requests fail with
  HTTP 400 `"auto" tool choice requires --enable-auto-tool-choice ...` even though the vars
  are set. The `NIM_PASSTHROUGH_ARGS` line above forwards the flags straight to vLLM and is
  the reliable fix — keep it set. (Set **both**: older builds honour the `NIM_*` vars,
  newer ones need the passthrough. If a build ever honours both and vLLM complains about
  duplicate `--enable-auto-tool-choice` args, drop the `NIM_PASSTHROUGH_ARGS` line.)
- Don't pin `NIM_TENSOR_PARALLEL_SIZE` unless that profile is already cached — forcing
  an uncached profile triggers an NGC download that fails on blocked-egress hosts
  ([profiles](#gpu-profiles--the-cache)).
- Pin the GPU(s) with `--gpus '"device=6,7"'` if you don't want all of them.

---

## Step 1b — The tool-call parser (the critical part)

NVIDIA ships `llama_nemotron_toolcall_parser_no_streaming.py` for this model. As the
name says, it is **non-streaming only** — its `extract_tool_calls_streaming()` is a hard
stub:

```python
raise NotImplementedError("Tool calling is not supported in streaming mode!")
```

Because OpenClaw streams, every tool turn hits that stub and dies mid-stream. The HTTP
status is a misleading `200 OK` (headers flush before the generator errors), so the
turn "succeeds" at the transport layer while the SSE body carries the exception. Your
NIM logs will show that `NotImplementedError` repeating. A `stream:false` probe passes,
giving false confidence — always test streaming ([Step 3](#step-3--verify-both-non-stream-and-stream)).

### The valid parser

A ready-to-use, streaming-capable parser is committed in this repo at
[`deploy/nims/llama_nemotron_toolcall_parser_streaming.py`](nims/llama_nemotron_toolcall_parser_streaming.py).
Drop it on the GPU host and point the NIM at it:

```bash
scp deploy/nims/llama_nemotron_toolcall_parser_streaming.py \
    <user>@<NIM_HOST>:<PLUGIN_DIR>/parser.py
# on the host:
python3 -m py_compile <PLUGIN_DIR>/parser.py && echo "parser OK"
docker restart nim-nemotron-super-49b-tools     # vLLM loads the parser at startup
```

The rest of this section explains *what that file does*, so you can audit it or adapt it
to a different model/vLLM.

Nemotron emits its tool call as a single `<TOOLCALL>[ {...} ]</TOOLCALL>` block in one
shot (it does not stream arguments token-by-token). So a correct streaming parser only
needs to **buffer until the block is complete, then emit it once**, reusing the model's
existing non-streaming `extract_tool_calls()`. The committed file replaces the stub in
each parser class with this:

```python
def extract_tool_calls_streaming(
    self, previous_text, current_text, delta_text,
    previous_token_ids, current_token_ids, delta_token_ids, request,
):
    return _stream_buffered(self, previous_text, current_text, delta_text, request)
```

and add these two module-level helpers (they rely on each parser's
`tool_call_start_token` / `tool_call_end_token` and its `extract_tool_calls`, so the
same code works for the `llama_nemotron_json`, `_xml`, and `_pythonic` parsers):

```python
def _emit_tool_calls(self, current_text, request, prefix_content):
    parsed = self.extract_tool_calls(current_text, request)
    if not parsed.tools_called or not parsed.tool_calls:
        return DeltaMessage(content=prefix_content) if prefix_content else None
    deltas = []
    for i, tc in enumerate(parsed.tool_calls):
        deltas.append(DeltaToolCall(
            index=i,
            id=(tc.id or ("call_" + random_uuid())),
            type="function",
            function=DeltaFunctionCall(name=tc.function.name, arguments=tc.function.arguments),
        ))
    return DeltaMessage(content=(prefix_content or None), tool_calls=deltas)


def _stream_buffered(self, previous_text, current_text, delta_text, request):
    start = self.tool_call_start_token          # e.g. "<TOOLCALL>"
    end = self.tool_call_end_token              # e.g. "</TOOLCALL>"
    if start not in current_text:               # no tool call yet -> stream content
        return DeltaMessage(content=delta_text) if delta_text else None
    if start not in previous_text:              # block just started in this delta
        head = current_text[:current_text.find(start)]
        new_head = head[len(previous_text):] if head.startswith(previous_text) else ""
        if end not in current_text:
            return DeltaMessage(content=new_head) if new_head else None
        return _emit_tool_calls(self, current_text, request, new_head or None)
    if end not in current_text:                 # inside block, not finished -> hold
        return None
    if end not in previous_text:                # block just closed -> emit once
        return _emit_tool_calls(self, current_text, request, None)
    return None                                 # already emitted
```

Behavior:
1. Before `<TOOLCALL>` appears → pass content deltas through (so the model's
   `<think>…</think>` reasoning still streams).
2. After the marker starts, before `</TOOLCALL>` → withhold deltas.
3. When the block closes → parse once and emit a single `tool_calls` delta with
   id + name + full arguments; guard against re-emitting on later deltas.

> **Cosmetic note:** because the model emits `<TOOLCALL>` a few characters at a time, the
> partial marker can briefly leak into `content` before the tool call fires. It does not
> affect execution (OpenClaw keys off `tool_calls`). Suppress it later by buffering
> partial-marker prefixes if desired.

### vLLM version compatibility

The plugin's imports must match the vLLM shipped **inside the NIM image**. NIM `latest`
currently ships **vLLM 0.26.0**, whose module layout is:

```python
from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
from vllm.entrypoints.openai.engine.protocol import (
    DeltaFunctionCall, DeltaMessage, DeltaToolCall,
    ExtractedToolCallInformation, FunctionCall, ToolCall,
)
from vllm.tool_parsers.abstract_tool_parser import ToolParser, ToolParserManager
from vllm.tokenizers import TokenizerLike as AnyTokenizer
```

Two gotchas if you bump the NIM image and the container crashes on plugin load with
`ModuleNotFoundError`:
- Older NIMs put these under `vllm.entrypoints.openai.protocol` and
  `vllm.entrypoints.openai.tool_parsers.abstract_tool_parser`.
- `random_uuid` was removed from `vllm.utils` — define it locally: `random_uuid = lambda: uuid4().hex`.
- The parser `__init__` must accept extra args: `def __init__(self, tokenizer, *args, **kwargs)` —
  a bare `(self, tokenizer)` fails to load on newer vLLM.

After editing the plugin, `python3 -m py_compile <PLUGIN_DIR>/parser.py`, then
`docker restart` the NIM (vLLM loads the parser at startup).

---

## Step 2 — Embeddings NIM

```bash
docker run -d --name nv-embedqa-e5-v5 \
  --gpus device=<N> --shm-size=8g \
  -p <EMBED_PORT>:8000 \
  -e NGC_API_KEY="$NGC_API_KEY" \
  -v <NIM_CACHE_DIR>:/opt/nim/.cache \
  nvcr.io/nim/nvidia/nv-embedqa-e5-v5:latest
```

> **Do not** swap `embed_model` / `embed_dim` unless you re-ingest the corpus —
> `embed_dim` is the VastDB vector column width. `nv-embedqa-e5-v5` = dim **1024**.

---

## Step 3 — Verify (both non-stream AND stream)

Non-streaming is not enough — OpenClaw streams, so **test streaming too**.

**Non-streaming (baseline):**

```bash
curl -sS http://<NIM_HOST>:<CHAT_PORT>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-3.3-nemotron-super-49b-v1.5","stream":false,"tool_choice":"auto",
       "messages":[{"role":"user","content":"Weather in Tel Aviv? Call the tool."}],
       "tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
       "max_tokens":128}' | jq '.choices[0].message.tool_calls'
```

**Streaming (the one that matters):**

```bash
curl -sS -N http://<NIM_HOST>:<CHAT_PORT>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-3.3-nemotron-super-49b-v1.5","stream":true,"tool_choice":"auto",
       "messages":[{"role":"user","content":"Weather in Paris? Call the tool."}],
       "tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
       "max_tokens":128}'
```

**PASS** = the SSE stream contains a `"tool_calls"` delta with `get_weather` +
`{"city":"Paris"}` and ends with `"finish_reason":"tool_calls"`, and `docker logs` shows
**no** `NotImplementedError`.

---

## Step 4 — Wire agents on VAST to the local NIMs

Endpoints live in **one place** — `deploy/config/workflow-ui.config.yaml` — and are
rendered into the sandbox egress allow-list automatically. Set the `model:` block:

```yaml
model:
  enabled: true
  base_url: "http://<NIM_HOST>:<CHAT_PORT>/v1"      # chat NIM (tools ON)
  api_key: ""
  allow_anonymous: true                      # empty key must not read as "disabled"
  model: "nvidia/llama-3.3-nemotron-super-49b-v1.5"
  embed_base_url: "http://<NIM_HOST>:<EMBED_PORT>/v1"
  embed_api_key: ""
  embed_model: "nvidia/nv-embedqa-e5-v5"
  embed_dim: 1024
```

Key points:
- Use the **host IP**, not the hostname — sandbox pods reach it over cluster networking.
- `allow_anonymous: true` + empty `api_key` is required for keyless NIMs (otherwise an
  empty key is treated as "no auth configured").
- Then apply + deploy. The nemoclaw apply renders `<NIM_HOST>:<CHAT_PORT>` and `:<EMBED_PORT>` into
  every department's `sandbox-policy.yaml` `nvidia_api` allow-list — **do not** hand-edit
  the policies:

```bash
./deploy/nemoclaw/vast-claw-nemoclaw.sh     # render egress allow-list from config
./deploy/vast-claw-deploy.sh                # recreate secret + restart backend
```

Start a **fresh** workflow — already-live sandboxes hold the previously rendered config.

---

## Troubleshooting — every gap to expect

| Symptom | Root cause | Fix |
| --- | --- | --- |
| `NotImplementedError: Tool calling is not supported in streaming mode!` (repeats; HTTP 200 then stream dies) | Stock `no_streaming` parser loaded; OpenClaw streams | Use `deploy/nims/llama_nemotron_toolcall_parser_streaming.py` ([Step 1b](#step-1b--the-tool-call-parser-the-critical-part)) |
| Container won't load plugin: `ModuleNotFoundError` on vLLM imports | Parser written for a different vLLM than the NIM ships | Match import paths / `__init__` signature to the image's vLLM ([vLLM compat](#vllm-version-compatibility)) |
| `HTTP 400: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` (even though the `NIM_*` vars are set) | The NIM launcher (2.0.10) doesn't translate the `NIM_*` function-calling vars into vLLM CLI flags | Set `NIM_PASSTHROUGH_ARGS="--enable-auto-tool-choice --tool-call-parser llama_nemotron_json --tool-parser-plugin /opt/nim/plugins/parser.py"` to forward the flags to vLLM directly ([Step 1a](#step-1a--launch-the-chat-nim)) |
| `401 status code (no body)` from the model call | Talking to **hosted** NGC without a valid key | Self-hosted: `api_key: ""` + `allow_anonymous: true`. Hosted: real `nvapi-...` key |
| `ProviderAuthError: No API key found for provider "nvidia"` | Empty key read as "disabled" | Set `allow_anonymous: true` (the app injects a placeholder auth token) |
| `502 Bad Gateway` from a probe during startup | vLLM backend still initializing behind nginx | Normal — wait; clears once ready |
| `ERROR ... Download failed ... ConnectionError (xfiles.ngc.nvidia.com)` | Forced a profile whose blobs aren't cached, on a blocked-egress host | Don't pin `NIM_TENSOR_PARALLEL_SIZE`/profile unless cached; warm cache + `NIM_OFFLINE=1` ([profiles](#gpu-profiles--the-cache)) |
| Container exits immediately on start | Profile not cached / not enough VRAM | Use the cached tensor-parallel profile (the 49B commonly runs TP across 2 GPUs) |
| Probe passes but OpenClaw still fails on tools | You only tested `stream:false` | Run the **streaming** probe ([Step 3](#step-3--verify-both-non-stream-and-stream)) |

---

## GPU profiles & the cache

- NIM picks a **model profile** matching the GPUs it sees. The 49B typically runs
  tensor-parallel across 2 cards; a single-GPU (TP1) profile may not be in your cache.
- Forcing `NIM_TENSOR_PARALLEL_SIZE` can select an **uncached** profile → NIM tries to
  download from NGC → fails on blocked-egress hosts. Leave it unset to auto-pick.
- Once weights are cached, run with `NIM_OFFLINE=1` so it never reaches the NGC CDN, and
  optionally pin `NIM_MODEL_PROFILE=<id>` (list with the NIM's `list-model-profiles`).

---

## Hosted NGC alternative (when you don't need self-hosting)

If data-locality isn't required, hosted NGC handles streaming tool calls server-side —
no plugin, no GPUs:

```yaml
model:
  base_url: "https://integrate.api.nvidia.com/v1"
  api_key: "nvapi-..."          # real key, via the mounted secret; never commit it
  allow_anonymous: false
  embed_base_url: "https://integrate.api.nvidia.com/v1"
```

Everything else (nemoclaw apply, deploy) is identical — the egress allow-list re-renders
to `integrate.api.nvidia.com:443` from the config.
