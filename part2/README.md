# Part 2 - Starkare SWE-agent

I Part 2 byggde jag vidare på första agenten. Skillnaden är att den här delen inte längre parsar `COMMAND:` ur rå text. Här använder jag OpenAI:s vanliga tool-calling med JSON-schema för verktygen, men själva loopen och dispatchen är fortfarande min egen kod.

Jag ser den här delen som en lokal SWE-agent. Den kan köra bash, läsa filer, skapa filer och göra punktändringar i filer. Den sparar också sessionen i en jsonl-fil så att historiken finns kvar under samma session.

## Köra

Från `assignment2`:

```bash
python3 -m part2.agent
```

En enskild uppgift:

```bash
python3 -m part2.agent --task "Skapa hello.py som skriver ut hej och kör den"
```

Fortsätta en session:

```bash
python3 -m part2.agent --session session_123
```

Om man vill köra en demo utan y/n-frågor:

```bash
python3 -m part2.agent --auto-yes --task "Lista filerna i mappen"
```

## Hur den fungerar

`agent.py` laddar config, renderar system-prompten och håller koll på sessionen. När användaren skriver något skickas historiken till `run_structured_loop`. Den loopen anropar modellen med verktygen från `common/tools_schema.py`.

Om modellen returnerar `tool_calls` kör min kod verktygen och lägger tillbaka resultatet i historiken. Om modellen svarar utan `tool_calls` räknas det som slutsvaret. Det finns en gräns i `max_tool_rounds` så att agenten inte kan loopa för evigt.

## Verktygen

- `bash` kör kommandon via samma säkerhetsspärr som i Part 1.
- `file_read` läser filer med output-limit.
- `file_create` skapar nya filer.
- `file_edit` gör en exakt find/replace i en befintlig fil.

`file_edit` är medvetet ganska strikt. `find`-strängen måste finnas exakt en gång i filen, annars får modellen ett fel tillbaka. Jag valde det för att inte råka ersätta flera ställen i en fil av misstag.

## System-prompt och SWE-scope

Prompten ligger inte hårdkodad i Python-filen. Den laddas från:

```text
part2/prompts/system.j2
```

Sökvägen kommer från `part2/config.yaml`. I prompten står också att agenten bara ska hjälpa med software engineering. Om man frågar något helt annat, till exempel en allmänkunskapsfråga, ska den avböja och styra tillbaka till kod, filer, debug, tester eller liknande.

Ett enkelt test:

```bash
python3 -m part2.agent --task "Vad är huvudstaden i Frankrike?"
```

Då ska den inte börja svara på frågan, utan säga att den håller sig till SWE.

## Output-limit

Bash-output och filinnehåll begränsas till 4000 tecken. Gränsen finns både i verktygskoden och i system-prompten, så modellen får veta att långa outputs kan bli kapade och att den behöver läsa i mindre bitar.

## Filer i den här delen

```text
part2/
  agent.py
  config.yaml
  prompts/system.j2
  README.md
  sessions/
```

`sessions/` är bara körhistorik. Jag skulle inte skicka med gamla sessioner om jag inte vill visa en specifik demo.
