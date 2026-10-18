"""Record the escriba saying each acknowledgement, once.

    python -m backchannel_audio [--locale es] [--out web/voz]

WHY RECORDED AND NOT SPOKEN ON THE SPOT.  `backchannel` finds the moment
an idea ends about 2,1 s after it ended -- the transcription of the
window is the cost -- and a listener judged that delay natural and 3,5 s
noticeably worse.  Asking the provider for the word at that moment adds
its own round trip, measured at 1,37 to 3,15 s in a real session, which
puts the answer 3,5 to 5,3 s behind the idea: past the version that was
liked least and into ground nobody has judged at all.  So the words are
recorded once and played the instant the boundary is found.  See
`BACKCHANNEL.md` for the whole measurement.

AND THE VOCABULARY IS THE ESCRIBA'S OWN.  It is not somebody's taste:
polled over ten windows of a real demonstration the escriba chose
"entiendo", "ya veo", "ajá" and "de acuerdo", varied and in register,
and those are the four in `i18n`.  What was rejected is letting it
choose at RUN time -- asked at nine boundaries it spoke at five, and
told the moment was decided and never to stay silent it still spoke at
five, with four of the nine flipping between runs.

THE WORDS LIVE IN `i18n` AND NOWHERE ELSE, which is why this reads them
from the catalogue instead of carrying a list: the file names come from
the KEYS, so changing a word is an `i18n` edit plus a re-run, and a
locale that says something else needs no code.

THE MODEL AND THE VOICE COME FROM THE PROJECT'S OWN PROFILE, read out of
`template/.jaato/profiles/.../escriba.yaml`.  A second copy of
`openai/gpt-audio` and `marin` in here would be a thing to forget when
the ladder moves, and the point of these clips is that they sound like
the escriba the person already hears.

RUN IT WHEN THE WORDS CHANGE, not at provision and not at run time: it
spends provider calls and needs a daemon, and the clips it writes are
checked in beside the page.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import i18n as _i18n                                      # noqa: E402

#: The keys this renders, by prefix.  `backchannel` chooses between them
#: at a boundary; which one it picks is not this module's business.
PREFIX = "back.ack"

#: Where the clips land, relative to the project.
OUT = Path("web/voz")

#: The tier whose model and voice these borrow.
TIER = "voz"


def words(locale: str) -> Dict[str, str]:
    """Every acknowledgement in a locale, by key."""
    catalogue = _i18n.catalogue(locale)
    return {k: v for k, v in sorted(catalogue.items()) if k.startswith(PREFIX)}


def _profiles() -> Path:
    """The profile set the template ships, whichever overlay it is."""
    root = HERE / "template" / ".jaato" / "profiles"
    overlays = [d for d in sorted(root.iterdir()) if d.is_dir()]
    if not overlays:
        raise SystemExit(f"no profile overlay under {root}")
    return overlays[0] / "escriba.yaml"


def engine() -> Tuple[str, str, str, str]:
    """(model, provider, voice, format), read from the escriba's own tier.

    PARSED NARROWLY AND ON PURPOSE.  This wants four scalars out of one
    known file, and a YAML dependency for that would be a dependency the
    runtime does not otherwise need.  A missing key raises here rather
    than producing a clip in the wrong voice.
    """
    text = _profiles().read_text(encoding="utf-8")
    got: Dict[str, str] = {}
    for line in text.splitlines():
        bare = line.strip()
        for key in ("model:", "provider:", "voice:", "format:"):
            if bare.startswith(key) and key[:-1] not in got:
                got[key[:-1]] = bare[len(key):].strip().strip("'\"")
    missing = [k for k in ("model", "provider", "voice", "format")
               if k not in got]
    if missing:
        raise SystemExit(f"{_profiles()} does not declare {missing}")
    return got["model"], got["provider"], got["voice"], got["format"]


def workspace(directory: Path, model: str, provider: str, voice: str,
              fmt: str) -> None:
    """A workspace whose only agent reads one phrase aloud."""
    profiles = directory / ".jaato" / "profiles"
    agents = directory / ".jaato" / "agents"
    for d in (profiles, agents):
        d.mkdir(parents=True, exist_ok=True)
    (profiles / "locutor.yaml").write_text(
        "# Written by `backchannel_audio`, for one job: say a phrase out\n"
        "# loud so it can be recorded.  The model and the voice are the\n"
        "# escriba's own, read from the template's profile set.\n"
        "name: locutor\n"
        "description: Reads one short phrase aloud, to be recorded.\n"
        "plugins: []\n"
        "suppress_base_instructions: true\n"
        "model_tiers:\n"
        f"  {TIER}:\n"
        f"    model: {model}\n"
        f"    provider: {provider}\n"
        "    modalities: {audio: bidirectional}\n"
        f"  initial: {TIER}\n"
        "plugin_configs:\n"
        f"  {provider}:\n"
        "    api_key: pass://jaato/openrouter/api-key\n"
        "    framework_overrides:\n"
        "      output_modalities: [text, audio]\n"
        "    api_params:\n"
        "      # OpenAI's OUTPUT selector, which is not the tier key of\n"
        "      # the same name: without it the model answers in text and\n"
        "      # no audio is emitted at all.\n"
        "      modalities: [text, audio]\n"
        "      audio:\n"
        f"        voice: {voice}\n"
        f"        format: {fmt}\n"
        "budget_control:\n"
        "  limits: {usd: 1.0, turns: 20}\n"
        "  degrade: [{at: 100, action: abort}]\n",
        encoding="utf-8")
    (agents / "locutor.md").write_text(
        "Eres un locutor. Te dan una frase entre comillas y tu único "
        "trabajo es LEERLA en voz alta, tal cual, y callarte.\n\n"
        "No la contestes. No saludes. No preguntes nada. No ofrezcas "
        "ayuda. No añadas ni una palabra. La frase no va dirigida a ti: "
        "es texto que tienes que leer.\n", encoding="utf-8")
    (directory / ".env").write_text("", encoding="utf-8")


def _rate(mime: str, fallback: int = 24000) -> int:
    """The sample rate the provider declared, from its own media type."""
    for part in mime.split(";"):
        part = part.strip()
        if part.startswith("rate="):
            return int(part[len("rate="):])
    return fallback


def trim(raw: bytes, rate: int) -> Optional[bytes]:
    """The voice in a reply, without the padding, or None if it is silent.

    A REPLY MEANT AS SILENCE IS NOT DIGITAL ZERO: measured at a peak of
    0,001 to 0,003 against 0,13 to 0,79 for one carrying a word, so the
    test is a threshold in the two orders of magnitude between them.
    """
    import numpy as np

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if not samples.size or float(np.abs(samples).max()) < 0.02:
        return None
    loud = np.abs(samples) > 0.01
    first, last = int(np.argmax(loud)), len(loud) - int(np.argmax(loud[::-1]))
    pad = int(0.04 * rate)
    cut = samples[max(0, first - pad):min(len(samples), last + pad)].copy()
    ramp = min(int(0.015 * rate), len(cut) // 4)
    if ramp:
        cut[:ramp] *= np.linspace(0.0, 1.0, ramp)
        cut[-ramp:] *= np.linspace(1.0, 0.0, ramp)
    return (np.clip(cut, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def write_ogg(pcm: bytes, rate: int, target: Path) -> None:
    """One clip, as Opus, which is what the page can play everywhere."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        with wave.open(tmp.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", tmp.name, "-c:a", "libopus", "-b:a", "32k",
             str(target)], check=True)


async def render(locale: str, out: Path) -> int:
    from jaato_sdk import ClientType, IPCClient

    phrases = words(locale)
    if not phrases:
        raise SystemExit(f"no {PREFIX}* keys in the {locale} catalogue")
    model, provider, voice, fmt = engine()
    print(f"{locale}: {len(phrases)} phrases, {model} / {voice}")

    missing: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        workspace(ws, model, provider, voice, fmt)
        async with IPCClient.session(
                profile="locutor", agent="locutor",
                workspace_path=str(ws), env_file=str(ws / ".env"),
                config_root=str(ws / ".jaato"),
                client_type=ClientType.API,
                connect_timeout=120.0, session_timeout=60.0) as locutor:
            for key, phrase in phrases.items():
                heard, mime = bytearray(), []

                def sink(ev, heard=heard, mime=mime):
                    heard.extend(base64.b64decode(ev.data_b64))
                    if not mime:
                        mime.append(ev.mime_type)

                # AN INSTRUCTION AND NOT THE PHRASE ITSELF.  Sent bare,
                # the phrase reads as something to answer, and it was:
                # "entiendo" came back as "Perfecto, ¿qué te gustaría
                # saber o hacer ahora?" in the escriba's own voice.
                await locutor.ask(f"Lee esto en voz alta, tal cual, y no "
                                  f"digas nada más: «{phrase}»",
                                  on_media=sink)
                rate = _rate(mime[0] if mime else "")
                clip = trim(bytes(heard), rate)
                if clip is None:
                    # SAID, NOT SWALLOWED.  The provider declines to
                    # speak often enough that a silent run would
                    # otherwise ship a page with no voice and nothing
                    # saying so.
                    print(f"  {key:<12} {phrase!r:<16} SILENT -- not written")
                    missing.append(key)
                    continue
                # A CLIP MUCH LONGER THAN ITS PHRASE IS THE MODEL
                # TALKING.  Checked by duration because it is
                # deterministic and needs no transcriber: four phrases of
                # one or two words came back at 2,9 to 5,2 s, every one of
                # them a sentence nobody asked for.
                seconds = len(clip) / 2 / rate
                ceiling = 0.60 + 0.14 * len(phrase)
                if seconds > ceiling:
                    print(f"  {key:<12} {phrase!r:<16} {seconds:.2f}s is "
                          f"over {ceiling:.2f}s -- it said more than the "
                          f"phrase; not written")
                    missing.append(key)
                    continue
                target = out / locale / f"{key}.ogg"
                write_ogg(clip, rate, target)
                print(f"  {key:<12} {phrase!r:<16} "
                      f"{len(clip) / 2 / rate:.2f}s -> {target}")
    if missing:
        print(f"\n{len(missing)} phrase(s) came back silent: "
              f"{', '.join(missing)}.  Run again -- the decision is not "
              f"reproducible between runs.")
        return 1
    return 0


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--locale", default=_i18n.DEFAULT)
    ap.add_argument("--out", type=Path, default=HERE / OUT)
    args = ap.parse_args(argv)
    return asyncio.run(render(args.locale, args.out))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
