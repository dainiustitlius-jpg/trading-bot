# trading-bot

Dienos prekybos botas JAV akcijoms, kuris naudoja 11 `trader-*` Claude skills
(`.claude/skills/`, iš [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), MIT)
kaip skaičiavimo variklį. Planas: [docs/PLAN.md](docs/PLAN.md).

Šiuo metu (1–2 etapai): botas atlieka vieną pilną patikrą. Ji eina per visą
grandinę `scanner -> news -> entry -> exit -> timemachine -> budget -> risk`
ir sustoja ties orderiu, kurį `trader-risk` praleido (PASS).
**Orderių botas dar nesiunčia.**

## Paleidimas Windows (PowerShell)

Reikia [Python 3.11+](https://www.python.org/downloads/) (diegiant pažymėkite
„Add python.exe to PATH“) ir [Git](https://git-scm.com/download/win).

```powershell
cd $HOME
git clone -b claude/eloquent-hawking-x84hhz https://github.com/dainiustitlius-jpg/trading-bot.git
cd trading-bot

Copy-Item config\bot.example.toml config\bot.toml
$env:PYTHONPATH = "src"                                    # galioja tik šiame lange
$env:SEC_USER_AGENT = "Vardas Pavardė jus@pavyzdys.lt"     # nebūtina: 8-K pranešimams

python -m tradingbot doctor
python -m tradingbot pass --now "2026-10-19 10:15" --data tests/fixtures/replay-2026-10-19 --workspace runs/replay
python -m unittest discover -s tests
```

Jei `python` atidaro Microsoft Store, naudokite `py` vietoj `python`.

## Paleidimas Linux / macOS

Reikia Python 3.11 ar naujesnio. Išorinių priklausomybių nėra.

```bash
cp config/bot.example.toml config/bot.toml     # ir pasitaisykite limitus
export SEC_USER_AGENT="Vardas Pavardė jus@pavyzdys.lt"   # 8-K pranešimams trader-news

PYTHONPATH=src python3 -m tradingbot doctor       # aplinkos patikra
PYTHONPATH=src python3 -m tradingbot pass         # viena patikra dabar (gyvi Yahoo duomenys)
PYTHONPATH=src python3 -m tradingbot evaluate TSLA
```

Arba `pip install -e .` ir tada komanda `tradingbot ...`.

## Replay (be interneto)

`--now` užfiksuoja New York laikrodį, `--data` nurodo išsaugotus duomenis:

```bash
PYTHONPATH=src python3 -m tradingbot pass --now "2026-10-19 10:15" \
    --data tests/fixtures/replay-2026-10-19 --workspace runs/replay
```

Duomenų aplanko struktūra aprašyta `src/tradingbot/skills.py` (`ReplayData`).

## Testai

```bash
PYTHONPATH=src python3 -m unittest discover -s tests     # arba: pytest
ruff check . && ruff format --check src tests
```

`tests/test_pipeline_replay.py` atkartoja skills README pavyzdį: TSLA,
2026-10-19, įėjimas 364.18, stop 358.32, target 370.04 (1R), 8 akcijos, PASS.

## Saugumas

Tai mokymosi ir tyrimų įrankis, ne finansinė konsultacija. Pagal nutylėjimą
naudojamas paper režimas. Live režime orderį visada patvirtinate jūs pats per
`/trader-broker`. `trader/`, `runs/` ir `config/bot.toml` neįtraukiami į git.
