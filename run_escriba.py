"""escriba — un segundo cerebro que entrevista en voz y recuerda.

    python run_escriba.py

Pulsa para hablar. Cuenta lo que sepas. Cuando te calles, se duerme.
La próxima vez despierta sabiendo lo que le contaste.

------------------------------------------------------------------
Lo que este fichero quiere demostrar
------------------------------------------------------------------
Todo el trato con el framework son CUATRO líneas — dos sesiones y tres
`ask` — y ninguna de ellas es fontanería:

    async with IPCClient.session(profile="escriba", ...) as escriba:
        await escriba.ask(SALUDO, on_media=boca.hablar)
        await escriba.ask("", attachments=[dicho], on_media=boca.hablar)

    async with IPCClient.session(profile="curator", ...) as curador:
        await curador.ask(DRENAJE)

Dos bloques y no uno anidado, porque son dos momentos: el curador no
participa en la conversación, es lo que pasa DESPUÉS de ella — y
abrirlo antes costaba 5,6 s de silencio delante de la persona.

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

import memoria
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

#: Cuánto puede estar la persona SIN EMPEZAR a hablar antes de dar la
#: conversación por terminada.  Mide abandono, no duración: mientras la
#: tecla esté pulsada el plazo se reinicia (`voice.Ears.escuchar`), así
#: que una explicación larga nunca lo agota.
#:
#: Generoso a propósito.  Es la red de seguridad para cuando alguien se
#: levanta y se va; la manera DELIBERADA de terminar es Ctrl-C, que
#: consolida igual.
SILENCIO_S = 120.0


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

    # Lo que quedara sin juzgar de la última vez, y SOLO si quedó algo.
    #
    # Va por delante de abrir al escriba a propósito: su inventario se
    # rinde al CREAR su sesión, así que esto es lo único que puede hacer
    # que lo de la última vez entre en el saludo de hoy.  Al revés —que
    # es como estaba— el drenaje terminaba después de que el inventario
    # ya estuviera hecho, y no servía para nada.
    #
    # Condicional, porque incondicional costaba 5,6 s de silencio en cada
    # arranque para no hacer nada el 99 % de las veces.  Y dicho en voz
    # alta en la terminal: es trabajo que se hace antes de saludar y no
    # tiene por qué ser invisible.
    pendientes = memoria.sin_consolidar(WORKSPACE)
    if pendientes:
        print(f"· quedaron {pendientes} memorias sin consolidar de la última "
              f"vez — las juzgo antes de empezar")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **conexion) as curador:
            await curador.ask(DRENAJE)
        print("· consolidado; ya puedo empezar sabiéndolo")

    with voice.Ears() as oidos:
        # El curador no se abre para la conversación, y no es un descuido:
        # que se pueda saludar en tres segundos en vez de en nueve.
        #
        # medido, abrirlo aquí costaba 1,6 s de sesión más 4,0 s de turno,
        # el 64 % de los 8,8 s que se tardaba en decir la primera palabra.
        # Solo se paga ese precio cuando hay algo que juzgar (arriba), y
        # entonces sirve para algo porque va antes del inventario.
        async with IPCClient.session(profile="escriba", agent="escriba",
                                     **conexion) as escriba:

            await escriba.ask(SALUDO, on_media=boca.hablar)
            print(f"escriba: {boca.ultimo() or '(habló)'}")

            # El prompt va VACÍO en un turno hablado: la pregunta ES el
            # adjunto.  Un texto al lado sería una segunda pregunta entre
            # las que la persona tendría que elegir.
            #
            # Ctrl-C se recoge AQUÍ y no fuera: cortar es la manera normal
            # de despedirse, y lo aprendido en la conversación se pierde
            # entero si la consolidación no llega a correr.
            try:
                while (dicho := await oidos.escuchar(SILENCIO_S)) is not None:
                    await escriba.ask("", attachments=[dicho],
                                      on_media=boca.hablar)
                    print(f"escriba: {boca.ultimo() or '(habló)'}")
                print("· nadie al otro lado")
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n· hasta luego")

        # Y ahora sí, con la conversación cerrada y nadie esperando, el
        # curador: consolidar lo aprendido es lo que hará que la próxima
        # vez despierte sabiéndolo.  Aquí su coste no se lo come nadie.
        print("· consolidando")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **conexion) as curador:
            await curador.ask(DRENAJE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ptt_capture.SourceMuted as exc:
        sys.exit(f"micrófono mudo: {exc}")
    except KeyboardInterrupt:
        # Un Ctrl-C DENTRO de la conversación ya se recoge ahí dentro y
        # consolida.  Este solo cubre el corte antes o después de eso.
        sys.exit(130)
