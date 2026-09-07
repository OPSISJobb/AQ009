# AQ009 - hjulstyrning med 9 positioner

Ersätter den gamla CU007-lådan. Styr ett hjul (som en revolvercylinder) med
9 fasta positioner via en stegmotordriver (STEP + DIR, ingen ENABLE) på
Raspberry Pi:ns GPIO plus en hemlägessensor.

Två gränssnitt mot samma motor:

- **RS232** - kommandon från styrsystemet ("AR"). Det är detta som styr i drift.
- **Webbsida** - manuell körning, hemkörning och kalibrering på touchskärmen.

## Hårdvara / koppling

- **Motor**: Smooth (Ningbo Smooth Electric) 23HD604Y-T20, FH171027 -
  NEMA23, 23HD-serien = 1,8°/steg (200 fullsteg/varv)
- **STEP** → GPIO12 (BCM), till drivkortets pulsingång
- **DIR** → GPIO16 (BCM), till drivkortets riktningsingång
- **Hemlägessensor** → GPIO26 (BCM), som ingång
- **RS232** → `/dev/ttySC0` (Waveshare 2-CH RS232 HAT). En USB-RS232-adapter
  blir i stället oftast `/dev/ttyUSB0`.

Drivern har ingen ENABLE-ingång, bara STEP och DIR. Programmet använder ändå
bara framåtriktningen för "gå till position" och hemkörning (tar alltid vägen
framåt runt hjulet, aldrig genvägen bakåt) - hemkörningens precision bygger på
att alltid närma sig sensorn steg för steg från samma håll. DIR används
däremot för jogg-knapparna i kalibreringsläget. Går bakåt-jogg åt fel håll:
vänd `DIR_INVERT` till `True` i `config.py`.

Allt (pinnar, aktiv nivå på sensorn, steg/varv, seriellport, hastigheter)
ställs in i **`config.py`**. Koden behöver inte ändras på fler ställen.

`STEPS_PER_REV` är totalt antal pulser per varv som drivern förväntar sig
(fullsteg × mikrosteg inställt på drivern). Uppmätt till 400 här (200 × 2).
Är du osäker: sätt ett rimligt värde, kör en hemkörning och kalibrera sedan
positionerna via webbsidan - varje positions verkliga stegvärde sparas
separat, så det spelar mindre roll om `STEPS_PER_REV` inte stämmer exakt.

## Installation

Beroendena installeras systemvitt med apt - ingen venv används:

```bash
sudo apt install python3-flask python3-flask-socketio \
                 python3-simple-websocket python3-serial python3-lgpio
```

`python3-serial` och `python3-lgpio` följer oftast redan med Raspberry Pi OS.
`requirements.txt` listar samma beroenden för den som hellre installerar med
pip (`pip install --break-system-packages -r requirements.txt`).

RS232-HAT:en kräver att SPI + `sc16is75x-spi`-overlayet är aktiverat i
`/boot/firmware/config.txt` för att `/dev/ttySC0` ska finnas.

## Köra

```bash
cd AQ009
python3 app.py
```

Öppna `http://localhost:8080/` på touchskärmen (eller `http://<pi-ip>:8080/`
från en annan dator på samma nät).

**Riktig hårdvara krävs** - det finns ingen simulerad motor. Kan `lgpio` inte
initiera GPIO avbryts starten med ett fel istället för att köra vidare.

Ingen hemkörning sker vid uppstart - hjulet står kvar tills AR skickat en av
följderna nedan eller någon tryckt Hemkörning på webbsidan. Tills dess är
läget okänt och positionskommandon avvisas med "Ej hemkörd".

## Webbsidan

- **Hemkörning**: kör framåt steg för steg tills hemsensorn slår till (samma
  riktning och upplösning varje gång) och nollställer stegräknaren. Måste
  göras efter varje omstart/strömavbrott innan positionerna går att välja.
  Kan ta upp till ett varv, och går långsamt eftersom sökningen inte kan backa.
- **1-9**: kör hjulet till respektive position, alltid framåt.
- **NÖDSTOPP**: stoppar motorn omedelbart. Efter nödstopp är läget okänt igen -
  kör hemkörning på nytt.
- **Kalibrera**: jogg-knappar (±1/±10/±100 steg) för att flytta hjulet till
  exakt rätt fysiskt läge, och en "Spara N"-knapp per position. Sparas till
  `data/calibration.json` och läses in vid nästa start.

## RS232-protokoll

Det verkliga protokollet som AR pratar med den enhet AQ009 ersätter -
återskapat ur sniffad AR↔CU007-trafik, inte påhittat. En textrad per kommando,
avslutad med `\r` (**inte** `\n`), 9600 8N1. Hjulets 9 positioner motsvarar
AR:s "cylindrar".

**Första siffran är enhetsadress.** Gamla CU007-lådan var bara enhet 0 och teg
på allt till enhet 1-3; AR gjorde då 3 försök med 2 s mellanrum per kommando
innan den gick vidare. AQ009 ersätter hela kedjan och svarar som samtliga
enheter.

| AR skickar | Betydelse | AQ009 svarar |
|---|---|---|
| `00`, `10`, `20`, `30` | Ping till enhet 0-3 (AR öppnar sin setup-meny) | `001.0`, `101.0`, ... |
| `01<C>1` | Öppna cylinder `<C>` (1-9) → `goto_position(<C>)` | `01<C>11.0` |
| `01<C>0` | Stäng cylinder `<C>` - ingen rörelse, hjulet har bara en aktiv position åt gången | `01<C>01.0` |
| `11<N>1` / `11<N>0` | Cylinder 8-9, `N` = cylinder − 7. AR bryter `01`-mönstret just för dessa två | `11<N>11.0` / `11<N>01.0` |
| `<E>101` | Växla till kalibreringsläge på enhet `<E>` (cylinder 0 finns inte fysiskt) | `<E>1011.0` |
| `<E>100` | Växla tillbaka till mätningsläge | `<E>1001.0` |

AR skickar alltid stäng på föregående cylinder innan den skickar öppna på
nästa, och varje kommando 3 gånger i rad (dess egen bekräftelselogik) - varje
rad besvaras för sig.

### Hemkörning från AR

Två kompletta följder kör hem hjulet:

| Följd | Betydelse |
|---|---|
| `00`, `10`, `20`, `30` | AR går in i sin setup-meny |
| `0101`, `1101`, `2101`, `3101` | AR går över till kalibreringsläge |

Följden måste komma i rätt ordning, men AR:s omförsök (samma kod igen var 2:e
sekund) bryter den inte, och inte heller cylinderkommandon däremellan - se
`Foljd` i `serial_listener.py`. `<E>100` (tillbaka till mätningsläge) kör
*inte* hem.

### Kvittensformat

Två varianter finns sedda i verklig trafik och båda går att välja i
`config.py`:

| `SERIAL_ACK_FORMAT` | `SERIAL_ACK_SUFFIX` | `0110` kvitteras |
|---|---|---|
| `"eko"` (standard) | `"1.0"` (standard) | `01101.0` - hela kommandot ekas |
| `"00"` | `"2.0"` | `00102.0` - avsändarfältet byts till `00` |

Standard är `eko` + `1.0`: det är exakt vad den riktiga CU007-lådan skickade
i sniffad trafik. Den använde aldrig `2.0`.

Skickar din motpart ett annat format: justera `parse_command()` i
`serial_listener.py` - resten av systemet berörs inte.

## Filer

| Fil | Roll |
|---|---|
| `app.py` | Startpunkt: webbserver + startar RS232-lyssnaren |
| `config.py` | All konfiguration - pinnar, mekanik, hastigheter, RS232 |
| `stepper.py` | Motorstyrning: hemkörning, gå till position, jogg, nödstopp |
| `calibration.py` | Läser/sparar `data/calibration.json` |
| `serial_listener.py` | RS232-protokollet mot AR |
| `templates/`, `static/` | Webbsidan |

Repot innehåller bara det som behövs för drift. Utvecklings- och
analysverktygen (linjelyssnare, signalregister med betydelseuppslag, den
sniffade CU007-loggen och motortesterna) ingår inte - de behövs bara vid
protokollanalys och bringup, inte för att köra hjulet.
