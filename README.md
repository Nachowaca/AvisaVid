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
