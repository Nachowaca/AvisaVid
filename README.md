# AvisaVid

App de escritorio simple (macOS) para cargar un video y leer:
- Texto que aparece en pantalla (OCR)
- Lo que se dice en el audio (transcripción local con Whisper)

También muestra formato, duración, resolución y tamaño del archivo,
y te deja editar el texto detectado antes de exportarlo a `.txt`.

## 1. Instalar dependencias del sistema

```bash
brew install ffmpeg tesseract tesseract-lang
```

## 2. Instalar dependencias de Python

```bash
cd AvisaVid
pip3 install -r requirements.txt
```

> La primera vez que transcriba audio, `faster-whisper` va a descargar
> el modelo "base" (~150MB). Necesita internet esa primera vez; después
> funciona offline.

## 3. Correr la app

```bash
python3 app.py
```

## Uso

1. "Elegir video…" y seleccioná el archivo.
2. Vas a ver formato / duración / resolución / tamaño arriba.
3. "Analizar video" — extrae frames y hace OCR, y si hay audio lo transcribe.
4. El resultado aparece abajo con marca de tiempo, ej:
   `[00:07] (pantalla) Estamos preparados`
   `[00:12] (audio) alguna frase dicha en el video`
5. Podés editar el texto directo en el cuadro.
6. "Exportar .txt" para guardar.

## Compilar la app (.app + .dmg) — solo en macOS

Esto empaqueta todo (Python incluido) para no depender de tener nada instalado
en la Mac donde la abras.

```bash
# 1) Herramienta de empaquetado
pip3 install py2app

# 2) Convertir icon.png a .icns (formato de ícono de macOS)
mkdir icon.iconset
sips -z 16 16   icon.png --out icon.iconset/icon_16x16.png
sips -z 32 32   icon.png --out icon.iconset/icon_16x16@2x.png
sips -z 32 32   icon.png --out icon.iconset/icon_32x32.png
sips -z 64 64   icon.png --out icon.iconset/icon_32x32@2x.png
sips -z 128 128 icon.png --out icon.iconset/icon_128x128.png
sips -z 256 256 icon.png --out icon.iconset/icon_128x128@2x.png
sips -z 256 256 icon.png --out icon.iconset/icon_256x256.png
sips -z 512 512 icon.png --out icon.iconset/icon_256x256@2x.png
sips -z 512 512 icon.png --out icon.iconset/icon_512x512.png
cp icon.png icon.iconset/icon_512x512@2x.png
iconutil -c icns icon.iconset

# 3) Compilar la app
python3 setup.py py2app

# 4) Armar el .dmg
hdiutil create -volname "AvisaVid" -srcfolder dist/AvisaVid.app -ov -format UDZO AvisaVid.dmg
```

Queda `AvisaVid.dmg` en la carpeta del proyecto. Subilo a Drive, bajalo en la
otra Mac, abrilo y arrastrá `AvisaVid.app` a Aplicaciones.

**Primera vez que la abras (en cualquier Mac, incluida la tuya):** macOS va a
avisar "no se puede verificar el desarrollador" porque no está firmada — es
esperable para una app de uso personal. Solución: click derecho sobre
`AvisaVid.app` → **Abrir** → confirmar. Solo hace falta esa vez; después abre
normal con doble click.

**Importante:** `ffmpeg` y `tesseract` (instalados con `brew` en el paso 1)
NO quedan incluidos en el `.app` — tienen que estar instalados en la Mac
donde la corras. Si la otra Mac no los tiene, vas a ver el aviso de
"Faltan dependencias" al abrir la app; corré `brew install ffmpeg tesseract
tesseract-lang` ahí también.

## Notas / límites de esta primera versión

- El OCR muestrea ~1 frame cada tanto (según duración, hasta 24 frames),
  no es cuadro por cuadro — suficiente para carteles/subtítulos que
  duran más de un segundo.
- La transcripción usa el modelo "base" de Whisper (rápido, buena
  precisión en español). Si querés más precisión, se puede cambiar a
  "small" o "medium" en `app.py` (línea `WhisperModel("base", ...)`),
  a costo de más tiempo de proceso.
- No hay drag & drop todavía (solo botón "Elegir video…"). Si lo querés,
  se agrega con `tkinterdnd2` en una siguiente vuelta.
