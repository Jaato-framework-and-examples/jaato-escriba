# jaato-escriba

**El segundo cerebro de una persona. Le pregunta en voz alta, y la
siguiente vez recuerda lo que le contó.**

```bash
python run_escriba.py
```

Pulsa para hablar. Cuando te calles 45 s, se duerme y consolida.

---

## La forma

Dos sesiones bajo el mismo workspace, y ninguna de las dos termina por
su cuenta:

```
        ┌─ sesión escriba ──────────────────────────────┐
  tú ──▶│  tier voz        oye y habla   (gpt-audio)    │──▶ tú
        │      ▲ │ enter_tier                           │
        │      │ ▼                                      │
        │  tier escribano  anota       (gpt-4o-mini)    │
        └───────────────┬───────────────────────────────┘
                        │ store_memory (raw)
                        ▼
        ┌─ sesión curator ──────────────────────────────┐
        │  valida o descarta lo que el escriba anotó    │
        └───────────────────────────────────────────────┘
```

**Por qué dos tiers y no uno.** El esquema de herramientas es de toda la
sesión; lo que cambia el tier es qué modelo está al volante cuando se
decide llamarlas. Un modelo de audio ANUNCIA la herramienta en vez de
invocarla — medido en `jaato-cascade-audio-interchange`: dijo «voy a
abrir el parte» y no llamó a nada. Y cada nombre del esquema es una
palabra que puede leer en voz alta. Así que oye y habla uno, y escribe
otro.

**Por qué el curador es imprescindible.** Todo lo que el escriba anota
nace en CRUDO, y una memoria en crudo no la alcanza ninguna búsqueda por
tags ni se inyecta al despertar (`list_memory_tags`: *«pending_curation
… which no tag search can reach»*). Sin alguien que la promueva a
`validated`, el segundo cerebro no recuerda nada. El curador no es
limpieza: es lo que hace cierto el «recuerda».

**Por qué ninguna sesión declara `completion_payload_schema`.** Declarar
el esquema habilita `signal_completion`, y llamarlo deja la sesión
quiescente. Una conversación no es un one-shot — y jaato#845 lo hace
irreversible: ni `session.wake` ni `inject_prompt` transportan un
adjunto, así que a una sesión multimodal terminada no se le puede volver
a hablar. La sesión sigue viva y el driver vuelve a preguntar.

## Quién abre la conversación

La abre el driver, con una acotación — `[La sesión se abre…]` — y las
palabras las pone la persona. El escriba saluda y hace **dos preguntas
a la vez**, que es la única vez que le está permitido: si quieres
enseñarle algo nuevo, o si seguís con un tema concreto que **elige él**
de lo que ya sabe, nombrando qué le falta de ese tema.

Para poder elegirlo tiene que saber qué sabe en el momento de despertar,
y ahí no vale la inyección automática de memorias: `enrich_prompt`
escoge las pistas POR PALABRAS CLAVE del prompt
(`memory/plugin.py:847-861`), y una acotación no tiene ninguna, así que
no engancharía con nada. El inventario se calcula antes del primer turno
con un prefetch:

    .jaato/agents/escriba.md      {{!py:scripts/inventario.py}}
    .jaato/scripts/inventario.py  render(context, args) -> str

Corre en la preparación de la sesión, llega al plugin de memoria por
`context.registry` y rinde los temas con sus cuentas y su antigüedad. Sin
round-trip al modelo y **sin herramientas nuevas en el esquema** — que
importa, porque cada nombre del esquema es una palabra que el modelo de
voz puede leer en voz alta.

**El inventario es la lista completa de lo que sabe, y la persona se lo
dice así.** Un ejemplo con contenido realista en la persona es munición
para confabular: la primera versión traía un saludo de muestra que hablaba
de hidroponía, y con el inventario vacío el modelo lo recitó y lo adornó
—«apunté que usas un sistema hidropónico, pero no sé qué nutrientes
añades»— inventándose la vida del usuario en su primera frase. Para un
segundo cerebro eso es la peor avería posible: si se inventa lo que sabe,
ya no se le puede creer nada.

Arreglado quitando el ejemplo (la forma se describe en prosa, que no se
puede recitar) y poniendo la regla donde están los datos: cada rama del
prefetch enuncia la suya, porque el prefetch es quien sabe si hay temas o
no. Comprobado en los dos casos — sin memorias dice «empezamos de cero» y
no nombra ningún tema; con memorias propone el más flojo y cita su
descripción real.

El reparto es el de siempre: aquí se cuenta lo CONTABLE — qué temas hay,
cuántas piezas, cuándo se tocó cada uno. Cuál está flojo lo juzga el
escriba, porque eso es una valoración y no una cuenta.

## Los ficheros

| | |
|---|---|
| `run_escriba.py` | El driver. Todo el SDK son dos sesiones y tres `ask`. |
| `voice.py` | Oídos y boca: el puente hilo↔asyncio y el sumidero de audio. |
| `ptt_capture.py`, `pulse_playback.py` | Copiados sin tocar de `jaato-cascade-audio-interchange`. No saben nada de jaato. |
| `.jaato/agents/*.md` | Las dos personas. |
| `.jaato/profiles/` | `_base_*` agnóstico + set `openrouter_gpt_audio`. |

Todo lo que no es SDK vive fuera del driver a propósito: `run_escriba.py`
debe leerse como lo que quiere demostrar.

## Lo que el SDK se lleva

`IPCClient.session()` conecta, configura y crea la sesión. `Session.ask`
es dueño de la receta de enviar-y-esperar (`first-of {TURN_COMPLETED,
SESSION_TERMINATED}`), así que un turno no puede colgarse aquí.
Suscribirse a eventos, contar terminales y desuscribirse no aparece en
este repo porque no es del que escribe el driver.

`ask` y no `complete`: **una llamada es un TURNO**, y la conversación es
el bucle que la repite sobre la MISMA sesión. `complete` espera a que la
sesión TERMINE — correcto para una etapa de cascada, fatal para una
charla.

La simetría que hace posible hablar: `ask` devuelve lo que el modelo
ESCRIBIÓ y `on_media` entrega lo que DIJO, según suena. El audio del
usuario entra por `attachments`, y en un turno hablado el prompt va
VACÍO — la pregunta ES el adjunto.

## Incidente del 2026-09-09 — leer antes de tocar los permisos

En su primer arranque el curador **borró tres memorias reales del
usuario**, dos personales, con 22 y 18 usos. Se recuperaron íntegras del
journal de la sesión, que es suerte y no diseño.

Dos causas, ambas estructurales:

1. **`delete_memory` estaba en la lista blanca**, y la persona decía
   «prefiere descartar a borrar». La prosa es una sugerencia; la lista
   blanca es el contrato. Al re-probar con la herramienta retirada, el
   modelo **volvió a intentar borrar** y fue denegado — que es la prueba
   de cuál de las dos capas manda.
2. **`allowed_scopes: ["project"]` no aislaba nada.** Es un *write-side
   gate*: se aplica solo al almacenar (`memory/plugin.py:1064`).
   `retrieve_memories` y `delete_memory` no lo consultan, y el nivel
   global apunta por defecto a `~/.jaato/memories.jsonl` — el almacén de
   toda la máquina.

Arreglado retirando `delete_memory` del whitelist y redirigiendo
`global_storage_path` al workspace. Verificado: el curador pasó de
recuperar 3 memorias del usuario a «no hay memorias en crudo», y el
almacén global quedó sin tocar.

> `jaato-scaffold validate` avisa de que `global_storage_path` «is not a
> declared memory config knob (silently ignored at runtime)». Es falso:
> `plugin.py:310` lo lee, y el cambio de comportamiento lo confirma. La
> lista de knobs declarados del validador está desactualizada — pero si
> una versión futura retira el knob, esta separación se pierde en
> silencio.

## Procedencia — verificado contra el framework INSTALADO

Buena parte del diseño viene de `jaato-cascade-audio-interchange`, cuyo
`KNOWN_ISSUES.md` es una foto contra un servidor más viejo. Contra el
0.7.0 instalado:

| | estado real en 0.7.0 |
|---|---|
| **#822** tier sin `model`/`provider` arriba no arranca | **ARREGLADO.** `runner_spawn.py:455-464` documenta la cadena `profile.provider → model_tiers[initial].provider → JAATO_PROVIDER`. Comprobado: este perfil, solo-tiers, crea sesión en 1,5 s. Si siguiera vivo, serían 60 s y `envelope.provider_name is empty`. |
| **#845** ni `wake` ni `inject_prompt` llevan adjunto | **SIGUE VIVO.** `inject_prompt(text, source_type, source_id, timeout)`; `command_router.py` no menciona adjuntos. Solo `send_message(text, attachments=…)` los lleva — que es la razón de que ninguna sesión declare esquema de completion. |

No verificado aquí, heredado de los comentarios de aquel repo: que
`temperature: 0.0` hace bucle en gpt-audio (818 s medidos) y que el
`-mini` no sale del seseo. Son medidas de comportamiento del modelo, no
del framework, y pueden haber cambiado.

## Requisitos

`parec`, `paplay`, `pactl`, `pw-metadata`, un daemon jaato en
`/tmp/jaato.sock`, y credencial de OpenRouter en
`~/.jaato/openrouter_auth.json` (`openrouter-auth`). El micrófono es el
`wraith_mic` de `ptt_capture.py`.
