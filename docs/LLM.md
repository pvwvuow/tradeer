# Ask AI from the app (optional)

Spec C13. The AI Lab can send its review straight to an AI, using **your own** API key and
any OpenAI-compatible endpoint (OpenAI, OpenRouter, Azure-compatible gateways, or a local
server such as Ollama or LM Studio). It is **off by default**; the manual export in step 1
works without it.

## Turning it on

AI Lab, card "Or ask your own AI from here":

1. Tick **Use my AI endpoint**, check the endpoint (default `https://api.openai.com/v1`)
   and the model (default `gpt-4o-mini`).
2. Paste the **API key**. It is saved in Windows Credential Manager under the profile, never
   in a file, a log or a crash report. **Remove key** deletes it.
3. Optional: the prices in USD per one million input and output tokens, so each request
   logs its estimated cost.
4. **Save**. The change is written to `llm.json` in the profile folder and to the audit log.

Only `https://` endpoints are accepted, plus plain `http://` for a server on this PC
(`localhost`, `127.0.0.1`), which needs no key. A URL with a user name, password, query or
fragment is refused.

## What is sent

One request per click on **Ask AI**, with the scope chosen in step 1 (strategy, mode, days):

- the statistics, the largest groups of each breakdown, the probability calibration,
  MFE/MAE, costs, the top reasons for rejected signals and the recent backtests;
- each strategy's current parameters and their limits;
- your question (or a default one).

Never sent: passwords, the API key (it is only in the `Authorization` header), the trade
list, and the account number unless you tick **Send the account number**. The summary is
capped at 12,000 characters and goes through the same secret masker as the logs.

## What happens with the answer

It is advice only. The answer is put into step 2 and checked like a pasted one: every value
against the strategy's schema. Nothing changes until you run the backtest comparison (step 3)
and activate it, which works only in Paper or Analysis-only mode (step 4). Real orders are
never sent from the AI Lab.

Every request logs (category `llm`) the model, the summary size, the tokens in and out and
the estimated cost. Errors are shown on the card and logged without the key.
