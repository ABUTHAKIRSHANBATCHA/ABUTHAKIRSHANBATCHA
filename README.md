# Personal AI Assistant

A voice assistant that runs on your own Windows PC, controls it, knows your BIM tools,
speaks English and Tamil, and shows a holographic display on the PC and on your phone.
The name is a setting, so you can choose it later.

## What it does today

| Area | Status |
|---|---|
| Holographic HUD (PC and phone, same conversation on both) | Working |
| Voice in, English and Tamil (tap the mic, `EN` / `தமிழ்` button, or say "switch to Tamil") | Working in Chrome / Edge |
| Spoken replies in the language you used | Working (Tamil needs a Tamil voice installed on the device) |
| PC control: open apps, files and websites, read and write files, run PowerShell | Working |
| Approval before any command or file write, by button or by saying "yes" / "சரி" | Working |
| Phone connection over Wi-Fi with one-time PIN pairing | Working |
| Find installed BIM software, launch it, find model files (.rvt .nwd .ifc .dwg ...) | Written for Windows; not yet run on a Windows PC |
| Live Revit model queries (model info, element counts, warnings, levels, sheets, links) | Written; not yet run inside Revit |
| Brain (Claude) | Needs your API key; without one it only opens things and reports status |

## Start it (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) (tick "Add to PATH").
2. Get an API key from <https://platform.claude.com> and set it once in a terminal:
   `setx ANTHROPIC_API_KEY "your-key"` (then open a new terminal).
3. Double-click `start.bat`. The HUD opens at <http://127.0.0.1:8765>.

Use Chrome or Edge. Tap the mic (or press Space) and talk. `HANDS-FREE` keeps listening
after each reply. Tap the mic while it is speaking to interrupt.

## Connect your phone

1. Phone and PC on the same Wi-Fi.
2. On the PC, open `MENU`. It shows an address like `https://192.168.1.20:8443` and a PIN.
3. Open that address in Chrome on the phone, accept the certificate warning once
   (the certificate is created on your PC), and enter the PIN.
4. In Chrome's menu choose "Add to Home screen" to get an app icon.

Windows may ask to allow Python through the firewall the first time; allow it for
private networks.

## Give it a name

Copy `config.example.json` to `config.json` and edit:

```json
{ "name": "Jarvis", "owner": "Abu" }
```

Other settings: `model`, `effort` (`low` is faster, `high` is smarter),
`default_language` (`en-IN` or `ta-IN`), `bim_search_roots` (folders to search for models).

## Connect Revit

1. Install [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases).
2. pyRevit tab > Settings > Custom Extension Directories > add this project's `revit` folder.
3. pyRevit Settings > Routes > enable the Routes server (port 48884). Restart Revit.

Then ask "how many warnings are in my model?" The bridge is read-only.

## Safety

- The PC's own browser is trusted. Every other device must pair with the PIN, and each PIN
  works once.
- Running commands and writing files always wait for your approval.
- Anyone holding a paired phone can talk to the assistant, so pair only your own devices.
  To unpair everything, delete `data/devices.json`.
- Your speech is turned into text by the browser (Chrome sends audio to Google for this),
  and the text goes to the Claude API.

## Not built yet

- **Recognising only your voice** (speaker verification) and a wake word ("Hey ...").
  Both need an audio model running on the PC; today, access is protected by pairing instead.
- Using the phone away from home Wi-Fi.
- Changing the Revit model (the bridge only reads), Navisworks and ACC automation.
- Controlling the phone itself (calls, messages, notifications).
- A physical hologram: the display is a hologram-style screen. A fan or pyramid
  holographic display can show it as-is.

## Development

```
pip install -r requirements.txt pytest httpx
python -m pytest tests
python -m assistant
```
