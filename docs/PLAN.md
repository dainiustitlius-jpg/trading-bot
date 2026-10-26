# Trading bot planas (ant day-trader-skill pagrindo)

## Tikslas

Python botas, kuris pats veda dienos prekybos procesą JAV akcijoms,
naudodamas 11 įdiegtų `trader-*` skills (`.claude/skills/`) kaip skaičiavimo
variklį. Režimas: **hibridas**.

- **Paper režime** botas viską daro pats: skenuoja, tikrina įėjimą, planuoja
  stop/target, simuliuoja, dydina, tikrina riziką ir „įvykdo“ orderį savo
  simuliuotame brokeryje.
- **Live režime** botas sustoja ties READY bilietu ir praneša jums. Orderį
  išsiunčiate jūs per `/trader-broker` Claude chate (jūsų „taip“ + „Allow
  once“). Botas niekada nesuklastoja jūsų patvirtinimo.
- Rinkos: pirma JAV akcijos (long). Kripto ir futures vėliau, per tą pačią
  architektūrą.

Šis planas pakeičia ankstesnį (savo backtest variklis nuo nulio): akcijoms
indikatorių, rizikos, dydžio, NYSE kalendoriaus ir Monte Carlo logika jau
yra skills'uose ir padengta 315 testų, todėl jos neperrašome.

## Kodėl skills naudojame per CLI, o ne importuojame

Kiekvienas skriptas turi stabilų CLI kontraktą: `--json` (mašininis
išvedimas), `--now` (fiksuotas laikrodis New York laiku), `--offline`
(išsaugoti duomenys vietoj tinklo), `--write` (failas `trader/` aplanke).
Botas juos kviečia per `subprocess` su `cwd` = darbo aplankas. Taip:

- skills galima atnaujinti iš upstream neliečiant boto kodo;
- `--now` + `--offline` leidžia tą patį procesą paleisti istoriniams
  duomenims (replay/backtest);
- skirtingi režimai turi atskirus darbo aplankus (`runs/paper/trader/`,
  `trader/` live), todėl paper bandymai niekada nesumaišomi su live žurnalu.

## Architektūra

```
                 ┌──────────────── tradingbot (Python) ────────────────┐
 laikas/NYSE ──▶ │ Scheduler ─▶ Pipeline (būsenų mašina) ─▶ Executor   │
                 │                 │                         │        │
                 │            SkillRunner                PaperBroker  │
                 │   (subprocess, --json/--now/--offline)  arba       │
                 │                 │                   LiveNotifier   │
                 └─────────────────┼─────────────────────────┼────────┘
                                   ▼                         ▼
          .claude/skills/trader-*/*.py  ◀──▶  <workspace>/trader/*.json
```

Vieno sandorio grandinė (skills tarpusavyje bendrauja per `trader/*.json`):

```
scanner ─▶ news ─▶ entry ─▶ exit ─▶ timemachine ─▶ budget ─▶ risk ─▶ executor
watchlist.json      entry.json  plan.json  timemachine.json  orders.json  state/risk.json
```

## Projekto struktūra

```
trading-bot/
├── .claude/skills/trader-*/     # įdiegti skills (nekeičiame, tik atnaujiname)
├── pyproject.toml               # Python 3.11+, pytest, ruff; jokių sunkių priklausomybių
├── config/bot.example.yaml      # grafikas, filtrai, režimas (paper|live), pranešimai
├── src/tradingbot/
│   ├── skills.py        # SkillRunner: kelias iki skripto, run(args) -> (exit_code, json)
│   ├── workspace.py     # darbo aplankas, trader/config.json paruošimas iš config.example.json
│   ├── clock.py         # NYSE būsena per `alerts.py clock --json`, replay laikrodis
│   ├── pipeline.py      # dienos būsenų mašina (žr. žemiau)
│   ├── filters.py       # boto taisyklės ant skills JSON: news žymos, timemachine verdiktas
│   ├── broker/
│   │   ├── paper.py     # simuliuotas bracket orderis ant 5 min. barų; rašo state/broker.json
│   │   └── notify.py    # live: READY bilietas -> pranešimas (Telegram / el. paštas)
│   ├── journal.py       # SQLite sandorių žurnalas: R, $, priežastys, kiekvieno žingsnio JSON
│   ├── replay.py        # istorinės dienos paleidimas su --now/--offline
│   └── cli.py           # tradingbot run|replay|report|doctor
└── tests/               # offline testai su fixtures (TSLA 2026-10-19 ir kt.)
```

## Dienos būsenų mašina (`pipeline.py`)

Visi laikai New York (Vilniuje +7 val.).

| laikas | žingsnis | skills |
| --- | --- | --- |
| pirmadienį 08:00 | simbolių sąrašas | `scan.py universe` |
| 08:30 | pre-market skenavimas, naujienos, artėjantys rezultatai | `scan.py daily`, `news.py --json`, `facts.py` |
| 09:50–15:30 kas 5 min. | ORB skenavimas, kandidatai pagal apyvartą | `scan.py orb --json` arba `entry.py watchlist --json` |
| kandidatui | naujienų filtras | `news.py --tickers X --json` |
| | įėjimas (tik uždarytas baras) | `entry.py check X --write` |
| | stop/target | `exit.py plan --write` |
| | 10 000 replay + istorija | `timemachine.py run --until <flat_by> --json --write` |
| | dydis | `size.py --write` |
| | rizikos vartai | `check.py --json` (PASS / BLOCK) |
| PASS | vykdymas | paper: `PaperBroker`; live: pranešimas, laukiama jūsų |
| atidarytas sandoris | stebėjimas kas 5 min. | `alerts.py add --from-plan`, `alerts.py check` |
| 15:55 | uždaryti viską | `ticket.py flatten` -> `check.py` -> vykdymas |
| 16:10 | dienos ataskaita | `journal.py` |

Boto filtrai (`filters.py`, konfigūruojami): praleisti, jei naujienose yra
`halt`, `legal`, `!` 8-K arba rezultatai šiandien/rytoj; praleisti, jei
Time Machine verdiktas yra „Leans/Strongly toward the stop“ arba istorijoje
mažiau nei N signalų. Botas niekada nekeičia skills limitų ir neperrašo
orderio, kad jis praeitų `trader-risk`.

MVP apribojimas: skills turi po vieną `entry.json` / `plan.json` /
`orders.json`, todėl kandidatai apdorojami po vieną ir vienu metu atidarytas
daugiausia vienas sandoris (`max_trades_per_day` iš konfigūracijos lieka
galioti).

## Paper brokeris

- Įėjimas: kito 5 min. baro atidarymo kaina + konfigūruojamas slippage.
- Bracket: kiekviename bare pirma tikrinamas stop, tada target (kaip Time
  Machine, todėl rezultatai palyginami); gap per stop fiksuojamas faktine
  kaina, ne stop kaina.
- Rašo `state/broker.json` įrašus ta pačia schema, kurią skaito
  `trader-risk` (data, kind, max_loss, status `placed`), ir pildo
  `portfolio.json` per `ticket.py fill`. Taip dienos nuostolio ir sandorių
  skaičiaus limitai veikia ir paper režime.
- `ticket.py approve` botas nekviečia niekada: tai jūsų „taip“.

## Live režimas

1. Pasirenkate brokerį (`/trader-broker` apima Alpaca MCP ir Interactive
   Brokers), pirma paper sąskaitą.
2. Botas atlieka viską iki `check.py` PASS ir siunčia pranešimą su bilietu.
3. Jūs Claude chate paleidžiate `/trader-broker`: bilietas, jūsų „taip“,
   „Allow once“. Botas pasiima įvykdymą iš `state/broker.json` ir toliau
   stebi poziciją.

## Etapai

1. ✅ **Karkasas**: `pyproject.toml`, `SkillRunner`, `workspace`, `clock`,
   `tradingbot doctor` (patikrina Python versiją, skills, `SEC_USER_AGENT`,
   `trader/config.json`).
2. ✅ **Vieno sandorio grandinė** offline: TSLA 2026-10-19 replay turi duoti tą
   patį kaip skills README (įėjimas 364.18 10:15, stop 358.32, target 370.04,
   8 akcijos, PASS).
3. ✅ **Paper brokeris + žurnalas**, dienos ataskaita (sandoriai, laimėjimų
   dalis, vidutinis R, $).
4. ✅ **Scheduler ir gyvas paper režimas** visai dienai su Yahoo duomenimis.
5. ✅ **Replay/backtest**: paskutinių ~60 dienų (Yahoo 5 min. riba) paleidimas
   per visą grandinę, rezultatai lyginami su Time Machine istorija.
6. ✅ **Live su patvirtinimu** ir pranešimais.
7. Vėliau: kripto (ccxt) ir futures adapteriai su savo scanner/entry/risk
   moduliais, bet tais pačiais `plan.json` / `orders.json` kontraktais;
   ML balas ir LLM naujienų vertinimas kaip papildomi `filters.py` filtrai.

## Kur botas veiks

Ši Claude sesija yra laikinas debesies konteineris, ne serveris. Botui reikia
nuolat veikiančio kompiuterio ar VPS JAV prekybos valandomis (16:30–23:00
Vilniaus laiku), pvz. `systemd` paslauga arba Docker.

## Saugumas

- Paper pagal nutylėjimą; live įjungiate tik jūs, savo konfigūracijoje.
- API raktai tik aplinkos kintamuosiuose, niekada repozitorijoje ar chate.
- `trader/` ir `runs/` yra `.gitignore` (asmeniniai duomenys, portfelis).
- Nemokami Yahoo duomenys yra neoficialūs ir vėluoja; tai mokymosi ir
  tyrimo įrankis, ne finansinė konsultacija.

## Patikrinimas

- `pytest`: unit testai (`SkillRunner`, filtrai, paper brokeris) ir offline
  e2e su TSLA 2026-10-19 fixtures.
- `tradingbot replay --date 2026-10-19 --symbols TSLA --offline tests/fixtures`
  atkuria README skaičius.
- `tradingbot run --mode paper` prekybos dieną: žurnale matomi visi žingsniai
  ir sandoriai, o `trader-risk` limitai laikomi.
