#!/usr/bin/env python3
"""
Gerador de Burndown Chart para GitHub Projects V2 (projetos de usuário)
Usa a API GraphQL do GitHub para buscar dados reais do projeto.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


# ── GraphQL query para projeto de USUÁRIO (user-level project V2) ──────────
USER_PROJECT_QUERY = """
query($login: String!, $projectNumber: Int!, $cursor: String) {
  user(login: $login) {
    projectV2(number: $projectNumber) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            ... on Issue {
              number
              title
              state
              closedAt
              createdAt
              labels(first: 20) {
                nodes { name }
              }
            }
          }
        }
      }
    }
  }
}
"""


def graphql(token: str, query: str, variables: dict) -> dict:
    resp = requests.post(
        "https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data


def fetch_all_items(token: str, login: str, project_number: int) -> tuple[str, list]:
    """Busca todos os itens do projeto (paginação automática)."""
    items = []
    cursor = None
    title = ""

    while True:
        variables = {"login": login, "projectNumber": project_number}
        if cursor:
            variables["cursor"] = cursor

        data = graphql(token, USER_PROJECT_QUERY, variables)
        project = data["data"]["user"]["projectV2"]
        title = project["title"]
        page = project["items"]

        for node in page["nodes"]:
            content = node.get("content")
            if content and content.get("__typename") != "DraftIssue":
                items.append(content)

        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    return title, items


def parse_points(labels: list[dict], prefix: str) -> int:
    """Extrai pontos do label. Ex: prefix='size ', label='size 2' → 2"""
    total = 0
    for lbl in labels:
        name = lbl["name"]
        if name.lower().startswith(prefix.lower()):
            suffix = name[len(prefix):].strip()
            try:
                total += int(suffix)
            except ValueError:
                pass
    return total


def build_burndown(
    items: list,
    sprint_start: datetime,
    sprint_end: datetime,
    points_label_prefix: str,
) -> tuple[list, list, list]:
    """
    Retorna três séries diárias entre sprint_start e sprint_end:
      - ideal  : linha ideal de burndown
      - real   : pontos restantes (issues abertas) por dia
    """
    # Converte datas para aware UTC
    def to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    sprint_start = to_utc(sprint_start)
    sprint_end = to_utc(sprint_end)
    today = datetime.now(timezone.utc)

    # Calcula pontos totais e data de fechamento de cada issue
    issues = []
    for item in items:
        if item.get("state") is None:
            continue  # não é issue
        labels = item.get("labels", {}).get("nodes", [])
        pts = parse_points(labels, points_label_prefix) if points_label_prefix else 1
        if pts == 0:
            pts = 1  # conta como 1 se não tiver label de pontos
        closed_at = None
        if item["state"] == "CLOSED" and item.get("closedAt"):
            closed_at = datetime.fromisoformat(
                item["closedAt"].replace("Z", "+00:00")
            )
        issues.append({"points": pts, "closed_at": closed_at})

    total_points = sum(i["points"] for i in issues)
    print(f"  Total de issues: {len(issues)}")
    print(f"  Total de pontos: {total_points}")

    # Gera série diária
    dates = []
    real_remaining = []

    current = sprint_start
    while current <= min(sprint_end, today):
        remaining = 0
        for iss in issues:
            if iss["closed_at"] is None or iss["closed_at"] > current:
                remaining += iss["points"]
        dates.append(current.date())
        real_remaining.append(remaining)
        current += timedelta(days=1)

    # Linha ideal
    num_days = (sprint_end.date() - sprint_start.date()).days
    ideal = []
    for i, d in enumerate(dates):
        day_idx = (d - sprint_start.date()).days
        ideal_val = total_points * (1 - day_idx / num_days) if num_days > 0 else 0
        ideal.append(max(0, ideal_val))

    return dates, real_remaining, ideal, total_points


def plot_burndown(
    title: str,
    dates: list,
    real: list,
    ideal: list,
    total_points: int,
    sprint_end: datetime,
    output_path: str,
):
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")

    date_nums = mdates.date2num(dates)

    # Área sob a linha real
    ax.fill_between(date_nums, real, alpha=0.15, color="#58a6ff", step="post")

    # Linha ideal (tracejada)
    ax.plot(date_nums, ideal, "--", color="#8b949e", linewidth=1.5,
            label="Ideal", alpha=0.8)

    # Linha real
    ax.step(date_nums, real, where="post", color="#58a6ff", linewidth=2.5,
            label="Real (pontos restantes)", marker="o", markersize=5,
            markerfacecolor="#58a6ff")

    # Linha hoje
    today_num = mdates.date2num([datetime.now().date()])
    ax.axvline(today_num, color="#f78166", linestyle=":", linewidth=1.5,
               label="Hoje", alpha=0.9)

    # Configurações dos eixos
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45, ha="right", color="#c9d1d9", fontsize=9)
    plt.yticks(color="#c9d1d9", fontsize=9)

    ax.set_xlim(date_nums[0] - 0.5, date_nums[-1] + 0.5)
    ax.set_ylim(-0.5, total_points * 1.1 + 1)

    ax.set_xlabel("Data", color="#8b949e", fontsize=11)
    ax.set_ylabel("Pontos Restantes", color="#8b949e", fontsize=11)
    ax.set_title(f"🔥 {title} — Burndown Chart",
                 color="#e6edf3", fontsize=14, fontweight="bold", pad=15)

    ax.spines["bottom"].set_color("#30363d")
    ax.spines["left"].set_color("#30363d")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors="#8b949e")
    ax.yaxis.label.set_color("#8b949e")
    ax.grid(color="#21262d", linestyle="-", linewidth=0.7, alpha=0.8)

    legend = ax.legend(facecolor="#161b22", edgecolor="#30363d",
                       labelcolor="#c9d1d9", fontsize=10)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Gráfico salvo em: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Gera burndown chart para GitHub Projects V2 (projeto de usuário)"
    )
    parser.add_argument("--token",   required=True, help="GitHub Personal Access Token")
    parser.add_argument("--login",   required=True, help="Username do GitHub (ex: pedroRMQ)")
    parser.add_argument("--project", required=True, type=int, help="Número do projeto (ex: 8)")
    parser.add_argument("--start",   required=True, help="Início do sprint YYYY-MM-DD")
    parser.add_argument("--end",     required=True, help="Fim do sprint YYYY-MM-DD")
    parser.add_argument("--points-label", default="size ",
                        help="Prefixo do label de pontos (ex: 'size '). Vazio = conta issues")
    parser.add_argument("--output",  default="burndown.png", help="Caminho do PNG de saída")
    args = parser.parse_args()

    sprint_start = datetime.strptime(args.start, "%Y-%m-%d")
    sprint_end   = datetime.strptime(args.end,   "%Y-%m-%d")

    print(f"Buscando projeto #{args.project} do usuário {args.login}...")
    title, items = fetch_all_items(args.token, args.login, args.project)
    print(f"  Projeto: '{title}'")

    dates, real, ideal, total = build_burndown(
        items, sprint_start, sprint_end, args.points_label
    )

    if not dates:
        print("AVISO: Nenhum dado de sprint encontrado (sprint ainda não começou?)")
        sys.exit(1)

    plot_burndown(title, dates, real, ideal, total, sprint_end, args.output)
    print("Concluído!")


if __name__ == "__main__":
    main()
