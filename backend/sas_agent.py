"""
SAS Code Generation Agent
- 멀티턴 대화
- 에러 시 최대 3회 자동 수정 재시도
- 테이블 + 텍스트 요약 출력
"""

from sas_executor import SASExecutor
import code_generator as llm

from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

MAX_RETRIES = 3
MAX_HISTORY_TURNS = 10


def _print_tables(tables: list):
    if not tables:
        return
    for i, df in enumerate(tables):
        title = f"테이블 {i+1}" if len(tables) > 1 else "결과"
        rt = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=True)
        for col in df.columns:
            rt.add_column(str(col), style="cyan")
        for _, row in df.iterrows():
            rt.add_row(*[str(v) for v in row])
        console.print(rt)


def _print_code(code: str, title: str = "생성된 SAS 코드"):
    syntax = Syntax(code, "sas", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=f"[bold yellow]{title}[/]", border_style="yellow"))


def run():
    console.print(Panel(
        "[bold cyan]SAS Code Generation Agent[/]\n[dim]종료: exit 입력[/]",
        border_style="cyan",
        expand=False,
    ))

    history = []
    history_summary = ""

    with SASExecutor() as executor:
        while True:
            try:
                user_input = console.input("\n[bold green]요청 >[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]종료합니다.[/]")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "종료"):
                console.print("[dim]종료합니다.[/]")
                break

            # 1. SAS 코드 생성
            with console.status("[bold blue]SAS 코드 생성 중...[/]", spinner="dots"):
                code = llm.generate(user_input, history, history_summary)
            _print_code(code, "생성된 SAS 코드")

            # 2. 실행 (에러 시 최대 3회 재시도)
            result = None
            for attempt in range(1, MAX_RETRIES + 1):
                spin_msg = (
                    f"[bold blue]SAS 실행 중...[/]"
                    if attempt == 1
                    else f"[bold yellow]재시도 {attempt}/{MAX_RETRIES}...[/]"
                )
                with console.status(spin_msg, spinner="dots2"):
                    result = executor.execute(code)

                if result["success"]:
                    break

                console.print(f"[bold red]✗ 에러 감지[/] ({attempt}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES:
                    with console.status("[bold yellow]코드 수정 중...[/]", spinner="dots"):
                        code = llm.fix(code, result["log"], history, history_summary)
                    _print_code(code, f"수정된 SAS 코드 (시도 {attempt})")
                else:
                    error_lines = [
                        l for l in result["log"].splitlines() if "ERROR" in l
                    ]
                    console.print(Panel(
                        "\n".join(error_lines[:10]) or result["log"][:500],
                        title="[bold red]최대 재시도 초과 — 에러 로그[/]",
                        border_style="red",
                    ))

            if result is None or not result["success"]:
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": f"[실패]\n{code}"})
                continue

            # 3. 결과 출력
            console.print("\n[bold green]✓ 실행 성공[/]")
            _print_tables(result["tables"])

            if not result["tables"] and result["output"].strip():
                console.print(Panel(
                    result["output"][:2000],
                    title="[bold]SAS 출력[/]",
                    border_style="dim",
                ))

            # 4. 요약
            with console.status("[bold blue]요약 생성 중...[/]", spinner="dots"):
                summary_input = result["output"] if result["output"].strip() else result["log"]
                summary = llm.summarize(user_input, code, summary_input)
            console.print(Panel(summary, title="[bold cyan]요약[/]", border_style="cyan"))

            # 히스토리 업데이트
            history.append({"role": "user", "content": user_input})
            history.append({
                "role": "assistant",
                "content": f"[SAS 코드]\n{code}\n\n[결과 요약]\n{summary}",
            })

            # 10턴 초과 시 오래된 2턴 압축
            if len(history) > MAX_HISTORY_TURNS * 2:
                turns_to_compress = history[:2]
                history = history[2:]
                with console.status("[dim]이전 대화 압축 중...[/]", spinner="dots"):
                    history_summary = llm.compress_history(history_summary, turns_to_compress)


if __name__ == "__main__":
    run()
