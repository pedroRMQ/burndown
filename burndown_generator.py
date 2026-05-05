#!/usr/bin/env python3
"""
Gerador de Burndown Chart para GitHub Projects V2
- Suporta projetos de usuário (--login) e de organização (--org)
- Mostra toda a sprint no eixo X (incluindo dias futuros)
- Detecta "Done" pelo campo Status do Projects V2
"""

import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ─── Queries GraphQL ──────────────────────────────────────────────────────────

USER_PROJECT_QUERY = """
query($login: String!, $projectNumber: Int!, $cursor: String) {
  user(login: $login) {
    projectV2(number: $projectNumber) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValues(first: 100) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
                updatedAt
              }
            }
          }
          content {
            ... on Issue {
              number
              title
              state
              closedAt
              createdAt
              labels(first: 100) {
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

ORG_PROJECT_QUERY = """
query($org: String!, $projectNumber: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $projectNumber) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValues(first: 100) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
                updatedAt
              }
            }
          }
          content {
            ... on Issue {
              number
              title
              state
              closedAt
              createdAt
              labels(first: 100) {
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


# ─── GraphQL helper ───────────────────────────────────────────────────────────

def graphql(token, query, variables):
    resp = requests.post(
        "https://api.github.com/graphql",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors:\n{json.dumps(data['errors'], indent=2)}")
    return data


# ─── Busca itens do projeto ───────────────────────────────────────────────────

def fetch_all_items(token, login, project_number, org=None):
    """
    Busca todos os itens do projeto.
    Se `org` for fornecido, usa a query de organização;
    caso contrário usa a query de usuário.
    """
    items = []
    cursor = None
    title = ""

    while True:
        if org:
            variables = {"org": org, "projectNumber": project_number}
            if cursor:
                variables["cursor"] = cursor
            data = graphql(token, ORG_PROJECT_QUERY, variables)
            project = data["data"]["organization"]["projectV2"]
        else:
            variables = {"login": login, "projectNumber": project_number}
            if cursor:
                variables["cursor"] = cursor
            data = graphql(token, USER_PROJECT_QUERY, variables)
            project = data["data"]["user"]["projectV2"]

        title = project["title"]
        page = project["items"]

        for node in page["nodes"]:
            content = node.get("content")
            if not content:
                continue

            status_name = None
            status_updated_at = None
            for fv in node.get("fieldValues", {}).get("nodes", []):
                field = fv.get("field", {})
                if field.get("name", "").lower() == "status":
                    status_name = fv.get("name")
                    status_updated_at = fv.get("updatedAt")
                    break

            items.append({
                "content": content,
                "status": status_name,
                "status_updated_at": status_updated_at,
            })

        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    return title, items


# ─── Utilitários ──────────────────────────────────────────────────────────────

def parse_points(labels, prefix):
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


def get_done_at(item):
    """
    Retorna o datetime em que a issue foi concluída, ou None se ainda pendente.

    Prioridade:
    1. Status == "done" no board + status_updated_at
    2. Status == "done" no board + closedAt como fallback
    3. Status == "done" no board sem nenhum timestamp → agora (evita tratar como pendente)
    4. Issue fechada no GitHub (state == CLOSED) mas sem status "done" explícito
    """
    status = (item.get("status") or "").strip().lower()
    content = item["content"]

    if status == "done":
        raw = item.get("status_updated_at")
        if raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # Done no board mas sem timestamp de status: usa closedAt
        if content.get("closedAt"):
            return datetime.fromisoformat(content["closedAt"].replace("Z", "+00:00"))
        # Último recurso: considera concluída agora
        return datetime.now(timezone.utc)

    # Fechada no GitHub mas sem status "done" explícito no board
    if content.get("state") == "CLOSED" and content.get("closedAt"):
        return datetime.fromisoformat(content["closedAt"].replace("Z", "+00:00"))

    return None


def to_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_date(dt):
    """Converte um datetime UTC para a data no fuso horário local da máquina.
    Ex: 2026-05-05 01:18 UTC → 2026-05-04 em Recife (UTC-3).
    """
    return dt.astimezone().date()


# ─── Construção do burndown ───────────────────────────────────────────────────

def build_burndown(items, sprint_start, sprint_end, points_prefix):
    sprint_start = to_utc(sprint_start)
    sprint_end   = to_utc(sprint_end)
    today        = datetime.now().astimezone()

    issues = []
    for item in items:
        content = item["content"]

        # Ignora drafts e PRs (sem state)
        if content.get("state") is None:
            print(f"  IGNORADO: item sem state (draft ou PR) — '{content.get('title', '?')}'")
            continue

        labels = content.get("labels", {}).get("nodes", [])
        pts = parse_points(labels, points_prefix) if points_prefix else 1
        if pts == 0:
            pts = 1
            print(f"  AVISO: issue #{content.get('number')} sem label de pontos, contando como 1")

        done_at = get_done_at(item)

        # Ignora conclusões feitas ANTES do início da sprint
        if done_at is not None and local_date(done_at) < sprint_start.date():
            print(
                f"  AVISO: #{content.get('number')} concluída antes da sprint "
                f"({local_date(done_at)}), tratando como pendente."
            )
            done_at = None

        issues.append({"points": pts, "done_at": done_at})
        print(
            f"    #{content.get('number')} '{content.get('title')}' "
            f"| status={item['status']} | pts={pts} | done_at={done_at}"
            + (f" (local: {local_date(done_at)})" if done_at else "")
        )

    total_points = sum(i["points"] for i in issues)
    done_count   = sum(1 for i in issues if i["done_at"] is not None)

    print(f"  Issues encontradas : {len(issues)}")
    print(f"  Issues concluídas  : {done_count}")
    print(f"  Total de pontos    : {total_points}")

    # Linha ideal: toda a sprint
    num_days  = (sprint_end.date() - sprint_start.date()).days
    all_dates = []
    ideal_vals = []
    current = sprint_start
    while current.date() <= sprint_end.date():
        day_idx = (current.date() - sprint_start.date()).days
        ideal   = total_points * (1 - day_idx / num_days) if num_days > 0 else 0
        all_dates.append(current.date())
        ideal_vals.append(max(0.0, ideal))
        current += timedelta(days=1)

    # Série real: só plota se a sprint já começou.
    # Usa local_date(done_at) para converter UTC → fuso local antes de comparar,
    # evitando que tarefas concluídas à noite apareçam no dia errado.
    real_dates = []
    real_vals  = []
    if today.date() >= sprint_start.date():
        current = sprint_start
        while current.date() <= min(sprint_end.date(), today.date()):
            remaining = sum(
                i["points"]
                for i in issues
                if i["done_at"] is None or local_date(i["done_at"]) > current.date()
            )
            real_dates.append(current.date())
            real_vals.append(remaining)
            current += timedelta(days=1)

        # Sobrescreve o último ponto (hoje) com o estado real atual:
        # desconta tudo que tem done_at definido, independente da hora.
        real_vals[-1] = sum(i["points"] for i in issues if i["done_at"] is None)

    return real_dates, real_vals, all_dates, ideal_vals, total_points


# ─── Plot ─────────────────────────────────────────────────────────────────────

def plot_burndown(title, real_dates, real_vals, all_dates, ideal_vals, total_points, output_path):
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")

    real_nums  = mdates.date2num(real_dates)
    all_nums   = mdates.date2num(all_dates)
    today_num  = mdates.date2num([datetime.now().astimezone().date()])[0]

    # Preenchimento azul transparente contínuo (sem formato de degrau)
    if real_vals:
        ax.fill_between(real_nums, real_vals, alpha=0.12, color="#58a6ff")

    # Linha tracejada ideal
    ax.plot(all_nums, ideal_vals, "--", color="#8b949e",
            linewidth=1.8, label="Ideal", alpha=0.85, zorder=2)

    # Linha real desenhada em diagonal, conectando ponto a ponto
    if real_vals:
        ax.plot(real_nums, real_vals,
                color="#58a6ff", linewidth=2.5,
                marker="o", markersize=6,
                markerfacecolor="#58a6ff",
                label="Real (pontos restantes)", zorder=3)

    ax.axvline(today_num, color="#f78166", linestyle=":",
               linewidth=1.8, label="Hoje", alpha=0.95, zorder=4)

    ax.set_xlim(all_nums[0] - 0.3, all_nums[-1] + 0.3)
    ax.set_ylim(-0.3, total_points * 1.15 + 0.5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45, ha="right", color="#c9d1d9", fontsize=8)
    plt.yticks(color="#c9d1d9", fontsize=9)

    ax.axvspan(today_num, all_nums[-1] + 0.3, alpha=0.04, color="#ffffff", zorder=0)

    ax.set_xlabel("Data", color="#8b949e", fontsize=11)
    ax.set_ylabel("Pontos Restantes", color="#8b949e", fontsize=11)
    ax.set_title(f"Burndown Chart — {title}",
                 color="#e6edf3", fontsize=14, fontweight="bold", pad=15)

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#30363d")

    ax.tick_params(colors="#8b949e")
    ax.grid(color="#21262d", linestyle="-", linewidth=0.7, alpha=0.8)
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=10)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Gráfico salvo em: {output_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Gera burndown chart a partir de um GitHub Project V2"
    )
    parser.add_argument("--token",        required=True,  help="GitHub personal access token")
    parser.add_argument("--login",        required=True,  help="Login do usuário GitHub (dono do projeto de usuário)")
    parser.add_argument("--org",          default=None,   help="Login da organização (use quando o projeto for de uma org)")
    parser.add_argument("--project",      required=True,  type=int, help="Número do projeto no GitHub")
    parser.add_argument("--start",        required=True,  help="Início da sprint (YYYY-MM-DD)")
    parser.add_argument("--end",          required=True,  help="Fim da sprint (YYYY-MM-DD)")
    parser.add_argument("--points-label", default="size ", help="Prefixo do label de pontos (ex: 'size ')")
    parser.add_argument("--output",       default="burndown.png", help="Caminho do arquivo de saída")
    args = parser.parse_args()

    sprint_start = datetime.strptime(args.start, "%Y-%m-%d")
    sprint_end   = datetime.strptime(args.end,   "%Y-%m-%d")

    if sprint_start >= sprint_end:
        print("ERRO: --start deve ser anterior a --end")
        sys.exit(1)

    owner_label = args.org if args.org else args.login
    print(f"Buscando projeto #{args.project} de '{owner_label}'...")

    title, items = fetch_all_items(
        args.token, args.login, args.project, org=args.org
    )
    print(f"  Projeto: '{title}'")

    real_dates, real_vals, all_dates, ideal_vals, total = build_burndown(
        items, sprint_start, sprint_end, args.points_label
    )

    if not real_dates:
        print("  Sprint ainda não começou — gerando gráfico apenas com linha ideal.")

    plot_burndown(title, real_dates, real_vals, all_dates, ideal_vals, total, args.output)
    print("Concluído!")


if __name__ == "__main__":
    main()
