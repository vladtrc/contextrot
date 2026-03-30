# Sandbox

Единый UV-проект для экспериментов, черновых расчётов и генерации картинок под разные посты.

Идея такая: `sandbox` держит один общий `uv`-env, а внутри лежат отдельные подпроекты по темам статей.

Структура:

- `main.py` - нейтральная стартовая точка для проверки окружения
- `jl_projection/` - подпроект под пост о случайных проекциях и JL-лемме
- `jl_projection/images/` - графики и картинки для поста
- `jl_projection/data/` - промежуточные данные
- `jl_projection/notebooks/` - notebook-like артефакты, если решишь идти через Jupyter

Запуск:

```bash
cd sandbox
UV_CACHE_DIR=.uv-cache uv sync
UV_CACHE_DIR=.uv-cache uv run python main.py
```

Сборка подпроекта:

```bash
cd sandbox
make jl_projection
```
