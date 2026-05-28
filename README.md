# Assignment 2

Jag har delat upp uppgiften i tre delar, på samma sätt som instruktionen gör. Delarna bygger på varandra, men jag lämnar in dem separat.

Part 1 är den enklaste agenten. Den använder en egen ReAct-loop och låter modellen skriva vanliga textrader som `THOUGHT`, `ACTION`, `COMMAND` och `FINAL`. Min kod parsar texten själv och kör bash-kommandon efter en säkerhetskontroll.

Part 2 använder OpenAI:s vanliga tool-calling med JSON-schema. Det är fortfarande min egen loop runt modellen, men modellen får strukturerade verktyg: bash, file_read, file_create och file_edit. Den sparar även sessionen i jsonl.

Part 3 kopplar agenten till Hell's Agents Hub. Där behövde jag lägga till saker som inte spelar så stor roll lokalt: PASS-logik, cooldown, rate-limit, token-budget, samarbetsläge och regler för att inte läcka känslig information i gruppchatten.

## Köra från roten

Skapa miljö och installera beroenden:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fyll i `OPENAI_API_KEY` i `.env`.

Kör delarna:

```bash
python3 -m part1.react_agent
python3 -m part2.agent
python3 -m part3.chat_agent
```

## Inlämning

Jag skulle inte skicka med `.env`, `agent_workspace/`, cache-filer eller gamla sessioner om de inte behövs för en demo.

För Part 1 räcker det med:

```text
part1/
common/llm_client.py
common/bash_tool.py
common/safety.py
requirements.txt
.env.example
```

För Part 2:

```text
part2/
common/llm_client.py
common/bash_tool.py
common/safety.py
common/file_edit_tool.py
common/tools_schema.py
common/structured_loop.py
requirements.txt
.env.example
```

För Part 3:

```text
part3/
common/
requirements.txt
.env.example
```

Part 3 använder fler gemensamma moduler, så där är det enklast att skicka med hela `common/`.

## Snabb kontroll

De här testerna kräver inte OpenAI-anrop:

```bash
python3 -m common._smoke_test
PYTHONPATH=. python3 -m part3.test_collaboration
PYTHONPATH=. python3 -m part3.test_hub_client
```

Jag har använt fler små tester under utvecklingen, men de här räcker som en snabb sanity check om något flyttas innan inlämning.
