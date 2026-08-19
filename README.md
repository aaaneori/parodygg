# parodygg

<a id="english"></a>

**English** · [Українська](#ukrainian)

Champion win/pick/ban rate tracker for Grandmaster+Challenger solo queue on EUW.
A Python worker pulls matches from the Riot API once a day, aggregates them into
SQLite, and exports static JSON that a plain HTML/JS front end reads.

**Live site:** https://aaaneori.github.io/parodygg/

---

## How it works

```
Riot API ──> worker ──> SQLite ──> JSON export ──> git push ──> GitHub Pages
             (daily)              (docs/)
```

No server runs on the front end. The worker generates static files, commits
them, and Pages serves them — the site is just JSON plus vanilla JS.

| Module | Responsibility |
|---|---|
| `worker.py` | entry point: decides which days to collect, orchestrates |
| `riot_api.py` | HTTP, retries, rate limiting, error classification |
| `cache.py` | raw match payloads on disk, so a rerun never refetches |
| `collector.py` | match details → aggregated daily rows |
| `database.py` | schema and queries |
| `exporter.py` | database → JSON files → git push |
| `state.py` | last collected day and patch |

---

## Methodology

The decisions below are the point of the project. Each one changes the numbers.

**Sampled by lobby, not by player.** Matches are found through GM+Challenger
players, but every participant in those matches is counted. Counting only the
high-elo players themselves would systematically understate pick rate: a
champion picked by the other nine players in the lobby would be invisible.

**Percentages are computed from summed raw counts, never averaged across days.**
Storage keeps games and wins per day; rates are derived at read time.

> Day 1: 10 games, 8 wins (80%). Day 2: 90 games, 45 wins (50%).
> Averaging the daily rates gives 65%. The correct answer is 53% — 53 wins
> out of 100 games. Larger days have to carry more weight.

**Ban rate divides by matches × 2.** Each match has two ban phases, so the
denominator is twice the match count, not the match count.

**Bans belong to the champion, not the role.** You ban a champion before roles
exist. A champion played in two roles still has one ban count, which is why
bans live in their own table rather than being duplicated per role.

**Role thresholds are relative to the champion, not the patch.** On a champion
page, a role gets its own tab if it accounts for more than 5% of *that
champion's* games. Using total patch matches as the denominator would hide
secondary roles of anyone who isn't broadly popular.

**"/min" metrics average per-match ratios.** Damage per minute is computed
against each match's own duration and then averaged, which is deliberately not
the same as total damage over total minutes.

---

## Schema

Three tables, one per real entity:

```sql
daily_runs           (date, patch)                    -> matches_processed
champion_daily_bans  (date, patch, champion)          -> bans
champion_daily_stats (date, patch, champion, role)    -> games, wins, + 15 metrics
```

The key is `(date, patch)` rather than `date`: on patch-release day a single
date can legitimately hold two runs. Child tables cascade on delete, so
re-collecting a day wipes and rewrites it atomically instead of merging with a
previous attempt.

An earlier version kept all of this in one denormalized table, which forced
`DISTINCT` subqueries to undo the duplication and a pseudo-role called
`UNPICKED` for champions that were banned but never picked. Both are gone from
storage; the export still synthesizes `UNPICKED` rows so the front-end contract
stayed unchanged. `migrate_db.py` performs the one-off conversion with a backup
and full validation before commit.

---

## Reliability

- **Match cache.** Riot responses for a match never change, so they're stored
  zlib-compressed in a separate `cache.db`. A crashed run resumes instead of
  re-downloading; metrics can be recomputed for past days without touching the
  API. Trimmed to a 30-day rolling window.
- **Error classification.** 401/403 aborts the run loudly (a dead key would
  otherwise "collect" zero matches for an hour), 5xx backs off and retries, 404
  skips that one resource.
- **Per-match isolation.** One malformed match is skipped and logged; it used to
  take down the entire day's collection.
- **Patch-release days.** A collection window can span two patches — EUW plays
  the old one until ranked queues go down, the new one after maintenance. Both
  are aggregated separately and stored as two runs under the same date, so
  neither half is lost and each keeps its own denominator.
- **Backfill.** Up to 3 consecutive missed days are collected individually.
  Longer gaps are refused and logged rather than silently spending hours.
- **Logging** to a rotating file, so scheduled runs leave a trace.

---

## Tests

```bash
cd worker
python -m pytest        # 76 tests
```

Aggregation errors are silent — the numbers stay plausible and are simply
wrong. The suite pins the invariants above (ban rate denominator, no averaging
of daily percentages, bans not doubled across roles, challenge fractions scaled
to percent) plus the edge cases that have actually broken things: malformed
`gameVersion`, missing `teamPosition`, absent `challenges`, corrupted cache
entries.

One test documents known behaviour rather than desired behaviour: a match that
fails mid-parse still counts toward the denominator.

---

## Running it

```bash
cd worker
cp .env.example .env          # add your Riot API key and site folder path
pip install -r requirements.txt
python worker.py
```

The worker collects the previous full day (00:00–00:00), writes to
`champions.db`, exports JSON into `docs/`, and pushes. A scheduled task runs it
once a day.

---

## Known limitations

- **Unassigned lanes.** Riot occasionally can't determine a lane (~0.9% of
  matches) and sends an empty `teamPosition`. Those participants are skipped
  rather than guessed at, which slightly understates role-level totals.
- **Single region and queue.** EUW ranked solo/duo only. A personal Riot API key
  is limited per minute, so collection time scales directly with the sample:
  every extra region or lower rank adds hours to a run that already takes 30-40
  minutes. Going wider means a production key with higher limits.

<br>

---

<br>

<a id="ukrainian"></a>

[English](#english) · **Українська**

Трекер вінрейту, пікрейту та банрейту чемпіонів League of Legends для рангу
Grandmaster+Challenger, соло/дуо черга, сервер EUW. Раз на добу Python-воркер
забирає матчі з Riot API, зводить їх у SQLite і вивантажує статичний JSON, який
читає звичайний HTML/JS фронтенд.

**Живий сайт:** https://aaaneori.github.io/parodygg/

---

## Як це працює

```
Riot API ──> воркер ──> SQLite ──> експорт JSON ──> git push ──> GitHub Pages
             (щодоби)              (docs/)
```

Серверної частини у фронтенду немає взагалі. Воркер сам формує статичні файли,
комітить їх у репозиторій, а GitHub Pages роздає — сайт складається з JSON і
чистого JavaScript.

| Модуль | За що відповідає |
|---|---|
| `worker.py` | точка входу: визначає, які дні збирати, керує процесом |
| `riot_api.py` | запити, повтори, ліміти, розбір помилок |
| `cache.py` | сирі матчі на диску, щоб повторний запуск не качав те саме |
| `collector.py` | деталі матчів → зведені денні рядки |
| `database.py` | схема і запити |
| `exporter.py` | база → JSON-файли → git push |
| `state.py` | останній зібраний день і патч |

---

## Методологія

Саме ці рішення й визначають, які цифри побачить користувач.

**Рахуємо всю гру, а не лише топових гравців.** Матчі шукаються через гравців
Grandmaster і Challenger, але враховуються всі десятеро учасників такої гри.
Якби рахувалися тільки самі топові гравці, пікрейт виходив би заниженим:
чемпіон, якого взяли інші девʼятеро в лобі, взагалі не потрапив би до
статистики.

**Відсотки рахуються з накопичених сирих чисел і ніколи не усереднюються по
днях.** База зберігає кількість ігор і перемог за кожну добу, а відсотки
рахуються вже під час читання.

> Перший день: 10 ігор, 8 перемог — це 80%. Другий: 90 ігор, 45 перемог — 50%.
> Якщо усереднити ці два відсотки, вийде 65%. Насправді ж перемог 53 зі 100
> ігор, тобто 53%. День, у якому ігор більше, має важити більше.

**Банрейт ділиться на подвоєну кількість матчів.** У кожній грі дві команди
банять окремо, тож у знаменнику стоїть кількість матчів, помножена на два.

**Бан стосується чемпіона, а не ролі.** Банять ще до того, як стає відомо, хто
на якій лінії гратиме. Тому чемпіон, зіграний і в лісі, і на мідлі, має одну
спільну кількість банів — саме через це бани винесені в окрему таблицю, а не
дублюються для кожної ролі.

**Поріг для ролі рахується від ігор самого чемпіона.** На сторінці чемпіона роль
отримує окрему вкладку, якщо на неї припадає більше ніж 5% ігор цього чемпіона.
Якби знаменником були всі матчі патчу, у не надто популярних чемпіонів другорядні
ролі просто зникали б із перемикача.

**Показники «за хвилину» усереднюються по матчах.** Шкода за хвилину рахується
окремо для кожної гри, з її власною тривалістю, і лише потім усереднюється. Це
свідомо не те саме, що поділити всю шкоду на всі хвилини.

---

## Схема бази

Три таблиці, кожна під свою сутність:

```sql
daily_runs           (date, patch)                    -> matches_processed
champion_daily_bans  (date, patch, champion)          -> bans
champion_daily_stats (date, patch, champion, role)    -> games, wins, + 15 метрик
```

Ключ складається з дати й патчу, а не лише з дати: у день виходу патчу на одну
дату цілком може припадати два різні збори. Дочірні таблиці видаляються
каскадно, тому повторний збір дня переписує його повністю, а не змішує з
попередньою спробою.

Спершу все це лежало в одній ненормалізованій таблиці. Через це доводилося
прибирати дублювання підзапитами з `DISTINCT` і тримати службову роль
`UNPICKED` для чемпіонів, яких банили, але жодного разу не взяли. Обидва
милиці зі сховища зникли, проте експорт і далі створює рядки `UNPICKED`, тому
для фронтенду нічого не змінилося. Разову конвертацію робить `migrate_db.py` —
з резервною копією і повною перевіркою сум перед тим, як щось записати.

---

## Надійність

- **Кеш матчів.** Зіграний матч уже не змінюється, тому відповідь Riot
  зберігається стиснутою в окремому файлі `cache.db`. Якщо запуск обірвався, він
  продовжить з місця зупинки, а метрики за минулі дні можна перерахувати взагалі
  не звертаючись до API. Кеш тримає останні 30 днів.
- **Різні помилки — різна реакція.** 401 і 403 означають, що ключ мертвий, і
  запуск одразу зупиняється: інакше воркер годину «збирав» би нуль матчів. На
  5xx він чекає і пробує ще раз, на 404 просто пропускає цей матч.
- **Один зіпсований матч не валить усе.** Раніше такий матч обривав збір за цілу
  добу; тепер він пропускається із записом у лог.
- **День виходу патчу.** Одне вікно збору може охоплювати дві версії: до
  вимкнення черг грають на старому патчі, після обслуговування — на новому.
  Обидві половини зводяться окремо і зберігаються як два запуски під однією
  датою, тож не втрачається жодна, і в кожної свій знаменник.
- **Заповнення пропусків.** Якщо воркер не працював кілька днів, він добере до
  трьох пропущених діб поспіль, кожну окремо. Довші прогалини свідомо не
  заповнюються — про це пишеться в лог.
- **Логи** пишуться у файл із щоденною ротацією, тому після нічного запуску
  завжди видно, що відбувалося.

---

## Тести

```bash
cd worker
python -m pytest        # 76 тестів
```

Помилка в підрахунках нічим себе не видає: числа лишаються схожими на правду,
хоча вони хибні. Тести закріплюють описані вище правила — знаменник банрейту,
заборону усереднювати денні відсотки, відсутність подвоєння банів по ролях,
переведення часток із `challenges` у відсотки. Окремо перевіряються випадки,
які вже колись ламали збір: пошкоджений `gameVersion`, порожній `teamPosition`,
відсутній блок `challenges`, побитий запис у кеші.

Один тест описує не бажану, а фактичну поведінку: матч, який впав під час
розбору, все одно враховується у знаменнику.

---

## Як запустити

```bash
cd worker
cp .env.example .env          # вкажіть свій ключ Riot API і шлях до папки сайту
pip install -r requirements.txt
python worker.py
```

Воркер збирає попередню повну добу — від 00:00 до 00:00, записує результат у
`champions.db`, вивантажує JSON у `docs/` і робить push. У планувальнику завдань
він запускається раз на добу.

---

## Що поки не враховано

- **Невизначена лінія.** Приблизно в 0.9% матчів Riot не може визначити, хто на
  якій лінії грав, і надсилає порожній `teamPosition`. Таких гравців краще
  пропустити, ніж вгадувати, але через це підсумки в розрізі ролей трохи
  занижені.
- **Один сервер і одна черга.** Поки що лише EUW, тільки рейтингова соло/дуо.
  Персональний ключ Riot API обмежений за кількістю запитів на хвилину, тож час
  збору росте прямо пропорційно до вибірки: кожен додатковий сервер чи нижчий
  ранг додає години до запуску, який і так триває 30-40 хвилин. Для ширшого
  покриття потрібен production-ключ із вищими лімітами.
