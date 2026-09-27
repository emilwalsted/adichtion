# Beslutninger

Daterede beslutninger og afgjorte punkter. Nyeste øverst.

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
  optagelserne og verificeret mod LabCharts egne teksteksporter; ADI's
  Windows-DLL bruges ikke og skal ikke bruges (det er hele pointen med
  projektet: alle platforme).
- **Verifikationsstatus skelnes eksplicit**: int16-kanaler er sample-eksakte
  mod eksporter; float32-kanaler (beregnede kanaler i 2016-filerne) er
  verificeret ved intern konsistens (kontinuitet over ~1250 blokovergange og
  invarianten `sum(d) = x[-1]`), ikke mod en eksport. Det står i
  `docs/FORMAT.md` §6–7 og må først ændres, når en eksport af en fil med
  beregnede kanaler er sammenlignet.
- **Blok-terminologi**: LabCharts "block" hedder `Record` i koden (som i ADI's
  SDK); "block" i koden er 16000-tick-lagerenheden. UI'et skal bruge LabCharts
  ord ("block"/"record") konsekvent over for brugeren – afgøres i UI-spec'en.
- **Ingen persondata i repoet.** Rigtige optagelser og eksporter ligger uden
  for git; tests bruger syntetiske filer fra `adichtion.synth`. Integrations-
  tests kører kun med `ADICHTION_SAMPLES` sat.
- **Åbne formatspørgsmål** (se `docs/FORMAT.md` §6) løses med data, ikke
  gætteri: v26-enhedsrecords ↔ kanaler, checksum-feltet, og en eksport med
  beregnede kanaler til float32-verifikation.
