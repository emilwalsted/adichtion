# Beslutninger

Daterede beslutninger og afgjorte punkter. Nyeste øverst.

## 2026-09-27 (sen aften) — UI'ets grundform

Afgjort med Emil ved spørgsmål inden byggeriet:

- **Alle blokke vises i forlængelse af hinanden** på én tidsakse (som LabChart),
  med blokgrænser og bloklabels i en stribe over kanalerne; ikke én blok ad
  gangen. Filtid = sekunder fra første bloks start med blokkene stødende op
  til hinanden; datapanelet nævner blok, tid i blokken og klokkeslæt.
- **Ordet i UI'et er "block"** (LabCharts eget), `Record` i koden.
- **Video: OpenCV (opencv-python-headless)**, fast afhængighed i
  `[ui]`-ekstraen. Først stillbillede ved markøren; samme aften udvidet med
  **afspilning**: play/pause fra markøren, til start/slut af blokkens klip,
  hastighed 0,1×–8× (halvér/fordobl). Afspilningen er et ur, ikke en
  medieafspiller: filtiden skrider frem med valgt hastighed, markøren følger
  med på alle kanaler og i panelerne, visningen blader med, og der pauses ved
  blokkens slutning (klippet hører til blokken). Billeder hentes pr. tik med
  "nyeste vinder", så en langsom dekoder springer billeder over frem for at
  sakke bagud.
- **Markering: klik = punkt, Shift+klik = interval** fra markøren til klikket;
  venstre-træk panorerer som i RespMech.
- Øvrigt fra spec'en: menulinje + toolbar + arbejdsområde, maksimeret start
  med åbn-dialog, x-akser altid låst sammen, individuel y-zoom, kanalhøjder
  der kan trækkes og udlignes ved kanalvalg, beregnede kanaler tonet med
  definitionen som overlay, tre paneler under kanalerne (fil, data ved
  markøren, video).
- **Tegning**: blokindeksets min/max tegnes straks, dekodede samples følger
  fra en arbejdstråd; brede visninger bruger indekset direkte (ingen
  dekodning, når en pixel dækker en kvart blok eller mere). Viseren
  genberegner aldrig en beregnet kanal.
- **Udtryk**: RespMechs tema (palet, QSS, pyqtgraph-farver) er portet til
  `ui/theme.py`; panelerne, splitterne og kanalstakken følger RespMechs
  opbygning.

## 2026-09-27 (nat) — beregnede kanalers definitioner og videoens offset

- **Definitionerne på beregnede kanaler hentes ad to veje, og filens data er
  dommeren.** `adichtion.macchart` læser dem fra LabChart 7 for Macs dokumenter
  og indstillingsfiler ("ADInst Data Res File": Mcro-chunks 30001/30002 som
  property-records), og `adichtion.infer` genfinder dem numerisk ved at teste
  LabCharts standardberegninger mod dataene. Mac-filens *aktuelle*
  indstillinger kan være yngre end `.adicht`-eksporten (et dokument havde to
  kanaler mere og et ændret udtryk), så den numeriske vej afgør, hvad der
  faktisk ligger i filen. Dokumentation: `docs/CALCULATED_CHANNELS.md`.
- **Beregningernes sample-adfærd er fastlagt på dataene** (verificeret på alle
  elleve 2016-filer): Arithmetic sample for sample; RMS som centreret vindue
  (w/2 før, w/2−1 efter; 200 samples = 50 ms i studiet) på kildens counts;
  Integral som rektangulær sum fra sample 0, nulstillet pr. record; Spirometry
  Flow som `cal × (råflow − nul(t))` med nulniveauet interpoleret lineært fra
  recordens første til dens sidste råsample; Spirometry Volume som integral med
  ADI's volumenkorrektion (dødrum 0,4 L, der genfyldes af den ene fase, og
  division med *k* af den anden fases flow ud over dødrummet, hele samples).
  Rå-input i 2016-filerne er afrundet til hele counts, mens beregningerne kørte
  på det uafrundede signal; derfor er "inden for ½ count" den bedste
  nøjagtighed for kanaler med rå-input, og "eksakt" (float32) for kanaler af
  float32-input. Statusordene i dokumentationen følger det.
- **Læseren regner ikke selv beregnede kanaler ud.** Det lagrede er LabCharts
  resultat og forbliver referencen; `infer` viser definitionen og dens
  nøjagtighed, og UI'et skal vise den som oplysning. Genberegning (fx med et
  andet dødrum) er en mulig senere funktion, aldrig en stiltiende erstatning.
- **Videoens `offset` er den kompenserede position**: `offset = start −
  video_start + forsinkelse`, hvor forsinkelsen er den kameraforsinkelse, man
  måler i LabCharts video-opsætning (visuel feedback) før optagelsen; den er
  fast inden for en fil og varierer mellem filer (0,17 s i begge sete filer).
  Afspilning: billedet til record-tid *t* ligger ved `offset + t`;
  `VideoClip.delay_s`/`file_time` eksponerer det. UI-spec'en skal stadig give
  brugeren mulighed for at justere manuelt. Afgjort med Emil.
- **Fortegnet i spirometrikorrektionen er optagelsens sag, ikke læserens.**
  I studiets filer ramte korrektionen det negative flow, som dér er
  inspiration (Arithmetic-kanalerne kalder det "In", og en 5 L negativ
  udsving efter 0,1 L positiv kan kun være en inspiration). Læseren rapporterer
  fortegnet; fortolkningen ligger hos brugeren.
- **Ikke afkodet i Mac-indstillingerne**: RMS-vinduet (nøglerne 0x2DC5..0x2DC8
  er konstante `(340, 2, 1.0, 84)` og ikke vinduet), Integral-kildens nøgle,
  dødrummet (0,4 L er default og står ikke i filen) og valget bag Spirometry
  Flows nulkorrektion. Dataene giver dem i stedet; punktet genåbnes kun med
  nye filer, hvor de afviger.

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
  *(Afgjort samme nat, se ovenfor: konstanten er den målte kameraforsinkelse.)*
- **Beregnede kanalers definitioner findes ikke i v26-filerne** (hele
  Map-streamen er nu forklaret, og der er ingen andre streams). v26-filerne
  bærer kun resultatet (float32-data) plus enhedsobjekt og titel; det er
  formentlig sådan LabChart for Mac skriver "Windows-format". Punktet er
  lukket for v26; for nyere filer med beregnede kanaler (v29+) skal det
  undersøges, om `Settings/ChannelSettings` bærer definitionerne, når Emil har
  en sådan fil. *(Definitionerne hentes nu fra Mac-filerne og fra dataene, se
  ovenfor.)*
- **Terminologi i UI'et**: filens "Record<N>.wmv" viser, at ADI selv kalder
  LabCharts blokke *records* i filsystemet; det taler for at bruge "record" i
  UI'et og nævne "block" som LabCharts ord. *(Omgjort senere samme dag: UI'et
  siger "block", se ovenfor.)*

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
