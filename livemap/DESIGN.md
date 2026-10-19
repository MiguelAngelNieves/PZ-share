# PZ Live Map — guía de diseño (estilo «D · Híbrido»)

Piel de «Radio de campo» (oscuro, oliva, ámbar, títulos condensados) sobre el esqueleto de «Mapa limpio»
(tarjetas flotantes redondeadas, números grandes, una tarjeta por jugador). Primero celular; en PC el
mismo contenido va en una barra lateral a la izquierda del mapa.

## Tokens

| Token | Valor | Uso |
|---|---|---|
| `--bg` | `#12150F` | Fondo de la app |
| `--surface` | `#1C2119` | Tarjetas |
| `--sheet` | `#1A1F17` | Hoja inferior y menú |
| `--chip` | `#2A3224` | Chips de estado, separadores |
| `--border` | `#2E3628` | Bordes de tarjetas |
| `--text` | `#ECE9DD` | Texto principal |
| `--muted` | `#A9A894` | Texto secundario (4.5:1 sobre `--bg`) |
| `--accent` | `#E8A33D` | Acción principal, «tú», selección |
| `--teal` | `#6CC4B6` | En vivo, segundo jugador |
| `--danger` | `#E58B7B` | Herido / sangrando (fondo `#3A221E`, borde `#5A2F28`) |
| `--fog` | `#0B0D09` al 84 % | Niebla de guerra |
| Radio | 22 px barras flotantes · 18 px tarjetas · 14 px chips grandes · 12 px chips | |
| Sombra | `0 4px 16px rgba(0,0,0,.35)` en lo que flota sobre el mapa | |

Tipografía: **Barlow Condensed 700** para títulos, día/hora y números grandes (vida, stats).
**Manrope 500–800** para todo lo demás. Nada en monoespaciada salvo coordenadas.

Colores de jugador: ámbar, verde azulado, luego `#9DB4F0`, `#E9A0D2`, `#C3E05A`, `#F0B27A` (el primero
siempre es «tú» si lo elegiste).

## Pantallas y componentes

- **Barra superior flotante**: `DÍA 14 · 21:40` (Barlow 24) + «PZ2026 · X hostea · N en línea» + chip EN VIVO.
- **Interruptor de niebla**: chips «Explorado · N km²» / «Mapa completo». Se recuerda por persona.
- **Marcador de jugador**: círculo 36 px de su color, inicial en Barlow, borde 3 px `--bg`. Desconectado = gris 50 %.
- **Marcas**: píldora oscura con ícono (base, loot, peligro, carro, otra) + texto corto. Tocar = editar/borrar.
- **Ping**: botón grande ámbar «PING» (56 px). Modo ping: tocar el mapa envía; el globo «X marcó aquí · 140 m al NE»
  vive 2 min en el mapa y llega al juego como texto sobre tu personaje.
- **Hoja inferior** (celular): una tarjeta por jugador en rejilla de 2: nombre, vida grande, barra, 2 estados.
- **Grupo**: tarjeta completa por jugador: vida, estados del juego (hambre, sed, cansancio, pánico, dolor,
  heridas por zona), arma, peso que carga, vehículo y gasolina. **Nunca** la infección (el personaje no la conoce).
- **Tareas**: lista compartida; casilla, texto, inicial de quien la creó; campo «Nueva tarea…» siempre visible.
- **Álbum**: capturas de Steam (F12) agrupadas por día; «Fotos / En el mapa».
- **Timelapse**: botón reproducir 56 px, barra de días, velocidades x1 / x8 / x32, frase de la bitácora del momento.
- **Logros**: 4 cifras (días, kills, km, km² explorados), récords, logros del grupo con progreso.
- **Menú inferior**: Mapa · Grupo · Tareas · Álbum · Logros (íconos de trazo 22 px, activo en ámbar).

## Estados

- Sin partida activa: barra superior «Nadie está jugando · última vez hace 2 h»; el mapa muestra lo explorado.
- Sin conexión: chip EN VIVO pasa a «RECONECTANDO» en `--muted`.
- Jugador herido: su tarjeta toma borde `--danger` y el número de vida en `#EFA594`.
- Jugador muerto: marcador con ✕, tarjeta atenuada, evento en la bitácora.
- Listas vacías: frase corta que dice qué hacer («Saca una captura con F12…», «Anoten lo que falta…»).

## Accesibilidad

Botones reales, objetivos ≥ 44 px, `aria-label` en botones de solo ícono, texto ≥ 4.5:1, el estado nunca
depende solo del color (siempre va con palabra).

## Responsivo

- < 900 px: celular (mapa a pantalla completa + hoja inferior + menú inferior; las demás secciones a pantalla completa).
- ≥ 900 px: barra lateral de 380 px con las secciones en pestañas + mapa a la derecha.
