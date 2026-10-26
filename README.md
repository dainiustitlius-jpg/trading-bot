# trading-bot

Dienos prekybos botas JAV akcijoms, kuris naudoja 11 `trader-*` Claude skills
(`.claude/skills/`, iš [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), MIT)
kaip skaičiavimo variklį. Planas: [docs/PLAN.md](docs/PLAN.md).

Ką botas daro:

- Backtest per paskutines ~60 dienų, live pozicijų sekimas ir pranešimai (ntfy, Telegram, el. paštas).
- Kas 5 min. prekybos metu eina per grandinę
  `scanner -> news -> entry -> exit -> timemachine -> budget -> risk`.
- **Paper režimas:** kai `trader-risk` duoda PASS, simuliuotas brokeris įvykdo
  bracket orderį ir seka stop, target bei uždarymo laiką (15:55 ET).
- **Live režimas:** botas tik paruošia bilietą, o orderį siunčiate jūs per
  `/trader-broker` Claude chate. Botas niekada nesiunčia orderio į tikrą brokerį.
- **Žurnalas ir ataskaita:** visi sprendimai rašomi į žurnalą, po uždarymo
  sukuriama dienos ataskaita.
- **Valdymo pultas:** naršyklėje adresu http://127.0.0.1:8765. Jame matosi
  šiandiena, kandidatai, sandoriai ir nustatymai, galima sustabdyti naujus
  įėjimus arba uždaryti poziciją.

## Paleidimas Windows (PowerShell)

Reikia [Python 3.11+](https://www.python.org/downloads/) (diegiant pažymėkite
„Add python.exe to PATH“) ir [Git](https://git-scm.com/download/win).

Pirmą kartą:

```powershell
cd $HOME
git clone -b claude/eloquent-hawking-x84hhz https://github.com/dainiustitlius-jpg/trading-bot.git
cd trading-bot
Copy-Item config\bot.example.toml config\bot.toml
```

Atnaujinti į naujausią versiją: `git pull` aplanke `trading-bot`.

Kiekvieną kartą atidarius naują PowerShell langą:

```powershell
cd $HOME\trading-bot
$env:PYTHONPATH = "src"
```

Išbandyti be interneto (praėjusios dienos pakartojimas ir pultas):

```powershell
python -m tradingbot replay --date 2026-10-19 --data tests/fixtures/replay-2026-10-19 --workspace runs/replay --dashboard
```

Tada naršyklėje atidarykite http://127.0.0.1:8765. Ctrl+C sustabdo.

Paleisti tikrą paper botą (prekybos dienomis 16:30–23:00 Vilniaus laiku):

```powershell
python -m tradingbot run
```

Botas tikrina rinką kas 5 min. ir rodo pultą adresu http://127.0.0.1:8765.
Kompiuteris turi neužmigti, kol botas dirba. Jei `python` atidaro Microsoft
Store, rašykite `py` vietoj `python`.

## Komandos

| komanda | ką daro |
| --- | --- |
| `doctor` | aplinkos patikra |
| `run` | boto ciklas kas 5 min. + pultas (`--no-dashboard`, `--port`) |
| `dashboard` | tik pultas |
| `replay --date D --data DIR --workspace W` | viena išsaugota diena su fiksuotu laikrodžiu (`--start`, `--end`, `--dashboard`) |
| `fetch` | atsisiunčia 60 dienų 5 min. barus backtest'ui (`--symbols`, numatyta 40 likvidžių JAV akcijų) |
| `backtest` | visas botas per išsaugotas dienas (`--fast` per sekundes, `--days`, `--symbols`, `--dashboard`) |
| `sweep` | 96 stop/target/uždarymo/filtrų variantai per kelias sekundes, surikiuota (`--variants`, `--full`, `--dashboard`) |
| `notify-test` | bandomasis pranešimas visais sukonfigūruotais kanalais |
| `pass` | viena patikra dabar, nieko nevykdo |
| `evaluate TSLA` | viena akcija per grandinę |

Bendri raktai: `--config` (numatytas `config/bot.toml`), `--workspace`
(numatytas `runs/<mode>`), `--now "2026-10-19 10:15"`, `--data DIR`.

## Failai

| kur | kas |
| --- | --- |
| `config/bot.toml` | boto nustatymai (keičiami ir pulte) |
| `runs/<mode>/trader/` | skills failai: `config.json`, `plan.json`, `orders.json`, `portfolio.json`, `state/broker.json` |
| `runs/<mode>/bot/journal.jsonl` | sprendimų žurnalas |
| `runs/<mode>/bot/trades.jsonl` | uždaryti sandoriai |
| `runs/<mode>/bot/reports/` | dienos ataskaitos |

Paper ir live naudoja atskirus aplankus, todėl paper bandymai niekada
nesusimaišo su live žurnalu.

## Backtest

```powershell
python -m tradingbot fetch
python -m tradingbot backtest --dashboard
```

`fetch` atsisiunčia 60 dienų duomenis į `data/history` (apie minutę).
`backtest` per kiekvieną dieną leidžia visą botą: skenerį, įėjimą, Time Machine,
dydį, rizikos patikrą ir paper brokerį. Kiekviena diena mato tik savo ir
ankstesnius barus. Rezultatai rodomi pulto skirtuke „Sandoriai“ ir faile
`runs/backtest/bot/backtest.json`.

Du apribojimai:
- **Naujienų filtras praleidžiamas,** nes senų antraščių gauti negalima.
- **Pirmos 20 dienų neprekiaujamos.** Jos tik užpildo Time Machine istoriją,
  todėl lieka apie 39 dienas.

Su 40 akcijų backtest trunka apie 15–30 min.

## Nustatymų palyginimas

```powershell
python -m tradingbot fetch
python -m tradingbot sweep --dashboard
```

`fetch` reikia paleisti iš naujo, kad atsisiųstų ir SPY (rinkos filtrui).

`sweep` per kelias sekundes palygina 96 variantus:

| Kas keičiama | Variantai |
| --- | --- |
| stop | `or-low`, `or-mid` |
| target | 1R, 2R, 3R |
| uždarymas | 11:00, 15:55 |
| filtras: apyvarta | pramušimo baro apyvarta ≥ 1.5× intervalo vidurkio |
| filtras: rinka | tik kai SPY kyla |
| filtras: tendencija | tik akcijos virš 20 dienų vidurkio |

Kiekvienas filtras bandomas įjungtas ir išjungtas.

Lentelė rodoma pulto skirtuke „Sandoriai“:

- **Kas reitinguojama.** Tik variantai su bent 20 sandorių, surikiuoti pagal
  vidutinį R. Mažesni pavyzdžiai rodomi atskirai, be reitingo.
- **Stulpelis t** rodo vidutinį R, padalytą iš jo paklaidos. Kai |t| < 2,
  rezultatas gali būti atsitiktinis.

Filtrus galima naudoti ir tikrame bote: nustatymuose (pulte) arba `[filters]` skiltyje
(`min_volume_x`, `market_up`, `above_ma20`, `max_gap_pct`). Bote ir backtest'e
veikia tos pačios taisyklės.

Tai **greitas režimas**: tos pačios skills funkcijos vienoje programoje, be
Time Machine ir naujienų filtrų. Testas tikrina, kad jis daro tuos pačius
sandorius kaip pilnas backtest. Pilnas, lėtas palyginimas: `--full`.

Savo variantai (raktai `stop`, `target`, `flat_by`, `vol`, `market`, `ma20`, `gap`):

```powershell
python -m tradingbot sweep --variants "stop=or-low,target=2R,flat_by=11:00,vol=2,market=on;stop=or-low,target=2R,gap=1"
```

Geriausias iš 96 variantų per tas pačias dienas iš dalies yra atsitiktinumas.
Prieš juo pasitikėdami, išbandykite jį kelias savaites paper režimu.

## Live režimas

Nustatymuose pasirinkite „Live su patvirtinimu“. Botas:

1. **Paruošia bilietą** ir praneša.
2. **Laukia jūsų patvirtinimo.** Claude chate paleidžiate `/trader-broker`,
   atsakote „taip“ ir paspaudžiate „Allow once“.
3. **Pradeda sekti poziciją,** kai `trader/state/broker.json` atsiranda įrašas
   `placed`. Jį įrašo `ticket.py result`.
4. **Praneša,** kai kaina pasiekia stop arba target ir kai ateina laikas uždaryti.
   Pats orderių nesiunčia.
5. **Uždaro sandorį žurnale,** kai įrašote pardavimą su `ticket.py fill`.

## Pranešimai

Faile `config/bot.toml` (arba pulto nustatymuose) užpildykite bent vieną kanalą:

| Kanalas | Ką įrašyti nustatymuose | Slaptas raktas (tik aplinkos kintamajame) |
| --- | --- | --- |
| **ntfy** | `ntfy_topic`: ilga, atsitiktinė tema | nereikia |
| **Telegram** | `telegram_chat_id` | `TELEGRAM_BOT_TOKEN` |
| **El. paštas** | `email_to`, `smtp_host`, `smtp_user` | `SMTP_PASSWORD` |

- **ntfy** yra paprasčiausias: įdiekite programėlę ntfy telefone ir užsiprenumeruokite
  tą pačią temą. Kas žino temos pavadinimą, tas mato pranešimus, todėl rinkitės ilgą
  atsitiktinę.
- **Telegram:** žetoną gausite iš @BotFather.

```powershell
$env:TELEGRAM_BOT_TOKEN = "123:ABC..."
python -m tradingbot notify-test
```

Replay ir backtest pranešimų nesiunčia.

## Paper brokerio taisyklės

- **Įėjimas:** plano įėjimo kaina (signalinio baro uždarymas) plius slippage
  (numatytas 5 bps).
- **Kiekvienas uždarytas 5 min. baras:** pirma tikrinamas stop, tada target.
  Baras, kuris atsidaro žemiau stop, įvykdomas atidarymo kaina.
- **Uždarymas:** iki 15:55 ET arba pulto mygtuku, paskutinio baro uždarymo
  kaina minus slippage.
- **Įrašai:** tokie patys, kokius daro `trader-broker`, todėl `trader-risk`
  dienos limitai galioja ir paper režime.

## Testai

```bash
PYTHONPATH=src python3 -m unittest discover -s tests     # arba: pytest
ruff check . && ruff format --check src tests
```

## Saugumas

- **Paskirtis.** Tai mokymosi ir tyrimų įrankis, ne finansinė konsultacija.
- **Pultas.** Pasiekiamas tik iš šio kompiuterio (127.0.0.1). Jo mygtukai
  priima tik JSON užklausas, todėl kita svetainė jų paspausti negali.
- **Git.** `runs/`, `trader/` ir `config/bot.toml` neįtraukiami į git.
