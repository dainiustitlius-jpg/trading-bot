# trading-bot

Dienos prekybos botas JAV akcijoms, kuris naudoja 11 `trader-*` Claude skills
(`.claude/skills/`, iš [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), MIT)
kaip skaičiavimo variklį. Planas: [docs/PLAN.md](docs/PLAN.md).

Ką botas daro:

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
