# Beslutninger

Daterede beslutninger og afgjorte punkter. Nyeste øverst.

## 2026-10-16 — PySide6 6.12.0 is excluded (it aborts on Python 3.10 and 3.11)

The CI job on Ubuntu with Python 3.10 died with "Aborted (core dumped)" part-way through the suite; the other five jobs were green.

- **Cause, measured:** with PySide6 6.12.0 on Python 3.10 and 3.11, `QPainter.setPen` and `QPainter.setRenderHint` each give back one
  reference to `None` per call (`sys.getrefcount(None)` falls by exactly the number of calls). After a few thousand calls the
  count reaches zero and the interpreter stops with `Fatal Python error: none_dealloc: deallocating None`. The crash site moves
  from run to run (the viewer repaints pyqtgraph axes, which call both methods), which is why it looked random. Python 3.12 is
  unaffected because `None` is immortal there; 6.11.1 is unaffected on 3.10.
- **Decision:** `PySide6>=6.6,!=6.12.0` in the `ui` extra and in the briefcase `requires` (a test keeps them equal), so a later
  6.12.x that fixes it is allowed. A regression test counts the references to `None` over many painter calls.
- **Not done:** no test was skipped or quarantined.

## 2026-10-06 — Splash-skærmen viser repoet igen, og 1.0.0 tagges

Ønske: før release skal splash-skærmen tilbage til den gamle, hvor GitHub-repoet fremgik.

- **Repo-linjen er tilbage** nederst til højre på splash-skærmen (`github.com/emilwalsted/adichtion`). Den blev fjernet
  2026-10-05, mens repoet var privat; nu er det offentligt. `ui/splash.py` er igen identisk med versionen før fjernelsen, så
  taglinen fra samme dag er bevaret, og `tests/ui/test_logo.py` kræver nu adressen i stedet for at forbyde den.
- **CHANGELOG:** punktet om `export --video` stod under `[Unreleased]`, men koden er på `main` og kommer med i tagget, så det er
  flyttet ind under 1.0.0. Datoen er rettet til 2026-10-06, linjen om at splash-skærmen ikke længere viser repoet er fjernet, og en
  tom `## [Unreleased]` står øverst igen.
- **PyPI venter på Emils godkendelse:** miljøet `pypi` får Emil som påkrævet godkender, før tagget sættes, så installerne udgives
  straks, mens PyPI-jobbet venter på et klik i Actions. En PyPI-version kan ikke erstattes.
- **README og release-noterne er rettet før tagget**, fordi README bliver PyPI-siden for 1.0.0: (a) Windows- og macOS-installerne
  har ingen kommandolinje (macOS-appen sender output til systemloggen via briefcases `std-nslog`, Windows-MSI'en er en GUI-stub
  uden konsol og uden PATH); kommandolinjen kommer med Ubuntu-pakken og Python-pakken. (b) Ubuntu-pakken bruger Ubuntu 24.04's
  Python 3.12 og virker ikke på Debian, så "Debian-based" er rettet. (c) RespMechs app holder kolonne 1 til tid, så første kanal i
  en MATLAB-eksport kan ikke tildeles dér; README anbefaler CSV, tab-tabellen eller Excel til appen. (d) `pip install adichtion`
  alene kan ikke skrive Excel eller videoklip (openpyxl og ffmpeg kommer med `ui`). (e) `Documentation` i `[project.urls]` peger på
  respmech.dk/adichtion.html.
- **Åbne produktvalg, ikke gjort:** en kommandolinje i Windows- og macOS-installerne, og en tidsrække i MATLAB-eksporten (eller en
  ændring i RespMech), så første kanal kan bruges i RespMechs app.

## 2026-10-05 (7) — Kommandolinjen kan skrive videoklip

Ønske: kommandolinje-interface til adichtion, "som med RespMech, så vidt muligt".

- **Hulanalysen viste, at kommandolinjen allerede dækker næsten alt, viseren kan** (info, comments, videos, export med tid eller
  kommentarer, check, verify, infer, macsettings, bench). Det eneste hul var videoklippet, der kun kunne skrives fra eksportdialogen.
- **`export --video original|h264`** bruger `adichtion.videoclip` uændret (samme tilstande, samme filnavneregel: dataens navn med klippets
  endelse, samme mappe) og samme rækkefølge som dialogen: klippet skrives under midlertidigt navn, dataeksporten køres, og først derefter
  omdøbes klippet; fejler dataeksporten, fjernes klippet. Bevidst fravalgt: et selvstændigt `clip`-underkommando (ville duplikere
  udvalgs-flagene) og automatisk klip uden flag (ffmpeg er ikke en hård afhængighed af CLI-installationen).
- **Ikke gjort:** nye læsekommandoer (fx kurver/statistik til terminalen), fordi ingen funktion i viseren mangler en CLI-vej ud over klippet.

## 2026-10-05 (6) — 1.0.0 klargjort (ikke tagget)

Ønske: gør alt klar til release af adichtion 1.0.0.

- **Versionen er 1.0.0 de tre steder** (`__init__.py`, `[tool.briefcase]`, `CHANGELOG.md`; `tests/test_main.py` holder dem lige).
  Changelog-posten er den hidtidige `[Unreleased]` samlet under ÉN `### Added`/`### Changed`/`### Fixed` (den havde fået
  dublerede overskrifter efter mange tilføjelser; ingen punkter er ændret eller fjernet), dateret 2026-10-05: ret datoen, hvis
  tagget sættes en anden dag. En tom `## [Unreleased]` står øverst igen.
- **PyPI-klassifikatoren er `Production/Stable`** (var `Alpha`), fordi 1.0.0 er den første stabile version. Skal den være `Beta`,
  er det en enlinjesrettelse i `pyproject.toml`.
- **Release-noterne** (`release.yml`, `publish-release`) har fået en linje om ffmpeg (GPL-bygget med libx264, medfølger via
  `imageio-ffmpeg`, kilde ffmpeg.org), som `docs/RELEASING.md` afsnit 2 kræver.
- **Ikke gjort (kun Emil):** tagget `v1.0.0`, PyPI-publisher og GitHub-environments (se (5)), de manuelle installer-tjek i
  `docs/RELEASING.md` afsnit 2 og Certum-signering af MSI'en.

## 2026-10-05 (5) — PyPI-udgivelse klargjort

Ønske: gør adichtion tilgængelig via PyPI som RespMech.

- **Samme mønster som RespMech:** `publish-pypi.yml` på `v*`-tagget (samme tag som installers), Trusted Publishing (ingen token),
  environments `pypi`/`testpypi`, manuelt kørsel = TestPyPI-tørløb, `pypa/gh-action-pypi-publish@v1.14.2` (v1.14.0 afviser
  Metadata-Version 2.5, som hatchling udsender; RespMech lærte det på 2.4.0). Testgate på Ubuntu (ikke macOS som RespMech):
  adichtions suite er hurtig og kører grøn på Linux.
- **Ekstra værn i buildjobbet:** bygget version = tag, `CHANGELOG.md` har `## [X.Y.Z]`, og hjulet installeres i en tom venv, hvor
  `adichtion --version` kører og den bare `adichtion` giver en ren besked og exit 1 (ingen PySide6). Det sidste er en rettelse:
  før gav `pip install adichtion` + `adichtion` et `ModuleNotFoundError`-traceback.
- **sdist:** udelukker `.claude/`, `.github/` og alle `CLAUDE.md`; beholder tests, docs, logo-kilder og installer-ikoner, fordi
  testene læser dem. Fravalgt: en minimal sdist uden tests (så kan pakkens egne tests ikke køres fra den).
- **Ikke gjort (kun Emil):** registrering af pending publisher på pypi.org/test.pypi.org, oprettelse af GitHub-environments, selve
  tagget. README'ens relative links og logo vises brudt på PyPI, også når repoet bliver offentligt, indtil README får absolutte URL'er. Buildjobbet afviser et tag uden for main.

## 2026-10-05 (4) — Videoklip ved interval-eksport

Ønske: eksportér et videoklip svarende til det eksporterede interval, som originalformat eller en moderne codec, med
datafilens navn og i samme mappe (ikke i en undermappe som LabChart).

- **ffmpeg som program, ikke OpenCV.** OpenCV-hjulet (4.12) har ingen H.264-encoder (målt: `avc1`/`H264` fejler med
  "Encoder not found"; kun `mp4v`, MJPG, WMV2 m.fl.). Klippene skæres derfor af et `ffmpeg`-program (`videoclip.find_ffmpeg`:
  `ADICHTION_FFMPEG`, ellers den ffmpeg, pakken `imageio-ffmpeg` medbringer, ellers `ffmpeg` på PATH). `imageio-ffmpeg>=0.5` er
  tilføjet `[ui]`-ekstraen og briefcase-`requires` (testen `test_the_bundle_requires_what_the_ui_extra_requires` holder dem ens).
  Prisen er en statisk ffmpeg på ca. 80 MB pr. platform i hver installer, og den er en GPL-bygning (libx264): forenelig med
  adichtions GPL-3.0-or-later, men Emil skal være opmærksom på kildekodeforpligtelsen ved udgivelse og på, at en macOS-dmg
  skal signere/notarisere den medfølgende binær (kan kun ses i et rigtigt installer-bygge). Fravalgt: OpenCV `mp4v` (MPEG-4
  part 2 er ikke moderne og giver store filer), og at skrive frames fra OpenCV til en ffmpeg-pipe (to afkodningsstier, ingen gevinst).
- **"Originalformat" = stream copy** (`-c copy`): samme container og codec, intet tabt, hurtigt. Ulempen er, at et kopieret
  klip kun kan starte på et nøgleframe, så det kan begynde før intervallet (målt på en syntetisk WMV med nøgleframe hvert
  sekund: op til ca. 1,5 s før; en rigtig LabChart-WMV kan have længere afstand); slutningen er nøjagtig. Dialogen siger det.
  Fravalgt: genkodning til "samme codec" (WMV3 har ingen encoder i ffmpeg).
- **H.264 = libx264, crf 20, yuv420p, `+faststart`, i en `.mp4`** (afspilles overalt); billednøjagtigt. Lyd bevares for ét
  klip (aac) og udelades, når flere blokke samles.
- **Flere blokke giver ét klip** (stykkerne i rækkefølge; tiden mellem blokke findes ikke i videoen og udelades). H.264
  skalerer alle stykker til det første stykkes størrelse; kopi kræver samme filtype og lige streams (ffmpeg afviser ellers, og
  fejlen vises). En blok uden videofil udelades og nævnes i dialogens note.
- **Spørgsmålet er en boks i eksportdialogen**, kun synlig når intervallets blokke har en videofil, og **den er fravalgt hver
  gang** (et klip er stort og må ikke skrives af vane); *slags* klip huskes som de andre valg. Findes klippet i forvejen,
  spørges der, før det erstattes.
- **Rettet efter selvreview:** `-map 0` med `-c copy` fejlede på LabChart for Mac-film (synkroniseringssporet
  ses som en datastrøm): nu `-map 0:v -map 0:a?`. Concat-demuxerens `inpoint` er absolut, `-ss` relativ til filens start:
  filens `start` (probe) lægges til. Et stykke efter videoens slutning gav en tom mp4 meldt som succes: stykkerne klippes nu
  mod filens varighed (`fit_segments`), tomme afvises, og et klip uden frames er en fejl. Klippet kan aldrig erstatte selve
  kildevideoen (afvises i dialogen og i `write_clip`), en mappe på klippets navn afvises, og en `commit`-fejl (fx afspiller
  har filen åben på Windows) er en advarsel om klippet, ikke en eksportfejl. Dokumentsti gøres absolut (concat læser stier
  relativt til listefilen). **Kendt grænse, ikke rettet:** Annuller tjekkes, når ffmpeg skriver en fremdriftslinje, så den
  kan reagere sent, hvis ffmpeg venter på et hængende netværksdrev eller en cloud-placeholder; probe har en 60 s grænse.
- **Rækkefølge og annullering:** klippet skæres først (0 til 80 % af statuslinjen) under et midlertidigt navn
  (`<navn>.part<endelse>`), derefter skrives datafilen, og først derefter omdøbes klippet; annullering eller en fejl i
  dataeksporten efterlader ingen af filerne. Kan klippet ikke laves (ingen ffmpeg, afvist af ffmpeg), skrives datafilen alligevel,
  og brugeren får en advarsel.

## 2026-10-05 (3) — Eksport, som RespMech kan indlæse, og MATLAB-format

Fund fra release-noteudkastet til RespMech 2.5.0 og Emils ønske: for de formater RespMech understøtter skal adichtion altid
skrive filer, RespMech kan indlæse, og der skal kunne eksporteres til MATLAB i den nyeste, mest gængse version RespMech
understøtter. Målt mod RespMechs egen `core.io.loaders.load` (både PyPI 2.4.0 og master) og mod pandas/scipy.

- **CSV med decimalkomma bruger `;` som skilletegn** (RespMech: `.csv` er komma-separeret, semikolon-separeret under
  decimalkomma). Før blev komma brugt begge steder, så ingen kunne læse filen. Kommentarer og titler med skilletegn,
  tab eller linjeskift renses til mellemrum (et komma i en CSV-kommentar bliver stadig et semikolon, som før); et
  linjeskift i en kommentar brød før en række.
- **Det LabChart-stilede `txt` forbliver, som LabChart skriver det, og er IKKE en tabel RespMech læser** (RespMech tager
  første linje som kolonnenavne). I stedet nyt format `tsv`: tabulator-separeret, én headerlinje, samme `block`-kolonne
  som CSV ved flere blokke, filendelse `.txt`. Fravalgt: at ændre `txt`s header (ville bryde LabChart-kompatibiliteten
  og `verify`/`read_labchart_text`), og at lade RespMech lære LabChart-headeren (RespMech-repoets sag).
- **Excel-arket er en ren tabel**: første række er kolonnetitlerne. Blok, start, markeringens start og interval står i
  celler til højre for tabellen (header-rækken og første datarække), ikke i en række over titlerne, som gjorde første
  datarække til en tekstrække for pandas og RespMech.
- **MATLAB: level 5 (`save -v6/-v7`, ukomprimeret)**, det RespMech (`scipy.io.loadmat`), MATLAB og Octave alle læser;
  v7.3 (HDF5) læser RespMech ikke. Skrevet med en lille egen skriver (`matfile.py`, kun numpy, streamet i bidder), så
  biblioteket ikke får scipy som afhængighed; testet ved at læse filerne tilbage med scipy og RespMechs loader.
  Layoutet er LabChart for Windows' blokeksport, dvs. RespMechs `windows`-variant: `data_block<N>` (kanaler x samples,
  doubles, i enheder, eller lagrede tællinger med `units=False`), `titles_block<N>`, `units_block<N>`,
  `samplerate_block<N>`, `record_block<N>`. RespMech læser kun `data_block1`: eksportér én blok ad gangen til det.
  Tegn skrives som miUTF8 (scipy dekoder miUINT16 som UTF-8 og ødelagde æøå; fundet af review). Fravalgt: Mac-varianten (RespMech indekserer dér gennem scipys variabelliste inkl. `__header__`, så kolonnenumre
  bliver uigennemskuelige).
- **Kendt rest, ikke ændret:** tidskolonnen er `%.7g` som LabChart (ved en optagelse over ca. 10.000 s ved 1 kHz mister
  den sin millisekund-opløsning); RespMech bruger ikke kolonnen.

## 2026-10-05 (2) — Den afkodede kurve bliver stående under scroll og zoom

Emil (skærmoptagelse på Mac'en efter P1 til P9): selv ved et langsomt scroll viste plottene de grove blokindeks-kurver
(ét min/max pr. lagerblok, 4 s ved 4 kHz), og det forstyrrede. Ingen afkodede værdier, ingen eksportbytes og intet i
det, API'et returnerede i forvejen, er ændret (`Record.quick_overview` og `curve_for_range(..., decode=False)` er nye).

- **To årsager, begge målt.** (1) `_coarse_pass` (P4) erstattede HELE den afkodede kurve med blokindekset, så snart
  visningen gik en pixel ud over den afkodede margen (25 % af visningen). (2) Detaljepasset var debounced 60 ms og
  startede forfra ved hvert træk, så under et uafbrudt scroll kom det aldrig. På en rigtig optagelse (9 viste kanaler,
  rigtigt vindue) viste 225 af 250 scroll-trin a 1 % udelukkende blokindeks-kurver.
- **Den afkodede kurve bliver stående** (`ChannelPlot.detail_usable`, `ChannelStack._draw_quick`): rækker den ind i
  visningen, tegnes kun striben uden for den hurtigt, på plottets anden kurve (`ChannelPlot.edge`); selve kurven røres
  ikke (ingen `setData`, samme `QPainterPath`). Det gælder også zoom: ved zoom ind beholdes kurven, til den er under
  1/8 af pixelopløsningen (`DETAIL_KEEP_DENSITY`; et større spring, fx zoom til et interval, får det hurtige billede af
  hele visningen som før), ved zoom ud står den i midten med en stribe på hver side. Fravalgt: at sætte kurve og stribe
  sammen i én kurve (ville bygge hele stien om ved hvert træk).
- **Striben afkoder aldrig** (`curve_for_range(coarse=True, decode=False)`), undtagen når der er zoomet ind til
  enkelte samples (da læses de, typisk fra cachen, som det hurtige pas altid har gjort for en visning under én blok).
- **Det hurtige billede bruger blok-resuméerne, hvor de findes** (`Record.quick_overview`, `lod._from_index`): 32
  min/max pr. blok fra P2/P9 i stedet for ét, blok for blok blandet med indekset for de blokke, der ikke er afkodet.
  Kun når en blok er mindst `COARSE_SUMMARY_PX` = 4 pixels bred, og højst for `COARSE_SUMMARY_RECORDS` = 12 optagelser
  pr. kurve; ellers den vektoriserede tabel fra P3. Målt på én optagelse med 2000 blokke: 0,09 ms pr. kurve fra
  tabellen, 0,15 til 0,22 ms med resuméer. Uden resuméer er resultatet bit for bit det gamle første billede.
- **Detaljepas også mens visningen bevæger sig** (`_schedule_render`, `RENDER_MAX_WAIT_MS` = 150): debouncen får
  et loft, så en hånd, der bliver ved, får et pas mindst så ofte. Et pas under bevægelse (`_render_now(settled=False)`)
  (a) beder under panorering om næste kurve, FØR visningen når enden af den, der står (`RENDER_AHEAD` = 15 % af
  visningen), og afkoder længere frem i bevægelsens retning (`RENDER_LEAD` = 50 % mod 25 % bagud), (b) lader et
  kørende job, der stadig dækker visningen, blive færdigt i stedet for at erstatte det, og (c) rører ikke y-akserne:
  autozoom tilpasser, når bevægelsen er slut, som før.
- **"Slut" følger trækkenes takt** (`_move_gap`, `SETTLE_GAPS` = 1,5, højst `SETTLE_MAX_MS` = 200): når et billede
  tager længere end debouncen (9 plots i et stort vindue: ca. 50 ms pr. træk på Emils Mac), fyrer timeren mellem to
  træk af et jævnt scroll. Før blev hvert træk så taget for et stop (autozoom og et nyt afkodningsjob pr. træk).
- **Kurver, der ankommer midt i en bevægelse, venter på næste træk** (`_held`, `_show_held`): de sættes i samme
  tur som trækket, så plottet stadig tegnes én gang pr. træk (P4's regel). Uden det gav hvert leveret job 9 ekstra
  gentegninger (p95 pr. trin 98 mod 60 ms i målingen).
- **Et forsinket resultat, der kun dækker en del af visningen, vises, hvis det viser mere, end det der står**
  (`_show_result`); resten tegnes som stribe. Før blev det kasseret.
- **Værn mod forgæves afkodning** (`_too_fast`, `_pan_speed`, `_job_s`): panoreres der hurtigere, end et pas som det
  seneste kan nå at blive færdigt (visningen ville have forladt det afkodede), springes passet over under bevægelsen,
  og passet efter sidste træk afkoder. Uden værnet kostede et hurtigt flick over kolde blokke i en bred visning
  GUI-tråden tid (arbejdstråden og tegningen deler GIL'en).
- **Målt på Emils Mac, rigtigt vindue, rigtig optagelse (9 viste kanaler), gammel mod ny kode, GUI-tid pr. trin:**

  | Scenarie | Trin uden afkodet kurve | GUI-tid pr. trin |
  |---|---|---|
  | 26 s visning, 1 % pr. trin, indekseret | 225/250 -> 0/250 | 58,9 -> 52,9 ms |
  | 370 s visning, 1 % pr. trin, indekseret | 226/250 -> 0/250 | 55,6 -> 51,6 ms |
  | 26 s visning, 1 % pr. trin, kold fil | 179/250 -> 0/250 | 49,7 -> 47,3 ms |
  | 26 s, 15 % pr. trin (flick), kold | 59/60 -> 0/60 helt grove (22 med en stribe) | 48,9 til 50,3 -> 47,8 til 48,3 ms |
  | 26 s, 40 % pr. trin, kold | 39/40 -> 0/40 helt grove (32 med en stribe) | 51,2 til 51,9 -> 52,4 til 52,6 ms |
  | 370 s, 15 % pr. trin, kold | 6 til 9 af 30 helt grove -> 19 af 30 | 82 til 84 -> 47,4 til 47,7 ms |

  `adichtion bench --pan` (standardscenariet): pan 57,3 til 57,6 -> 49,4 ms pr. trin, zoom 45,7 til 46,1 -> 43,3;
  `paint.plot` 708 til 712 -> 684. Tallene svinger et par ms fra kørsel til kørsel; konklusionen er "ikke dyrere", ikke
  et præcist tal for gevinsten. Den sidste række er værnet: kan afkodningen ikke følge med, vises det hurtige billede
  (på en indekseret fil er det resuméerne: 2 af 30 trin med en stribe), og afkodningen venter til bevægelsen er slut.
- **Ikke verificeret her:** følelsen med rigtig trackpad og mus (målingen kalder `pan_fraction` i takt, ikke ægte
  hjulevents, som kommer tættere end billederne) og Windows/Linux. Emil prøver.
- **Ikke gjort:** at lade striben afkode fra cachen, når blokkene ligger der (resuméerne dækker det samme uden kopi);
  færre gentegninger pr. træk (P4's post); at tegne det hurtige billede finere end resuméerne.

## 2026-10-05 (1) — Forhåndsafkodning og baggrundsopvarmning (P9)

Efter P2 (blokresuméer og hukommelsesstyret cache) bruger viseren ledig tid til at blive hurtigere, mens brugeren kigger:
dels afkodes blokkene lige uden for det afkodede område, dels gennemløbes hele filen i lav prioritet, så resuméerne findes.
Ingen afkodede værdier, ingen eksportbytes og intet i API'ets returværdier er ændret (`decode_block` har fået den valgfri
parameter `cache=True`).

- **Valgt: ét `Warmer`-objekt pr. åben fil (Qt-frit, `ui/warmup.py`) på en EGEN `JobRunner` (`ChannelStack.warm_runner`).**
  `JobRunner` er én tråd med en FIFO; et langt opvarmningsjob på `runner` ville have stillet hvert render-job bag sig.
  Forhåndsafkodning og opvarmning deler jobbet og en aktivitetsvagt, så de aldrig kører samtidig med hinanden eller med brugeren.
  Fravalgt: to separate jobs (ville skulle koordinere om CPU og tråd) og en `QTimer` på GUI-tråden (ville selv skabe hakkene).
- **Opvarmningen afkoder med `decode_block(cache=False)`:** kun resuméet bliver tilbage. En fil større end blokcachen
  (`CACHE_MAX_BYTES`) ville ellers have skubbet de blokke ud, brugeren kigger på. Forhåndsafkodningen bruger cachen (det er
  meningen, at den skal være varm).
- **Hvornår:** efter at `Activity` (berøres af hvert træk, klik, markørflyt, render og tegnet kurve) har været stille
  `IDLE_S` = 0,4 s (forhåndsafkodning: 0,12 s). Efter hver blok en pause, så højst `DUTY` = 0,3 af én kerne bruges, og aldrig
  mindre end 2 ms, så GIL'en altid afleveres (GUI-tegningen og arbejdstrådens Python deler den; reviewets afsnit 3.4).
  Målt i sandkassen på fil C (4 kanaler, 2 kHz, 100 min, 600 optagelser, 76 MB): opvarmningen tog 15,6 s i vægtid og ca. 5 s
  CPU for 4800 blokke. Længste hul i en 5 ms-`QTimer` på GUI-tråden var 6,6 til 7,7 ms under opvarmning mod 6,5 til 6,9 ms uden
  (jf. `tests/ui/test_warmup_ui.py`, hvor grænsen er løs, fordi sandkassens tid er støjfyldt).
- **Forhåndsafkodning udløses kun, når detaljepasset faktisk brugte afkodede samples** (`_render_job` sender nu et sidste led i
  nyttelasten: cache-hits+misses under passet var over nul). Niveauet (`samples`/`envelope`) duer ikke som kriterium: et
  `envelope` kan være afkodet og reduceret, og et blok-indeks-svar kan hedde `envelope` uden at have afkodet noget. Rækkevidden er
  `PREFETCH_SPAN` = en halv skærmbredde til hver side (ud over `RENDER_MARGIN`), højst `PREFETCH_MAX_BLOCKS` = 32 blokke pr. side
  og kanal, de nærmeste først, siderne vekslende; et nyt krav for en kanal erstatter resten af det gamle.
- **Opvarmningens rækkefølge** er synlige kanaler først (i visningens rækkefølge), derefter de øvrige; inden for en kanal de blokke,
  der ligger nærmest midten af den seneste visning først (`set_view`, kaldt fra hvert detaljepas). Blokke, der allerede har et
  resumé (visningen eller forhåndsafkodningen nåede dem), springes over uden at blive afkodet. Fremdriften er
  `(blokke med resumé, blokke med data)`, aflæst direkte fra resuméerne: den går aldrig baglæns og er rigtig, uanset hvem der
  afkodede.
- **Indstilling:** View, *Index in the background* (`view/warmup` i `QSettings`, standard til) slår kun OPVARMNINGEN fra;
  forhåndsafkodningen ved siden af visningen er begrænset (højst 64 blokke pr. kanal pr. detaljepas) og bliver. Statuslinjen
  viser "Indexing N %" i en permanent etiket, der altid er synlig med fast minimumsbredde (første visning af en ny etiket lagde
  statuslinjen ud igen: 70 til 110 ms i offscreen-kørslen).
- **Lukning:** `Warmer.cancel()` venter på den blok, der afkodes, og kaldes FØR `MainWindow` lukker den gamle fil (`set_file`
  og `close`). En fil, der bliver ved med at fejle ved afkodning (5 fejl), efterlades i fred.
- **Målt effekt (sandkasse, offscreen Qt, syntetisk fil C, ikke rigtige filer):** et første blik på en tiendedel af filen og
  tilbagevenden til en tiendedel: ca. 600 ms til ca. 140 til 230 ms, når filen er indekseret. "Tilbage til hele filen" er uændret
  (ca. 100 ms), fordi hele filen allerede besvares af blok-indekset; zoom til 20 s uændret (2 til 3 blokke afkodes, 94 til 177 ms
  i begge tilfælde, bimodal støj). Forventningen i opgavens overslag (hurtigere "tilbage til hele filen") holdt altså ikke for
  fil C: gevinsten ligger i mellemzoom, ikke i fuld-fil-visningen.
- **Videoafspilning holder aktiviteten varm** (`set_cursor(follow=True)` hver 40 ms): hverken opvarmning eller forhåndsafkodning kører under afspilning. Bevidst enkelt.
- **Aktivitetsvagten** røres af træk, klik, markør, render og tegnet kurve, IKKE af y-zoom, vinduesstørrelse eller sweven; pausen efter hver blok begrænser prisen. `progress()` og omrokering af sweepet koster få ms pr. 10.000 blokke på arbejdstråden (målt af review) uden for duty-regnskabet; omrokeringen venter nu på ro. GUI-hul-testen er en røgtest, ikke et bevis.
- **Review-fund, der blev rettet:** (1) `progress()` var en Python-løkke over alle optagelser x kanaler hvert 0,25 s og kostede 276 ms pr. kald ved 80.000 blokke (20.000 optagelser): filen tæller nu selv sine resuméer (`summarised_blocks()`, O(1)). (2) Opvarmningens 0,4 s-ventetid udsultede forhåndsafkodningen under løbende panorering: ventetiden afbrydes nu af et prefetch-krav. (3) Omrokering efter et nyt udsnit genløb alle færdige blokke: kun det, sweepet ikke har passeret, sorteres igen. (4) `used`-flaget brugte filens fælles cache-tællere (andre tråde gav falske forhåndsafkodninger): nu en tæller pr. tråd (`thread_block_requests()`). (5) Rapporter fra forrige fil ignoreres via job-id; et render-resultat fra forrige fil droppes ved filskift; sweepet starter ved visningen også når indekset besvarer passet; `bench --pan` kører uden opvarmning. Målt efter rettelserne på reviewets egen fil med 20.000 optagelser (80.000 blokke): længste hul i GUI-hjerteslaget 84 mod 84 ms uden opvarmning (før rettelserne 166 til 191 mod 75 til 80 ms); `_next_warm` højst 25 ms (før 60).
- **Hukommelse:** hvert resumé koster ca. 1,7 KB (objekt-overhead; selve tallene er 512 B). En fil med titusinder af korte optagelser giver titusinder af resuméer (80.000 blokke: ca. 133 MB ifølge reviewet); opgavens eksempel (2 timer, 8 kanaler, 4 kHz, ca. 14.000 blokke) ca. 25 MB. Slås opvarmningen fra, bliver resuméerne dovne som i P2.
- **Tallene er fra én sandkassekørsel** og varierer med maskinen (reviewet målte 28,5 s vægtid og 9,0 s CPU for samme gennemløb som de 15,6 s og 5 s ovenfor; andelen af en kerne, ca. 32 til 37 %, holdt).
- **Ikke gjort:** at udfylde manglende min/max i blokindekset for LabChart for Mac-dokumenter (`block_minmax(fill=True)`) som en del
  af opvarmningen; en pause under tegning ud over `Activity` (en tegning, der ikke følger af en bruger- eller render-hændelse, ses
  ikke); prioritering mellem flere åbne filer (der er kun én).
- **Ikke verificeret her:** den oplevede glathed, CPU-/blæserstøj og hukommelse på en rigtig maskine med rigtige filer, jf. P1.
  Emil kører en rigtig fil, kigger efter hak under opvarmningen ("Indexing N %" i statuslinjen), prøver menupunktet og gentager
  målingen (`adichtion bench FIL --pan` og scripts i performance-reviewet).

## 2026-10-04 (9) — Hurtigere tekst-/CSV-/Excel-eksport med fremdrift og annullér (P8)

Målt i sandkassen (600.000 rækker, fire kanaler, syntetisk fil, softwarerenderet Qt): CSV 1,76 s, tekst 1,45 s, Excel
omkring 32 s (opgavens tal fra reviewet). Tiden lå i `_fmt` pr. værdi og en Python-løkke pr. række, i Excel i
openpyxl pr. celle. Dialogen havde en ubestemt statuslinje uden annullér.

- **Tekst/CSV:** rækkerne formateres en blok ad gangen (`CHUNK_ROWS` = 50.000). Hver kolonne formateres med `%.7g`
  (`_strings`), og de enkelte rækker samles med ÉT `%` over hele blokken. De samme tegn som før: `tests/test_export_fast.py`
  sammenligner BYTE for BYTE med den gamle værdi-for-værdi-skriver (bevaret i testen som `legacy_write_text`) for
  txt/csv, begge decimaltegn, alle valgmuligheder, flere optagelser, kommentarer ved bloksamlinger, NaN/uendelig og
  float32. En ADC-kanal har langt færre forskellige værdier end rækker, så hver forskellig BITMØNSTER i en blok formateres én gang
  (ikke hver forskellig værdi: `-0.0` skal beholde sit fortegn, så `np.unique` kører på int64-visningen).
  Tidskolonnen gentager sig aldrig og springer det over. Målt: CSV 1,76 s til 0,75 til 0,95 s (ca. 2 gange, ikke de 3 gange
  opgaven satte som mål), tekst 1,45 s til ca. 0,75 s. Resten er afkodning (ca. 0,2 s) og Pythons egen `%.7g`.
  Støjen i sandkassen er stor (gentagne målinger 0,72 til 0,95 s); gentag på Mac'en.
- **Excel: ingen ny afhængighed.** `xlsxwriter` blev målt (200.000 rækker x 5 kolonner: 4,5 s mod openpyxls 8,1 s, kun
  1,8 gange) og fravalgt: for lidt vundet, og det ville have byttet en afhængighed ud i installers og `.deb`. I stedet streames
  arkenes `<sheetData>` som XML (`_xlsx_streamed`): openpyxl skriver selv en TOM skrivebeskyttet projektmappe med arkenes
  navne (alle pakkedele, typer, relationer, typografier uændret), og hvert ark får openpyxls egen kappe om `<sheetData>`
  med vores rækker indeni, i samme celleform som openpyxl (`<c r t="n"><v>%.16g</v></c>`, NaN/uendelig som `<v />`, tekst som
  `inlineStr`). 600.000 x 5 celler: ca. 3,5 s (ca. 9 gange). Er openpyxls kappe ikke den forventede (en anden version),
  falder skriveren tilbage til den gamle celle-for-celle-vej (`fast=False`, også testorakel).
- **Kendt forskel, bevidst:** en kommentar, der begynder med `=`, blev før lavet til en FORMEL af openpyxl; nu er den tekst
  (en kommentar er aldrig en formel). Tegn, XML ikke kan bære, skrives som Excels `_xHHHH_` (openpyxl afviste dem med en fejl). En bogstavelig `_x0041_` i en kommentar skrives som `_x005F_x0041_`: Excel viser den rigtigt, men openpyxl/pandas læser den tilbage med escapen (den gamle vej ville have fået Excel til at vise »A«).
- **Fremdrift og annullér:** `export_selections(..., progress=(done, total), stale=...)`; `ExportCancelled` rejses mellem
  blokke. En eksport skriver til `<mål>.part` og omdøber, når den er færdig: en annulleret eller fejlet eksport efterlader ingen
  halvskrevet fil og rører ikke en eksisterende fil med samme navn (før blev målet åbnet og afkortet med det samme).
  Mål, der ikke er en almindelig fil (enhed, rør), skrives direkte. Eksportdialogen har nu `Cancel` og viser procent.
  Vinduet, der lukkes under en eksport, annullerer den (arbejdstrådens `stale()`), i stedet for at vente på, at den bliver færdig.
- **Ikke gjort:** fremdrift pr. optagelse (kun samlet procent, den samme information); afbrydelse i selve afslutningen af
  Excel-filen (zip-lukningen er kort). Annullering sker ved næste blok, højst 50.000 rækker.
- **Ikke verificeret her:** at Excel selv åbner den streamede projektmappe uden reparationsdialog (LibreOffice kan ikke
  engang indlæse en triviel xlsx i sandkassen), og tidsgevinsten på rigtige filer. Emil kører `scripts/verify_exports.py` og
  `pytest tests/test_integration_samples.py` på Mac'en, åbner en eksporteret `.xlsx` i Excel og gentager målingen
  (`python scripts/bench`-scriptene i performance-reviewet eller `adichtion bench FIL --export`).

## 2026-10-04 (8) — Kommentar- og blokliste som model/view, klippoptælling på tråd (P7)

`InfoPanel.set_file` byggede ét `QTableWidgetItem` pr. celle: 5000 kommentarer 311-324 ms i panelet (fil D fra
performance-reviewet, 20 blokke), og `r.video_file` lavede ét `os.path.exists` pr. optagelse i oversigtslinjen.

- **Valgt:** Comments og Blocks er `QTableView` over en `_RowsModel` (`QAbstractTableModel`, der laver en celles
  tekst først, når viewet beder om den). Fast rækkehøjde og `setResizeContentsPrecision(200)` gør, at hverken række-
  højde eller kolonnebredde spørger alle rækker. `info.set_file` er nu ca. 2 ms (samme måling, offscreen); hele åbningen
  af filen ca. 455 ms til ca. 150 ms. Channels-tabellen er uændret `QTableWidget` (få rækker, `item(r, c)` bruges af tests).
- **Fravalgt: udfyld fanen første gang den vises.** Det flytter kun ventetiden til første klik på fanen og
  kræver, at alt, der læser tabellen, ved at fanen kan være tom; modellen er både enklere og hurtig for alle faner.
- **`_RowsView` beholder `cellClicked`/`cellDoubleClicked`/`rowCount()`** som `QTableWidget` havde dem, så panelets
  signaler og de eksisterende tests står uændret. Kommentarteksten er nu også tooltip (nyt, billigt). Der var ingen
  sortering at bevare (tabellerne har aldrig kunnet sorteres; rækkerne står i filens rækkefølge).
- **Klippoptælling:** `Record.video_candidate` (ren stiberegning, intet filsystem) er nyt; `video_file` bruger den og laver
  stadig ét opslag pr. kald. Panelet tæller de fundne klip på en baggrundstråd og tilføjer "(n found)" til oversigten,
  når nogle mangler (samme tekst og placering som før; vises altså lidt senere). Et sent svar for en tidligere fil
  forkastes med et token. `video_panel` kalder fortsat `video_file` for den viste optagelse alene.
- Ingen afkodede værdier, ingen eksportbytes og intet i API'ets returværdier er ændret. Udseendet af tabellerne
  (kolonnebredder, rækkehøjde i selve tabellerne, valgfarve) er kun set headless: Emil bør kigge på dem på Mac'en.

## 2026-10-04 (7) — Zero-copy datastrøm over en størrelsesgrænse (P6)

`AdichtFile._blocks` var en kopi af hele `Blocks_0` (362 MB: 0,45 s og 726 MB spidshukommelse ved åbning).
Nu er den et `memoryview` over containerens mmap, når strømmen er én sammenhængende klyngekæde
(`AdichtContainer.stream_view`; kæden kontrolleres i ét udsnit af klyngekortet), ellers kopi som før.

- **Mellemvalget, ikke ren zero-copy:** kun filer på mindst 64 MB (`file.ZERO_COPY_MIN_BYTES`) får
  vinduet; `ADICHTION_ZERO_COPY=0`/`1` og `AdichtFile(zero_copy=)` overstyrer (argument, så miljø, så størrelse).
  Mac-dokumenter læses som hidtil ind i hukommelsen (ikke ændret, opgavens punkt 3 valgt fra).
- **Risici, som opgaven bad Emil afveje:** (a) mmap'en holdes åben til `close()` for ALLE filer, også
  før denne ændring, så Windows' lås mod omdøb/slet er ikke nyt og ikke afhængig af grænsen; (b) netværksdrev
  og skyplaceholdere kan give sidefejl i GUI-/arbejdstråden ved afkodning, nu også for store filer; (c) en
  fil, der afkortes efter åbning, ville give SIGBUS/access violation. Delvist afbødet: hver afkodning af
  en ny blok (zero-copy) sammenligner filens størrelse med mappingens (`_check_not_truncated`), så den
  opdagelige afkortning giver `AdichtFormatError`. Racet er ikke lukket, og ændring uden afkortning opdages ikke.
- **Oprydning:** `close()` frigiver containerens views og sluger `BufferError`, hvis en slice stadig lever (en
  arbejdstråd midt i en afkodning eller en traceback); mappingen lukkes da, når sidste slice dør. Brug efter
  `close()` giver `ValueError`, ikke et nedbrud (testet med fire tråde mod close, ingen SIGSEGV).
  `iter_subblocks` og `_subblocks_from_map` frigiver deres views i `finally`.
- Ikke gjort: afvisning af ikke-lokale drev (kræver platformsheuristik); ukendt `ADICHTION_ZERO_COPY`-værdi
  falder lydløst tilbage til størrelsesreglen. Ikke målt på rigtige filer eller på Windows: Emil prøver, at en
  åben fil kan omdøbes/slettes, at et netværksdrev opfører sig, og kører `tests/test_integration_samples.py`.

## 2026-10-04 (6) — Filen åbnes på en baggrundstråd (P5)

`MainWindow.open_file` startede før `AdichtFile(path)` i GUI-tråden (0,45 s for 362 MB, flere sekunder for 2 GB).
Nu åbnes filen på en egen `JobRunner`-tråd og installeres på GUI-tråden, når den er klar.

- **Nyeste åbning vinder** (frem for at afvise): en macOS FileOpen eller et dobbeltklik under en åbning må ikke
  gå tabt. Resultatet af en annulleret, erstattet eller sen åbning (vinduet lukkes) lukkes ubrugt.
- **Dialogen** er en vindues-modal `QProgressDialog` med Annullér, vist efter 300 ms; indtil da er statuslinjen
  eneste tegn. Eksport og indlæsning af Mac-indstillinger venter, mens en åbning kører.
- **`open_file` returnerer True = startet.** `open_file_sync` er den gamle synkrone vej til tests og `bench --pan`.
- Den annullerede åbnings tråd kan ikke afbrydes midt i konstruktøren; den løber færdig og lukkes.
- Træk-og-slip findes ikke i viseren (ingen `dropEvent`), så der var ingen sådan vej at samle.
- Ikke målt på rigtige filer: effekten på en stor fil er Emils at bekræfte på Mac'en.

## 2026-10-04 (5) — Blokindeks som flade tabeller og vektoriseret hurtigpas (P3)

Reviewet målte, at `lod.curve_for_range`, `global_range` og åbningen af `ChannelStack` gennemløb alle
optagelser én ad gangen i GUI-tråden (ca. 0,24 ms pr. optagelse). Filer med tusinder af korte optagelser
fik sekunders frysning ved hver rendering.

- **`AdichtFile.block_table(channel, fill=False)`** bygger én gang pr. kanal et `ChannelBlockTable`: flade
  numpy-arrays af alle lagerblokke i alle optagelser (optagelse, start- og sluttick, min/max i
  `Record.read`s enheder, `first[r]`..`first[r+1]` = optagelsens blokke). Enhedsomregningen sker én gang pr.
  distinkt enhedsobjekt på alle dets blokke, og min/max sættes i orden elementvis (negativ hældning), så
  tallene er identiske med `Record.block_envelope` (undtagen når første blok mangler min/max og hældningen er negativ: den gamle kode bytter kun efter første element, tabellen elementvis). En blok uden gemt min/max er NaN i begge; `fill=True`
  afkoder dem som `block_minmax` (kun det kald, ikke selve tabellen, afkoder). En ufuldstændig tabel bygges
  igen, når nogen beder om `fill`, eller når en anden har udfyldt blokke siden (`_envelope_gen`).
  `clear_cache()` glemmer tabellerne. Tabellen holdes i filen, ikke i Timeline: den afhænger kun af filen.
- **Hurtigpasset og det udfyldende pas deler ét kodevej** (`_index_curves`): for alle optagelser, som
  blokindekset kan besvare, ét sæt `np.fmin/fmax.at` over de berørte blokke (ét udsnit af tabellen) i stedet
  for en Python-løkke pr. optagelse; de få øvrige (enderne af intervallet, eller et interval inde i én
  optagelse) tager den gamle vej `_record_curve`, uændret. Kurvens opbygning er den gamle: samme antal
  buckets pr. optagelse (`max(1, round(...))`), samme NaN-skillelinje mellem optagelser, samme x-værdier.
  Den gamle implementering ligger som reference i `tests/ui/test_lod_table.py`; detaljepasset er lig den
  inden for float-tolerance; hurtigpasset dækker mindst den gamle kurve og er lig den, hvor den gamle brugte indekset.
- **To nye regler for hvad indekset besvarer** (`_from_index`): en HEL optagelse i én bucket er blokindekset
  eksakt (optagelsens egen min/max, hvad afkodning giver), i begge pas; og i hurtigpasset op til
  `COARSE_WHOLE_BUCKETS` = 8 buckets pr. hel optagelse (hver viser da optagelsens min/max, et første billede
  som den afkodede kurve erstatter). Før afkodede hurtigpasset hver kort optagelse i GUI-tråden første gang
  (3000 optagelser: sekunder koldt). Hurtigpasset dækker mindst det gamle (testet som indeslutning), kun bredere.
- **`global_range`** er nu nanmin/nanmax over tabellen.
- **Blok-etiketterne i markørstrimlen** (`MarkerStrip`) oprettes lazy: de var en `pg.TextItem` pr. optagelse
  (3000 stk. = ca. 1,9 s af installationen), og hver flytning gennemløb dem alle. Nu gennemløbes kun de
  synlige blokke, og efter en vist etiket hoppes til første blok, der starter fri af den (`searchsorted`), så
  højst ca. bredde/etiketbredde etiketter findes. Etiketbredden måles én gang pr. antal cifre.
- **Afvigelser fra den gamle kurve (kendte, bevidste):** (a) tabellen bytter min/max elementvis, den gamle kode
  kun efter første blok; en negativ hældning med konstant første blok gav før et for snævert y-område, nu det
  sande (en rettelse); (b) en hel kort Mac-optagelse uden gemt min/max giver NaN i hurtigpasset (før afkodet i
  GUI-tråden), det udfyldes i detaljepasset; (c) detaljepasset udfylder kun de manglende blokke i de optagelser,
  der besvares fra indekset (`_fill_missing`, med `stale()`-tjek), aldrig hele kanalen; (d) optagelser på et par
  ticks beholder deres samples (`covered > 2 * n_part`). Blokresuméerne, en afkodning efterlader, gør at en
  senere kurve kan afvige op til ét resuméebucket ved bucketenderne (P2's dokumenterede tolerance).
- **Målt (sandkasse, syntetiske filer, offscreen Qt):** fil E (2 kanaler, 3000 optagelser): hurtigpas fuld
  visning 511 -> 1,4 ms pr. kanal, y-område alle kanaler 208 -> 17 ms, `_install_file` 4153 -> 327 ms; fil C
  (600 optagelser): 939 -> 133 ms. Målet "under 10 ms" og "under 500 ms" er nået i sandkassen; Mac'en er ikke
  målt.
- **Fravalgt:** at flette optagelser smallere end en pixel ind i fælles buckets (opgavens "højst to gange
  pixelbredden i punkter"): kurven får som før en bucket pr. optagelse, dvs. højst n_buckets plus én pr. sådan
  optagelse (P2's regel); 3000 optagelser giver ca. 9000 punkter (to pr. optagelse plus skillelinjen), som tegner uden problem (ikke målt over nogle titusinder punkter; filer med langt flere optagelser bør have flettede buckets, som en senere opgave), og en sammenflettet
  kurve ville miste skillelinjerne mellem optagelser. Heller ikke vektoriseret: `interval_stats` og de
  optagelser, der kræver afkodning (n buckets pr. optagelse, ikke hele optagelsen); de er arbejdstrådens og
  små i antal. `Record.overview`/`block_envelope` er uændrede (CLI og eksport får de samme tal).
- **Ikke i tabellen:** Mac-dokumenter er med (samme `channel_record`/`block_minmax`); kun en test på syntetiske
  Mac-dokumenter med manglende min/max og en kanal uden data er mulig i sandkassen. Rigtige dokumenter:
  `pytest tests/test_integration_macdocs.py` på Mac'en.

## 2026-10-04 (4) — Ét gentegning pr. plot pr. flytning (P4)

Emils Mac-målinger (P1b): hvert plot tegnedes 3 til 5 gange pr. brugerhandling, og selve tegnepasset (akser,
gitter) kostede kun 3 til 4,5 ms. Aksetegning og gitter er derfor ikke ændret; opgaven blev at samle opdateringerne.

- **Årsag 1, pyqtgraph:** `ViewBox` anvender sin nye transform først i `prepareForPaint`, dvs. inde i det
  første paint-event. Kæden `sigTransformChanged` (kurven, akserne, tekstelementerne) beder da om endnu en
  opdatering, som kommer efter det område, første paint tegnede. Resultat: to paints pr. plot pr. flytning
  (målt med et event-filter på hvert plots viewport og `QGraphicsScene.changed`). `ChannelViewBox.updateViewRange`
  kalder nu `updateMatrix()` med det samme (kun når viewet har en størrelse), så transformen er aktuel, når
  event-løkken tegner.
- **Årsag 2, grov kurve 60 ms efter:** den debouncede `_render` satte den grove kurve som et selvstændigt paint.
  `ChannelStack._coarse_pass` kører nu i samme tur som flytningen (fra `_on_x_range`), og `ChannelPlot.coarse_key`
  (x0, x1, buckets) får den debouncede pass til ikke at tegne den samme kurve igen. Detaljepasset i arbejdstråden
  og dets job-indsendelse er uændret og stadig debounced.
- **Forbehold og værn:** et synkront grovpas koster GUI-tråden tid pr. input. Filer med tusinder af optagelser
  har et langsomt blokindeks (P3). `COARSE_SYNC_BUDGET_MS = 8` med et glidende gennemsnit (`_coarse_ms`) slår
  derfor det synkrone pas fra, så snart et pas koster mere, og den debouncede vej (som før) tager over. Højst ét
  synkront pas pr. billede (`COARSE_SYNC_MIN_GAP_S` 16 ms): reviewet målte 10 gange så mange grovpas i en burst af
  kønlagte input uden den grænse. Det første pas efter åbning er ubeskyttet (accepteret; før kom det 60 ms senere).
- **Forsinket detaljeresultat:** `_on_progress` kasserer et resultat, der ikke dækker det aktuelle view (viewet er
  flyttet siden jobbet startede), så det ikke overskriver det friske grove billede.
- **Målt (sandkasse, offscreen, syntetisk fil: 9 kanaler, 4 kHz, 68 MB, `bench --pan`):** `paint.plot` 1633 -> ca. 705
  på 75 input (2,4 -> 1,04 pr. plot), pan pr. trin 325 -> 163 til 187 ms. Skærmbilleder (lyst og mørkt tema,
  fem visninger) er pixel-identiske før og efter. Mac'en er ikke målt endnu.
- **Ikke gjort (bevidst):** gitter og tickniveauer (opgavens oprindelige punkt 1 og 2), fordi målingerne ikke
  støtter dem. Cursorflyt giver stadig to paints pr. plot (selve cursoren og den lille værdi-etiket øverst i hvert plot,
  som er et børne-widget); det er en anden og billigere post.

## 2026-10-04 (3) — Blok-resuméer, hukommelsesstyret cache og ingen 8-bucket-gulv (P2)

Reviewet (afsnit 3.2) og Emils Mac-målinger viste, at en fuld-fil-visning afkodede hele filen igen
og igen: cachen på 256 blokke kan ikke rumme en fil, og hver optagelse havde et gulv på 8 buckets.

- **Resuméer pr. blok i samplerummet** (`file._ChannelSummaries`, 32 buckets, rå min/max i dekoderens
  dtype, konverteret ved brug med `Record.convert`, så de følger enhedsskift og negative hældninger).
  De lagres ved den første afkodning af en blok, pr. (optagelse, kanal), uden for blokcachen og med
  filens levetid; `clear_cache()` glemmer dem også (kold start til `adichtion bench`). Ca. 256 byte pr.
  blok og kanal.
- **Tolerancen er dokumenteret og testet:** et detaljepas fra resuméer bruges kun, når en pixelbucket er
  mindst lige så bred som den bredeste resuméebucket (ca. 1/32 blok). Et resuméebucket føder alle
  pixelbuckets, det overlapper, så en pixelbucket kan vise et ekstremum højst ét resuméebucket (500 ticks
  ved en fuld blok) uden for sine egne samples og aldrig misse et. Samme regel gælder ved enderne af det
  ønskede interval. Ellers afkodes som før.
- **`Record.overview(..., from_summaries=False)` er opt-in:** eksisterende kaldere (CLI, eksport, bench)
  får uændrede værdier; kun `lod.curve_for_range` slår det til.
- **Cachen er styret af bytes** (25 % af ledig RAM ved åbning, mindst 256 MB, højst 1 GB; Linux
  `MemAvailable`, Windows `GlobalMemoryStatusEx`, ellers halvdelen af den installerede RAM). Blokke af
  16-bit-kanaler gemmes som int16, hvis alle værdier passer, og udleveres som en ny int32-array, så
  `decode_block` returnerer det samme som før. `cache_blocks=` virker stadig (antalsgrænse oveni).
- **Gulvet `max(8, …)` er væk** (nu `max(1, …)`): en optagelse smallere end en pixel får én bucket, så
  kurven har højst `n_buckets` plus én pr. sådan optagelse.
- **Fravalgt:** baggrundsopvarmning af resuméerne (P9), vektoriseret hurtig-pas over alle optagelser (P3),
  og at lade `overview` bruge resuméer som standard (ville ændre CLI/eksport-tal ved en bivirkning).
- Ingen afkodede værdier og ingen eksportbytes ændres. `SubBlockIndex.channel_dtype` huskes pr. kanal
  (den gennemløb før alle sub-blokke ved hvert kald og kostede kvadratisk tid i `Record.convert`).

## 2026-10-04 (2) — Panoreringstrinnet deles op i sine dele (P1b)

Emils Mac-målinger viste ca. 80 ms GUI-blokering pr. panoreringstrin, men kun ca. 40 ms var tegning;
resten var umålt. `ADICHTION_PERF=1` kan nu fordele et trin (liste i `src/adichtion/ui/CLAUDE.md`).

- **Tidtagning pr. Qt-event-type** (`QApplication.notify`, kun med måling slået til) frem for kun
  at gætte på kendte kandidater: den dækker også det, vi ikke har mistænkt (layout, timere, kø-signaler fra
  arbejdstråden). Kun det yderste event tælles (en indlejret `sendEvent` er en del af det), og kun GUI-tråden.
  Navnene er `ev.<type>.<modtagerklasse>` med typen fra en fast liste; begge er kode, ikke data.
  `ADICHTION_PERF_EVENTS=0` slår det fra, hvis den egen overhead mistænkes for at forstyrre.
- **`perf.quiet_span` og `perf.count`:** frekvente trin aggregeres (antal, sum, maks) og udskrives kun fra
  `slow_ms`; tællere (gentegninger, områdesignaler, input) står i opsummeringen. Forhold mellem
  tællerne viser signalkaskaden direkte (fx 4 områdesignaler pr. hjul-trin ved 4 plots).
- **`bench --pan` kører det rigtige vindue** (ikke en model af det), med pauser på hændelsesløkken i stedet
  for en tæt `processEvents`-løkke. Trinnene går gennem samme kode som musen (`pan_fraction`, `scaleBy`),
  ikke syntetiske museevents. Tal fra sandkassens softwarerenderer er kun relative; de afgørende tal er Emils.
- Intet i adfærden ændres: ingen afkodede værdier, ingen eksportbytes, ingen optimering endnu (den afhænger af
  Emils tal, P4).

## 2026-10-04 — Indbygget tidsmåling: kun tal, og intet når den er slået fra

Performance-reviewet kunne kun måle i en sandkasse med softwarerenderet Qt, så forslagene
P2 til P9 kan ikke vurderes uden tal fra Emils Mac med rigtige optagelser. `ADICHTION_PERF=1`
(stderr, valgfrit `ADICHTION_PERF_FILE` som JSON-linjer) og `adichtion bench FIL` giver dem.

- **Kun tal ud af processen.** En hændelse er et navn skrevet i koden plus numeriske felter;
  `perf.event` afviser alt andet (undtagen et af tre faste LOD-niveauer). Filnavne, kanaltitler,
  kommentartekster, datoer og forsøgsperson-id'er kan derfor ikke havne i en log, hvad enten
  det er med vilje eller ved en fejl. `adichtion bench` udskriver heller ikke stien (et
  filnavn kan bære et id). Testene låser det.
- **Slået fra er den ikke bare billig, men fraværende.** `perf.span` giver ét delt tomt
  objekt, `MainWindow` opretter ingen `StallMonitor` (ingen 5 ms-timer), og `paintEvent` på
  kanalplottene går direkte til `super()`. Cache-tællerne (`AdichtFile.cache_stats()`) er to
  heltal under den lås cachen allerede tager.
- **Frame-tid er afstanden mellem to gentegninger inden for samme træk** (gentegninger mere end
  0,5 s fra hinanden er adskilte bursts, ikke langsomme frames); hvert plot måles for sig, så
  med flere kanaler ligger gentegningerne efter hinanden. Kun frames fra 40 ms og GUI-hul fra
  50 ms udskrives enkeltvis, alle indgår i opsummeringen.
- **`bench` måler alle kanaler pr. kurvepas** (som en rendering tegner dem) og rapporterer kold
  (cachen og de udfyldte envelopes ryddet) og varm (andet kald). Vinduer længere end filen
  gentages ikke. Ingen visning i appen endnu (valgfri i opgaven).
- Fravalgt: `cProfile`/sampling-profiler (tal pr. trin er det, P2 til P9 skal bruge, og en
  profiler ændrer tiderne); et separat `bench`-script uden for pakken (kommandoen skal findes i
  den installerede app, hvor Emil kører den).

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
