"""What the person said, in text, from the recording we already keep.

WHY LOCALLY AND NOT FROM THE MODEL.  The conversation model heard the
utterance and could be asked to repeat it, but what comes back is its
RENDERING — tidied, shortened, occasionally translated — and a line
labelled "what you said" must not be somebody's paraphrase of it.  A
transcriber reads the same bytes an auditor would and answers the same
question twice.  It also works backwards: every recording already in
`audio/` can be transcribed today, which the model cannot do for a
conversation that has ended.

WHERE THE TEXT LIVES, and why it is not the manifest.  `in_<att>.txt`
sits beside `in_<att>.mp3` in the archive directory, so it is governed
by `conversation_retention_days` exactly as the recording is and goes
when the recording goes.  Put in the manifest it would inherit
`retention_days` instead — 180 against 60 here — and a person's own
words, in text, would outlive the voice they were pruned from by four
months.  Same clock, same file name, no second rule to remember.

OFF UNLESS A MODEL IS NAMED.  `faster-whisper` plus its model is the
heaviest thing this project installs, and on a small host it competes
with the recordings themselves for disk.  No default model, no silent
download: the caller names one or nothing transcribes.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator, Optional, Tuple

#: What a recording's transcript is called.  `in_*` on purpose: the
#: housekeeping sweep treats everything matching it as the conversation
#: half, so the text is pruned on the recording's clock without
#: `housekeeping` needing to know this module exists.
def text_path(recording: Path) -> Path:
    return recording.with_suffix(".txt")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise Unavailable("ffmpeg is not installed; it is what decodes the recording")
    return exe


class Unavailable(RuntimeError):
    """Transcription was asked for and cannot be done."""


class Transcriber:
    """One loaded model, reused.

    Loading costs seconds and hundreds of megabytes of RAM, so it
    happens once, lazily, at the first utterance rather than at import:
    a deployment that never transcribes should not pay for a model it
    does not use.
    """

    def __init__(self, model: str, language: Optional[str] = "es") -> None:
        self.model_name = model
        #: The escriba is a peninsular-Spanish application, so the
        #: language is DECLARED rather than detected.  Detection on a
        #: six-second utterance is unreliable and its failure mode is a
        #: confident transcript in the wrong language; declaring it
        #: costs nothing and removes the guess.  `None` re-enables
        #: detection for a deployment that is not monolingual.
        self.language = language
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise Unavailable(
                    "faster-whisper is not installed: `uv sync --extra stt`") from exc
            # int8 on CPU: the only configuration that fits a host with a
            # couple of gigabytes free and no GPU.
            self._model = WhisperModel(self.model_name, device="cpu",
                                       compute_type="int8")
        return self._model

    def transcribe(self, recording: Path) -> str:
        """Decode with ffmpeg, then transcribe the samples.

        NOT by handing the path to the model.  `faster-whisper` decodes
        through PyAV, and the pair that resolves today does not work:
        `faster-whisper` 1.2.1 calls `av.open(..., metadata_errors=…)`
        and `av` 19 has no such argument — a TypeError on the first
        utterance.  Pinning `av` would make this project's install
        depend on a compatibility window nobody here controls.

        ffmpeg is already a hard requirement — the browser records
        webm/opus and every turn is transcoded to MP3 through it — so
        decoding with the same tool adds nothing and removes the
        fragile path.  16 kHz mono float32 is what the model wants, and
        what the recording already is.
        """
        import numpy as np

        raw = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(recording),
             "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
            capture_output=True)
        if raw.returncode != 0:
            raise Unavailable(
                f"ffmpeg could not decode {recording.name}: "
                f"{raw.stderr.decode('utf-8', 'replace')[:200]}")
        samples = np.frombuffer(raw.stdout, dtype=np.float32)
        if not samples.size:
            return ""
        segments, _info = self._load().transcribe(
            samples, language=self.language, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def write(self, recording: Path) -> Optional[str]:
        """Transcribe and store beside the recording, once.

        An existing transcript is kept: re-transcribing the same bytes
        with a different model would silently change what the archive
        says a person said, and the archive is evidence.
        """
        target = text_path(recording)
        if target.exists():
            return target.read_text(encoding="utf-8").strip() or None
        text = self.transcribe(recording)
        if not text:
            return None
        target.write_text(text + "\n", encoding="utf-8")
        return text


def spoken(recording: Path) -> Optional[str]:
    """The transcript of a recording, if one was ever written."""
    target = text_path(recording)
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8").strip() or None


def pending(root: Path) -> Iterator[Path]:
    """Every archived utterance with no transcript, oldest first."""
    for d in sorted(root.iterdir()) if root.is_dir() else ():
        if not d.is_dir():
            continue
        for mp3 in sorted(d.glob("in_*.mp3")):
            if not text_path(mp3).exists():
                yield mp3


def backfill(workspaces: Path, model: str,
             language: Optional[str] = "es") -> Tuple[int, int]:
    """Transcribe what every workspace already holds.

    The reason this module can exist at all: the recordings are already
    there, so a transcript is not only for conversations yet to happen.
    """
    engine = Transcriber(model, language=language)
    done = skipped = 0
    for person in sorted(workspaces.iterdir()) if workspaces.is_dir() else ():
        audio = person / "workspace" / "audio"
        for mp3 in pending(audio):
            if engine.write(mp3):
                done += 1
            else:
                skipped += 1
            print(f"{mp3.relative_to(workspaces)}: "
                  f"{'written' if done else 'nothing heard'}")
    return done, skipped


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m transcribe",
        description="Transcribe archived utterances that have no transcript.")
    p.add_argument("--root", required=True,
                   help="the workspace root, or one workspace's audio/ directory")
    p.add_argument("--model", required=True,
                   help="whisper model: tiny (~75 MB), base (~145 MB), "
                        "small (~480 MB). Downloaded on first use")
    p.add_argument("--language", default="es",
                   help="declared, not detected (default: %(default)s). "
                        "Pass '' to let the model decide")
    args = p.parse_args(argv)
    root = Path(args.root)
    try:
        done, skipped = backfill(root, args.model, args.language or None)
    except Unavailable as exc:
        print(f"transcribe: {exc}", file=sys.stderr)
        return 2
    print(f"{done} transcribed, {skipped} with nothing in them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
