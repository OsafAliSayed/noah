# A custom "Hey Noah" wake word

Noah starts with openWakeWord's pretrained **"Hey Jarvis"** model, which needs no training. A custom model is trained once, off the laptop, then dropped in as a file.

## Train it

openWakeWord trains wake word models from synthetic speech, so you don't record hundreds of samples yourself. Its training notebook does everything: it generates thousands of "hey noah" clips with Piper voices, mixes in background noise and room echo, and trains a small model.

1. Open the openWakeWord automatic training notebook in Google Colab (linked from the openWakeWord README under "Training New Models"). A free Colab GPU runtime is enough.
2. Set the target phrase to `hey noah`. Phrases with two or more syllables work best. "Noah" alone is short and common, so expect more false wake-ups with it.
3. Keep the default number of examples (tens of thousands). Training takes about an hour.
4. Download the `.onnx` file it produces, for example `hey_noah.onnx`.

## Use it

```bash
mkdir -p ~/.local/share/noah
cp ~/Downloads/hey_noah.onnx ~/.local/share/noah/
```

Then set it in `~/.config/noah/config.toml` (run `noah config init` first if you have no config file):

```toml
[voice]
wakeword_model = "~/.local/share/noah/hey_noah.onnx"
wakeword_threshold = 0.5
```

Restart Noah (`systemctl --user restart noah` if it runs as a service).

## Tune it

- **False wake-ups** (it wakes when nobody said it): raise `wakeword_threshold` to 0.6 or 0.7.
- **Missed wake-ups** (you say it and nothing happens): lower it to 0.4, or speak a little closer to the mic.
- **Fan noise:** run something heavy and talk over it. If wake-ups get unreliable, turn on PipeWire's noise suppression (`noise-suppression-for-voice`) and select the filtered source as `mic_device`.
- Run `noah run -v` to see debug logs while you test.
