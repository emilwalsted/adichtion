# Beslutninger

Daterede beslutninger og afgjorte punkter. Nyeste øverst.

## 2026-09-27 (aften) — Map-grammatikken, float32 og video

- **Map-streamen parses efter sin grammatik, ikke ved scanning.** `chartdata.py`
  læser record → bloklist → kanalindgange (enhedsobjekt, v29-kanalopsætning,
  blokindeks med offset og min/max) sekventielt og opløser objektreferencer.
  Den gamle scanning er bevaret som fallback (`ChartData.parse_mode == "scan"`),
  så en fil med en ukendt variant stadig kan læses; UI'et skal vise det som en
  advarsel, for i scan-tilstand kendes hverken LSB eller blokindeks.
- **Enheder er pr. record.** Hver record bærer (eller refererer) et
  enhedsobjekt pr. kanal; `Record.read` bruger sin egen records objekt. Der
  gættes ikke længere på en dokumentbred kobling, og `AdichtFile.units_for`
  tager et record-argument (default 0). Konverteringen er slået til, når bit
  16 i objektets flag er sat; kalibreringsstrenge uden flag er gamle rester og
  ignoreres.
- **Alle kanaler skaleres med filens LSB**, også beregnede (float32) kanaler:
  `værdi = slope × (LSB × lagret) + offset` (til) eller `LSB × lagret` (fra).
  Det gør `set_range_volts`/`--range-volts` til en nødløsning for
  scan-tilstand, ikke en normal vej. Verificeret sample-eksakt mod LabCharts
  MATLAB-eksport (rå EMG, Pdi, RMS).
- **float32-dekoderen er rettet og verificeret**: samplerne ligger i omvendt
  rækkefølge inden for hvert 32-bit-ord, første sample gemmes absolut, og
  summen skal køres i float32 (`numpy.cumsum(dtype=float32)`). Den tidligere
  "re-base ved sample 31" var et artefakt af den forkerte rækkefølge og er
  fjernet. Verifikationsstatus for float32 er nu **sample-eksakt** (to
  MATLAB-eksporter) og ikke længere "kun intern konsistens".
- **PackBits-kode 128 er en kørsel på 129**, ikke en no-op (Apples TN1023).
  Fejlen ramte 10 af ca. 4000 int16-blokke i én fil; testen mod filens egne
  min/max pr. blok fanger den slags fremover (`adichtion check`).
- **Upakkede sub-blokke bærer ikke 32-bit-flaget**; datatypen tages fra
  kanalindgangen. Uden rettelsen blev upakkede float32-blokke læst som int16.
- **Blokindekset fra Map-streamen er den primære vej til sub-blokkene**
  (`_subblocks_from_map`); gennemløbet af `Blocks_0` bruges kun som fallback og
  til kontrol (`walk_subblocks`, integrationstest). Min/max pr. blok
  eksponeres som `Record.block_envelope`, og `Record.overview` bruger dem, når
  opløsningen tillader det, så et helt records oversigtsplot koster ingen
  dekodning.
- **Video**: LabCharts videomodul gemmer én fil pr. record i
  `<dokumentnavn> movies/Record<N>.wmv` og et lille stream i dokumentet
  (genkendt på sin GUID) med start/stop/offset pr. record. `AdichtFile.videos`,
  `Record.video` og `Record.video_file` eksponerer det; `offset_s` er LabCharts
  eget tal for records starttid i videofilen. Om 0,17 s-konstanten i offsettet
  er en kameraforsinkelse, afgøres først, når en video afspilles synkront i
  UI'et; UI-spec'en skal give brugeren mulighed for at justere offsettet.
- **Beregnede kanalers definitioner findes ikke i v26-filerne** (hele
  Map-streamen er nu forklaret, og der er ingen andre streams). v26-filerne
  bærer kun resultatet (float32-data) plus enhedsobjekt og titel; det er
  formentlig sådan LabChart for Mac skriver "Windows-format". Punktet er
  lukket for v26; for nyere filer med beregnede kanaler (v29+) skal det
  undersøges, om `Settings/ChannelSettings` bærer definitionerne, når Emil har
  en sådan fil.
- **Terminologi i UI'et**: filens "Record<N>.wmv" viser, at ADI selv kalder
  LabCharts blokke *records* i filsystemet; det taler for at bruge "record" i
  UI'et og nævne "block" som LabCharts ord.

## 2026-09-27 — grundlaget

- **Projektet er et bibliotek først, en app bagefter.** `src/adichtion/` er
  læselaget (container → Map-stream → blokke → `AdichtFile`), og UI-laget
  kommer oven på `AdichtFile`/`Record` uden at røre lagene under. Emil har
  yderligere specifikationer til UI'et; det er bevidst ikke påbegyndt.
- **Model: RespMech.** Samme opbygning (src-layout, hatchling, GPL-3.0-or-later,
  CLI + senere PySide6/pyqtgraph-GUI, briefcase til installers). Licensen er
  valgt som RespMechs; ændres den, skal `pyproject.toml`, `LICENSE` og README
  følges ad.
- **Sandheden er filerne, ikke SDK'et.** Formatet er reverse engineered fra
  optagelserne og verificeret mod LabCharts egne eksporter; ADI's
  Windows-DLL bruges ikke og skal ikke bruges (det er hele pointen med
  projektet: alle platforme).
- **Verifikationsstatus skelnes eksplicit** i `docs/FORMAT.md` (verified /
  consistent / inferred) og må først ændres, når der er data for det.
- **Blok-terminologi**: LabCharts "block" hedder `Record` i koden (som i ADI's
  SDK); "block" i koden er 16000-tick-lagerenheden.
- **Ingen persondata i repoet.** Rigtige optagelser og eksporter ligger uden
  for git; tests bruger syntetiske filer fra `adichtion.synth`. Integrations-
  tests kører kun med `ADICHTION_SAMPLES` sat.
- **Åbne formatspørgsmål** (se `docs/FORMAT.md` §7) løses med data, ikke
  gætteri.
