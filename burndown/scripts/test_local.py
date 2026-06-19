#!/usr/bin/env python3
"""
Testa build_burndown() e plot_burndown() localmente, com issues inventadas
(sem chamar a API do GitHub, sem token, sem internet).

Edite a lista ISSUES e as datas de cada cenário abaixo pra simular qualquer
situação: sprint sem atraso, com atraso concentrado em um dia, atraso
espalhado em vários dias, issues que nunca foram concluídas, etc.

Uso:
    python test_local.py
Gera os PNGs em ./test_output/
"""

from datetime import datetime
import os
import importlib.util

# Importa as funções do burndown.py sem precisar instalar nada como pacote
spec = importlib.util.spec_from_file_location("burndown", "burndown.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

os.makedirs("test_output", exist_ok=True)


def make_issue(number, points, closed_at=None):
    """Monta uma issue falsa no mesmo formato que viria da API GraphQL."""
    return {
        "number": number,
        "title": f"Issue fake #{number}",
        "state": "CLOSED" if closed_at else "OPEN",
        "closedAt": closed_at,
        "createdAt": "2026-04-28T00:00:00Z",
        "labels": {"nodes": [{"name": f"size {points}"}]},
    }


def run_scenario(name, issues, sprint_start, sprint_end):
    print(f"\n--- Cenário: {name} ---")
    real_dates, real_vals, all_dates, ideal_vals, total, delay_dates, delay_vals = bd.build_burndown(
        issues, sprint_start, sprint_end, points_prefix="size "
    )
    output_path = f"test_output/{name}.png"
    bd.plot_burndown(
        name, real_dates, real_vals, all_dates, ideal_vals, total,
        delay_dates, delay_vals, output_path
    )


# ── Cenário 1: atraso concentrado em um único dia (igual ao seu print) ──────
run_scenario(
    "atraso_um_dia",
    [
        make_issue(1, 3, closed_at="2026-04-29T10:00:00Z"),
        make_issue(2, 3, closed_at="2026-05-03T10:00:00Z"),
        make_issue(3, 3, closed_at="2026-06-19T10:00:00Z"),  # atrasada, fecha "hoje"
    ],
    sprint_start=datetime(2026, 4, 28),
    sprint_end=datetime(2026, 6, 1),
)

# ── Cenário 2: atraso espalhado em vários dias ───────────────────────────────
run_scenario(
    "atraso_varios_dias",
    [
        make_issue(1, 3, closed_at="2026-04-29T10:00:00Z"),
        make_issue(2, 4, closed_at="2026-06-05T10:00:00Z"),  # atrasada
        make_issue(3, 3, closed_at="2026-06-12T10:00:00Z"),  # atrasada
        make_issue(4, 2, closed_at="2026-06-19T10:00:00Z"),  # atrasada
    ],
    sprint_start=datetime(2026, 4, 28),
    sprint_end=datetime(2026, 6, 1),
)

# ── Cenário 3: sem atraso (tudo concluído antes do due date) ────────────────
run_scenario(
    "sem_atraso",
    [
        make_issue(1, 3, closed_at="2026-04-29T10:00:00Z"),
        make_issue(2, 3, closed_at="2026-05-10T10:00:00Z"),
        make_issue(3, 3, closed_at="2026-05-30T10:00:00Z"),
    ],
    sprint_start=datetime(2026, 4, 28),
    sprint_end=datetime(2026, 6, 1),
)

# ── Cenário 4: atrasada e ainda aberta (nunca foi concluída) ─────────────────
run_scenario(
    "atraso_com_issue_aberta",
    [
        make_issue(1, 3, closed_at="2026-04-29T10:00:00Z"),
        make_issue(2, 4, closed_at="2026-06-10T10:00:00Z"),  # atrasada, fechou depois
        make_issue(3, 3, closed_at=None),                    # nunca fechou
    ],
    sprint_start=datetime(2026, 4, 28),
    sprint_end=datetime(2026, 6, 1),
)

print("\nPronto! Veja os PNGs em ./test_output/")
