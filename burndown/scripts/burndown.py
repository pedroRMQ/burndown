#!/usr/bin/env python3
"""
Gerador de Burndown Chart para GitHub Projects/Issues, por MILESTONE (sprint).

- Cada sprint = um milestone do GitHub (ex: H1, H2, H3, H4).
- A data de TÉRMINO da sprint vem do campo `due_on` do milestone.
- A data de INÍCIO da sprint é informada manualmente em `sprints.yml`
  (o GitHub não guarda data de início de milestone).
- Conclusão é detectada pela issue estar CLOSED (state) + closedAt.
- Pontos são extraídos de uma label com prefixo (ex: "size 3" -> 3).

Uso (uma sprint):
    python burndown.py --token $GH_TOKEN --owner fulano --repo meu-repo \
        --milestone H1 --start 2026-06-01 --output burndown/H1.png

Uso (todas as sprints definidas em sprints.yml):
    python burndown.py --token $GH_TOKEN --owner fulano --repo meu-repo \
        --config sprints.yml --output-dir burndown/
"""

import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

import requests
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ─── Query GraphQL: issues de um milestone específico ────────────────────────

MILESTONE_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $milestoneNumber: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    milestone(number: $milestoneNumber) {
      title
      dueOn
      issues(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
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
"""

LIST_MILESTONES_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    milestones(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        dueOn
        state
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


# ─── Resolução de milestone (nome -> number) ──────────────────────────────────

def find_milestone_number(token, owner, repo, milestone_title):
    """Procura, entre todos os milestones do repo, o que tem o título informado."""
    cursor = None
    while True:
        variables = {"owner": owner, "repo": repo}
        if cursor:
            variables["cursor"] = cursor
        data = graphql(token, LIST_MILESTONES_QUERY, variables)
        page = data["data"]["repository"]["milestones"]
        for node in page["nodes"]:
            if node["title"].strip().lower() == milestone_title.strip().lower():
                return node["number"], node["dueOn"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    raise RuntimeError(f"Milestone '{milestone_title}' não encontrado em {owner}/{repo}")


# ─── Busca issues do milestone ────────────────────────────────────────────────

def fetch_milestone_issues(token, owner, repo, milestone_number):
    issues = []
    cursor = None
    title = ""
    due_on = None

    while True:
        variables = {"owner": owner, "repo": repo, "milestoneNumber": milestone_number}
        if cursor:
            variables["cursor"] = cursor
        data = graphql(token, MILESTONE_ISSUES_QUERY, variables)
        milestone = data["data"]["repository"]["milestone"]
        if milestone is None:
            raise RuntimeError(f"Milestone número {milestone_number} não encontrado.")

        title = milestone["title"]
        due_on = milestone["dueOn"]
        page = milestone["issues"]

        for node in page["nodes"]:
            issues.append(node)

        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    return title, due_on, issues


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


def get_done_at(issue):
    """
    Retorna o datetime em que a issue foi concluída, ou None se ainda pendente.
    Critério: state == CLOSED, usando closedAt.
    """
    if issue.get("state") == "CLOSED" and issue.get("closedAt"):
        return datetime.fromisoformat(issue["closedAt"].replace("Z", "+00:00"))
    return None


def to_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_date(dt):
    """Converte um datetime UTC para a data no fuso horário local da máquina."""
    return dt.astimezone().date()


# ─── Construção do burndown ───────────────────────────────────────────────────

def build_burndown(issues_raw, sprint_start, sprint_end, points_prefix):
    sprint_start = to_utc(sprint_start)
    sprint_end   = to_utc(sprint_end)
    today        = datetime.now().astimezone()

    issues = []
    for content in issues_raw:
        labels = content.get("labels", {}).get("nodes", [])
        pts = parse_points(labels, points_prefix) if points_prefix else 1
        if pts == 0:
            pts = 1
            print(f"  AVISO: issue #{content.get('number')} sem label de pontos, contando como 1")

        done_at = get_done_at(content)

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
            f"| state={content.get('state')} | pts={pts} | done_at={done_at}"
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

        # Sobrescreve o último ponto com o estado real atual, mas só quando o
        # último ponto plotado É hoje (sprint em andamento). Se a sprint já
        # terminou, o último ponto deve refletir o estado em sprint_end, não o
        # de hoje — senão a linha de atraso (que parte desse ponto) fica
        # descontinuada em relação à linha azul.
        if today.date() <= sprint_end.date():
            real_vals[-1] = sum(i["points"] for i in issues if i["done_at"] is None)

    # ─ Linha de atraso ─
    # Issues que ainda estavam pendentes no fim da sprint (due_on), mas que JÁ
    # foram concluídas (em qualquer data, mesmo após o due_on). Issues que
    # continuam abertas hoje não entram aqui — não preveem futuro.
    pending_at_end = [
        i for i in issues
        if i["done_at"] is None or local_date(i["done_at"]) > sprint_end.date()
    ]
    late_done = [
        i for i in pending_at_end
        if i["done_at"] is not None and local_date(i["done_at"]) > sprint_end.date()
    ]

    delay_dates = []
    delay_vals  = []
    if late_done and sprint_end.date() <= today.date():
        last_done_date = max(local_date(i["done_at"]) for i in late_done)
        # Ponto de partida: mesmo valor em que a linha real "travou" no due_on
        start_value = sum(i["points"] for i in pending_at_end)

        delay_dates.append(sprint_end.date())
        delay_vals.append(start_value)

        current = sprint_end.date() + timedelta(days=1)
        while current <= last_done_date:
            remaining = sum(
                i["points"] for i in pending_at_end
                if i["done_at"] is None or local_date(i["done_at"]) > current
            )
            delay_dates.append(current)
            delay_vals.append(remaining)
            current += timedelta(days=1)

    return real_dates, real_vals, all_dates, ideal_vals, total_points, delay_dates, delay_vals


# ─── Plot ─────────────────────────────────────────────────────────────────────

def plot_burndown(title, real_dates, real_vals, all_dates, ideal_vals, total_points,
                   delay_dates, delay_vals, output_path):
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")

    real_nums  = mdates.date2num(real_dates)
    all_nums   = mdates.date2num(all_dates)

    if real_vals:
        ax.fill_between(real_nums, real_vals, alpha=0.12, color="#58a6ff")

    ax.plot(all_nums, ideal_vals, "--", color="#8b949e",
            linewidth=1.8, label="Ideal", alpha=0.85, zorder=2)

    if real_vals:
        ax.plot(real_nums, real_vals,
                color="#58a6ff", linewidth=2.5,
                marker="o", markersize=6,
                markerfacecolor="#58a6ff",
                label="Real (pontos restantes)", zorder=3)

    if delay_vals:
        delay_nums = mdates.date2num(delay_dates)
        ax.plot(delay_nums, delay_vals,
                color="#f85149", linewidth=2.5,
                marker="o", markersize=6,
                markerfacecolor="#f85149",
                label="Atraso", zorder=3)

    x_min = all_nums[0]
    x_max = max(all_nums[-1], mdates.date2num(delay_dates[-1])) if delay_dates else all_nums[-1]
    ax.set_xlim(x_min - 0.3, x_max + 0.3)
    ax.set_ylim(-0.3, max(total_points * 1.15, 1) + 0.5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45, ha="right", color="#c9d1d9", fontsize=8)
    plt.yticks(color="#c9d1d9", fontsize=9)

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


# ─── Execução de uma sprint ────────────────────────────────────────────────────

def run_one_sprint(token, owner, repo, milestone_title, sprint_start_str, points_prefix, output_path):
    print(f"\n=== Sprint '{milestone_title}' ===")
    milestone_number, due_on = find_milestone_number(token, owner, repo, milestone_title)

    if not due_on:
        print(f"  ERRO: milestone '{milestone_title}' não tem due date definida no GitHub. Pulando.")
        return False

    sprint_start = datetime.strptime(sprint_start_str, "%Y-%m-%d")
    sprint_end = datetime.fromisoformat(due_on.replace("Z", "+00:00")).replace(tzinfo=None)

    if sprint_start.date() >= sprint_end.date():
        print(f"  ERRO: start ({sprint_start.date()}) deve ser anterior ao due_on ({sprint_end.date()}). Pulando.")
        return False

    title, _, issues_raw = fetch_milestone_issues(token, owner, repo, milestone_number)
    print(f"  Milestone: '{title}' | início: {sprint_start.date()} | due_on: {sprint_end.date()}")

    real_dates, real_vals, all_dates, ideal_vals, total, delay_dates, delay_vals = build_burndown(
        issues_raw, sprint_start, sprint_end, points_prefix
    )

    if not issues_raw:
        print("  AVISO: nenhuma issue encontrada nesse milestone. Gráfico não gerado.")
        return False

    if delay_dates:
        print(f"  AVISO: entrega(s) com atraso detectada(s), estendendo gráfico até {delay_dates[-1]}.")

    plot_burndown(title, real_dates, real_vals, all_dates, ideal_vals, total,
                  delay_dates, delay_vals, output_path)
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Gera burndown chart(s) a partir de milestones do GitHub"
    )
    parser.add_argument("--token", required=True, help="GitHub token (repo scope)")
    parser.add_argument("--owner", required=True, help="Dono do repositório (user ou org)")
    parser.add_argument("--repo",  required=True, help="Nome do repositório")
    parser.add_argument("--points-label", default="size ", help="Prefixo do label de pontos (ex: 'size ')")

    # Modo 1: uma sprint via linha de comando
    parser.add_argument("--milestone", default=None, help="Título do milestone (ex: H1)")
    parser.add_argument("--start",     default=None, help="Início da sprint (YYYY-MM-DD)")
    parser.add_argument("--output",    default=None, help="Caminho do PNG de saída (modo uma sprint)")

    # Modo 2: todas as sprints de um arquivo de config
    parser.add_argument("--config",     default=None, help="Caminho do sprints.yml")
    parser.add_argument("--output-dir", default="burndown", help="Diretório de saída (modo config)")

    args = parser.parse_args()

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        sprints = config.get("sprints", [])
        if not sprints:
            print("ERRO: nenhuma sprint definida em sprints.yml (chave 'sprints').")
            sys.exit(1)

        import os
        os.makedirs(args.output_dir, exist_ok=True)

        generated = []
        for sprint in sprints:
            name = sprint["milestone"]
            start = sprint["start"]
            output_path = f"{args.output_dir}/{name}.png"
            ok = run_one_sprint(
                args.token, args.owner, args.repo, name, start, args.points_label, output_path
            )
            if ok:
                generated.append(name)

        # Lista de gráficos gerados, para o passo de atualizar o README
        with open(f"{args.output_dir}/_generated.json", "w", encoding="utf-8") as f:
            json.dump(generated, f)

        print(f"\nConcluído! Gráficos gerados: {generated}")

    else:
        if not (args.milestone and args.start and args.output):
            print("ERRO: modo single requer --milestone, --start e --output (ou use --config).")
            sys.exit(1)
        run_one_sprint(
            args.token, args.owner, args.repo, args.milestone, args.start, args.points_label, args.output
        )
        print("Concluído!")


if __name__ == "__main__":
    main()
