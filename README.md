# Legal AI Search demo

Django application for searching legal knowledge. Without KMS credentials, it
uses a small, clearly labelled local demo dataset; it does not represent a
production legal knowledge base.

## Deploy on Vercel

The repository is connected to Vercel, so pushing a commit to `main` starts a
new deployment. The production deployment also needs these environment
variables in Vercel Project Settings → Environment Variables:

| Variable | Required | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Yes | Generate a new random secret; keep it private. |
| `DJANGO_DEBUG` | Yes | Set to `false`. |
| `DJANGO_ALLOWED_HOSTS` | Yes | Use `.vercel.app` to allow Vercel project and preview hostnames. |
| `GEMINI_API_KEY` | Optional | Enables Gemini and model discovery. |
| `GROQ_API_KEY` | Optional | Enables Groq and model discovery. |
| `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY` | Optional | Each enables that provider and model discovery. |
| `OPENAI_API_KEY` | Optional | Enables OpenAI and model discovery. |
| `ANTHROPIC_API_KEY` | Optional | Enables Claude and model discovery. |
| `<NAME>_API_KEYS` | Optional | Comma-separated backup keys for a provider, e.g. `GEMINI_API_KEYS`. |
| `AI_MODEL_CHAIN` | Optional | Models to try in order, e.g. `gemini:model-id,groq:model-id`. |
| `KMS_API_BASE_URL` | Optional | Enables searches against the connected KMS. |
| `KMS_API_TOKEN` | Optional | Token for the KMS API. |

Add provider keys to Vercel itself, not to the repository or browser code. After
changing an environment variable, redeploy the project so the running app picks
it up. A provider's model list is fetched only when its key is available. Without a provider key, the app can still search the
local demo dataset using its built-in query analysis fallback.

## Run locally

```sh
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py runserver
```

Put local credentials in `.env`; `.env` is ignored by Git.
