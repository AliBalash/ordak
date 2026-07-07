from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.automation.gemini_worker import GeminiJobRequest, WorkerRuntime, run_gemini_job
from app.config import settings


@dataclass
class ConsoleRuntime:
    screenshots: list[Path] = field(default_factory=list)
    output_images: list[Path] = field(default_factory=list)
    trace_path: Path | None = None
    answer: str | None = None
    error: str | None = None

    def update_status(self, status: str) -> None:
        print(f"[STATUS] {status}")

    def append_log(self, message: str, level: str = "info") -> None:
        print(f"[{level.upper()}] {message}")

    def attach_screenshot(self, path: Path) -> None:
        self.screenshots.append(path)
        print(f"[ARTIFACT] Screenshot: {path}")

    def attach_output_image(self, path: Path) -> None:
        self.output_images.append(path)
        print(f"[ARTIFACT] Output image: {path}")

    def remember_conversation_state(self, tab_info) -> None:
        print(f"[TAB] window={tab_info.window_id} tab={tab_info.tab_id} url={tab_info.url}")

    def set_trace_path(self, path: Path) -> None:
        self.trace_path = path
        print(f"[ARTIFACT] Trace: {path}")

    def save_answer(self, answer: str) -> None:
        self.answer = answer

    def save_error(self, message: str, status: str = "failed", error_code: str | None = None) -> None:
        self.error = f"{status}: {message}"
        if error_code:
            self.error += f" [{error_code}]"


def main() -> None:
    provider = "gemini"
    mode = "chat"
    args = []
    for arg in sys.argv[1:]:
        if arg.startswith("--provider="):
            provider = arg.split("=", 1)[1].strip() or provider
            continue
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1].strip() or mode
            continue
        args.append(arg)

    question = " ".join(args).strip() or "به فارسی توضیح بده Docker چیست و Kubernetes چیست."
    runtime = ConsoleRuntime()
    wrapper = WorkerRuntime(
        update_status=runtime.update_status,
        append_log=runtime.append_log,
        attach_screenshot=runtime.attach_screenshot,
        attach_output_image=runtime.attach_output_image,
        remember_conversation_state=runtime.remember_conversation_state,
        set_trace_path=runtime.set_trace_path,
        save_answer=runtime.save_answer,
        save_error=runtime.save_error,
        should_cancel=lambda: False,
    )
    answer = run_gemini_job(
        "manual-test",
        GeminiJobRequest(
            question=question,
            provider=provider,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            start_new_chat=True,
        ),
        runtime=wrapper,
        app_settings=settings,
    )
    print("\nFinal answer:\n")
    print(answer)


if __name__ == "__main__":
    main()
