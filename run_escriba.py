"""escriba — un segundo cerebro que entrevista en voz y recuerda.

    python run_escriba.py

Pulsa para hablar. Cuenta lo que sepas. Cuando te calles, se duerme.
La próxima vez despierta sabiendo lo que le contaste.

------------------------------------------------------------------
Lo que este fichero quiere demostrar
------------------------------------------------------------------
Todo el trato con el framework son CINCO líneas — dos sesiones y tres
`ask` — y ninguna de ellas es fontanería:

    async with IPCClient.session(...) as escriba, \
               IPCClient.session(...) as curator:
        await curator.ask(DRENAJE)
        await escriba.ask(SALUDO, on_media=boca.hablar)
        await escriba.ask("", attachments=[dicho], on_media=boca.hablar)

`IPCClient.session` conecta, configura y crea la sesión; `Session.ask`
es dueño de la receta de enviar-y-esperar (`first-of {TURN_COMPLETED,
SESSION_TERMINATED}`), así que un turno no puede colgarse en este
código.  Suscribirse a eventos, contar terminales, desuscribirse: nada
de eso aparece aquí porque nada de eso es de quien escribe el driver.

`on_media` es la simetría que hace posible una conversación hablada:
`ask` devuelve lo que el modelo ESCRIBIÓ y `on_media` entrega lo que
DIJO, según suena.  El audio del usuario entra por `attachments`.

Lo que no es SDK — micrófono, altavoz, el puente hilo/asyncio — vive en
`voice.py`, y debajo en dos módulos copiados sin tocar de
`jaato-cascade-audio-interchange` (`ptt_capture`, `pulse_playback`).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from jaato_sdk import ClientType, IPCClient

import ptt_capture
import voice

WORKSPACE = Path(__file__).resolve().parent

#: Abre la sesión.  Una acotación, no una pregunta: las palabras del
#: saludo son de la persona (`agents/escriba.md`), y esto solo le dice
#: que el micrófono ya está abierto.
SALUDO = "[La sesión se abre. El usuario está a la escucha.]"

#: Despierta al curador.  Sus reglas — cuántas de una vez, qué se
#: valida — son suyas, no del driver.
#:
#: «Juzga», no «vacía».  La primera redacción decía «Vacía lo que haya en
#: crudo» y el modelo la leyó como lo que parece: vaciar.  Eso no fue la
#: causa del incidente del 2026-09-09 — la causa fue tener
#: `delete_memory` en la lista blanca — pero un verbo que invita a
#: destruir no tiene por qué estar aquí.
DRENAJE = "Juzga lo que haya en crudo."

#: Sin pulsar el micrófono durante este rato, la conversación terminó.
#: Es la única señal de despedida que hay: la sesión de voz no declara
#: esquema de completion a propósito, así que no puede decidir por sí
#: misma que ha acabado.
SILENCIO_S = 45.0


async def main() -> int:
    boca = voice.Tongue()
    conexion = dict(workspace_path=str(WORKSPACE),
                    env_file=str(WORKSPACE / ".env"),
                    # API: un driver headless.  El servidor retira
                    # `signal_completion` de las sesiones raíz de un
                    # cliente TERMINAL/WEB/CHAT, y aquí no lo usamos —
                    # pero declarar la identidad real es lo que hace que
                    # el filtro aplique lo correcto.
                    client_type=ClientType.API)

    with voice.Ears() as oidos:
        async with IPCClient.session(profile="escriba", agent="escriba",
                                     **conexion) as escriba, \
                   IPCClient.session(profile="curator", agent="curator",
                                     **conexion) as curador:

            # Antes de saludar: lo que quedara en crudo de la última vez.
            # Si aquella sesión murió a medias, esto lo recoge ahora.
            await curador.ask(DRENAJE)

            await escriba.ask(SALUDO, on_media=boca.hablar)
            print(f"escriba: {boca.ultimo() or '(habló)'}")

            # El prompt va VACÍO en un turno hablado: la pregunta ES el
            # adjunto.  Un texto al lado sería una segunda pregunta entre
            # las que la persona tendría que elegir.
            while (dicho := await oidos.escuchar(SILENCIO_S)) is not None:
                await escriba.ask("", attachments=[dicho],
                                  on_media=boca.hablar)
                print(f"escriba: {boca.ultimo() or '(habló)'}")

            # Y al dormirse, consolidar lo aprendido: es lo que hará que
            # la próxima vez despierte sabiéndolo.
            print("· silencio — consolidando")
            await curador.ask(DRENAJE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ptt_capture.SourceMuted as exc:
        sys.exit(f"micrófono mudo: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)
