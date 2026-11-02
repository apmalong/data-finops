# Airflow 3 standalone for the reference workload (#66, #74). Pinned by digest; the image
# bundles apache-airflow-providers-openlineage 2.20.1 under Airflow's own constraints.
FROM apache/airflow:3.3.2-python3.12@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

# dbt gets its own virtualenv so it never shares (or breaks) Airflow's pinned dependencies.
# The requirements carry hashes and the versions #24 ran with (uv.lock).
COPY --chown=airflow:root requirements-dbt.txt /home/airflow/requirements-dbt.txt
RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir --require-hashes \
        -r /home/airflow/requirements-dbt.txt
