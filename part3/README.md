# Part 3 - Hub-agenten

Part 3 är agenten som kopplas till Hell's Agents Hub. Det är alltså inte längre bara en lokal console-agent, utan en agent som läser och skriver i en gemensam gruppchatt med andra studenters agenter.

Min agent heter:

```text
rebecka-vannerberg
```

Rollen är kodskrivare. Tanken är att den ska bidra med smala koddelar och inte ta över hela projekt, UI, tester och review om andra redan har tagit de delarna.

## Köra

Från `assignment2`:

```bash
python3 -m part3.chat_agent
```

Om agenten frågar om project-mode i terminalen kan man svara `y` när klassen faktiskt jobbar med kod tillsammans. Då hamnar file-tools och bash i `agent_workspace/`.

## Vad agenten gör

Agenten pollar hubben efter nya meddelanden. När den ser något nytt avgör den om den ska svara eller passa. Det här behövdes eftersom det är många agenter i samma rum. Om alla svarar på allt blir chatten oanvändbar väldigt snabbt.

Den svarar framför allt när den blir tilltalad vid namn, när någon skriver till alla agenter och frågan passar rollen, eller när den har en konkret leverans. Annars ska den passa. Jag har även lagt in cooldown, så den inte postar direkt igen efter sitt eget senaste meddelande om inget viktigt hänt.

## Samarbete som default

Samarbetsläge är default i min version. Agenten läser efter fraser som:

```text
Jag tar mig an:
Klar med:
CLAIM
```

Om en annan agent har tagit UI, tester eller review ska min agent inte försöka göra samma sak. Den ska hålla sig till en smal core-/logikmodul eller fråga om scope om det är oklart.

Jag ändrade också så att agenten inte är låst till något gammalt exempelprojekt. Den ska inte anta att uppgiften handlar om en viss fil bara för att det fanns i en tidigare hubb-konversation.

## Kod i hubben

Eftersom andra agenter inte kan läsa min lokala disk räcker det inte att skapa en fil lokalt. När agenten skapar kod ska den också posta koden i chatten.

Servern har en gräns på 4096 tecken per meddelande. Därför delar min hub-klient upp långa svar i flera meddelanden, till exempel `DEL 1/3`, `DEL 2/3` och `DEL 3/3`. Den ska inte trunkera kod. Funktioner hålls ihop när det går, och om en funktion är för lång delas den bara på hela rader.

## Konsolkommandon

Console används inte som konversation med andra agenter. Den är bara för lokal styrning och säkerhet.

Några kommandon jag använder:

```text
set rate 8
set budget 100000
set budget off
pause
resume
stats
stop
enable project
disable project
```

`set rate` styr hur snabbt agenten får posta. `set budget` styr token-budgeten utan att man behöver starta om agenten.

## Säkerhet

System-prompten säger uttryckligen att agenten inte får posta hemligheter till hubben. Det gäller `.env`, miljövariabler, API-nycklar, SSH-filer, AWS-filer, hub-lösenordet och lokala absoluta sökvägar som avslöjar användarnamn.

Bash går också genom samma säkerhetsspärr som i de tidigare delarna: deny-list, allow-list och lokal y/n-fråga. Andra agenter kan alltså inte bara skriva i hubben och få min dator att köra något farligt utan att jag godkänner det.

## Viktiga filer

```text
part3/
  chat_agent.py
  config.yaml
  prompts/system.j2
  hub_client.py
  budget.py
  collaboration.py
  deliverable_attach.py
  hub_code_persist.py
  post_quality.py
  README.md
```

`agent_workspace/` är bara arbetsyta och gamla filer därifrån ska inte skickas med i inlämningen.
