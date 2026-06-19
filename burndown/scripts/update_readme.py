#!/usr/bin/env python3
"""
Atualiza o README.md inserindo (ou substituindo) uma seção com os
gráficos de burndown gerados, entre os marcadores:

<!-- BURNDOWN:START -->
... conteúdo gerado automaticamente ...
<!-- BURNDOWN:END -->

Se os marcadores não existirem no README, eles são adicionados ao final.
Lê a lista de sprints geradas em <output_dir>/_generated.json
(escrito pelo burndown.py no modo --config).
"""

import argparse
import json
import os
from datetime import datetime, timezone

START_MARK = "<!-- BURNDOWN:START -->"
END_MARK   = "<!-- BURNDOWN:END -->"


def build_section(output_dir, generated, repo_rel_path):
    lines = [START_MARK, ""]
    lines.append("## 📉 Burndown Charts")
    lines.append("")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"_Atualizado automaticamente em {now}._")
    lines.append("")

    if not generated:
        lines.append("_Nenhum gráfico disponível ainda._")
    else:
        # Mais recente primeiro (assume ordem do sprints.yml; pode inverter se preferir)
        for name in generated:
            img_path = f"{repo_rel_path}/{name}.png"
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"![Burndown {name}]({img_path})")
            lines.append("")

    lines.append(END_MARK)
    return "\n".join(lines)


def update_readme(readme_path, section):
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = ""

    if START_MARK in content and END_MARK in content:
        before = content.split(START_MARK)[0]
        after = content.split(END_MARK)[1]
        new_content = before + section + after
    else:
        sep = "\n\n" if content and not content.endswith("\n\n") else ""
        new_content = content + sep + section + "\n"

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def main():
    parser = argparse.ArgumentParser(description="Atualiza README com gráficos de burndown")
    parser.add_argument("--output-dir", default="burndown", help="Diretório onde os PNGs foram salvos")
    parser.add_argument("--readme", default="README.md", help="Caminho do README")
    args = parser.parse_args()

    generated_path = os.path.join(args.output_dir, "_generated.json")
    generated = []
    if os.path.exists(generated_path):
        with open(generated_path, "r", encoding="utf-8") as f:
            generated = json.load(f)

    section = build_section(args.output_dir, generated, args.output_dir)
    update_readme(args.readme, section)
    print(f"README atualizado com {len(generated)} gráfico(s): {generated}")


if __name__ == "__main__":
    main()
