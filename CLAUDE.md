# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

A playground for exercising LLM SDKs against multiple providers. Two tracks live here:

1. **`LiteLLM_example.ipynb`** — Google Colab notebook hitting [LiteLLM](https://github.com/BerriAI/litellm)'s `completion()` API against OpenAI, Azure AI Foundry, Azure Anthropic, Anthropic direct, Moonshot, and OpenRouter. Each cell is a standalone smoke test.
2. **OpenHands SDK Python samples** — local `.py` scripts exercising the [OpenHands](https://github.com/All-Hands-AI/OpenHands) SDK. (Being added; layout will solidify as samples land.)

The two tracks have different execution models — see below before editing either.

## LiteLLM notebook conventions

All cells in `LiteLLM_example.ipynb` follow the same shape; preserve it when adding new providers:

```python
from litellm import completion
from google.colab import userdata

response = completion(
    api_base = "<provider base URL>",
    api_key  = userdata.get('<SECRET_NAME>'),
    model    = "<litellm-prefix>/<model-id>",
    messages = [{"content": "Hello, how are you?", "role": "user"}]
)
print("<Provider> Response\n")
print(response)
```

- **Secrets** are read via `google.colab.userdata.get(...)` (Colab's Secrets panel) — never hardcode keys, and don't switch to `os.environ` / `.env` (this runs in Colab, not locally).
- **Model strings must carry the LiteLLM provider prefix** (`anthropic/...`, `azure/...`, `azure_ai/...`, `moonshot/...`, `openrouter/...`). The prefix is what routes the call; changing it changes the transport, not just the label. See the LiteLLM provider docs linked from each section header before editing.
- **Azure has two distinct prefixes in use** here: `azure/...` for Azure OpenAI-style deployments and `azure_ai/...` for Azure AI Foundry Anthropic deployments. They are not interchangeable.
- Keep each provider isolated in its own cell with a markdown header linking to the relevant LiteLLM provider doc — that's the navigation model for the notebook.

## Working with the LiteLLM notebook

- Edits are typically made on Colab and committed back via the "Open in Colab" → Save a copy to GitHub flow (recent commits are auto-generated `Created using Colab`). Local edits to the `.ipynb` are fine but expect Colab to rewrite cell metadata on next save.
- Do not run the notebook locally to validate changes — the `google.colab` import will fail outside Colab. Validate by reading the cell structure and confirming the LiteLLM call signature matches the linked provider doc.

## OpenHands SDK samples

- Run **locally**, not in Colab — assume a normal Python env with secrets from `os.environ` / `.env`, not `google.colab.userdata`. Don't copy the Colab secret pattern across.
- Keep each sample self-contained and runnable on its own, mirroring the notebook's "one cell, one provider" ethos.
- **Dependency management is uv**, not pip / poetry / conda. The repo's `pyproject.toml` is the single source of truth; deps live under the project (in `.venv/`), not globally.
  - Add a dep: `uv add <package>` (writes to `pyproject.toml` + `uv.lock`).
  - Run a sample: `uv run python path/to/sample.py` — uv resolves + syncs the env on demand, no manual `activate` needed.
  - Don't `pip install` into the project venv; it bypasses the lockfile.
- Python floor is `>=3.12` (set in `pyproject.toml`). Bump it deliberately if an SDK requires newer — don't silently relax it.
- This section will grow once the first samples land; update it with the actual layout and run commands at that point rather than guessing now.
