<!-- Поддерживаемый сообществом перевод docs/getting-started.md (исходный коммит: a7b9cee) — английская версия является основной. -->

# Начало работы

<!-- Installation section synchronized with docs/getting-started.md at e19f502. -->

## Установка

Используйте Python 3.10–3.12 и полный SHA проверенного коммита. Восемь собственных пакетов пока не опубликованы в PyPI как полный комплект; устанавливайте их вместе из репозитория с помощью установщика по манифесту. В комплект входят панель управления, мастер, каналы, сервер MCP, пакеты знаний и обучения. `release-runtime` предоставляет совместимый с OpenAI клиент, а `evidence-analysis` — средства чтения PDF и таблиц.

macOS / Linux:

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork init
lightwork dashboard
```

В Windows PowerShell сначала замените заполнитель SHA коммита:

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip==26.2.1
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe init
.\.venv\Scripts\lightwork.exe dashboard
```

Явные пути к программам позволяют не менять политику активации PowerShell. Настройте провайдера и песочницу в мастере. Установка из исходников не создаёт сторонние учётные записи, кредиты API, движок контейнеров или кластер Kubernetes. Прежде чем использовать интеграцию для клиента, проверьте реальную разрешённую операцию.

Проверьте комплект пакетов с помощью Python из того же окружения:

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```

В Windows используйте для этих программ `.\.venv\Scripts\python.exe` и `.\.venv\Scripts\lightwork.exe`.

Для аутентифицированной установки настольного приложения получите `deploy/desktop/install.sh` или `deploy/desktop/install.ps1` из проверенного коммита и задайте в `MAVERICK_REF` его полный SHA в нижнем регистре. Это по-прежнему установка из исходников, требующая доступа к репозиторию и компонентов платформы, а не автономный подписанный установщик продукта. Скрипты отклоняют изменяемые ссылки и не используют публичный индекс как запасной источник собственных пакетов. Переменные `MAVERICK_*` также можно записывать как `LIGHTWORK_*`.

## Первый запуск

```bash
maverick init
```

Мастер настройки занимает около 2 минут. Он записывает `~/.maverick/config.toml` и `~/.maverick/.env`.

Затем:

```bash
maverick start "Build a CLI that emails me a digest of today's top Hacker News stories — research the API, write it, and verify it runs"
```

## Наблюдайте, как рой декомпозирует цель

Запустите `maverick monitor` во втором терминале. Оркестратор планирует цель, а затем порождает специализированных субагентов, работающих параллельно: здесь исследователь разбирается с API, кодер пишет инструмент, а верификатор его запускает:

```
Goal #1 active  2m elapsed
Build a CLI that emails me a digest of today's top Hacker News stories

Plan tree
  ├─        done  #2 Research the Hacker News Firebase API
  ├─      active  #3 Write the digest CLI (fetch + format + send)
  ├─      active  #4 Verify it runs and emails a sample digest
  ├─     pending  #5 Write a short usage README

Latest episode #7 (running)  $0.0431  in=18,204 out=2,910 tools=11

Recent activity
  4s ago [researcher] decision: top stories live at /v0/topstories.json, then /v0/item/<id>.json
  3s ago [coder] tool_call: write_file hn_digest.py (118 lines)
  1s ago [verifier] tool_call: run "python hn_digest.py --dry-run" -> printed 10 stories

Cumulative spend on this DB: $0.21
```

Когда всё готово:

```bash
maverick status      # what's currently active or blocked
maverick skills      # what the swarm distilled from this run
maverick facts       # what it learned about you
```

## Пауза и возобновление

Если рою нужно что-то, на что можете ответить только вы, он приостанавливается и ставит вопрос в очередь:

```bash
maverick status
# shows: open questions: #3 (goal 1): Which dates are you traveling?

maverick answer 3 "May 15-29"
maverick resume
```

Цели переживают перезапуски. Вы можете закрыть ноутбук и вернуться завтра.

## Отмена правок файлов, сделанных запуском

С включённым журналом рабочего пространства (`[workspace] journal = true`,
либо ответив «да» на шаге *Workspace journal* мастера установки) каждый файл,
который агенты перезаписывают или создают через файловые инструменты,
фиксируется до изменения:

```bash
maverick journal list --goal 12     # что было затронуто
maverick journal diff --goal 12     # состояние до запуска и диск сейчас
maverick journal revert --goal 12   # восстановить все файлы, начиная с новейших
```

Откат записывает подписанную строку аудита `workspace_revert` и архивирует
журнал. Изменения через shell (`rm`, `mv`, перенаправления) в журнал не
попадают — эти команды фиксирует аудиторский след.

## Создание собственного специалиста из наблюдаемой задачи

Вам не обязательно описывать работу словами — вы можете её показать. Запишите
упорядоченный журнал того, как кто-то выполняет работу (предпринятые действия и
любые пояснения, почему именно так), в формате JSONL или простого текста с
префиксами, а затем передайте файл Lightwork:

```
ACTION[gmail]: send the morning digest -> ops@acme.com
NOTE: only the top 5 stories, with one-line summaries
SEE: digest looks right, ops confirmed receipt
```

```bash
maverick learn-demo demo.txt
```

Это разбирает демонстрацию, выводит черновик специалиста, показывает вам
полученный рабочий процесс и ждёт вашего одобрения, прежде чем сохранить. Секреты
скрываются ещё на входе, а черновик наследует тот же зажим возможностей и
проверку персоны, что и описанный пак, — ничего не активируется без вашего
согласия. Полезные флаги:

```bash
maverick learn-demo demo.txt --name "Morning Digest" --no-llm --yes
```

`--no-llm` детерминированно повторяет наблюдаемые шаги (инструменты = то, чем
пользовался человек); уберите его, чтобы модель предложила вариант на основе
транскрипта.

Тот же процесс фабрики агентов запускается, когда вы собираете пак в режиме
диалога:

```bash
maverick onboard
```

После одобрения `onboard` теперь обеспечивает пак всем необходимым — он
устанавливает навыки из каталога, которые нужны его рабочему процессу, и
синтезирует любые объявленные инструменты, которых нет среди встроенных, так что
только что одобренный специалист готов к работе с первого запуска. (Этот шаг
учитывает конфигурацию `[self_learning]` / `provision_packs` и никогда не
расширяет зажатую оболочку прав пака.)

## Голосовой ввод (встроенный, офлайн)

Кнопка микрофона в панели и инструмент `transcribe_audio` работают без ключа
провайдера: локальный движок Whisper поставляется вместе с пакетом панели, а
модель (с проверкой контрольной суммы) скачивается автоматически при первом
запуске панели (пропускается на развёртываниях с блокировкой egress или при
`[voice] auto_fetch_model = false`). Подробности — в CLI:

```bash
maverick voice setup    # скачивает модель с проверкой контрольной суммы (~148 МБ)
maverick voice status   # показывает, какие backend'ы распознавания речи доступны
maverick voice transcribe clip.wav   # быстрая проверка конвейера
```

Предпочитаете системный движок? Бинарник `whisper.cpp` в PATH
(`brew install whisper-cpp`) или faster-whisper встраиваются в ту же
цепочку. Задайте `[voice] stt_backend = "local"`, чтобы аудио не покидало
машину даже при настроенных ключах OpenAI/Groq.

## Смена моделей и провайдеров

Перезапускайте мастер настройки в любой момент:

```bash
maverick init
```

Или отредактируйте `~/.maverick/config.toml` напрямую. Секция `[models]` сопоставляет каждой роли агента строку вида `provider:model-id`. Схема описана в [`configuration.md`](../../configuration.md).

## Где хранятся данные

| Файл | Что это |
|---|---|
| `~/.maverick/config.toml` | Ваша конфигурация (развёртывание, модели, безопасность, бюджет) |
| `~/.maverick/.env` | API-ключи (chmod 600) |
| `~/.maverick/world.db` | Персистентная модель мира: цели, факты, эпизоды |
| `~/.maverick/skills/` | Файлы SKILL.md, автоматически извлечённые из успешных запусков |
| `~/maverick-workspace/` | Рабочий каталог песочницы по умолчанию |
| `~/.maverick/learned-skills/` | Навыки, извлечённые циклами обучения |
| `~/.maverick/dreams/` | Сводные инсайты, очередь репетиций, снимки обучения |

Все данные хранятся локально. Никуда ничего не передаётся, кроме ваших промптов выбранной вами облачной LLM.

Когда у вас за плечами будет несколько запусков, поверхность обучения сводится к
четырём командам: `maverick dream` (консолидировать опыт), `maverick hindsight`
(помогло обучение или, наоборот, ухудшило результат?), `maverick proof`
(результаты работы, сэкономленные затраты, ROI) и `maverick domains-lint`
(аудит каталога специалистов из 2,020 агентов), а также `maverick domains-audit`
(состояние управления: до чего может дотянуться каждый агент, что запрещает и в
чём отказывает) и `maverick domains-eval --check` (эталонные поведенческие
случаи).
