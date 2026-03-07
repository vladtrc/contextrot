---
layout: post
title: "Языки для AI-first разработки: победит не самый быстрый"
date: 2026-03-06
---

У программистов любимый спорт: спорить какой язык лучше.

У AI-эпохи этот спор другой: какой язык лучше не для вас, а для связки "агент + ревью-агент + CI".

На первый взгляд ответ очевиден: компилируемые языки.

Цикл "пиши -> запускай тесты -> чини пока зелёное" в них действительно кайфовый. Если проект собрался, огромный класс ошибок уже отстрелен. Кажется, что это автоматом делает Go/Rust/Java лучшими для AI-first.

И вот тут начинается контринтуитивное.

## Сюрприз из бенчмарков

Tencent в AutoCodeBench (arXiv:2508.09101, релиз v1 от 12 августа 2025, обновления v2 от 17 февраля 2026) прогнал модели по 20 языкам на сложных задачах.

И там нет истории "чем строже компилятор, тем выше результат".

В reasoning-режиме у Claude Opus 4 в таблице по языкам:

- Elixir: 80.3
- C#: 74.9
- Kotlin: 72.5
- Java: 55.9
- Python: 40.3
- TypeScript: 47.2
- Rust: 38.7

Даже их "Current Upper Bound" по языкам показывает ту же странность:

- Elixir: 97.5
- C#: 88.4
- Kotlin: 89.5
- Python: 63.3
- Rust: 61.3
- TypeScript: 61.3

Если бы дело было только в "компилируется / не компилируется", картина была бы другой.

## Почему интуиция "компилятор решает всё" ломается

AI-кодинг не одношаговый. Это цикл с обратной связью.

В том же AutoCodeBench есть секция про multi-turn refinement: когда модели дают ошибки исполнения и разрешают дорабатывать решение в несколько итераций. Результат: DeepSeek-V3-0324 растёт с 48.1 до 59.7 за три раунда, Qwen2.5-Coder-32B-Instruct с 35.8 до 47.4.

То есть выигрывает не "язык сам по себе", а качество и плотность feedback loop.

## А теперь самое неудобное для фанатов "только компиляция"

Python с жёстким режимом типизации и проверок уже не тот "скриптик без гарантий", который обычно высмеивают в тредах.

`mypy` прямо формулирует цель: довести кодовую базу до `mypy --strict`, чтобы типовые ошибки не проходили без явного обхода (`type: ignore` и т.д.).  
`pyright` даёт режимы `basic/standard/strict` и тонкую настройку диагностик.  
`TypeScript` с `strict` включает семейство строгих проверок и усиливает гарантии корректности.

Это не делает Python/TS равными Rust по модели памяти и runtime-свойствам. Но в AI-first сценарии "агент пишет и сам себя чинит по сигналам линтера/чекера/тестов" разрыв по практической надёжности резко сужается.

И это не только теория: в эмпирическом исследовании на Python-проектах введение type annotations ассоциировано со снижением дефектов примерно на 15%.

## Почему с Rust у агентов часто "дорого по итерациям"

Важно: это не "Rust плохой язык". Это "Rust + текущие LLM-агенты = дорогой цикл правок" в части задач.

Свежий официальный State of Rust 2025 (опубликован 2 марта 2026) показывает:

- `Slow compilation`: 27.29% респондентов называют это большой проблемой для продуктивности, ещё 54.68% говорят "можно улучшить".
- `Borrow checker not allowing valid code`: 9.45% "big problem", ещё 42.79% "could be improved".

Для human-only разработки это терпимо и часто окупается гарантиями безопасности.
Для AI-first контура это болезненнее: агент делает больше итераций, и каждая итерация дороже по времени.

Показательно, что для Rust уже появилась отдельная линия исследований "LLM чинит компиляционные ошибки Rust". В работе RustAssistant (ICSE 2025) даже специализированный подход с итерациями "LLM <-> rustc" даёт peak accuracy около 74% на реальных ошибках сборки.

То есть проблема настолько практическая, что под неё уже делают отдельные инструменты.

И это видно не только в академии. В инженерном кейсе ilert (24 февраля 2026, production-команда, Cursor + Rust) они прямо пишут, что Rust "notoriously difficult for modern LLMs", отдельно выделяют struggle с lifetime в async-коде и отмечают, что без жёстких rule-файлов агент склонен генерировать лишние `clone`/`mutex` и на этом деградирует архитектура.

## Реально важные параметры языка для AI-first

1. Скорость итерации по обратной связи (`compile/lint/typecheck/test`).
2. Простота синтаксиса (меньше двусмысленности, меньше токенов на "шум").
3. Качество тулинга (LSP, диагностики, предсказуемые ошибки).
4. Объём и чистота обучающих примеров.
5. Доступность документации.

И вот пятый пункт внезапно может быть главным.

## Неочевидный вывод

Лучше всего для агентов работают не языки "самые умные", а языки/экосистемы, где договорённости записаны рядом с кодом в машиночитаемом виде.

Примеры:

- Elixir: документация встроена как first-class практика (`@moduledoc`, `@doc`), и может извлекаться из байткода (`Code.fetch_docs/1`).
- C#: XML doc comments, компилятор собирает их в XML, можно включить предупреждения по недокументированным публичным API.
- Go: у exported-символов ожидаются doc comments, и тулчейн (`go doc`, `pkg.go.dev`, `gopls`) это системно использует.

Когда у API есть "человеческая" спецификация прямо в исходниках, модель тратит меньше попыток на угадывание контракта класса/библиотеки. И это напрямую конвертируется в меньшее количество итераций.

Идея шире программирования: в системах с агентами побеждает не тот, кто быстрее считает, а тот, кто дешевле коммуницирует намерение.

## Итого для практики

В AI-first мире выбор языка всё меньше про культ "моего любимого компилятора" и всё больше про дизайн среды обратной связи.

Поэтому вопрос "какой язык лучше для AI" стоит переформулировать:

не "где быстрее runtime", а "где дешевле цикл ошибка -> объяснение -> исправление".

Победить может язык, который вы вчера называли "не самым серьёзным", если у него более строгие договорённости, лучше docs surface и чище tooling feedback.

## Пруфы

- AutoCodeBench paper (arXiv): https://arxiv.org/abs/2508.09101
- AutoCodeBench HTML (таблицы, language Pass@1): https://ar5iv.labs.arxiv.org/html/2508.09101v1
- AutoCodeBench homepage (обновление v2 от 17 февраля 2026): https://autocodebench.github.io/
- 2025 State of Rust Survey (официально, 2 марта 2026): https://blog.rust-lang.org/2026/03/02/2025-State-Of-Rust-Survey-results/
- State of Rust chart: `which-problems-limit-your-productivity.png`: https://blog.rust-lang.org/2026/03/02/2025-State-Of-Rust-Survey-results/which-problems-limit-your-productivity.png
- RustAssistant (ICSE 2025, Microsoft Research): https://www.microsoft.com/en-us/research/publication/rustassistant-using-llms-to-fix-compilation-errors-in-rust-code/
- ilert engineering case study (Cursor + Rust struggles, Feb 24, 2026): https://www.ilert.com/blog/scaling-rust-development-with-cursor-ilert
- TypeScript `strict`: https://www.typescriptlang.org/tsconfig/strict.html
- mypy, `--strict` и внедрение в существующий код: https://mypy.readthedocs.io/en/stable/existing_code.html
- mypy CLI strict flags: https://mypy.readthedocs.io/en/stable/command_line.html
- Pyright configuration (`typeCheckingMode`, `strict`): https://raw.githubusercontent.com/microsoft/pyright/main/docs/configuration.md
- Empirical study: type annotations and defects in Python: https://openreview.net/forum?id=ewODkUrdth
- Elixir docs as first-class (`@doc`, `Code.fetch_docs/1`): https://hexdocs.pm/elixir/writing-documentation.html
- C# XML documentation comments: https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/xmldoc/
- Go doc comments and tooling: https://go.dev/doc/comment
