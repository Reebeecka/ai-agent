# Part 1 - ReAct bash agent

Det här är första delen av uppgiften. Jag har gjort en ganska enkel ReAct-agent som bara har ett verktyg: bash. Poängen här är inte att den ska vara smartast möjligt, utan att loopen och verktygsanropen är skrivna själv.

Agenten får en uppgift, ber modellen svara i textformatet `THOUGHT`, `ACTION`, `COMMAND` eller `FINAL`, och parsar sedan svaret själv med regex. Om modellen skriver ett kommando körs det, resultatet läggs tillbaka som `OBSERVATION`, och loopen fortsätter. Den stoppar när modellen skriver `FINAL` eller när max antal rundor är nått.

## Köra

Kör från `assignment2`:

```bash
python3 -m part1.react_agent
```

Eller med en uppgift direkt:

```bash
python3 -m part1.react_agent --task "lista filerna i mappen"
```

För demo utan y/n-fråga:

```bash
python3 -m part1.react_agent --auto-yes --task "visa pwd och lista filer"
```

## Viktiga delar

- `react_agent.py` innehåller själva loopen.
- `prompts/system.j2` säger till modellen vilket textformat den ska använda.
- `common/bash_tool.py` kör bash-kommandon.
- `common/safety.py` stoppar farliga kommandon innan de körs.

Jag använder alltså inte OpenAI:s inbyggda tools eller något agent-ramverk här. Modellen skriver vanlig text och min kod letar efter `COMMAND:` själv.

## Säkerhet

Innan bash körs går kommandot genom `check_and_confirm`. Där finns både en deny-list och en allow-list. Till exempel blockas `rm -rf /`, `sudo` och `curl ... | sh`. Efter det kommer en lokal y/n-fråga om man inte kör med `--auto-yes`.

Det är den delen som gör att säkerheten inte bara ligger i prompten. Kommandot måste faktiskt bli godkänt i kod innan `subprocess.run` används.

## Filer i den här delen

```text
part1/
  react_agent.py
  prompts/system.j2
  README.md
```
