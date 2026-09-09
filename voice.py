"""Los oídos y la boca del escriba — todo lo que NO es el SDK.

Dos adaptadores finos sobre dos módulos que ya funcionan y que se copian
sin tocar desde `jaato-cascade-audio-interchange`:

    ptt_capture.PushToTalkMic   una pulsación -> una `Utterance`
    pulse_playback.PulsePlayer  mime + bytes  -> el altavoz

Ninguno de los dos sabe nada de jaato, y ese es justamente el motivo de
que este fichero exista: `run_escriba.py` debe leerse como lo que quiere
demostrar — dos sesiones y un `ask` — y no como fontanería de audio con
una llamada al SDK enterrada dentro.

Lo único que se añade aquí es el puente entre dos mundos: el micrófono
entrega en un HILO y el driver espera con `await`.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

import ptt_capture
import pulse_playback

#: Lo que el modelo recibe.  `Utterance.wav()` ya devuelve WAV recortado.
UTTERANCE_MIME = "audio/wav"


class Ears:
    """El micrófono de pulsar-para-hablar, en forma de `await`.

    `PushToTalkMic` entrega cada pulsación llamando a un callback desde
    su propio hilo.  Un driver `asyncio` no puede recogerlo ahí, así que
    la cola es el punto de encuentro: el hilo del micrófono deposita con
    `call_soon_threadsafe`, y el bucle del driver espera con `get`.
    """

    def __init__(self, source: str = ptt_capture.SOURCE) -> None:
        self._cola: asyncio.Queue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._mic = ptt_capture.PushToTalkMic(self._entregar, source=source)

    def _entregar(self, u: "ptt_capture.Utterance") -> None:
        """Corre en el HILO del micrófono; solo cruza la frontera."""
        self._loop.call_soon_threadsafe(self._cola.put_nowait, u)

    def __enter__(self) -> "Ears":
        self._mic.start()          # levanta SourceMuted si está silenciado
        return self

    def __exit__(self, *_) -> None:
        self._mic.stop()

    def _en_marcha(self) -> bool:
        """¿Hay una intervención en curso ahora mismo?

        Dos estados, y hacen falta los dos:

        - `_press_from` deja de ser None mientras la tecla está pulsada.
        - `_cuts` guarda los cortes ya cerrados que todavía no se han
          entregado: entre soltar la tecla y que la intervención llegue a
          la cola pasa el rato de la cola de audio (TAIL_MS y lo que el
          lector tarde en alcanzar el offset), y en esa ventana la tecla
          ya está suelta pero la frase aún viene de camino.

        Mirar solo la tecla dejaría esa ventana contando como silencio.
        """
        return (self._mic._press_from is not None
                or not self._mic._cuts.empty())

    async def escuchar(self, timeout: float) -> Optional[dict]:
        """La siguiente intervención como adjunto, o None si no hay nadie.

        `timeout` mide ABANDONO, no duración: se reinicia mientras la
        persona esté hablando.

        Contarlo de otra manera fue un fallo real.  Una intervención se
        entrega cuando se SUELTA la tecla, no cuando se empieza a hablar
        (ptt_capture.py:426-432), así que un único `wait_for` sobre la
        cola mide «cuánto tardas en terminar de hablar».  Quien se paraba
        diez segundos a pensar y luego explicaba cuarenta entregaba a los
        cincuenta, y con el plazo en cuarenta y cinco la conversación se
        daba por acabada MIENTRAS seguía hablando. Una explicación larga
        era indistinguible de una habitación vacía.

        El tope de `MAX_UTTERANCE_SECONDS` acota la espera: una pulsación
        no puede durar para siempre, así que esto no se cuelga.
        """
        while True:
            self._mic.raise_if_faulted()  # un hilo muerto no es silencio
            try:
                u = await asyncio.wait_for(self._cola.get(), timeout)
            except asyncio.TimeoutError:
                if self._en_marcha():
                    continue              # sigue hablando; el plazo se reinicia
                return None
            return {"mime_type": UTTERANCE_MIME, "data": u.wav(),
                    "display_name": "intervencion.wav"}


class Tongue:
    """Reproduce lo que el modelo dice, según va llegando.

    Se pasa tal cual como `on_media=` a `Session.ask`: el SDK entrega la
    voz del modelo por ahí mientras el texto vuelve por el `return`.

    `finish` BLOQUEA hasta que ese trozo termina de sonar, y aquí eso es
    lo correcto y no un descuido: mientras el escriba habla no hay nada
    que escuchar, y volver a abrir el micrófono antes de que calle sería
    grabarse a sí mismo.
    """

    def __init__(self) -> None:
        self._player = pulse_playback.PulsePlayer()
        #: Lo que el proveedor dice haber dicho.  El trozo FINAL trae la
        #: transcripción de su propio audio (jaato#869); un turno hablado
        #: no devuelve texto por `ask`, así que sin esto no queda ni
        #: rastro en pantalla de lo que se oyó.
        self.dicho: list[str] = []

    def hablar(self, ev) -> None:
        self._player.feed(ev.stream_id, ev.mime_type,
                          base64.b64decode(ev.data_b64))
        if ev.final:
            if getattr(ev, "chunk", ""):
                self.dicho.append(ev.chunk)
            self._player.finish(ev.stream_id)

    def ultimo(self) -> str:
        """Lo hablado desde la última vez que se preguntó."""
        texto = " ".join(t.strip() for t in self.dicho if t.strip())
        self.dicho.clear()
        return texto
