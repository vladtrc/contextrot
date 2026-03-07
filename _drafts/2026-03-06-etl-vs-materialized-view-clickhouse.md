---
layout: post
title: "7 способов обновить данные в ClickHouse: когда какой использовать"
date: 2026-03-06
---

ClickHouse проектировался для append-only аналитики. Но данные в реальном мире меняются, и обновлять их всё равно нужно. Проблема в том, что способов это сделать минимум семь, и каждый с подводными камнями.

## 1. ALTER TABLE DELETE + INSERT (мутации)

Самый "понятный" паттерн: удалил старое, вставил новое.

```sql
ALTER TABLE metrics DELETE WHERE org_id = 'abc' AND date = '2026-03-01';
INSERT INTO metrics SELECT ... FROM raw_data WHERE org_id = 'abc' AND date = '2026-03-01';
```

Механика: `ALTER TABLE DELETE` — это мутация. ClickHouse находит все parts с подходящими строками и **переписывает их целиком**, исключая удалённые строки. Все колонки всех затронутых parts перезаписываются на диск.

По умолчанию мутации выполняются **асинхронно**. Между DELETE и INSERT данные могут быть в промежуточном состоянии.

**Когда использовать:** разовые исправления данных, compliance (GDPR). Не для регулярного ETL.

**Подводные камни:**
- Тяжёлый I/O — перезаписываются все колонки в затронутых parts
- Асинхронность — `mutations_sync = 0` по умолчанию, данные невидимо "лагают"
- Мутации выполняются последовательно — очередь забивается

[ALTER TABLE DELETE docs](https://clickhouse.com/docs/en/sql-reference/statements/alter/delete)

## 2. Staging + REPLACE PARTITION (атомарная замена)

Самый надёжный паттерн для batch ETL.

```sql
-- 1. Считаем в staging-таблицу (идентичная структура)
INSERT INTO metrics_staging SELECT ... FROM raw_data WHERE date = '2026-03-01';

-- 2. Атомарно меняем партицию
ALTER TABLE metrics REPLACE PARTITION '2026-03-01' FROM metrics_staging;

-- 3. Чистим staging
TRUNCATE TABLE metrics_staging;
```

Механика: партиция в target заменяется файлами из staging за одну атомарную операцию. Читатели **никогда** не видят промежуточное состояние — ни пустой партиции, ни частичных данных.

**Когда использовать:** регулярный ETL с пересчётом данных по партициям (день, месяц, tenant).

**Подводные камни:**
- Staging-таблица должна иметь **идентичную** схему: partition key, ORDER BY, PRIMARY KEY, storage policy, индексы
- Одна операция = одна партиция. Для замены 3 партиций нужно 3 операции (не полностью атомарно)
- Данные в staging **не удаляются** после REPLACE — чистите руками

**Почему лучше DELETE + INSERT:**

| | DELETE + INSERT | REPLACE PARTITION |
|---|---|---|
| Атомарность | Нет — окно между delete и insert | Да — одна операция |
| I/O | Перезапись parts при DELETE + запись при INSERT | Только перемещение файлов |
| Видимость для читателей | Могут увидеть пустой период | Всегда консистентно |

[ALTER TABLE PARTITION docs](https://clickhouse.com/docs/sql-reference/statements/alter/partition)

## 3. Incremental Materialized View

"Посчитать при записи" вместо "потом посчитаем".

```sql
CREATE MATERIALIZED VIEW hourly_stats_mv
TO hourly_stats
AS SELECT
    toStartOfHour(timestamp) AS hour,
    count() AS events,
    sum(value) AS total
FROM raw_events
GROUP BY hour;
```

Механика: при каждом INSERT в `raw_events` MV-запрос выполняется **только на вставленном блоке** (не на всей таблице) и записывает результат в target.

**Когда использовать:** append-only поток + простая агрегация по одной таблице.

**Подводные камни:**
- **JOIN триггерится только по левой таблице.** Если правая (dimension) таблица обновилась — MV не пересчитается. Это ломает ожидания "MV всё сделает сама".
- MV видит только вставленный блок, нет доступа к историческим данным
- Если MV-запрос упадёт — INSERT в исходную таблицу тоже упадёт

```sql
-- Этот MV НЕ обновится, когда изменится products:
CREATE MATERIALIZED VIEW sales_enriched_mv TO ... AS
SELECT s.*, p.category
FROM sales s
JOIN products p ON s.product_id = p.id;
-- ^ products обновились? MV не знает об этом.
```

[Incremental MV docs](https://clickhouse.com/docs/materialized-view/incremental-materialized-view)

## 4. Refreshable Materialized View

Полный пересчёт по расписанию. Близкий аналог dbt-модели внутри базы.

```sql
CREATE MATERIALIZED VIEW daily_report_mv
REFRESH EVERY 1 HOUR
TO daily_report
AS SELECT
    date,
    sum(revenue) AS total_revenue,
    count(DISTINCT user_id) AS unique_users
FROM orders o
JOIN products p ON o.product_id = p.id
GROUP BY date;
```

Механика: весь SELECT перевыполняется по расписанию. Результат **атомарно** заменяет содержимое target-таблицы. Есть режим `APPEND` для накопления.

**Когда использовать:** сложные JOIN'ы из нескольких таблиц, допустим refresh lag (минуты-часы).

**Подводные камни:**
- Полный пересчёт каждый раз — дорого на больших таблицах
- Интервал по wall-clock, не по факту прихода данных
- Относительно новая фича — тестируйте в своём окружении

```sql
-- Ручной trigger:
SYSTEM REFRESH VIEW daily_report_mv;
-- Мониторинг:
SELECT * FROM system.view_refreshes;
```

[Refreshable MV docs](https://clickhouse.com/docs/materialized-view/refreshable-materialized-view)

## 5. ReplacingMergeTree: OPTIMIZE FINAL vs SELECT FINAL

Не совсем "способ обновления", а движок с встроенной дедупликацией.

```sql
CREATE TABLE users (
    user_id UInt64,
    name String,
    updated_at DateTime
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY user_id;

-- "Обновить" = вставить новую версию
INSERT INTO users VALUES (1, 'Alice Updated', now());
```

Механика: при фоновых merge'ах строки с одинаковым `ORDER BY` ключом схлопываются — остаётся строка с максимальным `updated_at`. **Между merge'ами дубли сосуществуют.**

Два способа "увидеть" дедуплицированные данные:

```sql
-- Вариант A: OPTIMIZE FINAL — принудительный merge, дорого
OPTIMIZE TABLE users FINAL;
SELECT * FROM users WHERE user_id = 1;

-- Вариант B: SELECT FINAL — дедупликация на лету при чтении
SELECT * FROM users FINAL WHERE user_id = 1;
```

**Сравнение:**

| | OPTIMIZE TABLE FINAL | SELECT ... FINAL |
|---|---|---|
| Что делает | Физически мёржит parts на диске | Дедуплицирует при чтении |
| Стоимость | Перезапись данных, тяжёлый I/O | CPU при каждом запросе |
| Когда эффект | После завершения (минуты) | Мгновенно |
| Рекомендация ClickHouse | [Avoid Optimize Final](https://clickhouse.com/docs/optimize/avoidoptimizefinal) | Предпочтительно |

**Критическая настройка:** `do_not_merge_across_partitions_select_final = 1`. По тестам Altinity снижает время FINAL-запросов с 9s до 1.25s.

[ReplacingMergeTree docs](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree) | [Avoid Optimize Final](https://clickhouse.com/docs/optimize/avoidoptimizefinal)

## 6. Lightweight DELETE (DELETE FROM)

```sql
DELETE FROM metrics WHERE user_id = 42 AND date < '2026-01-01';
```

Механика: не перезаписывает parts. Проставляет маску `_row_exists = False` — последующие SELECT'ы фильтруют эти строки. Физическое удаление происходит при следующем фоновом merge.

**Сравнение с ALTER TABLE DELETE:**

| | DELETE FROM (lightweight) | ALTER TABLE DELETE (mutation) |
|---|---|---|
| Скорость | Быстро — пишет только маску | Медленно — перезаписывает parts |
| Видимость | Мгновенная | После завершения мутации |
| Физическое удаление | При следующем merge | Сразу |
| I/O | Минимальный | Тяжёлый |

**Когда использовать:** стандартное удаление строк. ClickHouse рекомендует как default-вариант.

**Подводные камни:**
- Не работает с projections без `lightweight_mutation_projection_mode`
- Массовые DELETE деградируют производительность SELECT (проверка маски)
- Для bulk-удаления по партиции `ALTER TABLE DROP PARTITION` быстрее обоих вариантов

[Lightweight DELETE docs](https://clickhouse.com/docs/sql-reference/statements/delete)

## 7. CollapsingMergeTree / VersionedCollapsingMergeTree

Самый сложный, но самый эффективный для high-throughput CDC.

```sql
CREATE TABLE balances (
    user_id UInt64,
    balance Decimal(18,2),
    Sign Int8,
    Version UInt32
) ENGINE = VersionedCollapsingMergeTree(Sign, Version)
ORDER BY user_id;

-- Обновить баланс: вставить отмену + новое значение
INSERT INTO balances VALUES
    (1, 100.00, -1, 1),  -- отменяем старое
    (1, 150.00,  1, 2);  -- вставляем новое
```

Механика: строки с одинаковым ключом, одинаковой версией и противоположными `Sign` схлопываются при merge. **Все запросы должны учитывать Sign:**

```sql
-- Правильно:
SELECT user_id, sum(Sign * balance) FROM balances GROUP BY user_id HAVING sum(Sign) > 0;
-- Неправильно:
SELECT * FROM balances; -- покажет и отмены, и дубли
```

**Когда использовать:** CDC из OLTP-баз с десятками тысяч обновлений в секунду.

**Подводные камни:**
- Приложение **обязано** хранить предыдущее состояние для генерации cancel-строк
- Все агрегации через `sum(Sign * value)` — забыл `Sign` = неправильные данные
- `VersionedCollapsing` >> `Collapsing` — не зависит от порядка вставки

[VersionedCollapsingMergeTree docs](https://clickhouse.com/docs/engines/table-engines/mergetree-family/versionedcollapsingmergetree)

## Итоговая таблица

| Паттерн | Частота | Латентность | Сложность | Лучший для |
|---|---|---|---|---|
| ALTER DELETE + INSERT | Редко | Минуты | Низкая | Разовые исправления |
| REPLACE PARTITION | Batch | Мгновенно (атомарно) | Средняя | ETL по партициям |
| Incremental MV | Непрерывно | Real-time | Низкая-средняя | Агрегации одной таблицы |
| Refreshable MV | Периодически | Минуты-часы | Низкая | Сложные JOIN'ы, витрины |
| ReplacingMergeTree | Непрерывно | Eventual | Средняя | UPSERT/CDC с версиями |
| Lightweight DELETE | Ad-hoc | Мгновенно (логически) | Низкая | Удаление строк |
| Collapsing engines | Непрерывно | Eventual | Высокая | High-throughput CDC |

## Как выбрать

Начните с вопроса: **данные приходят append-only или их нужно перезаписывать?**

Если append-only + простая агрегация — **incremental MV**. Это идиоматический ClickHouse.

Если нужен полный пересчёт партиции (пришли новые данные за вчера, нужно пересобрать) — **staging + REPLACE PARTITION**. Атомарно, предсказуемо, минимум I/O.

Если сложные JOIN'ы из нескольких таблиц и можно жить с лагом — **refreshable MV**.

Если CDC-поток из PostgreSQL/MySQL с десятками тысяч update/s — **ReplacingMergeTree** (проще) или **VersionedCollapsingMergeTree** (эффективнее, сложнее).

Если хочется "DELETE + INSERT как в PostgreSQL" — **подумайте ещё раз**. В ClickHouse это мутации, и они дорогие. Для ETL почти всегда лучше REPLACE PARTITION.
