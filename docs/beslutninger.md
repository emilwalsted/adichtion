# Beslutninger

Daterede beslutninger og afgjorte punkter. Nyeste øverst.

## 2026-09-29 (2) — Mac-dokumenternes film, genberegning og eksport med opsampling

Emils svar på de tre spørgsmål fra 28-09-2026 (6):

- **Filmene** ligger i en anden kopi af studiemappen (backuppen), i `<dokument> movies/`
  som på Windows. Hver film har et "Synchronisation track" med ét 8-byte-stempel pr.
  billede: optagetidspunktet i µs på Mac'ens `Microseconds()`-ur, samme ur som
  dokumentets `mTim`. Dermed er filmen synkroniseret uden gætteri: filmtid = blok-tid +
  en fast forskydning pr. film (3–5 ms på fire film fra 2016 og 2017, ens for alle
  billeder til 1 µs). `adichtion.qtmovie` læser kun `moov` og stemplerne; appen afspiller
  som for `.adicht` (`offset + t`). Kameraets egen forsinkelse før billedet når Mac'en er
  ikke registreret og korrigeres derfor ikke (Windows-formatet gemmer en målt værdi).
  Filerne var ikke hentet ned (0-byte-pladsholdere); de fire film blev læst
  med range-forespørgsler, og appen melder en pladsholder som ulæselig.
- **OpenCV under 4.13**: filmene er interlaced DV, og OpenCV 4.13 (FFmpeg 8) returnerer
  dem sorte; 4.10–4.12 afkoder dem som FFmpeg selv. Kravet står begge steder i
  `pyproject.toml`, og en test holder dem ens.
- **Genberegning af beregnede kanaler: ikke nu, måske senere.** De vises fortsat som
  "calculated, not stored" med definitionen.
- **Eksporten opsampler og siger det.** Har de valgte kanaler forskellige rater, får
  tabellen en række pr. tick, og de langsomme kanaler opsamples som LabCharts egen
  eksport: lineær interpolation, afrundet til hele counts væk fra nul, sidste værdi holdt
  efter sidste sample (`Record.read_ticks`). Verificeret mod de 10 WinChart-eksporter:
  99,998 % identiske; resten er ét count ved eksakte halve, som LabChart af og til
  runder mod nul (ingen afrundingsmodel genskaber det). Kommandolinjen og
  eksportdialogen skriver, hvilke kanaler der er opsamplet fra hvilken rate; filen selv
  er uændret i formatet (ingen ekstra headerlinjer, så RespMech m.fl. læser den som før).
  Kanaler med samme rate beholder den (ingen opsampling).

## 2026-09-29 — UI-rettelser: tidsrullebjælke, splash, tomrum, én Open-dialog

- **Vandret rullebjælke** under graferne (`ChannelStack.hbar`): håndtaget er det synlige
  tidsspænd, positionen dets start; deaktiveret, mens hele filen er i billedet. Skalaen er
  1 000 000 trin over filens varighed, så timelange filer holder sig inden for QScrollBars
  int32. Bjælken sætter kun x-området (`set_range`), som Alt+træk gør.
- **Splash mindst 3 s** (`MIN_SPLASH_MS` 1200 -> 3000).
- **Tomrum:** markørstrimlen 44 -> 38 px og centralwidgettens topmargin 6 -> 2 px. 32 px
  var for lidt: blokmærkat og kommentarflag overlappede. Målt med skærmbilleder af den
  syntetiske fil, ikke på en rigtig Mac.
- **Menupunktet *Load LabChart for Mac settings…* er fjernet.** *Open…* har tre filtyper
  (`.adicht`, Mac-dokumenter, Mac-indstillingsfiler); den valgte filtype afgør, om filen åbnes
  som optagelse eller læses ind som definitioner i den åbne fil. Fravalgt: at sniffe filen
  automatisk, fordi et Mac-dokument og en indstillingsfil deler magic-bytes og begge kan læses
  som indstillinger; valget ligger hos brugeren, som før. Uden åben optagelse forklarer en
  besked, at man skal åbne optagelsen først.

## 2026-09-28 (6) — LabChart for Mac-dokumenter læses gennem samme API

- **Emils ønske:** understøttelse af LabChart 7 for Mac's egne dokumenter (gemt uden
  filtypenavn), med studiemappen som materiale og fundene løbende i en MD-fil
  (`docs/MAC_FORMAT.md`).
- **Læseren:** `macdoc.py` gør et dokument til samme `ChartData` + et blokindeks med
  `AdichtFile`s grænseflade; API, eksport, CLI og app er uændrede ovenpå. Dokumentet genkendes
  på de første bytes, ikke på navnet. Verificeret mod de 10 WinChart-eksporter i mappen:
  rådata sample-eksakt (1 485 kanal×blok-kombinationer), LSB/enheder, kommentarer og
  starttider (±5 ms; eksporten afrunder til 10 ms).
- **Beregnede kanaler lagres ikke i dokumenterne** (LabChart for Mac regnede dem ved
  tegning). De genberegnes IKKE: det følger reglen om, at læseren aldrig genberegner, og en
  genberegning ville ikke være LabCharts tal. De vises som "calculated, not stored" med den
  lagrede definition og er grå i kanal- og eksportdialogerne. **Åbent for Emil:** skal appen
  kunne genberegne dem (som et bevidst, markeret valg, verificeret mod eksporternes float32-
  kanaler, hvor de findes)? (Besvaret 29-09-2026: ikke nu.)
- **Langsomme kanaler beholder deres egen samplerate** (100 Hz på en 4 kHz-tick): ingen
  opsampling, som WinChart-eksporten laver (dens mellemværdier er LabCharts interpolation,
  ikke lagrede data). Værdien under markøren er den seneste sample. Eksport af kanaler med
  forskellige samplerater i én tabel afvises som før, nu med rategrupperne i beskeden.
  **Åbent for Emil:** skal eksporten kunne opsample eller lave én tabel pr. rate? (Besvaret
  29-09-2026: opsample og sig det.)
- **Blokke uden gemt min/max** (hver blok af EMG-kanalerne i 4 dokumenter, 15 % af de
  langsomme kanalers blokke): GUI-tråden dekoder dem ikke; arbejdstråden udfylder dem.
- **Tider:** blokkenes start er lokal vægurstid uden zone, gemt med UTC-offset 0, som
  eksporten gør.
- **Film:** dokumenterne navngiver LabChartMovieN.mov pr. blok (`Cnt1`/`CntD`), men ingen af
  filerne findes i mappen, så tidskoblingen kan ikke afkodes; kun navnene vises. (Løst
  29-09-2026 med filerne fra backuppen.)
- **Rettet undervejs:** `macsettings` læste egenskab `0x2849` (21 i alle filer) som
  kanalantallet; det er `0x287F` (lig antallet af `Map4`-kanaler i alle 15 dokumenter).
  Og beregningernes indstillingsnøgler er ikke faste: LabChart tildeler hver udvidelse et
  nøgleområde, som filens `McrE` registrerer, og som flytter sig mellem installationer (tre
  dokumenter og to indstillingsfiler har andre områder). `macchart.extension_keys` slår
  nøglerne op i registret; med faste nøgler manglede de flyttede filers udtryk og kilder.

## 2026-09-28 (5) — Dock-ikonet: den pakkede Mac-app sætter intet applikationsikon

- **Symptom** (Emil, på en Retina-Mac med dmg'en fra *Build installers* på `main`):
  Finder viste det rigtige logo, men Dock-flisen og Cmd-Tab viste et sløret, pixeleret
  nøgent "a", så længe appen kørte.
- **Årsag (Qt-kæden verificeret i Qts kilde og i CI-loggen; det sidste Dock-trin udledt af
  symptomet, ikke målt på en Mac):**
  `ui/app.py` kaldte `QApplication.setWindowIcon(app_icon())`. Qt gør det til
  `NSApp.applicationIconImage` og erstatter dermed bundlets ikon i Dock'en, mens appen
  kører. dmg'en indeholder PySide6 **6.9.3**, ikke den nyeste: `min_os_version = "12.0"`
  får briefcase til at installere med `--platform macosx_12_0`, og PySide6 6.10+ findes
  kun som macosx_13_0-hjul. I Qt 6.9.1–6.10.0 kasserer `imageFromQIcon:withSize:` flisens
  størrelse, så NSImage'ets størrelse aldrig sættes; ifølge Apples dokumentation af
  `NSImage.size` tages den så fra den første repræsentation, og `app_icon()` tilføjer
  16 px først, det nøgne "a" (16 pt). At Dock'en derefter viser netop den repræsentation
  forstørret, frem for en af de større logo-repræsentationer i samme NSImage, er udledt:
  det passer med symptomet, men er ikke målt. (Qt 6.10.1 rettede det; 6.10.2+ vælger én
  pixmap i flisens størrelse.)
- **Rettelse:** i den pakkede macOS-app sætter `main()` intet applikationsikon
  (`application_icon_choice`); `adichtion.icns` er så Dock- og app-skifter-ikonet, det
  samme som Finder viser. Bundlet genkendes af `ui/macbundle.py`: stien skal være
  præcis `<X>.app/Contents/MacOS/<fil>`, og Info.plist'ens `CFBundleIdentifier` skal
  være vores (`MACOS_BUNDLE_ID`, som en test holder lig pyprojects `bundle` + appnavn). En
  ren `.app/Contents/MacOS`-delstrengstest er IKKE nok: framework-Python (python.org,
  Homebrew) kører selv inde i `Python.app`. Kørt fra kildekoden på macOS sætter appen
  `logo.dock_icon()`, kun det fulde logo (128–512 px), så ingen Qt-version kan give
  Dock'en det nøgne "a". Windows og Linux er uændrede (`app_icon()`, alle størrelser).
  `MainWindow.setWindowIcon` er urørt. Et vinduesikon når kun Dock'en via
  `QCocoaIntegration::focusWindowChanged`, og kun når appen hverken har et bundle-ikon
  (`CFBundleIconFile`) eller et `QGuiApplication`-ikon (verificeret i Qt 6.9.3's og
  6.11.2's kilde, ikke på en Mac). I den pakkede app forhindrer `adichtion.icns` det, fra
  kildekoden `dock_icon()`; ellers rammer vinduesikonet kun titellinjens dokumentknap, og
  kun med `setWindowFilePath`, som appen ikke bruger. Fjernes ikonet fra kildekode-kørslen,
  kan en ikke-bundlet Python (pyenv, uv, conda) give Dock'en det nøgne "a" igen.
- **Fravalgt:** at løfte `min_os_version` til 13.0 for at få PySide6 ≥ 6.10.2 (ville
  også rette det, men dropper macOS 12); at vende `app_icon()`s rækkefølge (afhænger af
  Qt-versionens rækkefølgeregel og giver stadig "a" i 6.10.2+, når `NSDockTile.size` ×
  pixelforhold ≤ 64 px; om det nogensinde sker, er ikke målt).
- **Ikke ændret:** `.icns`'en og beslutningen "ikon-tegningen op til 64 px" fra (2).
  I `.icns` er 32 og 64 px (16 og 32 pt @2x) stadig det nøgne "a", skarpt og med vilje;
  det ses i Finders listevisning og Aktivitetsovervågning på Retina, og formentlig ved en
  meget lille Dock (udledt af en model af NSImages repræsentationsvalg, ikke observeret:
  32 pt eller mindre på Retina; på en 1×-skærm afhænger det af, om macOS foretrækker
  1×-repræsentationer, og `.icns`'en har ingen 1×-chunk under 128 px). Ændres det, er det
  en ny designbeslutning. En test låser nu hver `.icns`-chunks tegning.
- **Retter (3) og (2):** det blå ikon i (3) var ikke Briefcases build-mappe: Emil havde
  installeret dmg'en fra en *genkørsel* af en kørsel fra aftenen før, og en genkørsel
  bygger sin oprindelige commit (`docs/RELEASING.md` §5). Briefcase-forklaringen gælder
  stadig for lokale builds. (2)s "samme renderer som appens dock-ikon" gælder kun kørsel
  fra kildekoden; den pakkede app viser `.icns`'en.

## 2026-09-28 (4) — UI-rettelser: zoom, sync, interval, ikoner, autozoom

- **Zoom- og scrollgrænser sættes på ALLE plots** (og på markørstrimlen), med `maxXRange`
  = filens længde. Før lå grænserne kun på det første plot; et træk på et andet plot lod
  dets x-område stå ude af trit med resten (målt: (50,65) mod (30,45) efter et træk forbi
  slutningen). Fuldt zoom-ud = hele filen over hele plotbredden.
- **Træk markerer et interval; Alt+træk panorerer** (ændrer "venstre-træk panorerer som i
  RespMech" fra tidligere, efter Emils ønske 28-09-2026, punkt 7). Panorering findes
  fortsat: Alt+træk, midterknap, sidelæns stryg (`ChannelPlotWidget`) og Alt+←/→ (en halv
  skærm). Fordi opgaven både ønsker træk-som-markering (punkt 7) og nævner træk-som-panorering
  (punkt 2), er rollerne et valg: *Vis > Træk markerer et interval* (til/fra, husket) bytter
  dem. Standard = markér. Et træk under 4 px er et klik. Markøren sættes ved trækkets start,
  som ved Shift+klik.
- **Højreklik på en graf**: "Export interval…" med et interval, ellers "Export whole
  recording…" (eksportdialogen får `default_scope`). Toolbarens Export uden interval bruger det
  huskede omfang, ellers blokken under markøren.
- **Eksportdialogen husker** format, omfang, kanalvalg (også en afkrydsning, hvis antallet af
  kanaler passer), decimaltegn, de tre valg og sidste mappe (`QSettings`, gruppe `export`).
  Omfang: eksplicit ønske > interval > husket > blok > hele filen.
- **Space med et interval** spiller fra intervallets start til dets slutning (inden for
  blokkens klip) og lader intervallet stå markeret; uden interval som før.
- **Auto zoom / Zoom to fit**: den gamle "Auto zoom" (engangs-tilpasning af y, Ctrl+A) hedder nu
  *Zoom to fit*; *Auto zoom* er en til/fra-knap (Ctrl+Shift+A, husket) der tilpasser y efter
  hver afsluttet bevægelse (efter renderens debounce, og igen når den dekodede kurve
  ankommer). *Zoom to fit* er deaktiveret, mens Auto zoom er slået til. `ChannelStack.auto_zoom()`
  er blevet til egenskaben `auto_zoom`; engangsmetoden hedder `zoom_to_fit()`.
- **Toolbar med ikoner** (`ui/icons.py`, egne linjeikoner i temaets farver, normal/aktiv/
  deaktiveret/til) og tooltips med navn, virkning og genvej.
- **Startdialogen** viser de senest åbnede filer og "Open a file…"; uden nylige filer går appen
  som før direkte til fildialogen (færre klik for en førstegangsbruger).
- **Liste-valg centrerer markøren**: kommentar (og blokrække) i filpanelet flytter markøren og
  ruller graferne, så den står i midten, zoom uændret (ved filens kanter stopper visningen).
  Opgaven nævner "video i listen"; listen med videoer er blok-fanen (én række pr. blok, med
  klippet i sidste kolonne), så en blokrække behandles på samme måde, og dobbeltklik på en
  blok viser hele blokken som før.
- **Y-aksens bredde er delt og målt**: alle kanaler får samme bredde, den bredeste
  tick-etiket kræver (trin på 4 px, med hysterese), i stedet for fast 90 px. Gevinsten afhænger
  af data (korte etiketter giver mest).
- Ikke afprøvet på en rigtig Mac/Windows-skærm: ikonernes udseende i mørkt tema, trackpadens
  sidelæns stryg og musefølelsen.

## 2026-09-28 (3) — "gammelt blåt ikon på Mac": revideret, ikke en kodefejl

- **Fundet ved en henvendelse** (en helt klassisk "checker det slog
  igennem?"-forespørgsel): appens ikon i repoet er allerede den nye a-trace-tegning, uden
  spor af den tidligere accent-blå ikon (`#2C6E9B`, den blå firkant med hvid kurve `ui/logo.py`
  havde FØR redesignet i "2026-09-28 (2)" nedenfor). `tests/ui/test_logo.py`s
  `test_installer_icons_exist_and_are_the_drawing` sammenligner de indtjekkede
  `installer/adichtion-*.png` pixel for pixel med den nuværende tegning og var grøn hele
  vejen; koden og de bundlede aktiver er indbyrdes konsistente.
- **Konklusion (revideret, ikke observeret på en rigtig Mac):** det blå ikon, Emil så, er
  formentlig et Briefcase-fænomen, ikke en fejl her. Briefcase kopierer kun
  `installer/adichtion.*` ind i app-bundlets ressourcer ved `briefcase create`; et senere
  `build`/`run` på et allerede eksisterende `build/`-træ genbruger det gamle ikon,
  medmindre `--update-resources` bruges (macOS lægger desuden sin egen ikon-cache
  oveni). Se `docs/RELEASING.md` §5 for den konkrete rettelse. Kilde: Briefcases egen
  dokumentation og beeware/briefcase#1766, ikke en test kørt i denne sandkasse (Qt/Briefcase
  bygger ikke installers her).

## 2026-09-28 (2) — logoet i appen, splash

- **Tegningen bor i appen**: geometri og palet ligger i `src/adichtion/ui/logo.py` (ren
  Python, Qt kun ved rendering), fordi appen og installer-ikonerne skal bære den samme kilde
  og to kopier ville drive fra hinanden. `design/logo/adichtion_logo.py` importerer modulet
  og skriver SVG'erne og PNG-renderne; en test fejler, hvis de indtjekkede SVG'er ikke
  længere er modulets output. (Erstatter "kilden er design/logo/adichtion_logo.py" nedenfor.)
- **Installer-ikoner fra hver størrelse**: `scripts/render_icons.py` renderer med Qt (samme
  renderer som appens dock-ikon) hver størrelse for sig, så 16–64 px er ikon-tegningen
  (a'et alene) og 128 px og op logoet; `.ico`/`.icns` samles af de størrelser, ikke ved at
  skalere 1024-renderen ned. Pillow skriver ikke 16- og 32-px 1×-slots i `.icns`; macOS
  skalerer dem fra 2×-slottene.
- **Splash på kurveskriverpapir** (`ui/splash.py`, efter RespMechs mønster: vises som det
  første, vinduet bygges bagved, afsløres efter mindst 1,2 s): samme papir og net som logoet,
  flisen til venstre, ordmærket i blæk med ravfarvet streg, tagline "Read and export .adicht
  files without LabChart" (Emil: nævn .adicht, så ordspillet i navnet ses, og fremhæv
  eksport, som LabCharts gratis Reader ikke kan), og langs bunden én blæk-kurve med rolig vejrtrækning og ét sniff
  markeret som udsnit. Klik lukker den ikke (der er intet bagved endnu). About-dialogen
  viser logoet.

## 2026-09-28 — app-ikon og logo: "a-trace"

- **Valgt koncept** (Emil, efter to runder med i alt tolv forslag): kurveskriverpapir
  (creme med grønt millimeternet), hvorpå én blæk-kurve skriver et lille a og fortsætter i
  et sniff-lignende udslag; udslaget er markeret som udsnit med et ravfarvet bånd, app'ens
  kernehandling. Papiret peger tilbage på kurveskriveren, som LabChart er opkaldt efter.
- **To tegninger på samme flise**: logoet (net, bånd, kurve med a og udslag) fra 128 px og
  op; ikonet (a'et alene med blæk på papiret) fra 64 px og ned. Samme fliseproportioner som
  RespMech (87,5 % af lærredet, 25 % hjørneradius), så de to apps står ens i docken.
- **Bevidst ulig naboerne**: ADInstruments' mærke er et mørkeblåt (#005581) ordmærke med en
  håndtegnet zigzag-spids; LabChart-ikonet er en blå gradient-cirkel med hvid ring, én hvid
  krusedulle og en stjerne. Derfor ingen cirkel, ingen blå flise, ingen hvid krusedulle,
  ingen stjerne. Fravalgt undervejs: dekodede kanaler ud af en lukket fil, regnbue-kanaler
  med kommentar-nål (så generisk ud og nålen lignede et flag i lille størrelse), rent
  monogram på lilla, dokument med kurve, åbnet klamme, stablede records.
- **Kilden er `design/logo/adichtion_logo.py`** (ren Python, skriver SVG; ingen filtre,
  masker eller clipPaths, så Qt's SVG-renderer tegner det som en browser). Palet og
  geometri står dér og i `design/logo/README.md`. Implementering i `ui/logo.py`,
  `installer/`-ikonerne og README udestår (tjekliste i samme README).

## 2026-09-27 (nat, 2) — én kommando og installérbare pakker

- **Appen hedder `adichtion`, ikke `adichtion-app`** (Emils krav). Ét entry
  point (`adichtion.__main__:main`): uden argumenter, eller med en
  `.adicht`-sti, starter appen; med en underkommando (`info`, `export`, …)
  eller `--help`/`--version` kører kommandolinjen. `python -m adichtion` og
  den pakkede app kører det samme. En test holder listen af underkommandoer i
  `__main__` lig med argparse'ns, så en ny kommando ikke ender med at starte
  appen.
- **Standalone-installers som RespMech: briefcase.** Windows-MSI (usigneret;
  signeres lokalt bagefter som for RespMech), macOS-dmg (Developer ID +
  notarisering kun når Apple-secrets er sat, ellers ad hoc) og Ubuntu 24.04
  `.deb` (bygget i Docker med `--target ubuntu:noble`, så pakken afhænger af
  distributionens egen Python). AppImage er fravalgt: briefcase-dokumentationen
  siger, at det ikke fungerer med binære wheels (numpy, PySide6, OpenCV).
  Workflowet `Build installers` kan køres manuelt (kun artifacts) eller af et
  `v*`-tag, som også opretter GitHub-releasen; tagget kræver Emils go
  (`docs/RELEASING.md`).
- **Versionen står to steder plus changelog**: `__version__` og
  `[tool.briefcase] version` (briefcase kan ikke læse en dynamisk version)
  samt en `## [X.Y.Z]`-post i `CHANGELOG.md`, som Debian-pakken kræver. En
  test fejler, hvis de tre ikke stemmer.
- **`.adicht` registreres som appens dokumenttype** (dobbeltklik åbner filen;
  macOS' FileOpen-event håndteres i `AdichtionApplication`); rollen er
  "viewer", appen skriver aldrig i en optagelse.
- Ikonet er appens eget tegnede logo (`ui/logo.py`) renderet til
  `installer/` i de størrelser, platformene vil have.

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
  venstre-træk panorerer som i RespMech (afløst 2026-09-28 (4): træk markerer nu).
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
