# Offline demo walkthrough

The walkthrough is now the documentation site's tutorial, **Your first allocation**:
<https://apmalong.github.io/data-finops/tutorials/first-allocation.html>
(source: `site/tutorials/first-allocation.html`). A test runs its commands on every change
(`tests/integration/test_m5_exit.py`).

For a quick start, run `uv sync --extra api --frozen` in a source checkout. Then run this command:

```sh
uv run data-finops demo        # open http://127.0.0.1:8000/
```
